"""kubectl wrapper. v0 is deliberately minimal: apply a manifest, get
the JobSet's namespace + name back. Wait/log streaming come in Phase 3.
"""

from __future__ import annotations

import subprocess

from cde import logging as log


class KubectlError(RuntimeError):
  """A kubectl invocation failed."""


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
