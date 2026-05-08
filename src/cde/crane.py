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

Crane-append fast-path for building images without a Docker daemon.

When `cde.yaml` sets `image.base_image`, `cde build` uses this module
instead of `docker build`. The flow:

  1. Resolve the base image to a digest (so the resulting tag pins to a
     specific base, immune to tag drift like :latest).
  2. Tar the local context (respecting .dockerignore), placing files
     under `workdir/` inside the tarball so they extract to that path
     in the resulting image.
  3. Hash the tar contents + base digest → 7-char tag like cde-<sha7>.
  4. Skip if `<repo>:cde-<sha7>` already exists in the registry.
  5. `crane mutate <base@digest> --append <tar> --workdir <workdir>
     --tag <repo>:cde-<sha7>` and let crane push for us.

Equivalent in spirit to xpk's `--base-docker-image + --script-dir`. No
Docker daemon required; only the `crane` binary needs to be on PATH
(install: https://github.com/google/go-containerregistry/tree/main/cmd/crane).
"""
from __future__ import annotations

import hashlib
import shutil
import subprocess
import tarfile
import tempfile
from pathlib import Path

from cde import context_hash, logging as log


class CraneError(RuntimeError):
  """A crane invocation failed."""


def is_available() -> bool:
  """Return True if `crane` is on PATH."""
  return shutil.which("crane") is not None


def resolve_digest(base_image: str) -> str:
  """Return the sha256:... digest of `base_image` via `crane digest`."""
  proc = subprocess.run(
      ["crane", "digest", base_image],
      capture_output=True, text=True, check=False,
  )
  if proc.returncode != 0:
    raise CraneError(
        f"crane digest {base_image} failed (exit {proc.returncode}): "
        f"{(proc.stderr or proc.stdout).strip()}"
    )
  return proc.stdout.strip()


def make_context_tarball(
    context_dir: Path, *, workdir: str, out: Path,
) -> None:
  """Tar everything under `context_dir` (respecting .dockerignore),
  placing files under `<workdir-relative>/` inside the tarball so they
  extract to `<workdir>` in the image.

  Tar entries are written in sorted relpath order with mtime=0 and
  uid/gid=0 so the tarball bytes are deterministic across runs."""
  prefix = workdir.lstrip("/").rstrip("/")
  if not prefix:
    raise CraneError(f"workdir must be non-root absolute path, got {workdir!r}")

  patterns = context_hash._read_dockerignore(context_dir)  # pylint: disable=protected-access

  files: list[Path] = []
  for p in context_dir.rglob("*"):
    if not p.is_file():
      continue
    rel = p.relative_to(context_dir)
    if context_hash._ignored(rel, patterns):  # pylint: disable=protected-access
      continue
    files.append(p)
  files.sort(key=lambda p: str(p.relative_to(context_dir)))

  import gzip
  import os

  # Write deterministic uncompressed tar first in a temporary file
  temp_tar = out.with_suffix(".tar")
  with tarfile.open(temp_tar, "w") as tar:
    for p in files:
      rel = p.relative_to(context_dir)
      info = tar.gettarinfo(str(p), arcname=f"{prefix}/{rel.as_posix()}")
      # Make the tarball deterministic: zero out mtime, owner, group.
      info.mtime = 0
      info.uid = 0
      info.gid = 0
      info.uname = ""
      info.gname = ""
      with open(p, "rb") as fh:
        tar.addfile(info, fileobj=fh)

  # Compress using Gzip with mtime=0 to ensure exact determinism
  with open(temp_tar, "rb") as f_in:
    with gzip.GzipFile(out, "wb", mtime=0) as f_out:
      shutil.copyfileobj(f_in, f_out)

  # Clean up uncompressed tar file
  temp_tar.unlink()


def context_sha7(tarball: Path, base_digest: str) -> str:
  """Hash the context tarball + base digest to a 7-char hex digest.
  Same length convention as the docker-build path."""
  h = hashlib.sha256()
  with open(tarball, "rb") as fh:
    for chunk in iter(lambda: fh.read(1 << 16), b""):
      h.update(chunk)
  h.update(b"\0")
  h.update(base_digest.encode("utf-8"))
  return h.hexdigest()[:7]


def append_and_push(
    *,
    base_image: str,
    base_digest: str,
    tarball: Path,
    workdir: str,
    tag: str,
) -> None:
  """Run `crane mutate` to append the tarball as a layer on top of base
  and push under `tag`. Raises CraneError on failure."""
  argv = [
      "crane", "mutate",
      f"{base_image}@{base_digest}",
      "--append", str(tarball),
      "--workdir", workdir,
      "--tag", tag,
  ]
  log.detail("$ %s", " ".join(argv))
  proc = subprocess.run(argv, capture_output=True, text=True, check=False)
  if proc.returncode != 0:
    raise CraneError(
        f"crane mutate failed (exit {proc.returncode}): "
        f"{(proc.stderr or proc.stdout).strip()}"
    )


def image_exists(tag: str) -> bool:
  """Return True if `tag` exists in the registry. Uses `crane manifest`."""
  proc = subprocess.run(
      ["crane", "manifest", tag],
      capture_output=True, text=True, check=False,
  )
  return proc.returncode == 0


def expected_tag(image_cfg, project_root: Path) -> str:
  """Compute the cde-<sha7> tag that `cde build / run / server` use,
  without actually building. Dispatches on `image_cfg.base_image`:

    base_image set     → tarball + base-digest hash (crane-append path)
    base_image unset   → context_hash over Dockerfile + tracked files
                         (docker-build path)

  For the crane path, this function shells out to `crane digest <base>`
  and constructs a temp tarball — same work that `cde build` would do.
  Modules that only need the tag (run / server) call this; build does
  its own pass and uses these primitives directly so it can reuse the
  tarball for the actual mutate step.
  """
  ctx_dir = (project_root / image_cfg.context).resolve()
  if image_cfg.base_image:
    with tempfile.TemporaryDirectory(prefix="cde-tag-") as tmp:
      tarball = Path(tmp) / "context.tar.gz"
      make_context_tarball(ctx_dir, workdir=image_cfg.workdir, out=tarball)
      digest = resolve_digest(image_cfg.base_image)
      sha7 = context_sha7(tarball, digest)
  else:
    from cde import context_hash  # late import: cde/__init__.py imports crane
    dockerfile = (project_root / image_cfg.dockerfile).resolve()
    sha7 = context_hash.context_hash(ctx_dir, dockerfile=dockerfile)
  return f"{image_cfg.repo_path}:cde-{sha7}"
