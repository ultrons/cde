"""`cde run` — render the project's template, kubectl-apply, record run.

v0 is fire-and-forget: we apply the manifest, write the history row,
and exit. Wait/log streaming come in Phase 3.

Per team-quota convention, we derive namespace and priorityClass from
the team key:

  namespace      = "team-<team>"          (chart's default namespacePrefix)
  priorityClass  = "<namespace>-priority"

Either can be overridden via cde.yaml.defaults_overrides.namespace /
.priority_class. The next iteration (v0.5) will read these from the
team-quota ConfigMap on the cluster.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

from cde import (
    config,
    context_hash,
    db,
    git_info,
    k8s,
    logging as log,
    paths,
    templating,
)
from cde import preferences as prefs_mod


def register(subparsers: argparse._SubParsersAction) -> None:
  p = subparsers.add_parser(
      "run",
      help="Render the template, apply via kubectl, record in history.",
  )
  p.add_argument("--tag", required=True, help="run id (e.g. v140)")
  p.add_argument(
      "--note", default="", help="freeform note recorded against this run"
  )
  p.add_argument(
      "--hypothesis",
      default="",
      help="what you set out to test with this run",
  )
  p.add_argument(
      "--value-class",
      dest="value_class",
      default=None,
      help="override defaults.value-class for this run",
  )
  p.add_argument(
      "--declared-minutes",
      dest="declared_minutes",
      type=int,
      default=None,
      help="override defaults.declared-duration-minutes for this run",
  )
  p.add_argument(
      "--num-slices",
      dest="num_slices",
      type=int,
      default=None,
      help="override defaults.num-slices for this run",
  )
  p.add_argument(
      "--set",
      action="append",
      default=[],
      metavar="KEY=VALUE",
      help="template variable override (repeatable)",
  )
  p.add_argument(
      "--render-only",
      action="store_true",
      help="render the manifest to stdout and exit (no apply, no record)",
  )
  p.add_argument(
      "--dry-run",
      action="store_true",
      help="render and apply with kubectl --dry-run=client (no real apply)",
  )
  p.set_defaults(func=run)


def _parse_set(items: list[str]) -> dict[str, str]:
  out: dict[str, str] = {}
  for it in items:
    if "=" not in it:
      raise SystemExit(f"--set must be KEY=VALUE, got {it!r}")
    k, v = it.split("=", 1)
    out[k.strip()] = v.strip()
  return out


def _derive_namespace_priorityclass(
    cfg: config.CdeConfig,
) -> tuple[str, str]:
  ovr = cfg.defaults_overrides
  if "namespace" in ovr and "priority_class" in ovr:
    return str(ovr["namespace"]), str(ovr["priority_class"])
  ns = str(ovr.get("namespace", f"team-{cfg.team}"))
  pc = str(ovr.get("priority_class", f"{ns}-priority"))
  return ns, pc


def run(args: argparse.Namespace) -> int:
  cfg_path = paths.project_config_path()
  if not cfg_path.is_file():
    log.err("no cde.yaml found (run `cde init` first)")
    return 1
  cfg = config.load(cfg_path)
  prefs = prefs_mod.load()
  project_root = cfg_path.parent

  set_overrides = _parse_set(args.set)
  value_class = args.value_class or cfg.defaults.value_class
  declared_min = (
      args.declared_minutes
      if args.declared_minutes is not None
      else cfg.defaults.declared_duration_minutes
  )
  num_slices = (
      args.num_slices
      if args.num_slices is not None
      else cfg.defaults.num_slices
  )
  namespace, priority_class = _derive_namespace_priorityclass(cfg)

  # Compute image tag from current build context. This must match what
  # `cde build` produced. Identical context → identical tag → user
  # presumed to have run cde build already (we don't auto-build in v0).
  ctx_dir = (project_root / cfg.image.context).resolve()
  dockerfile = (project_root / cfg.image.dockerfile).resolve()
  sha7 = context_hash.context_hash(ctx_dir, dockerfile=dockerfile)
  image_tag = f"{cfg.image.repo_path}:cde-{sha7}"

  # Build the substitution context for the template.
  template_path = (project_root / cfg.template).resolve()
  template_ctx: dict[str, Any] = {
      "run_id": args.tag,
      "image": image_tag,
      "team": cfg.team,
      "value_class": value_class,
      "declared_minutes": declared_min,
      "namespace": namespace,
      "priority_class": priority_class,
      "tpu_type": cfg.defaults.tpu_type,
      "num_slices": num_slices,
      "overrides": {**cfg.defaults_overrides, **set_overrides},
      "env": [],  # populated by --env in v0.5
  }

  log.step("rendering %s", template_path.relative_to(project_root))
  manifest = templating.render(template_path, template_ctx)

  if args.render_only:
    sys.stdout.write(manifest)
    return 0

  gi = git_info.info_for(project_root)
  if prefs.git.fail_on_dirty and gi.dirty:
    log.err("uncommitted changes detected (git.fail_on_dirty=true)")
    return 1
  if gi.dirty:
    log.warn("running with uncommitted changes (git_dirty=true)")

  # Record the row BEFORE apply — even a failed apply is data.
  run_row = db.Run(
      run_id=args.tag,
      submitter="",
      status="submitted",
      git_sha=gi.sha,
      git_dirty=gi.dirty,
      image_tag=image_tag,
      manifest_text=manifest,
      overrides=template_ctx["overrides"],
      template_path=str(template_path),
      team=cfg.team,
      value_class=value_class,
      declared_min=declared_min,
      k8s_namespace=namespace,
      jobset_name=args.tag,
      notes=args.note,
      hypothesis=args.hypothesis,
  )

  with db.open_db(_resolve_history_path(cfg)) as conn:
    if db.get_run(conn, args.tag) is not None:
      log.err("run id %r already exists in history; pick a fresh --tag", args.tag)
      return 1
    db.insert_run(conn, run_row)

  log.step(
      "applying to namespace=%s priorityClass=%s%s",
      namespace,
      priority_class,
      " (dry-run)" if args.dry_run else "",
  )
  try:
    out = k8s.apply(manifest, dry_run=args.dry_run)
  except k8s.KubectlError as exc:
    with db.open_db(_resolve_history_path(cfg)) as conn:
      db.set_status(conn, args.tag, "failed", finished=True)
    log.err("%s", exc)
    return 1

  if out:
    log.detail(out)

  with db.open_db(_resolve_history_path(cfg)) as conn:
    db.set_status(conn, args.tag, "running")

  log.ok("submitted %s as %s/%s", args.tag, namespace, args.tag)
  log.detail("kubectl logs -n %s -l cde.io/run-id=%s --prefix=true -f", namespace, args.tag)
  return 0


def _resolve_history_path(cfg: config.CdeConfig) -> Path:
  raw = cfg.history.path
  return Path(raw).expanduser() if raw.startswith("~") else Path(raw)
