"""`cde sync <run>` — kubectl-cp local edits into a running pod on save.

Foreground only. Watches `cfg.sync[*].src` and copies changed files into
the run's first pod at `cfg.sync[*].dest`. Ctrl-C to stop.

Why foreground (not daemon): a sync session is bound to one specific
running pod. When the pod restarts (eviction, OOM, you killed it), the
target is gone and the sync is meaningless. Foreground means it ends
when you stop iterating, naturally.

This is the cde-side replacement for `skaffold sync`. No magic
file-event-to-build-step coupling — sync is just transport.
"""

from __future__ import annotations

import argparse
import subprocess
from pathlib import Path

from cde import config, db, k8s, logging as log, paths, suggest, watcher
from cde import preferences as prefs_mod


def register(subparsers: argparse._SubParsersAction) -> None:
  from cde import cli, completers

  p = subparsers.add_parser(
      "sync",
      help="Watch local sync paths and kubectl-cp into the run's pod on save.",
  )
  cli.set_completer(
      p.add_argument(
          "run_id",
          nargs="?",
          help=(
              "run to sync into; defaults to the most-recent running run in"
              " this project."
          ),
      ),
      completers.run_id_completer,
  )
  p.add_argument(
      "--debounce-ms",
      type=int,
      default=None,
      help="coalescing window for rapid saves (default: from preferences)",
  )
  p.set_defaults(func=run)


def _resolve_db_path(cfg: config.CdeConfig | None) -> Path:
  if cfg and cfg.history.path:
    return Path(cfg.history.path).expanduser()
  return paths.history_db_path()


def _pick_run(
    args: argparse.Namespace, cfg: config.CdeConfig
) -> db.Run | None:
  with db.open_db(_resolve_db_path(cfg)) as conn:
    if args.run_id:
      r = db.get_run(conn, args.run_id)
      if r is None:
        ids = [x.run_id for x in db.list_runs(conn, limit=200)]
        log.err("no such run: %r.%s", args.run_id, suggest.hint(args.run_id, ids))
      return r
    # Default: most recent running run in this project.
    runs = db.list_runs(conn, project=cfg.project, status="running", limit=1)
    if not runs:
      log.err(
          "no running run in project %r; pass <run_id> explicitly",
          cfg.project,
      )
      return None
    return runs[0]


def _find_pod(namespace: str, run_id: str) -> str | None:
  proc = subprocess.run(
      [
          "kubectl", "get", "pods",
          "-n", namespace,
          "-l", f"cde.io/run-id={run_id}",
          "-o", "jsonpath={.items[0].metadata.name}",
      ],
      capture_output=True, text=True, check=False,
  )
  if proc.returncode != 0 or not proc.stdout.strip():
    return None
  return proc.stdout.strip()


def _kubectl_cp(local: Path, namespace: str, pod: str, in_pod: str) -> int:
  argv = ["kubectl", "cp", str(local), f"{namespace}/{pod}:{in_pod}"]
  log.detail("$ %s", " ".join(argv))
  return subprocess.call(argv)


def run(args: argparse.Namespace) -> int:
  cfg_path = paths.project_config_path()
  if not cfg_path.is_file():
    log.err("no cde.yaml found (run `cde init` first)")
    return 1
  cfg = config.load(cfg_path)
  prefs = prefs_mod.load()
  project_root = cfg_path.parent

  if not cfg.sync:
    log.err("cfg.sync is empty in cde.yaml — nothing to sync")
    return 1

  r = _pick_run(args, cfg)
  if r is None:
    return 1
  if not r.k8s_namespace:
    log.err("run %s has no k8s_namespace recorded", r.run_id)
    return 1

  pod = _find_pod(r.k8s_namespace, r.run_id)
  if pod is None:
    log.err(
        "no pod found for run %s in %s — is the JobSet still running?",
        r.run_id, r.k8s_namespace,
    )
    return 1

  log.step("sync target: %s/%s", r.k8s_namespace, pod)
  log.detail("paths:")

  # Map each watched src path → in-pod dest.
  src_to_dest: dict[Path, str] = {}
  for spec in cfg.sync:
    src = (project_root / spec.src).resolve()
    src_to_dest[src] = spec.dest
    log.detail("  %s → %s", src, spec.dest)

  debounce = args.debounce_ms if args.debounce_ms is not None else prefs.sync.watch_debounce_ms

  def _resolve_dest(local: Path) -> str | None:
    """Map a touched local path to its in-pod dest (best-effort)."""
    local = local.resolve()
    for src, dest in src_to_dest.items():
      try:
        rel = local.relative_to(src)
        # If src is a dir, join the relative path into dest. If src is a
        # single file, dest is the in-pod path verbatim.
        if src.is_dir():
          return f"{dest.rstrip('/')}/{rel.as_posix()}"
        return dest
      except ValueError:
        continue
    return None

  def _on_change(batch: list[Path]) -> None:
    nsync = 0
    for local in batch:
      in_pod = _resolve_dest(local)
      if in_pod is None:
        continue
      rc = _kubectl_cp(local, r.k8s_namespace, pod, in_pod)
      if rc == 0:
        nsync += 1
        log.ok("synced %s", local.name)
      else:
        log.warn("sync failed for %s (kubectl exit %d)", local, rc)
    if nsync == 0:
      log.detail("(no files matched a sync mapping)")

  log.step("watching for changes — Ctrl-C to stop")
  watch_paths = list(src_to_dest.keys())
  with watcher.Watcher(watch_paths, callback=_on_change, debounce_ms=debounce):
    watcher.block_forever()

  log.ok("sync stopped")
  return 0
