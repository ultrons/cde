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
    recent,
    templating,
)
from cde import preferences as prefs_mod


def register(subparsers: argparse._SubParsersAction) -> None:
  from cde import cli, completers

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
  cli.set_completer(
      p.add_argument(
          "--value-class",
          dest="value_class",
          default=None,
          help="override defaults.value-class for this run",
      ),
      completers.value_class_completer,
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
      help="template variable override (repeatable). Values are strings.",
  )
  p.add_argument(
      "--flag",
      action="append",
      default=[],
      metavar="NAME",
      help=(
          "Set override NAME to True (repeatable). Templates render bool-True"
          " overrides as bare `--name` flags, not `--name=value`."
      ),
  )
  p.add_argument(
      "--no-flag",
      dest="no_flag",
      action="append",
      default=[],
      metavar="NAME",
      help=(
          "Set override NAME to False (repeatable). Useful for explicitly"
          " disabling a flag inherited from --inherit or defaults_overrides."
      ),
  )
  cli.set_completer(
      p.add_argument(
          "--inherit",
          dest="inherit_from",
          default=None,
          metavar="RUN_ID",
          help=(
              "Copy --set overrides from <RUN_ID> as the base for this run."
              " --set values on this run override inherited keys."
              " Records parent_run for cde lineage."
          ),
      ),
      completers.run_id_completer,
  )
  p.add_argument(
      "--profile",
      action="store_true",
      help=(
          "Auto-wire profile_uri = <profile.base-uri>/<run_id>/ on the row"
          " and inject JAX_PROFILER_DIR=... into the pod env."
      ),
  )
  p.add_argument(
      "--wait",
      action="store_true",
      help=(
          "After applying, tail the JobSet's logs and update history when"
          " it finishes. Equivalent to: cde run ... && cde logs <tag>."
      ),
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


def _parse_flags(
    flag_items: list[str], no_flag_items: list[str]
) -> dict[str, bool]:
  """Merge --flag NAME (True) and --no-flag NAME (False) into one dict.
  --no-flag wins over --flag if both name the same key on this run, since
  it's the more explicit "I do not want this" gesture."""
  out: dict[str, bool] = {k.strip(): True for k in flag_items if k.strip()}
  for k in no_flag_items:
    name = k.strip()
    if name:
      out[name] = False
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

  set_overrides: dict[str, Any] = dict(_parse_set(args.set))
  flag_overrides = _parse_flags(args.flag, args.no_flag)
  # --flag / --no-flag take precedence over a same-key --set on this run:
  # mixing both for the same key is almost certainly a mistake, and the bool
  # form is the more explicit shape.
  for k, v in flag_overrides.items():
    if k in set_overrides:
      log.warn(
          "--flag/--no-flag %s overrides earlier --set %s=%s on this run",
          k, k, set_overrides[k],
      )
    set_overrides[k] = v

  # Sticky defaults from last run in this project. Only used when the
  # corresponding flag was NOT explicitly passed. Logged when applied so
  # the inheritance is never silent.
  sticky = recent.load(cfg.project)

  if args.value_class is not None:
    value_class = args.value_class
  elif sticky.value_class is not None:
    value_class = sticky.value_class
    log.detail("defaulting --value-class=%s from your last run", value_class)
  else:
    value_class = cfg.defaults.value_class

  if args.declared_minutes is not None:
    declared_min = args.declared_minutes
  elif sticky.declared_minutes is not None:
    declared_min = sticky.declared_minutes
    log.detail(
        "defaulting --declared-minutes=%d from your last run", declared_min
    )
  else:
    declared_min = cfg.defaults.declared_duration_minutes

  if args.num_slices is not None:
    num_slices = args.num_slices
  elif sticky.num_slices is not None:
    num_slices = sticky.num_slices
    log.detail("defaulting --num-slices=%d from your last run", num_slices)
  else:
    num_slices = cfg.defaults.num_slices
  namespace, priority_class = _derive_namespace_priorityclass(cfg)

  # --inherit: pull the parent's overrides as the base, then layer this
  # run's --set on top. Also captures the parent_run for lineage.
  parent_run_id: str | None = None
  inherited_overrides: dict[str, Any] = {}
  if args.inherit_from:
    history_path = _resolve_history_path(cfg)
    with db.open_db(history_path) as conn:
      parent = db.get_run(conn, args.inherit_from)
    if parent is None:
      log.err(
          "--inherit %r: no such run in history. Try `cde history` to list.",
          args.inherit_from,
      )
      return 1
    inherited_overrides = dict(parent.overrides)
    parent_run_id = parent.run_id
    log.detail(
        "inheriting %d override(s) from %s: %s",
        len(inherited_overrides),
        args.inherit_from,
        ", ".join(f"{k}={v}" for k, v in sorted(inherited_overrides.items())) or "(none)",
    )

  # Profile wiring
  profile_uri: str | None = None
  profile_dir = ""  # template inserts this into JAX_PROFILER_DIR env
  if args.profile:
    if cfg.profile is None or not cfg.profile.base_uri:
      log.err(
          "--profile passed but cde.yaml has no `profile.base-uri` set."
      )
      return 1
    profile_uri = cfg.profile.base_uri.rstrip("/") + f"/{args.tag}/"
    profile_dir = profile_uri
    log.detail("profile path: %s", profile_uri)

  # Compute image tag from current build context. This must match what
  # `cde build` produced. Identical context → identical tag → user
  # presumed to have run cde build already (we don't auto-build in v0).
  ctx_dir = (project_root / cfg.image.context).resolve()
  dockerfile = (project_root / cfg.image.dockerfile).resolve()
  sha7 = context_hash.context_hash(ctx_dir, dockerfile=dockerfile)
  image_tag = f"{cfg.image.repo_path}:cde-{sha7}"

  # Build the substitution context for the template.
  # Override layering, lowest precedence to highest:
  #   1. cfg.defaults_overrides   (project-stable knobs in cde.yaml)
  #   2. inherited from --inherit (parent run's --set values)
  #   3. --set on this run        (highest precedence)
  effective_overrides = {
      **cfg.defaults_overrides,
      **inherited_overrides,
      **set_overrides,
  }
  template_path = (project_root / cfg.template).resolve()
  env_pairs: list[dict[str, str]] = []
  if profile_dir:
    env_pairs.append({"name": "JAX_PROFILER_DIR", "value": profile_dir})
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
      "overrides": effective_overrides,
      "profile_dir": profile_dir,
      "env": env_pairs,
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
      project=cfg.project,
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
      profile_uri=profile_uri,
      notes=args.note,
      hypothesis=args.hypothesis,
      parent_run=parent_run_id,
  )

  with db.open_db(_resolve_history_path(cfg)) as conn:
    if db.get_run(conn, args.tag) is not None:
      log.err("run id %r already exists in history; pick a fresh --tag", args.tag)
      return 1
    db.insert_run(conn, run_row)

  ctx = k8s.current_context()
  log.step(
      "applying to context=%s namespace=%s priorityClass=%s%s",
      ctx or "(unset — kubectl will fail)",
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

  # Update sticky defaults for the next run in this project.
  recent.save(
      cfg.project,
      recent.RecentDefaults(
          value_class=value_class,
          team=cfg.team,
          num_slices=num_slices,
          declared_minutes=declared_min,
      ),
  )

  log.ok("submitted %s as %s/%s", args.tag, namespace, args.tag)
  log.detail("kubectl logs -n %s -l cde.io/run-id=%s --prefix=true -f", namespace, args.tag)

  if args.wait:
    # Reuse the cde logs path so behavior + status update logic is identical.
    log.step("--wait: tailing until done; Ctrl-C to detach")
    from cde.commands import logs as logs_cmd  # local import to avoid cycle

    wait_args = argparse.Namespace(
        run_id=args.tag,
        follow=True,
        since=None,
        all_pods=False,
        replica=None,
        container=None,
    )
    return logs_cmd.run(wait_args)

  return 0


def _resolve_history_path(cfg: config.CdeConfig) -> Path:
  raw = cfg.history.path
  if not raw:
    return paths.history_db_path()
  return Path(raw).expanduser()
