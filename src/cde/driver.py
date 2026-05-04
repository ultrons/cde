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

Container-driver subprocess wrapper.

Dispatches to docker | podman | nerdctl based on `prefs.build.driver`.
The three CLIs are nearly identical for what cde needs (build, push,
manifest inspect), so the wrapper is small.

Public API:

  Driver(prefs).build(context, dockerfile, tag, build_args)   -> int (returncode)
  Driver(prefs).push(tag)                                     -> int
  Driver(prefs).image_exists(tag)                             -> bool
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Sequence

from cde import logging as log
from cde.preferences import Preferences


class DriverError(RuntimeError):
  """A docker/podman/nerdctl invocation failed unexpectedly."""


class Driver:
  def __init__(self, prefs: Preferences):
    self._prefs = prefs.build

  def _argv(self, *args: str) -> list[str]:
    cmd: list[str] = []
    if self._prefs.sudo:
      cmd.append("sudo")
    cmd.append(self._prefs.driver)
    cmd.extend(args)
    return cmd

  def _run(
      self, args: Sequence[str], *, capture: bool = False, env: dict | None = None
  ) -> subprocess.CompletedProcess:
    argv = self._argv(*args)
    log.detail("$ %s", " ".join(argv))
    return subprocess.run(
        argv,
        capture_output=capture,
        text=True,
        env=env,
        check=False,
    )

  # -------------------------------------------------------------------------

  def build(
      self,
      *,
      context: Path,
      dockerfile: Path,
      tag: str,
      build_args: dict[str, str] | None = None,
  ) -> int:
    args: list[str] = ["build", "-t", tag, "-f", str(dockerfile)]
    for k, v in (build_args or {}).items():
      args.extend(["--build-arg", f"{k}={v}"])
    args.append(str(context))

    env = None
    if self._prefs.use_buildkit and self._prefs.driver == "docker":
      import os

      env = os.environ.copy()
      env["DOCKER_BUILDKIT"] = "1"

    proc = self._run(args, env=env)
    return proc.returncode

  def push(self, tag: str) -> int:
    proc = self._run(["push", tag])
    return proc.returncode

  def image_exists(self, tag: str) -> bool:
    """Best-effort: try `docker manifest inspect <tag>`. Returns False on
    any non-zero exit (registry not reachable, image missing, auth, etc.).
    Never raises — this is an optional optimisation, not a load-bearing check."""
    proc = self._run(["manifest", "inspect", tag], capture=True)
    return proc.returncode == 0
