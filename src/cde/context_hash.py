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

Deterministic hash over the docker build context.

The hash drives image tags: identical build context = identical hash =
no rebuild needed. We deliberately do this from the filesystem (not git)
so that uncommitted local edits get their own hash; you can still rebuild
mid-iteration without committing first.

Algorithm:

  1. Walk the context dir respecting .dockerignore (best-effort — falls
     back to walking everything if no .dockerignore).
  2. For each file in sorted relative-path order, hash:
       relpath\0  +  sha256(file contents)\0
  3. The final SHA256 of the concatenated stream is the context hash.

Truncated to 7 hex chars for the image tag (matches `git rev-parse
--short HEAD` convention).
"""
from __future__ import annotations

import fnmatch
import hashlib
from pathlib import Path


def context_hash(context_dir: Path, *, dockerfile: Path | None = None) -> str:
  """Return a 7-char hex digest of the build context.

  `dockerfile` is included in the hash even if it lives outside
  `context_dir` (Docker allows -f Dockerfile.foo with a different
  build context).
  """
  context_dir = context_dir.resolve()
  patterns = _read_dockerignore(context_dir)

  files: list[Path] = []
  for p in context_dir.rglob("*"):
    if p.is_file() and not _ignored(p.relative_to(context_dir), patterns):
      files.append(p)
  files.sort()

  hasher = hashlib.sha256()
  for f in files:
    rel = str(f.relative_to(context_dir)).replace("\\", "/")
    hasher.update(rel.encode("utf-8"))
    hasher.update(b"\0")
    hasher.update(_file_sha256(f))
    hasher.update(b"\0")

  if dockerfile is not None:
    df = dockerfile.resolve()
    if df.is_file() and df not in (f.resolve() for f in files):
      hasher.update(b"__dockerfile__\0")
      hasher.update(_file_sha256(df))
      hasher.update(b"\0")

  return hasher.hexdigest()[:7]


def _file_sha256(p: Path) -> bytes:
  h = hashlib.sha256()
  with p.open("rb") as f:
    for chunk in iter(lambda: f.read(65536), b""):
      h.update(chunk)
  return h.digest()


def _read_dockerignore(context_dir: Path) -> list[str]:
  """Return non-empty, non-comment lines from .dockerignore (or [])."""
  di = context_dir / ".dockerignore"
  if not di.is_file():
    return []
  out: list[str] = []
  for line in di.read_text(encoding="utf-8").splitlines():
    line = line.strip()
    if not line or line.startswith("#"):
      continue
    out.append(line)
  return out


def _ignored(rel: Path, patterns: list[str]) -> bool:
  """Best-effort .dockerignore matcher.

  We don't claim 100% Docker fidelity (! negation, ** semantics, etc.).
  This is good enough that the hash is stable in practice.
  """
  s = str(rel).replace("\\", "/")
  for pat in patterns:
    if pat.startswith("!"):
      # Negation — if a previous match excluded this, un-exclude. We
      # don't track previous matches, so treat negation as "always
      # include" (conservative; false-include is OK for hashing).
      continue
    if fnmatch.fnmatch(s, pat) or fnmatch.fnmatch(s, pat.rstrip("/") + "/*"):
      return True
    # Match by any path segment (Docker semantics for bare names).
    if "/" not in pat and any(fnmatch.fnmatch(part, pat) for part in s.split("/")):
      return True
  return False
