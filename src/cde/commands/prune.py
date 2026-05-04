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

`cde prune` — delete failed/evicted runs from history.

Default behavior is conservative: dry-run, current-project only, and
several safety filters that protect anything the user invested in.

  cde prune                    # show what would be pruned
  cde prune --apply            # actually delete

Default candidate set: status in {failed, evicted}, NOT tagged, NOT
annotated (no notes / no hypothesis), older than 7 days. Override
each filter explicitly:

  --status failed              # narrow to one status
  --include-tagged             # also prune tagged runs
  --include-annotated          # also prune runs with notes / hypothesis
  --keep-recent 0d             # ignore the recency filter (still NOT recommended)
  --include-running            # also prune zombie running/submitted rows
  --all                        # span all projects (default: current only)

Pruning is row-level only — the on-cluster JobSet (if it still exists)
is not touched. `cde reap` first to make sure the recorded statuses
are honest before pruning.

Lineage caveat: `parent_run` references survive even when the parent
is pruned. `cde lineage` truncates gracefully at the first missing
ancestor.
"""
from __future__ import annotations

import argparse
import datetime
import re
from pathlib import Path

from cde import config, db, logging as log, paths


_DURATION = re.compile(r"^(\d+)([smhd])$")
_DEFAULT_STATUSES = ("failed", "evicted")
_RUNNING_STATUSES = ("running", "submitted")


def register(subparsers: argparse._SubParsersAction) -> None:
  p = subparsers.add_parser(
      "prune",
      help=(
          "Delete failed / evicted runs from history."
          " Dry-run by default; pass --apply to delete."
      ),
  )
  p.add_argument(
      "--apply", action="store_true",
      help="actually delete (default: dry-run; print what would be deleted)",
  )
  p.add_argument(
      "--status", default=",".join(_DEFAULT_STATUSES),
      help=(
          "comma-separated statuses to prune. Default: failed,evicted."
          " Pass --include-running to also include running/submitted."
      ),
  )
  p.add_argument(
      "--include-tagged", action="store_true",
      help="also prune runs that have tags (default: keep them)",
  )
  p.add_argument(
      "--include-annotated", action="store_true",
      help=(
          "also prune runs with non-empty notes / hypothesis"
          " (default: keep them — annotations are usually the value)"
      ),
  )
  p.add_argument(
      "--keep-recent", default="7d", metavar="DURATION",
      help=(
          "never prune runs newer than this (default: 7d). Pass 0d to"
          " disable the recency filter."
      ),
  )
  p.add_argument(
      "--include-running", action="store_true",
      help=(
          "also include running/submitted rows in the candidate set."
          " Use after `cde reap` if you have stuck/zombie rows."
      ),
  )
  p.add_argument(
      "--all", dest="all_projects", action="store_true",
      help="span all projects (default: current project only)",
  )
  p.set_defaults(func=run)


def _resolve_db_path(cfg: config.CdeConfig | None) -> Path:
  if cfg is not None and cfg.history.path:
    return Path(cfg.history.path).expanduser()
  return paths.history_db_path()


def _parse_duration_to_iso(s: str) -> str | None:
  """Parse '7d' / '24h' / '0d' / etc. into an ISO-8601 cutoff timestamp.
  Returns None for '0d' / '0' (i.e. recency filter disabled)."""
  s = s.strip()
  m = _DURATION.match(s)
  if not m:
    raise SystemExit(
        f"--keep-recent: expected like 7d, 24h, 90m, 30s; got {s!r}"
    )
  n, unit = int(m.group(1)), m.group(2)
  if n == 0:
    return None
  delta = {"s": 1, "m": 60, "h": 3600, "d": 86400}[unit] * n
  cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(
      seconds=delta
  )
  return cutoff.isoformat(timespec="seconds")


def _row_summary(r: db.Run) -> str:
  return (
      f"{r.run_id:14s}  {r.status:8s}  {r.project[:18]:18s}  "
      f"{r.ts_submitted[:19]}  "
      + (f"tags={','.join(r.tags)}" if r.tags else "")
  ).rstrip()


def run(args: argparse.Namespace) -> int:
  cfg_path = paths.project_config_path()
  cfg: config.CdeConfig | None = None
  project_filter: str | None = None
  if cfg_path.is_file():
    try:
      cfg = config.load(cfg_path)
      project_filter = cfg.project
    except config.ConfigError:
      pass
  if args.all_projects:
    project_filter = None

  statuses = {s.strip() for s in args.status.split(",") if s.strip()}
  if args.include_running:
    statuses.update(_RUNNING_STATUSES)
  if not statuses:
    log.err("--status resolved to an empty set")
    return 2

  cutoff = _parse_duration_to_iso(args.keep_recent)

  with db.open_db(_resolve_db_path(cfg)) as conn:
    rows = db.list_runs(conn, project=project_filter, limit=100000)

  candidates: list[db.Run] = []
  kept_for: dict[str, int] = {
      "tagged": 0, "annotated": 0, "recent": 0, "wrong_status": 0,
  }
  for r in rows:
    if r.status not in statuses:
      kept_for["wrong_status"] += 1
      continue
    if r.tags and not args.include_tagged:
      kept_for["tagged"] += 1
      continue
    if (r.notes or r.hypothesis) and not args.include_annotated:
      kept_for["annotated"] += 1
      continue
    if cutoff and r.ts_submitted >= cutoff:
      kept_for["recent"] += 1
      continue
    candidates.append(r)

  scope = (
      f"project={project_filter}" if project_filter else "all projects"
  )
  log.step(
      "%d candidate(s) to prune (%s; statuses=%s; keep-recent=%s)",
      len(candidates), scope, ",".join(sorted(statuses)), args.keep_recent,
  )

  if candidates:
    log.detail("RUN_ID          STATUS    PROJECT             SUBMITTED            TAGS")
    log.detail("--------------  --------  ------------------  -------------------  ------")
    for r in candidates:
      log.detail("%s", _row_summary(r))

  saved_msg = ", ".join(
      f"{k}={v}" for k, v in kept_for.items() if v
  ) or "none"
  log.detail("(saved by filters — %s)", saved_msg)

  if not args.apply:
    log.detail("(dry-run — pass --apply to delete %d row(s))", len(candidates))
    return 0

  if not candidates:
    log.ok("nothing to prune")
    return 0

  with db.open_db(_resolve_db_path(cfg)) as conn:
    deleted = 0
    for r in candidates:
      if db.delete_run(conn, r.run_id, submitter=r.submitter):
        deleted += 1
  log.ok("pruned %d run(s)", deleted)
  return 0
