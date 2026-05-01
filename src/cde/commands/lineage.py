"""`cde lineage <run_id>` — walk the parent_run chain backwards.

Shows the iterative trail this run was forked from. Useful for asking
"what's the full history of changes between this run and where it
started?"
"""

from __future__ import annotations

import argparse
from pathlib import Path

from cde import config, db, logging as log, paths


def register(subparsers: argparse._SubParsersAction) -> None:
  from cde import cli, completers

  p = subparsers.add_parser(
      "lineage",
      help="Walk the parent_run chain back to the root run.",
  )
  cli.set_completer(p.add_argument("run_id"), completers.run_id_completer)
  p.set_defaults(func=run)


def _resolve_db_path() -> Path:
  cfg_path = paths.project_config_path()
  if cfg_path.is_file():
    try:
      cfg = config.load(cfg_path)
      raw = cfg.history.path
      if raw:
        return Path(raw).expanduser()
    except config.ConfigError:
      pass
  return paths.history_db_path()


def run(args: argparse.Namespace) -> int:
  chain: list[db.Run] = []
  with db.open_db(_resolve_db_path()) as conn:
    current = db.get_run(conn, args.run_id)
    if current is None:
      log.err("no such run: %r", args.run_id)
      return 1
    seen: set[str] = set()
    while current is not None:
      if current.run_id in seen:
        log.warn("lineage cycle detected at %s; stopping walk", current.run_id)
        break
      seen.add(current.run_id)
      chain.append(current)
      if not current.parent_run:
        break
      current = db.get_run(conn, current.parent_run)

  print(f"lineage of {args.run_id} ({len(chain)} run(s)):")
  for i, r in enumerate(chain):
    arrow = "    " if i == 0 else "  ↑ "
    note_first = (r.notes.splitlines()[0] if r.notes else "")[:60]
    overrides_s = " ".join(f"{k}={v}" for k, v in sorted(r.overrides.items()))
    print(f"{arrow}{r.run_id}  [{r.status}]  {overrides_s}")
    if note_first:
      print(f"        — {note_first}")
  return 0
