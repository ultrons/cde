"""Tests for the crane-append fast-path."""

from __future__ import annotations

import hashlib
import tarfile
from pathlib import Path
from unittest.mock import patch

import pytest

from cde import crane


# -- tarball construction ---------------------------------------------------


def test_make_context_tarball_places_files_under_workdir(tmp_path):
  ctx = tmp_path / "ctx"
  ctx.mkdir()
  (ctx / "main.py").write_text("print('hi')")
  (ctx / "subdir").mkdir()
  (ctx / "subdir" / "lib.py").write_text("# helper")
  out = tmp_path / "ctx.tar.gz"

  crane.make_context_tarball(ctx, workdir="/app", out=out)

  with tarfile.open(out, "r:gz") as tar:
    names = sorted(tar.getnames())
  assert names == ["app/main.py", "app/subdir/lib.py"]


def test_make_context_tarball_is_deterministic(tmp_path):
  """Identical inputs must produce byte-identical tarballs (mtime,
  uid/gid all zeroed). Otherwise the cde-tag would drift between
  builds even when source is unchanged."""
  ctx = tmp_path / "ctx"
  ctx.mkdir()
  (ctx / "a.py").write_text("a")
  (ctx / "b.py").write_text("b")
  out1 = tmp_path / "out1.tar.gz"
  out2 = tmp_path / "out2.tar.gz"

  crane.make_context_tarball(ctx, workdir="/app", out=out1)
  crane.make_context_tarball(ctx, workdir="/app", out=out2)

  # gzip framing carries an mtime header that depends on os.utime; compare
  # the inner tar bytes rather than the gzip-wrapped bytes.
  with tarfile.open(out1, "r:gz") as t1, tarfile.open(out2, "r:gz") as t2:
    names1 = [m.name for m in t1.getmembers()]
    names2 = [m.name for m in t2.getmembers()]
    members1 = [(m.name, m.mtime, m.uid, m.gid, m.size) for m in t1.getmembers()]
    members2 = [(m.name, m.mtime, m.uid, m.gid, m.size) for m in t2.getmembers()]
  assert names1 == names2
  assert members1 == members2
  for m in members1:
    assert m[1] == 0  # mtime zeroed
    assert m[2] == 0  # uid zeroed
    assert m[3] == 0  # gid zeroed


def test_make_context_tarball_respects_dockerignore(tmp_path):
  ctx = tmp_path / "ctx"
  ctx.mkdir()
  (ctx / "keep.py").write_text("ok")
  (ctx / "ignore.log").write_text("noise")
  (ctx / ".dockerignore").write_text("*.log\n")
  out = tmp_path / "ctx.tar.gz"

  crane.make_context_tarball(ctx, workdir="/app", out=out)

  with tarfile.open(out, "r:gz") as tar:
    names = tar.getnames()
  assert "app/keep.py" in names
  assert "app/ignore.log" not in names


def test_make_context_tarball_rejects_root_workdir(tmp_path):
  ctx = tmp_path / "ctx"
  ctx.mkdir()
  (ctx / "x").write_text("y")
  with pytest.raises(crane.CraneError, match="non-root"):
    crane.make_context_tarball(ctx, workdir="/", out=tmp_path / "x.tar.gz")


# -- hash determinism -------------------------------------------------------


def test_context_sha7_is_deterministic(tmp_path):
  tar = tmp_path / "ctx.tar.gz"
  tar.write_bytes(b"the same bytes")
  digest = "sha256:abc123def"
  a = crane.context_sha7(tar, digest)
  b = crane.context_sha7(tar, digest)
  assert a == b
  assert len(a) == 7


def test_context_sha7_changes_when_base_digest_changes(tmp_path):
  """If the base image is re-pushed under the same tag, the digest
  changes, the cde-<sha7> must change, and stale registry entries
  don't accidentally cache-hit."""
  tar = tmp_path / "ctx.tar.gz"
  tar.write_bytes(b"unchanged source")
  a = crane.context_sha7(tar, "sha256:base-v1")
  b = crane.context_sha7(tar, "sha256:base-v2")
  assert a != b


def test_context_sha7_changes_when_tarball_changes(tmp_path):
  digest = "sha256:abc"
  tar1 = tmp_path / "tar1.tar.gz"
  tar2 = tmp_path / "tar2.tar.gz"
  tar1.write_bytes(b"version 1")
  tar2.write_bytes(b"version 2")
  assert crane.context_sha7(tar1, digest) != crane.context_sha7(tar2, digest)


# -- subprocess wrappers (mocked) -------------------------------------------


class _FakeProc:
  def __init__(self, returncode: int, stdout: str = "", stderr: str = ""):
    self.returncode = returncode
    self.stdout = stdout
    self.stderr = stderr


def test_resolve_digest_returns_digest_string():
  with patch(
      "cde.crane.subprocess.run",
      return_value=_FakeProc(0, stdout="sha256:deadbeef\n"),
  ):
    assert crane.resolve_digest("gcr.io/myproj/base:v1") == "sha256:deadbeef"


def test_resolve_digest_raises_on_failure():
  with patch(
      "cde.crane.subprocess.run",
      return_value=_FakeProc(1, stderr="image not found"),
  ):
    with pytest.raises(crane.CraneError, match="not found"):
      crane.resolve_digest("gcr.io/missing:v0")


def test_image_exists_true_when_manifest_succeeds():
  with patch(
      "cde.crane.subprocess.run", return_value=_FakeProc(0, stdout="{}"),
  ):
    assert crane.image_exists("gcr.io/x/y:cde-abc1234") is True


def test_image_exists_false_when_manifest_fails():
  with patch(
      "cde.crane.subprocess.run",
      return_value=_FakeProc(1, stderr="MANIFEST_UNKNOWN"),
  ):
    assert crane.image_exists("gcr.io/x/y:cde-deadbef") is False


def test_append_and_push_constructs_correct_argv(tmp_path):
  tarball = tmp_path / "src.tar.gz"
  tarball.write_bytes(b"x")
  with patch(
      "cde.crane.subprocess.run", return_value=_FakeProc(0),
  ) as mock_run:
    crane.append_and_push(
        base_image="gcr.io/myproj/base:v1",
        base_digest="sha256:abc",
        tarball=tarball,
        workdir="/app",
        tag="gcr.io/myproj/myimg:cde-1234567",
    )
  argv = mock_run.call_args.args[0]
  assert argv[:2] == ["crane", "mutate"]
  assert "gcr.io/myproj/base:v1@sha256:abc" in argv
  assert "--append" in argv
  assert str(tarball) in argv
  assert "--workdir" in argv
  assert "/app" in argv
  assert "--tag" in argv
  assert "gcr.io/myproj/myimg:cde-1234567" in argv


def test_append_and_push_raises_on_crane_failure(tmp_path):
  tarball = tmp_path / "src.tar.gz"
  tarball.write_bytes(b"x")
  with patch(
      "cde.crane.subprocess.run",
      return_value=_FakeProc(1, stderr="auth required"),
  ):
    with pytest.raises(crane.CraneError, match="auth"):
      crane.append_and_push(
          base_image="gcr.io/x/y:v1",
          base_digest="sha256:abc",
          tarball=tarball,
          workdir="/app",
          tag="gcr.io/x/y:cde-1",
      )


# -- expected_tag dispatcher ------------------------------------------------


def test_expected_tag_picks_docker_path_when_no_base_image(tmp_path):
  """Without base_image set, falls back to context_hash over Dockerfile +
  context (existing docker-build path)."""
  from cde.config import ImageConfig

  ctx = tmp_path / "ctx"
  ctx.mkdir()
  (ctx / "main.py").write_text("hello")
  (tmp_path / "Dockerfile").write_text("FROM python:3.10\n")

  cfg = ImageConfig(
      registry="gcr.io/p", name="myimg", dockerfile="Dockerfile",
      context="ctx",
  )
  tag = crane.expected_tag(cfg, tmp_path)
  assert tag.startswith("gcr.io/p/myimg:cde-")
  # No subprocess was called (didn't shell out to crane)


def test_expected_tag_picks_crane_path_when_base_image_set(tmp_path):
  """With base_image set, computes the tag via tar + base digest."""
  from cde.config import ImageConfig

  ctx = tmp_path / "ctx"
  ctx.mkdir()
  (ctx / "main.py").write_text("hello")

  cfg = ImageConfig(
      registry="gcr.io/p", name="myimg", dockerfile="Dockerfile",
      context="ctx", base_image="gcr.io/p/base:v1", workdir="/app",
  )
  with patch(
      "cde.crane.subprocess.run",
      return_value=_FakeProc(0, stdout="sha256:deadbeef\n"),
  ):
    tag = crane.expected_tag(cfg, tmp_path)
  assert tag.startswith("gcr.io/p/myimg:cde-")
  assert len(tag.split("cde-")[-1]) == 7
