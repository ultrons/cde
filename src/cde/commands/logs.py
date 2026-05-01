"""`cde logs <run>` — tail the JobSet's pods, then sync status to history.

Default streams logs (kubectl -f) and waits until the JobSet finishes
(or the user Ctrl-C's out). After kubectl exits, polls JobSet status
once and updates the history row's status + ts_finished.

Use `--no-follow` for a one-shot read (no status update).
"""

from __future__ import annotations

import argparse
import datetime
from pathlib import Path

from cde import config, db, k8s, logging as log, paths, suggest


def register(subparsers: argparse._SubParsersAction) -> None:
  from cde import cli, completers

  p = subparsers.add_parser(
      "logs",
      help="Tail a run's pods. After it finishes, refresh the history row.",
  )
  cli.set_completer(p.add_argument("run_id"), completers.run_id_completer)
  p.add_argument(
      "--no-follow",
      dest="follow",
      action="store_false",
      help="print existing logs and exit (no live tail, no status update)",
  )
  p.add_argument(
      "--since",
      default=None,
      help="kubectl --since= (e.g. 5m, 1h)",
  )
  p.set_defaults(func=run, follow=True)


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


def _now() -> str:
  return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def run(args: argparse.Namespace) -> int:
  with db.open_db(_resolve_db_path()) as conn:
    r = db.get_run(conn, args.run_id)
    if r is None:
      ids = [x.run_id for x in db.list_runs(conn, limit=200)]
      log.err("no such run: %r.%s", args.run_id, suggest.hint(args.run_id, ids))
      return 1

  if not r.k8s_namespace:
    log.err("run %s has no k8s_namespace recorded — cannot fetch logs", r.run_id)
    return 1

  label = f"cde.io/run-id={r.run_id}"
  log.step(
      "tailing %s/%s (label %s)%s",
      r.k8s_namespace, r.jobset_name or r.run_id, label,
      "" if args.follow else " (no follow)",
  )

  rc = k8s.stream_logs(
      namespace=r.k8s_namespace,
      label=label,
      follow=args.follow,
      since=args.since,
  )

  if not args.follow:
    return rc

  # After --follow exits, refresh status from JobSet.
  log.step("refreshing run status from JobSet")
  try:
    js = k8s.get_jobset_status(r.k8s_namespace, r.jobset_name or r.run_id)
  except k8s.KubectlError as exc:
    log.warn("status refresh skipped: %s", exc)
    return rc

  if js.status == "running":
    log.detail("JobSet still running (kubectl logs likely terminated by SIGINT)")
    return rc

  if js.status == "unknown":
    log.warn("JobSet not found — was it evicted or never reached the cluster?")
    with db.open_db(_resolve_db_path()) as conn:
      db.update_run(conn, r.run_id, status="evicted", ts_finished=_now())
    return rc

  with db.open_db(_resolve_db_path()) as conn:
    db.update_run(
        conn, r.run_id, status=js.status, ts_finished=_now(),
    )
  level = log.ok if js.status == "ok" else log.warn
  level("status: %s%s", js.status, f" ({js.reason})" if js.reason else "")
  return rc
