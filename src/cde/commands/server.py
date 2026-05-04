"""`cde server up | down | wait-ready` — inference server lifecycle.

Distinct from batch `cde run` because:

  - The server stays up; it doesn't terminate. Status stays 'running'.
  - Health is observed externally (kubectl port-forward to /health).
  - Teardown is explicit (cde server down).
  - You typically iterate on bench/eval clients without restarting it.

Uses cfg.server.template (separate from cfg.template). Records as a
regular run row with status 'running'.
"""

from __future__ import annotations

import argparse
import datetime
import subprocess
import time
import urllib.request
import urllib.error
from pathlib import Path
from typing import Any

from cde import (
    config,
    crane,
    db,
    git_info,
    k8s,
    logging as log,
    paths,
    suggest,
    templating,
)


def register(subparsers: argparse._SubParsersAction) -> None:
  from cde import cli, completers

  p = subparsers.add_parser("server", help="Inference-server lifecycle.")
  sp = p.add_subparsers(dest="server_cmd", required=True)

  pu = sp.add_parser("up", help="Render the server template and apply.")
  pu.add_argument("--tag", required=True, help="server-run id (e.g. server-001)")
  pu.add_argument("--note", default="")
  pu.add_argument(
      "--set", action="append", default=[], metavar="KEY=VALUE",
      help="template variable override (repeatable)",
  )
  pu.add_argument("--num-slices", dest="num_slices", type=int, default=None)
  pu.add_argument("--declared-minutes", dest="declared_minutes", type=int, default=None)
  pu.add_argument(
      "--context",
      dest="kubectl_context",
      default=None,
      help=(
          "kubectl context to apply to. Default: kubectl config"
          " current-context (snapshotted at submit, recorded on the run row,"
          " and reused by cde server down / wait-ready / logs / status)."
      ),
  )
  cli.set_completer(
      pu.add_argument("--value-class", dest="value_class", default=None),
      completers.value_class_completer,
  )
  pu.set_defaults(func=_up)

  pd = sp.add_parser("down", help="Tear down a running server.")
  cli.set_completer(pd.add_argument("run_id"), completers.run_id_completer)
  pd.set_defaults(func=_down)

  pw = sp.add_parser("wait-ready", help="Block until /health responds 200.")
  cli.set_completer(pw.add_argument("run_id"), completers.run_id_completer)
  pw.add_argument("--timeout-s", type=int, default=600)
  pw.add_argument("--poll-interval-s", type=int, default=5)
  pw.set_defaults(func=_wait_ready)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _resolve_db_path(cfg: config.CdeConfig) -> Path:
  if cfg.history.path:
    return Path(cfg.history.path).expanduser()
  return paths.history_db_path()


def _now() -> str:
  return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def _parse_set(items: list[str]) -> dict[str, str]:
  out: dict[str, str] = {}
  for it in items:
    if "=" not in it:
      raise SystemExit(f"--set must be KEY=VALUE, got {it!r}")
    k, v = it.split("=", 1)
    out[k.strip()] = v.strip()
  return out


def _derive_namespace_priorityclass(cfg: config.CdeConfig) -> tuple[str, str]:
  ovr = cfg.defaults_overrides
  ns = str(ovr.get("namespace", f"team-{cfg.team}"))
  pc = str(ovr.get("priority_class", f"{ns}-priority"))
  return ns, pc


def _load_cfg() -> tuple[config.CdeConfig, Path] | None:
  cfg_path = paths.project_config_path()
  if not cfg_path.is_file():
    log.err("no cde.yaml found")
    return None
  cfg = config.load(cfg_path)
  return cfg, cfg_path.parent


# ---------------------------------------------------------------------------
# server up
# ---------------------------------------------------------------------------


def _up(args: argparse.Namespace) -> int:
  loaded = _load_cfg()
  if loaded is None:
    return 1
  cfg, project_root = loaded

  if cfg.server is None:
    log.err(
        "no `server:` section in cde.yaml — set server.template before"
        " using `cde server`."
    )
    return 1

  template_path = (project_root / cfg.server.template).resolve()
  if not template_path.is_file():
    log.err("server template not found: %s", template_path)
    return 1

  image_tag = crane.expected_tag(cfg.image, project_root)

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
      "profile_dir": "",
      "env": [],
      "server_port": cfg.server.port,
      "health_url": cfg.server.health_url,
  }

  log.step("rendering server template %s", template_path.relative_to(project_root))
  manifest = templating.render(template_path, template_ctx)

  if args.kubectl_context:
    ctx = args.kubectl_context
  else:
    ctx = k8s.current_context()
    if ctx is None:
      log.err(
          "no kubectl context available — pass --context, or set one with"
          " `kubectl config use-context <name>`."
      )
      return 1
    log.info(
        "no --context given; using current default %s — pass --context to override",
        ctx,
    )

  gi = git_info.info_for(project_root)
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
      k8s_context=ctx,
      jobset_name=args.tag,
      notes=args.note,
      tags=["server"],
  )

  with db.open_db(_resolve_db_path(cfg)) as conn:
    if db.get_run(conn, args.tag) is not None:
      log.err("server run %r already exists; tear down with cde server down", args.tag)
      return 1
    db.insert_run(conn, run_row)

  log.step("applying server manifest to context=%s namespace=%s", ctx, namespace)
  try:
    out = k8s.apply(manifest, context=ctx)
  except k8s.KubectlError as exc:
    with db.open_db(_resolve_db_path(cfg)) as conn:
      db.set_status(conn, args.tag, "failed", finished=True)
    log.err("%s", exc)
    return 1

  if out:
    log.detail(out)
  with db.open_db(_resolve_db_path(cfg)) as conn:
    db.set_status(conn, args.tag, "running")

  log.ok("server up: %s/%s", namespace, args.tag)
  log.detail("cde server wait-ready %s            # block until /health", args.tag)
  log.detail("cde logs %s                         # tail server logs", args.tag)
  log.detail("cde server down %s                  # tear down", args.tag)
  return 0


# ---------------------------------------------------------------------------
# server down
# ---------------------------------------------------------------------------


def _down(args: argparse.Namespace) -> int:
  loaded = _load_cfg()
  if loaded is None:
    return 1
  cfg, _ = loaded

  with db.open_db(_resolve_db_path(cfg)) as conn:
    r = db.get_run(conn, args.run_id)
    if r is None:
      ids = [x.run_id for x in db.list_runs(conn, limit=200)]
      log.err("no such run: %r.%s", args.run_id, suggest.hint(args.run_id, ids))
      return 1

  if not r.k8s_namespace:
    log.err("run %s has no k8s_namespace recorded", r.run_id)
    return 1

  argv = ["kubectl"]
  if r.k8s_context:
    argv.append(f"--context={r.k8s_context}")
  argv.extend([
      "delete", "jobset", r.jobset_name or r.run_id,
      "-n", r.k8s_namespace, "--ignore-not-found=true",
  ])
  log.step("$ %s", " ".join(argv))
  rc = subprocess.call(argv)
  if rc != 0:
    log.warn("kubectl delete returned %d", rc)

  with db.open_db(_resolve_db_path(cfg)) as conn:
    db.update_run(conn, args.run_id, status="ok", ts_finished=_now())
  log.ok("server down: %s", args.run_id)
  return 0


# ---------------------------------------------------------------------------
# server wait-ready
# ---------------------------------------------------------------------------


def _wait_ready(args: argparse.Namespace) -> int:
  loaded = _load_cfg()
  if loaded is None:
    return 1
  cfg, _ = loaded
  if cfg.server is None:
    log.err("no `server:` section in cde.yaml")
    return 1

  with db.open_db(_resolve_db_path(cfg)) as conn:
    r = db.get_run(conn, args.run_id)
    if r is None:
      ids = [x.run_id for x in db.list_runs(conn, limit=200)]
      log.err("no such run: %r.%s", args.run_id, suggest.hint(args.run_id, ids))
      return 1

  if not r.k8s_namespace:
    log.err("run %s has no k8s_namespace recorded", r.run_id)
    return 1

  port = cfg.server.port
  health_url = cfg.server.health_url

  ctx_args: list[str] = (
      [f"--context={r.k8s_context}"] if r.k8s_context else []
  )

  # Find the pod (we need it for the port-forward).
  q = subprocess.run(
      ["kubectl"] + ctx_args + [
          "get", "pods",
          "-n", r.k8s_namespace,
          "-l", f"cde.io/run-id={args.run_id}",
          "-o", "jsonpath={.items[0].metadata.name}",
      ],
      capture_output=True, text=True, check=False,
  )
  pod = q.stdout.strip()
  if q.returncode != 0 or not pod:
    log.err("no pod yet for run %s — wait a few seconds and retry", args.run_id)
    return 1

  log.step(
      "port-forward %s%s/%s :%d",
      f"context={r.k8s_context} " if r.k8s_context else "",
      r.k8s_namespace, pod, port,
  )
  pf = subprocess.Popen(
      ["kubectl"] + ctx_args + [
          "port-forward",
          f"pod/{pod}", "-n", r.k8s_namespace,
          f"{port}:{port}",
      ],
      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
  )

  try:
    deadline = time.monotonic() + args.timeout_s
    while time.monotonic() < deadline:
      try:
        with urllib.request.urlopen(health_url, timeout=2) as resp:
          if 200 <= resp.status < 300:
            log.ok("ready: %s responded %d", health_url, resp.status)
            return 0
      except (urllib.error.URLError, ConnectionError, TimeoutError):
        pass
      time.sleep(args.poll_interval_s)
    log.err("timed out waiting for %s after %ds", health_url, args.timeout_s)
    return 1
  finally:
    pf.terminate()
    try:
      pf.wait(timeout=2)
    except subprocess.TimeoutExpired:
      pf.kill()
