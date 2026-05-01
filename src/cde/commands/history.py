"""`cde history` — query the run database.

Default: 20 most-recent runs in the *current* project (read from the
nearest cde.yaml). `--all` ignores the project filter; `--project X`
filters by another project.

When called with a single positional run id, prints that one row's full
JSON. With `--json` (no positional), prints the full list as JSON
(stable schema: list of dicts; one row per entry).
"""

from __future__ import annotations

import argparse
import datetime
import json
import re
import sys
from dataclasses import asdict
from pathlib import Path

from cde import config, db, logging as log, paths, suggest


def register(subparsers: argparse._SubParsersAction) -> None:
  # Late imports to avoid an import cycle through cli.py.
  from cde import cli, completers

  p = subparsers.add_parser(
      "history",
      help="List runs (or show one full row) from the local SQLite history.",
  )
  cli.set_completer(
      p.add_argument(
          "run_id",
          nargs="?",
          help="optional: print this run's full row as JSON",
      ),
      completers.run_id_any_project_completer,
  )
  p.add_argument(
      "--limit", "-n", type=int, default=None,
      help="max rows in the table view (default: from preferences, fallback 20)",
  )
  p.add_argument(
      "--json", action="store_true", help="emit machine-readable JSON",
  )
  p.add_argument(
      "--all", action="store_true",
      help="ignore the per-project filter; show runs across all projects",
  )
  cli.set_completer(
      p.add_argument(
          "--project", default=None,
          help="filter by a specific project (overrides the current cde.yaml)",
      ),
      completers.project_completer,
  )
  cli.set_completer(
      p.add_argument("--tag", default=None, help="filter by tag (string match)"),
      completers.tag_completer,
  )
  p.add_argument(
      "--status", default=None,
      choices=["submitted", "running", "ok", "failed", "evicted"],
      help="filter by status",
  )
  p.add_argument(
      "--since", default=None, metavar="DURATION",
      help="filter to runs newer than DURATION (e.g. 7d, 24h, 90m)",
  )
  p.set_defaults(func=run)


def _resolve_project_filter(args: argparse.Namespace) -> str | None:
  if args.all:
    return None
  if args.project:
    return args.project
  cfg_path = paths.project_config_path()
  if cfg_path.is_file():
    try:
      cfg = config.load(cfg_path)
      return cfg.project
    except config.ConfigError:
      return None
  return None


def _resolve_db_path(args: argparse.Namespace) -> Path:
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


_DURATION = re.compile(r"^(\d+)([smhd])$")


def _since_to_iso(since: str) -> str:
  m = _DURATION.match(since.strip())
  if not m:
    raise SystemExit(f"--since: expected like 7d, 24h, 90m, 30s; got {since!r}")
  n, unit = int(m.group(1)), m.group(2)
  delta = {"s": 1, "m": 60, "h": 3600, "d": 86400}[unit] * n
  cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(
      seconds=delta
  )
  return cutoff.isoformat(timespec="seconds")


def _row_dict(r: db.Run) -> dict:
  d = asdict(r)
  return d


def run(args: argparse.Namespace) -> int:
  db_path = _resolve_db_path(args)

  with db.open_db(db_path) as conn:
    if args.run_id:
      row = db.get_run(conn, args.run_id)
      if row is None:
        # Suggest close matches from history.
        all_runs = db.list_runs(conn, limit=200)
        ids = [r.run_id for r in all_runs]
        log.err("no such run: %r.%s", args.run_id, suggest.hint(args.run_id, ids))
        return 1
      print(json.dumps(_row_dict(row), indent=2, sort_keys=True))
      return 0

    project = _resolve_project_filter(args)
    since = _since_to_iso(args.since) if args.since else None
    limit = args.limit if args.limit is not None else 20
    rows = db.list_runs(
        conn,
        project=project,
        status=args.status,
        tag=args.tag,
        since=since,
        limit=limit,
    )

  if args.json:
    print(json.dumps([_row_dict(r) for r in rows], indent=2, sort_keys=True))
    return 0

  if not rows:
    if project:
      log.info("no runs in project %r%s", project,
               f" matching --tag={args.tag}" if args.tag else "")
    else:
      log.info("no runs in history")
    return 0

  _print_table(rows, project_shown=(project is None))
  return 0


def _print_table(rows: list[db.Run], *, project_shown: bool) -> None:
  """Compact one-line-per-run table to stdout."""
  headers = ["RUN", "STATUS", "TEAM", "VC", "OVERRIDES", "AGE", "TAGS", "NOTE"]
  if project_shown:
    headers.insert(0, "PROJECT")

  data: list[list[str]] = []
  now = datetime.datetime.now(datetime.timezone.utc)
  for r in rows:
    age = _age(now, r.ts_submitted)
    overrides_s = " ".join(
        f"{k}={v}" for k, v in sorted(r.overrides.items())
    )
    if len(overrides_s) > 30:
      overrides_s = overrides_s[:27] + "..."
    note_s = (r.notes.splitlines()[0] if r.notes else "")
    if len(note_s) > 40:
      note_s = note_s[:37] + "..."
    row = [
        r.run_id,
        r.status,
        r.team or "",
        r.value_class or "",
        overrides_s,
        age,
        ",".join(r.tags),
        note_s,
    ]
    if project_shown:
      row.insert(0, r.project or "(default)")
    data.append(row)

  widths = [len(h) for h in headers]
  for row in data:
    for i, cell in enumerate(row):
      widths[i] = max(widths[i], len(cell))

  fmt = "  ".join(f"{{:<{w}}}" for w in widths)
  print(fmt.format(*headers))
  print("  ".join("-" * w for w in widths))
  for row in data:
    print(fmt.format(*row))


def _age(now: datetime.datetime, ts: str) -> str:
  if not ts:
    return "?"
  try:
    t = datetime.datetime.fromisoformat(ts.replace("Z", "+00:00"))
  except ValueError:
    return "?"
  if t.tzinfo is None:
    t = t.replace(tzinfo=datetime.timezone.utc)
  delta = now - t
  s = int(delta.total_seconds())
  if s < 60:
    return f"{s}s"
  if s < 3600:
    return f"{s // 60}m"
  if s < 86400:
    return f"{s // 3600}h"
  return f"{s // 86400}d"
