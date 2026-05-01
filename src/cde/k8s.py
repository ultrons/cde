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


# ---------------------------------------------------------------------------
# apply
# ---------------------------------------------------------------------------


def apply(manifest_yaml: str, *, dry_run: bool = False) -> str:
  """Run `kubectl apply -f -` with the given manifest. Returns kubectl's
  stdout. Raises KubectlError on failure with stderr included."""
  args = ["kubectl", "apply", "-f", "-"]
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


def get_jobset_status(namespace: str, name: str) -> JobSetStatus:
  """Read a JobSet via kubectl, classify its status."""
  proc = subprocess.run(
      ["kubectl", "get", "jobset", name, "-n", namespace, "-o", "json"],
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


def stream_logs(
    *, namespace: str, label: str, follow: bool = True, since: str | None = None,
) -> int:
  """Spawn `kubectl logs -l <label> --prefix=true [-f]` inheriting parent
  stdio. Returns kubectl's exit code (130 on Ctrl-C)."""
  args = [
      "kubectl", "logs",
      "-n", namespace,
      "-l", label,
      "--prefix=true",
      "--max-log-requests=64",
  ]
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
    *, namespace: str, label: str, command: Sequence[str] = ("/bin/bash",)
) -> int:
  """Find the first pod matching `label` and exec the given command in it
  with TTY attached. Returns kubectl's exit code."""
  q = subprocess.run(
      [
          "kubectl", "get", "pods",
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
  argv = ["kubectl", "exec", "-it", pod, "-n", namespace, "--", *command]
  log.detail("$ %s", " ".join(argv))
  return subprocess.call(argv)
