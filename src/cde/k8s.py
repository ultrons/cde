"""kubectl wrapper.

Deliberately minimal: apply a manifest, query JobSet status, stream
logs, exec into a pod. Anything more complex (port-forward, watch CRDs,
multi-namespace) is best left to the user shelling kubectl directly.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from typing import Sequence

from cde import logging as log


# DB-status vocabulary (mirrors db.Run.status).
STATUS_OK = "ok"
STATUS_FAILED = "failed"
STATUS_RUNNING = "running"
STATUS_SUBMITTED = "submitted"


class KubectlError(RuntimeError):
  """A kubectl invocation failed."""


def current_context() -> str | None:
  """Return `kubectl config current-context`, or None if it's unset/empty."""
  proc = subprocess.run(
      ["kubectl", "config", "current-context"],
      capture_output=True, text=True, check=False,
  )
  if proc.returncode != 0:
    return None
  ctx = proc.stdout.strip()
  return ctx or None


def _kctl(context: str | None) -> list[str]:
  """Build the kubectl prefix. If context is non-empty, pin it explicitly so
  the call is immune to the shell's current-context drifting between when
  cde resolved it and when kubectl runs."""
  if context:
    return ["kubectl", f"--context={context}"]
  return ["kubectl"]


# ---------------------------------------------------------------------------
# apply
# ---------------------------------------------------------------------------


def apply(
    manifest_yaml: str, *, dry_run: bool = False, context: str | None = None,
) -> str:
  """Run `kubectl apply -f -` with the given manifest. Returns kubectl's
  stdout. Raises KubectlError on failure with stderr included.

  If `context` is given, kubectl is invoked with `--context=<context>` so
  the apply targets that cluster regardless of the shell's current-context."""
  args = _kctl(context) + ["apply", "-f", "-"]
  if dry_run:
    args.extend(["--dry-run=client", "-o", "name"])
  log.detail("$ %s", " ".join(args))
  proc = subprocess.run(
      args,
      input=manifest_yaml,
      capture_output=True,
      text=True,
      check=False,
  )
  if proc.returncode != 0:
    raise KubectlError(
        f"kubectl apply failed (exit {proc.returncode}):\n{proc.stderr.strip()}"
    )
  return proc.stdout.strip()


# ---------------------------------------------------------------------------
# JobSet status
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class JobSetStatus:
  """Summary of a JobSet's current state in our DB's status vocabulary."""

  status: str            # "running" | "ok" | "failed" | "unknown"
  reason: str | None     # JobSet condition reason (if terminal)
  message: str | None
  raw_phase: str | None  # the JobSet's own phase, for debugging


def get_jobset_status(
    namespace: str, name: str, *, context: str | None = None,
) -> JobSetStatus:
  """Read a JobSet via kubectl, classify its status."""
  proc = subprocess.run(
      _kctl(context) + ["get", "jobset", name, "-n", namespace, "-o", "json"],
      capture_output=True, text=True, check=False,
  )
  if proc.returncode != 0:
    err = proc.stderr.strip() or proc.stdout.strip()
    if "not found" in err.lower() or "NotFound" in err:
      # Could be evicted by Kueue and garbage-collected, or never reached
      # the cluster. Caller decides how to interpret.
      return JobSetStatus(
          status="unknown", reason="not-found", message=err, raw_phase=None,
      )
    raise KubectlError(f"kubectl get jobset failed: {err}")

  try:
    obj = json.loads(proc.stdout)
  except json.JSONDecodeError as exc:
    raise KubectlError(f"kubectl get jobset returned invalid JSON: {exc}") from exc
  return classify_jobset(obj)


def classify_jobset(obj: dict) -> JobSetStatus:
  """Classify a parsed JobSet object's status. Pure function — exposed
  so unit tests can hit it without subprocess'ing kubectl."""
  status = obj.get("status") or {}
  conditions = status.get("conditions") or []
  raw_phase = status.get("phase")

  for cond in conditions:
    if cond.get("status") != "True":
      continue
    ctype = (cond.get("type") or "").lower()
    if ctype in ("completed", "succeeded"):
      return JobSetStatus(
          status=STATUS_OK,
          reason=cond.get("reason"),
          message=cond.get("message"),
          raw_phase=raw_phase,
      )
    if ctype == "failed":
      return JobSetStatus(
          status=STATUS_FAILED,
          reason=cond.get("reason"),
          message=cond.get("message"),
          raw_phase=raw_phase,
      )

  # Not terminal — call it running. ts_started is set elsewhere.
  return JobSetStatus(
      status=STATUS_RUNNING, reason=None, message=None, raw_phase=raw_phase,
  )


# ---------------------------------------------------------------------------
# Log streaming
# ---------------------------------------------------------------------------


def list_pods(
    namespace: str, label: str, *, context: str | None = None,
) -> list[str]:
  """Return pod names matching `label`, sorted by creation time (oldest first)."""
  proc = subprocess.run(
      _kctl(context) + [
          "get", "pods",
          "-n", namespace, "-l", label,
          "--sort-by=.metadata.creationTimestamp",
          "-o", "jsonpath={.items[*].metadata.name}",
      ],
      capture_output=True, text=True, check=False,
  )
  if proc.returncode != 0:
    err = (proc.stderr or proc.stdout).strip()
    raise KubectlError(f"kubectl get pods failed: {err}")
  return [n for n in proc.stdout.strip().split() if n]


def get_pods_summary(
    namespace: str, label: str, *, context: str | None = None,
) -> list[dict]:
  """Pod-level rollup for `cde status`: name, phase, ready, restarts, age,
  node. Returns [] if kubectl errors (caller decides how to render)."""
  proc = subprocess.run(
      _kctl(context) + [
          "get", "pods", "-n", namespace, "-l", label,
          "--sort-by=.metadata.creationTimestamp",
          "-o", "json",
      ],
      capture_output=True, text=True, check=False,
  )
  if proc.returncode != 0:
    return []
  try:
    obj = json.loads(proc.stdout)
  except json.JSONDecodeError:
    return []
  out = []
  for item in obj.get("items", []):
    md = item.get("metadata", {}) or {}
    sp = item.get("spec", {}) or {}
    st = item.get("status", {}) or {}
    cs = st.get("containerStatuses", []) or []
    ready = sum(1 for c in cs if c.get("ready"))
    total = len(cs) or len(sp.get("containers", []))
    restarts = sum(c.get("restartCount", 0) for c in cs)
    out.append({
        "name": md.get("name", ""),
        "phase": st.get("phase", "Unknown"),
        "ready": f"{ready}/{total}",
        "restarts": restarts,
        "node": sp.get("nodeName") or "",
        "creation_ts": md.get("creationTimestamp", ""),
    })
  return out


def get_workload_admission(
    namespace: str, jobset_name: str, *, context: str | None = None,
) -> dict | None:
  """Find the Kueue Workload for this JobSet and return a small summary:
  {admitted: bool, queue, reason, message, conditions: [...]}.
  Returns None if no Workload found or kubectl errors."""
  proc = subprocess.run(
      _kctl(context) + [
          "get", "workload", "-n", namespace,
          "-l", f"kueue.x-k8s.io/job-uid",   # any workload created by Kueue
          "-o", "json",
      ],
      capture_output=True, text=True, check=False,
  )
  if proc.returncode != 0:
    return None
  try:
    obj = json.loads(proc.stdout)
  except json.JSONDecodeError:
    return None
  # Find the Workload whose ownerRef points at this JobSet name.
  for wl in obj.get("items", []) or []:
    owners = wl.get("metadata", {}).get("ownerReferences", []) or []
    if not any(o.get("name") == jobset_name for o in owners):
      continue
    spec = wl.get("spec", {}) or {}
    st = wl.get("status", {}) or {}
    conds = st.get("conditions", []) or []
    admitted = any(
        c.get("type") == "Admitted" and c.get("status") == "True" for c in conds
    )
    return {
        "name": wl.get("metadata", {}).get("name", ""),
        "queueName": spec.get("queueName", ""),
        "admitted": admitted,
        "conditions": [
            {
                "type": c.get("type"),
                "status": c.get("status"),
                "reason": c.get("reason"),
                "message": c.get("message"),
            }
            for c in conds
        ],
    }
  return None


def get_recent_events(
    namespace: str, name: str, *, context: str | None = None, limit: int = 5,
) -> list[dict]:
  """Recent kubectl events for an object (jobset or pod). Returns [] on error."""
  proc = subprocess.run(
      _kctl(context) + [
          "get", "events", "-n", namespace,
          "--field-selector", f"involvedObject.name={name}",
          "--sort-by=.lastTimestamp",
          "-o", "json",
      ],
      capture_output=True, text=True, check=False,
  )
  if proc.returncode != 0:
    return []
  try:
    obj = json.loads(proc.stdout)
  except json.JSONDecodeError:
    return []
  items = obj.get("items", []) or []
  out = []
  for ev in items[-limit:]:
    out.append({
        "type": ev.get("type", ""),
        "reason": ev.get("reason", ""),
        "lastTimestamp": ev.get("lastTimestamp", "") or ev.get("eventTime", ""),
        "message": ev.get("message", ""),
    })
  return out


def stream_logs(
    *,
    namespace: str,
    label: str,
    follow: bool = True,
    since: str | None = None,
    container: str | None = None,
    context: str | None = None,
) -> int:
  """Stream kubectl logs across every pod matching `label`. Use this for the
  `-a/--all-pods` mode of `cde logs`. Returns kubectl's exit code."""
  args = _kctl(context) + [
      "logs",
      "-n", namespace,
      "-l", label,
      "--prefix=true",
      "--max-log-requests=64",
  ]
  if container:
    args.extend(["-c", container])
  else:
    args.append("--all-containers=true")
  if follow:
    args.append("-f")
  if since:
    args.append(f"--since={since}")

  log.detail("$ %s", " ".join(args))
  try:
    return subprocess.call(args)
  except KeyboardInterrupt:
    return 130


def stream_pod_logs(
    *,
    namespace: str,
    pod: str,
    follow: bool = True,
    since: str | None = None,
    container: str | None = None,
    context: str | None = None,
) -> int:
  """Stream kubectl logs for a single pod. Default: all containers, prefixed."""
  args = _kctl(context) + ["logs", "-n", namespace, pod]
  if container:
    args.extend(["-c", container])
  else:
    args.extend(["--all-containers=true", "--prefix=true"])
  if follow:
    args.append("-f")
  if since:
    args.append(f"--since={since}")

  log.detail("$ %s", " ".join(args))
  try:
    return subprocess.call(args)
  except KeyboardInterrupt:
    return 130


# ---------------------------------------------------------------------------
# kubectl exec
# ---------------------------------------------------------------------------


def exec_into_first_pod(
    *,
    namespace: str,
    label: str,
    command: Sequence[str] = ("/bin/bash",),
    context: str | None = None,
) -> int:
  """Find the first pod matching `label` and exec the given command in it
  with TTY attached. Returns kubectl's exit code."""
  q = subprocess.run(
      _kctl(context) + [
          "get", "pods",
          "-n", namespace, "-l", label,
          "-o", "jsonpath={.items[0].metadata.name}",
      ],
      capture_output=True, text=True, check=False,
  )
  if q.returncode != 0 or not q.stdout.strip():
    err = (q.stderr or "no pods found").strip()
    raise KubectlError(
        f"no pod for label {label!r} in {namespace!r}: {err}"
    )
  pod = q.stdout.strip()
  argv = _kctl(context) + ["exec", "-it", pod, "-n", namespace, "--", *command]
  log.detail("$ %s", " ".join(argv))
  return subprocess.call(argv)
