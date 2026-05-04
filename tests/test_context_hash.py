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

Tests for the deterministic build-context hash.
"""
from __future__ import annotations

from pathlib import Path

from cde import context_hash


def _write(p: Path, content: str = "x") -> None:
  p.parent.mkdir(parents=True, exist_ok=True)
  p.write_text(content)


def test_hash_is_deterministic(tmp_path):
  ctx = tmp_path / "ctx"
  _write(ctx / "Dockerfile", "FROM scratch\n")
  _write(ctx / "src" / "a.py", "print('a')\n")
  _write(ctx / "src" / "b.py", "print('b')\n")

  h1 = context_hash.context_hash(ctx)
  h2 = context_hash.context_hash(ctx)
  assert h1 == h2
  assert len(h1) == 7


def test_hash_changes_when_a_file_changes(tmp_path):
  ctx = tmp_path / "ctx"
  _write(ctx / "Dockerfile", "FROM scratch\n")
  _write(ctx / "src" / "a.py", "print('a')\n")

  h1 = context_hash.context_hash(ctx)
  _write(ctx / "src" / "a.py", "print('a-modified')\n")
  h2 = context_hash.context_hash(ctx)
  assert h1 != h2


def test_hash_changes_when_a_file_added(tmp_path):
  ctx = tmp_path / "ctx"
  _write(ctx / "Dockerfile", "FROM scratch\n")
  _write(ctx / "src" / "a.py", "print('a')\n")

  h1 = context_hash.context_hash(ctx)
  _write(ctx / "src" / "b.py", "print('b')\n")
  h2 = context_hash.context_hash(ctx)
  assert h1 != h2


def test_dockerignore_excludes_files(tmp_path):
  ctx = tmp_path / "ctx"
  _write(ctx / "Dockerfile", "FROM scratch\n")
  _write(ctx / "src" / "a.py", "print('a')\n")
  _write(ctx / ".dockerignore", "scratch/\n*.log\n")

  h_before = context_hash.context_hash(ctx)
  _write(ctx / "scratch" / "throwaway.txt", "garbage")
  _write(ctx / "build.log", "build noise")
  h_after = context_hash.context_hash(ctx)
  assert h_before == h_after, "dockerignore'd files should not change the hash"


def test_external_dockerfile_factored_in(tmp_path):
  ctx = tmp_path / "ctx"
  _write(ctx / "src" / "a.py", "print('a')\n")
  df1 = tmp_path / "Dockerfile.x"
  _write(df1, "FROM scratch\n")

  h1 = context_hash.context_hash(ctx, dockerfile=df1)
  _write(df1, "FROM ubuntu:22.04\n")
  h2 = context_hash.context_hash(ctx, dockerfile=df1)
  assert h1 != h2
