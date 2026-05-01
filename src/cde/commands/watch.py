"""`cde watch` — observe source paths and print when they change.

Deliberately doesn't auto-rebuild. The point is to surface "your build
context drifted; run cde build when you're ready" — never to surprise
the user with a rebuild they didn't ask for.

Watches the union of:
  - Every cfg.sync[*].src (if present),
  - Otherwise the build context (cfg.image.context).

Ctrl-C to stop.
"""

from __future__ import annotations

import argparse
import datetime
from pathlib import Path

from cde import config, logging as log, paths, watcher
from cde import preferences as prefs_mod


def register(subparsers: argparse._SubParsersAction) -> None:
  p = subparsers.add_parser(
      "watch",
      help="Observe source paths and print when they change (no rebuild).",
  )
  p.add_argument(
      "--debounce-ms",
      type=int,
      default=None,
      help="window for coalescing rapid saves (default: from preferences)",
  )
  p.set_defaults(func=run)


def _resolve_watch_paths(cfg: config.CdeConfig, project_root: Path) -> list[Path]:
  if cfg.sync:
    return [(project_root / s.src).resolve() for s in cfg.sync]
  return [(project_root / cfg.image.context).resolve()]


def run(args: argparse.Namespace) -> int:
  cfg_path = paths.project_config_path()
  if not cfg_path.is_file():
    log.err("no cde.yaml found (run `cde init` first)")
    return 1
  cfg = config.load(cfg_path)
  prefs = prefs_mod.load()
  project_root = cfg_path.parent

  watch_paths = _resolve_watch_paths(cfg, project_root)
  for p in watch_paths:
    if not p.exists():
      log.warn("watch path does not exist (skipping): %s", p)
  watch_paths = [p for p in watch_paths if p.exists()]
  if not watch_paths:
    log.err("nothing to watch (no cfg.sync paths and no cfg.image.context)")
    return 1

  debounce = args.debounce_ms if args.debounce_ms is not None else prefs.sync.watch_debounce_ms

  log.step("watching:")
  for p in watch_paths:
    log.detail("  %s", p)
  log.detail("debounce: %dms — Ctrl-C to stop", debounce)

  def _on_change(batch: list[Path]) -> None:
    ts = datetime.datetime.now().strftime("%H:%M:%S")
    log.info("[%s] build context changed (%d file(s))", ts, len(batch))
    for p in batch[:5]:
      log.detail("  %s", p)
    if len(batch) > 5:
      log.detail("  … and %d more", len(batch) - 5)
    log.detail("(run `cde build` to rebuild the image)")

  with watcher.Watcher(watch_paths, callback=_on_change, debounce_ms=debounce):
    watcher.block_forever()

  log.ok("watch stopped")
  return 0
