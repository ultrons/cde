"""`cde profile path <run>` — print the GCS path stored for a run.

The actual pull/open viewer is v0.5; this minimal verb just makes the
URI scriptable so you can `gsutil ls $(cde profile path v140)` or pipe
into xprof.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from cde import config, db, logging as log, paths, suggest


def register(subparsers: argparse._SubParsersAction) -> None:
  p = subparsers.add_parser("profile", help="Profile-related verbs.")
  sp = p.add_subparsers(dest="profile_cmd", required=True)

  pp = sp.add_parser("path", help="Print the profile_uri stored for a run.")
  pp.add_argument("run_id")
  pp.set_defaults(func=_path)


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


def _path(args: argparse.Namespace) -> int:
  with db.open_db(_resolve_db_path()) as conn:
    run = db.get_run(conn, args.run_id)
    if run is None:
      ids = [r.run_id for r in db.list_runs(conn, limit=200)]
      log.err("no such run: %r.%s", args.run_id, suggest.hint(args.run_id, ids))
      return 1
  if not run.profile_uri:
    log.err(
        "run %s has no profile_uri (was --profile passed at submit time?)",
        args.run_id,
    )
    return 1
  print(run.profile_uri)
  return 0
