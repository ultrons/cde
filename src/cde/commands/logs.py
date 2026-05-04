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

`cde logs <run>` — tail the JobSet's pods, then sync status to history.

Default streams logs from pod 0 (oldest pod matching the run label), all
containers, prefixed. Multi-pod runs (e.g. JobSet with N replicas) are
legible by default — pass `-a` to fan out across every pod, or `-r N` to
pick a specific replica.

After kubectl exits, polls JobSet status once and updates the history
row's status + ts_finished. Use `--no-follow` for a one-shot read (no
status update).
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
  p.add_argument(
      "-a", "--all-pods",
      dest="all_pods",
      action="store_true",
      help="stream logs from every pod (default: pod 0 only)",
  )
  p.add_argument(
      "-r", "--replica",
      dest="replica",
      type=str,
      default=None,
      help=(
          "stream logs from pod index N (0-based, sorted by creation time)"
          " or from a named replicatedJob (e.g. -r worker, -r pathways-head)."
          " Multi-replicatedJob JobSets (Pathways) usually want named access."
      ),
  )
  p.add_argument(
      "-c", "--container",
      dest="container",
      default=None,
      help="filter to a single container name (default: all containers, prefixed)",
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
  ctx = r.k8s_context or None  # legacy rows have "" → fall through to current

  if args.all_pods and args.replica is not None:
    log.err("cannot combine -a/--all-pods with -r/--replica")
    return 2

  if args.all_pods:
    log.step(
        "tailing %s/%s across all pods (label %s)%s",
        r.k8s_namespace, r.jobset_name or r.run_id, label,
        "" if args.follow else " (no follow)",
    )
    rc = k8s.stream_logs(
        namespace=r.k8s_namespace,
        label=label,
        follow=args.follow,
        since=args.since,
        container=args.container,
        context=ctx,
    )
  else:
    # Resolve --replica: numeric index → pod[N] across all matching pods;
    # string (name) → first pod of the named replicatedJob (matches the
    # JobSet-injected label `jobset.sigs.k8s.io/replicatedjob-name`).
    pod_label = label
    is_named = False
    if args.replica is not None:
      try:
        idx = int(args.replica)
      except ValueError:
        # Named replicatedJob: scope the pod listing to that replica.
        pod_label = (
            f"{label},jobset.sigs.k8s.io/replicatedjob-name={args.replica}"
        )
        idx = 0
        is_named = True
    else:
      idx = 0

    try:
      pods = k8s.list_pods(r.k8s_namespace, pod_label, context=ctx)
    except k8s.KubectlError as exc:
      log.err("%s", exc)
      return 1
    if not pods:
      if is_named:
        log.err(
            "no pods for replicatedJob %r on run %s",
            args.replica, r.run_id,
        )
      else:
        log.err(
            "no pods for run %s in %s (label %s)",
            r.run_id, r.k8s_namespace, label,
        )
      return 1
    if idx < 0 or idx >= len(pods):
      log.err(
          "replica %d out of range (run has %d pod%s: 0..%d)",
          idx, len(pods), "" if len(pods) == 1 else "s", len(pods) - 1,
      )
      return 1
    pod = pods[idx]
    log.step(
        "tailing %s/%s (%s, %d total)%s",
        r.k8s_namespace, pod,
        f"replica={args.replica}" if is_named
        else f"replica {idx}/{len(pods) - 1}",
        len(pods),
        "" if args.follow else " (no follow)",
    )
    if len(pods) > 1 and not args.all_pods and args.replica is None:
      log.detail("(pass -a to stream all pods, or -r N / -r <name> to pick another)")
    rc = k8s.stream_pod_logs(
        namespace=r.k8s_namespace,
        pod=pod,
        follow=args.follow,
        since=args.since,
        container=args.container,
        context=ctx,
    )

  if not args.follow:
    if r.status == "running":
      log.detail(
          "run still marked 'running' in history; "
          "rerun without --no-follow, or `cde reap %s` to refresh.",
          r.run_id,
      )
    return rc

  # After --follow exits, refresh status from JobSet.
  log.step("refreshing run status from JobSet")
  try:
    js = k8s.get_jobset_status(r.k8s_namespace, r.jobset_name or r.run_id, context=ctx)
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
