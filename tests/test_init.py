"""Tests for `cde init`."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.fixture
def in_tmp(tmp_path, monkeypatch) -> Path:
  monkeypatch.chdir(tmp_path)
  monkeypatch.setenv("CDE_HOME", str(tmp_path / ".cde"))
  return tmp_path


def _run_cde(*args: str, env_overrides: dict | None = None) -> subprocess.CompletedProcess:
  env = os.environ.copy()
  if env_overrides:
    env.update(env_overrides)
  return subprocess.run(
      [sys.executable, "-m", "cde", *args],
      capture_output=True, text=True, env=env, check=False,
  )


def test_init_creates_files(in_tmp):
  result = _run_cde("init", env_overrides={"CDE_HOME": str(in_tmp / ".cde")})
  assert result.returncode == 0, result.stderr
  assert (in_tmp / "cde.yaml").is_file()
  assert (in_tmp / "manifests" / "jobset.yaml.j2").is_file()
  # SQLite DB should be initialised under CDE_HOME
  assert (in_tmp / ".cde" / "history.sqlite").is_file()


def test_init_refuses_to_overwrite_without_force(in_tmp):
  (in_tmp / "cde.yaml").write_text("# pre-existing\n")
  result = _run_cde("init", env_overrides={"CDE_HOME": str(in_tmp / ".cde")})
  assert result.returncode == 1
  assert "already exists" in result.stderr
  assert (in_tmp / "cde.yaml").read_text() == "# pre-existing\n"


def test_init_force_overwrites(in_tmp):
  (in_tmp / "cde.yaml").write_text("# pre-existing\n")
  result = _run_cde(
      "init", "--force",
      env_overrides={"CDE_HOME": str(in_tmp / ".cde")},
  )
  assert result.returncode == 0, result.stderr
  text = (in_tmp / "cde.yaml").read_text()
  assert "image:" in text
  assert "# pre-existing" not in text


def test_init_no_history_skips_db(in_tmp):
  result = _run_cde(
      "init", "--no-history",
      env_overrides={"CDE_HOME": str(in_tmp / ".cde")},
  )
  assert result.returncode == 0, result.stderr
  assert not (in_tmp / ".cde" / "history.sqlite").exists()


def test_scaffolded_cde_yaml_loads(in_tmp):
  """Make sure the scaffolded cde.yaml is parseable. cde init pre-fills
  project + image.name from the cwd basename; we just patch the
  remaining REPLACE-ME tokens (registry, team) before loading."""
  result = _run_cde(
      "init", "--project", "demo-project",
      env_overrides={"CDE_HOME": str(in_tmp / ".cde")},
  )
  assert result.returncode == 0, result.stderr

  text = (in_tmp / "cde.yaml").read_text()
  patched = text.replace("REPLACE-ME", "alpha")
  cfg_path = in_tmp / "cde.yaml.patched"
  cfg_path.write_text(patched)

  from cde import config
  cfg = config.load(cfg_path)
  assert cfg.project == "demo-project"
  assert cfg.image.name == "demo-project"  # init defaulted it
  assert cfg.team == "alpha"


def test_init_defaults_project_to_cwd_basename(in_tmp):
  result = _run_cde("init", env_overrides={"CDE_HOME": str(in_tmp / ".cde")})
  assert result.returncode == 0, result.stderr
  text = (in_tmp / "cde.yaml").read_text()
  expected = in_tmp.name
  assert f"project: {expected}" in text
  assert f"name: {expected}" in text


def test_init_explicit_project(in_tmp):
  result = _run_cde(
      "init", "--project", "my-cool-experiment",
      env_overrides={"CDE_HOME": str(in_tmp / ".cde")},
  )
  assert result.returncode == 0, result.stderr
  text = (in_tmp / "cde.yaml").read_text()
  assert "project: my-cool-experiment" in text
