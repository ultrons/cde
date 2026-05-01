"""`cde reap` — refresh status for any in-flight runs in this project.

Iterates over runs whose status is `submitted` or `running`, polls the
JobSet, and updates the row. Useful when you've submitted a bunch of
runs and just want history to show the truth without manually tailing
each one.

`--all` widens the scan to every project on this machine.
"""

from __future__ import annotations

import argparse
import datetime
from pathlib import Path

from cde import config, db, k8s, logging as log, paths


def register(subparsers: argparse._SubParsersAction) -> None:
  p = subparsers.add_parser(
      "reap",
      help="Refresh status for in-flight runs by polling JobSets.",
  )
  p.add_argument(
      "--all",
      action="store_true",
      help="scan in-flight runs from every project, not just this one",
  )
  p.add_argument(
      "--limit",
      type=int,
      default=200,
      help="max in-flight rows to refresh per call",
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


def _resolve_project_filter(args: argparse.Namespace) -> str | None:
  if args.all:
    return None
  cfg_path = paths.project_config_path()
  if cfg_path.is_file():
    try:
      cfg = config.load(cfg_path)
      return cfg.project
    except config.ConfigError:
      pass
  return None


def _now() -> str:
  return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def run(args: argparse.Namespace) -> int:
  project = _resolve_project_filter(args)

  with db.open_db(_resolve_db_path()) as conn:
    in_flight = [
        r for r in db.list_runs(conn, project=project, limit=args.limit)
        if r.status in (k8s.STATUS_SUBMITTED, k8s.STATUS_RUNNING)
    ]

  if not in_flight:
    log.info("no in-flight runs%s", "" if project is None else f" in project {project!r}")
    return 0

  log.step("refreshing %d run(s)", len(in_flight))
  changed = 0
  for r in in_flight:
    if not r.k8s_namespace:
      log.detail("%s: no k8s_namespace; skipping", r.run_id)
      continue
    name = r.jobset_name or r.run_id
    try:
      js = k8s.get_jobset_status(r.k8s_namespace, name)
    except k8s.KubectlError as exc:
      log.warn("%s: status check failed: %s", r.run_id, exc)
      continue

    if js.status == r.status:
      continue

    new_status = js.status
    finished = new_status in (k8s.STATUS_OK, k8s.STATUS_FAILED, "evicted")
    if js.status == "unknown":
      new_status = "evicted"
      finished = True
    fields: dict = {"status": new_status}
    if finished:
      fields["ts_finished"] = _now()
    with db.open_db(_resolve_db_path()) as conn:
      db.update_run(conn, r.run_id, **fields)
    changed += 1
    log.detail("%s: %s → %s", r.run_id, r.status, new_status)

  log.ok("reaped %d run(s); %d unchanged", changed, len(in_flight) - changed)
  return 0
