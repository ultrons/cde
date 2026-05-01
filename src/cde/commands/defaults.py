"""`cde defaults` — show or reset sticky last-used values."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict

from cde import config, logging as log, paths, recent


def register(subparsers: argparse._SubParsersAction) -> None:
  p = subparsers.add_parser(
      "defaults",
      help="Show or reset the sticky defaults inherited from your last run.",
  )
  g = p.add_mutually_exclusive_group()
  g.add_argument("--show", action="store_true", help="show current defaults (default)")
  g.add_argument("--reset", action="store_true", help="clear defaults for this project")
  g.add_argument("--reset-all", action="store_true", help="clear defaults for every project")
  p.set_defaults(func=run)


def _resolve_project() -> str:
  cfg_path = paths.project_config_path()
  if cfg_path.is_file():
    try:
      cfg = config.load(cfg_path)
      return cfg.project
    except config.ConfigError:
      pass
  return ""


def run(args: argparse.Namespace) -> int:
  if args.reset_all:
    recent.reset(project=None)
    log.ok("cleared sticky defaults for every project")
    return 0

  project = _resolve_project()
  if args.reset:
    recent.reset(project=project)
    log.ok("cleared sticky defaults for project %r", project or "(unknown)")
    return 0

  d = recent.load(project)
  out = {f: getattr(d, f) for f in recent.STICKY_FIELDS}
  out["project"] = project or None
  out["ts_updated"] = d.ts_updated
  print(json.dumps(out, indent=2, sort_keys=True))
  return 0
