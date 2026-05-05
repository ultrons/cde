"""Copyright 2026 Google LLC

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

     https://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.

`cde delete <run>` — delete the on-cluster JobSet for a recorded run.

Routes via the recorded `k8s_context` from the run row, so the delete
targets the cluster the run was originally applied to. Pairs with
`cde prune` (row-level cleanup): together they fully clean up a
finished run.

  cde delete v140              # kubectl delete the JobSet, keep the row
  cde delete v140 --purge      # also drop the history row
  cde delete v140 --force      # delete even if recorded status is running

By default refuses to delete a run with status running/submitted —
killing an active run is destructive and almost always a mistake. Pass
--force when you really mean it.
"""
from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

from cde import config, db, k8s, logging as log, paths


_ACTIVE_STATUSES = {"running", "submitted"}


def register(subparsers: argparse._SubParsersAction) -> None:
  from cde import cli, completers

  p = subparsers.add_parser(
      "delete",
      help="Delete the on-cluster JobSet for a recorded run.",
  )
  cli.set_completer(p.add_argument("run_id"), completers.run_id_completer)
  p.add_argument(
      "--purge",
      action="store_true",
      help="also delete the local history row after the JobSet is gone",
  )
  p.add_argument(
      "--force",
      action="store_true",
      help=(
          "delete even if recorded status is running/submitted (otherwise"
          " refuses to kill an active run)"
      ),
  )
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
  db_path = _resolve_db_path()
  with db.open_db(db_path) as conn:
    return _delete_one(conn, args)


def _delete_one(conn: sqlite3.Connection, args: argparse.Namespace) -> int:
  r = db.get_run(conn, args.run_id, submitter="")
  if r is None:
    log.err("run %r not found in history (db=%s)", args.run_id, conn)
    return 1

  if r.status in _ACTIVE_STATUSES and not args.force:
    log.err(
        "run %r recorded status is %r — refusing without --force",
        r.run_id, r.status,
    )
    log.detail("`cde reap %s` first if you suspect the row is stale", r.run_id)
    return 1

  ns = r.k8s_namespace or ""
  jobset_name = r.jobset_name or r.run_id
  ctx = r.k8s_context or None

  if not ns:
    log.err(
        "run %r has no recorded k8s_namespace — was it ever applied?",
        r.run_id,
    )
    return 1

  log.step(
      "deleting jobset %s/%s on %s",
      ns, jobset_name, ctx or "(current context)",
  )
  try:
    deleted = k8s.delete_jobset(ns, jobset_name, context=ctx)
  except k8s.KubectlError as exc:
    log.err("kubectl delete failed: %s", exc)
    return 1

  if deleted:
    log.ok("deleted jobset %s/%s", ns, jobset_name)
  else:
    log.detail("jobset %s/%s already gone", ns, jobset_name)

  if args.purge:
    if db.delete_run(conn, r.run_id, submitter=r.submitter):
      log.ok("purged history row for %s", r.run_id)
    else:
      log.warn("history row for %s vanished before purge", r.run_id)

  return 0
