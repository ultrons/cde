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

`cde status <run>` — live cluster view of a recorded run.

Distinct from `cde history <run>` (the recorded row at submit + last
status refresh) and `cde reap` (status refresh write-back). This verb
asks the cluster *now* — Kueue admission state, JobSet phase,
replicatedJobsStatus counts, pod-level rollup, recent events. Useful
when a run is sitting Pending and you want to know why without doing
kubectl-archaeology.

Routes via the recorded `k8s_context` from the run row, so it queries
the right cluster regardless of the shell's current context.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from cde import config, db, k8s, logging as log, paths, suggest


def register(subparsers: argparse._SubParsersAction) -> None:
  from cde import cli, completers

  p = subparsers.add_parser(
      "status",
      help="Live cluster view of a run (admission, pods, events).",
  )
  cli.set_completer(p.add_argument("run_id"), completers.run_id_completer)
  p.add_argument(
      "--json", action="store_true",
      help="emit machine-readable JSON (the same fields as the table view)",
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


def _gather(r: db.Run) -> dict:
  ctx = r.k8s_context or None
  ns = r.k8s_namespace or ""
  jobset_name = r.jobset_name or r.run_id
  label = f"cde.io/run-id={r.run_id}"

  out: dict = {
      "run_id": r.run_id,
      "project": r.project,
      "recorded_status": r.status,
      "image_tag": r.image_tag,
      "k8s_context": r.k8s_context or "",
      "k8s_namespace": ns,
      "jobset_name": jobset_name,
  }

  # Live JobSet
  try:
    js = k8s.get_jobset_status(ns, jobset_name, context=ctx)
    out["jobset"] = {
        "status": js.status,
        "reason": js.reason,
        "message": js.message,
        "raw_phase": js.raw_phase,
    }
  except k8s.KubectlError as exc:
    out["jobset"] = {"status": "unknown", "error": str(exc)}

  # Per-replicatedJob rollup (Pathways: pathways-head + worker; regular: slice)
  out["replicated_jobs"] = k8s.get_replicated_jobs_status(
      ns, jobset_name, context=ctx,
  )

  # Live Workload (Kueue admission)
  out["workload"] = k8s.get_workload_admission(ns, jobset_name, context=ctx)

  # Pods
  out["pods"] = k8s.get_pods_summary(ns, label, context=ctx)

  # Events on the JobSet (pod events would be voluminous; one ring is enough
  # for the common "why isn't this admitting" / "why did it just fail" case)
  out["events"] = k8s.get_recent_events(ns, jobset_name, context=ctx, limit=8)

  return out


def _render_text(d: dict) -> None:
  log.step(
      "%s/%s on %s — recorded status: %s",
      d["k8s_namespace"], d["jobset_name"],
      d["k8s_context"] or "(no context recorded — using current)",
      d["recorded_status"],
  )

  js = d.get("jobset", {})
  if js.get("status") == "unknown":
    log.warn("JobSet not found on cluster — was it deleted? (%s)", js.get("error", ""))
  else:
    bits = [f"phase={js.get('raw_phase') or 'n/a'}", f"status={js['status']}"]
    if js.get("reason"):
      bits.append(f"reason={js['reason']}")
    log.detail("JobSet: %s", "  ".join(bits))
    if js.get("message"):
      log.detail("        %s", js["message"])

  rjs_rollup = d.get("replicated_jobs", [])
  if rjs_rollup:
    log.detail(
        "ReplicatedJobs: %d",
        len(rjs_rollup),
    )
    for rj in rjs_rollup:
      log.detail(
          "  %-20s active=%d ready=%d succeeded=%d failed=%d",
          rj["name"][:20], rj["active"], rj["ready"],
          rj["succeeded"], rj["failed"],
      )

  wl = d.get("workload")
  if wl is None:
    log.detail("Workload: (none — JobSet not admitted by Kueue, or already cleaned up)")
  else:
    log.detail(
        "Workload: %s admitted=%s queue=%s",
        wl["name"], wl["admitted"], wl["queueName"],
    )
    for c in wl.get("conditions", []):
      if c.get("status") == "True" or c.get("type") in ("PodsReady", "Admitted"):
        line = f"          {c.get('type')}={c.get('status')}"
        if c.get("reason"):
          line += f" ({c['reason']})"
        if c.get("message"):
          line += f" — {c['message']}"
        log.detail("%s", line)

  pods = d.get("pods", [])
  if not pods:
    log.detail("Pods: (none)")
  else:
    from collections import Counter
    phase_counts = Counter(p["phase"] for p in pods)
    log.detail(
        "Pods: %d total — %s",
        len(pods),
        ", ".join(f"{ph}:{n}" for ph, n in phase_counts.most_common()),
    )
    # First few pods in detail
    for p in pods[: min(5, len(pods))]:
      log.detail(
          "  %-30s %-9s ready=%s restarts=%d node=%s",
          p["name"][:30], p["phase"], p["ready"], p["restarts"],
          p["node"][:30] if p.get("node") else "-",
      )
    if len(pods) > 5:
      log.detail("  … (%d more)", len(pods) - 5)

  events = d.get("events", [])
  if events:
    log.detail("Recent events on JobSet:")
    for e in events:
      log.detail(
          "  %-22s %-7s %-22s %s",
          e["lastTimestamp"][:22] or "?",
          e["type"],
          e["reason"][:22],
          e["message"][:80],
      )


def run(args: argparse.Namespace) -> int:
  with db.open_db(_resolve_db_path()) as conn:
    r = db.get_run(conn, args.run_id)
    if r is None:
      ids = [x.run_id for x in db.list_runs(conn, limit=200)]
      log.err("no such run: %r.%s", args.run_id, suggest.hint(args.run_id, ids))
      return 1

  if not r.k8s_namespace:
    log.err("run %s has no k8s_namespace recorded", r.run_id)
    return 1

  data = _gather(r)
  if args.json:
    sys.stdout.write(json.dumps(data, indent=2, default=str) + "\n")
    return 0
  _render_text(data)
  return 0
