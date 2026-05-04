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

Git introspection. Used to record (sha, dirty) on every run row.

If the project isn't a git repo (or git isn't on PATH), returns
(None, False) — cde works fine without git, you just lose the
provenance record.
"""
from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class GitInfo:
  sha: str | None              # full or short SHA, None if not a git repo
  dirty: bool                  # True if uncommitted changes (only valid when sha is set)


def _run(cmd: list[str], cwd: Path) -> str | None:
  try:
    out = subprocess.run(
        cmd, cwd=cwd, check=False, capture_output=True, text=True, timeout=5,
    )
  except (FileNotFoundError, subprocess.TimeoutExpired):
    return None
  if out.returncode != 0:
    return None
  return out.stdout.strip()


def info_for(path: Path) -> GitInfo:
  """Return (sha, dirty) for the git repo containing `path`."""
  sha = _run(["git", "rev-parse", "HEAD"], cwd=path)
  if sha is None:
    return GitInfo(sha=None, dirty=False)

  status = _run(["git", "status", "--porcelain"], cwd=path)
  dirty = bool(status)
  return GitInfo(sha=sha, dirty=dirty)
