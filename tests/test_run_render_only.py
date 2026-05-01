"""Smoke test: `cde run --render-only` produces a valid YAML manifest
without touching docker / kubectl / the cluster."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml


@pytest.fixture
def project(tmp_path, monkeypatch) -> Path:
  monkeypatch.chdir(tmp_path)
  monkeypatch.setenv("CDE_HOME", str(tmp_path / ".cde"))
  monkeypatch.setenv("CDE_PREFERENCES", str(tmp_path / "prefs.yaml"))

  # Scaffold via cde init
  init = subprocess.run(
      [sys.executable, "-m", "cde", "init"],
      capture_output=True, text=True, env=dict(os.environ),
  )
  assert init.returncode == 0, init.stderr

  # Patch REPLACE-ME tokens in cde.yaml
  cfg = (tmp_path / "cde.yaml").read_text()
  cfg = cfg.replace("REPLACE-ME", "alpha")
  (tmp_path / "cde.yaml").write_text(cfg)

  # Minimal Dockerfile + a tracked source file (for context-hash to have
  # something to hash)
  (tmp_path / "Dockerfile").write_text("FROM scratch\n")
  (tmp_path / "main.py").write_text("print('hello')\n")
  return tmp_path


def _cde(*args, env=None) -> subprocess.CompletedProcess:
  e = os.environ.copy()
  if env:
    e.update(env)
  return subprocess.run(
      [sys.executable, "-m", "cde", *args],
      capture_output=True, text=True, env=e, check=False,
  )


def test_render_only_produces_valid_yaml(project):
  result = _cde("run", "--tag", "v001", "--render-only")
  assert result.returncode == 0, result.stderr
  doc = yaml.safe_load(result.stdout)
  assert doc["kind"] == "JobSet"
  assert doc["metadata"]["name"] == "v001"
  assert doc["metadata"]["namespace"] == "team-alpha"
  labels = doc["metadata"]["labels"]
  assert labels["team"] == "alpha"
  assert labels["kueue.x-k8s.io/queue-name"] == "lq"
  assert labels["value-class"] == "development"           # from defaults
  assert labels["declared-duration-minutes"] == "60"

  # priorityClass on the pod template
  pod_spec = (
      doc["spec"]["replicatedJobs"][0]["template"]["spec"]
      ["template"]["spec"]
  )
  assert pod_spec["priorityClassName"] == "team-alpha-priority"

  # the four labels also on pod template (so Kueue propagates them)
  pt_labels = (
      doc["spec"]["replicatedJobs"][0]["template"]["spec"]
      ["template"]["metadata"]["labels"]
  )
  assert pt_labels["declared-duration-minutes"] == "60"
  assert pt_labels["cde.io/run-id"] == "v001"


def test_overrides_from_set_appear_in_args(project):
  result = _cde(
      "run", "--tag", "v002", "--render-only",
      "--set", "ep=32", "--set", "fsdp=16",
  )
  assert result.returncode == 0, result.stderr
  doc = yaml.safe_load(result.stdout)
  args_str = (
      doc["spec"]["replicatedJobs"][0]["template"]["spec"]
      ["template"]["spec"]["containers"][0]["args"][0]
  )
  assert "--ep=32" in args_str
  assert "--fsdp=16" in args_str


def test_value_class_override(project):
  result = _cde(
      "run", "--tag", "v003", "--render-only",
      "--value-class", "benchmark",
  )
  assert result.returncode == 0, result.stderr
  doc = yaml.safe_load(result.stdout)
  assert doc["metadata"]["labels"]["value-class"] == "benchmark"


def test_render_only_does_not_record_history(project):
  _cde("run", "--tag", "v004", "--render-only")
  hist_db = Path(os.environ["CDE_HOME"]).expanduser() / "history.sqlite"
  if hist_db.exists():
    import sqlite3
    rows = sqlite3.connect(hist_db).execute("SELECT COUNT(*) FROM runs").fetchone()
    assert rows[0] == 0


def test_invalid_set_format_errors(project):
  result = _cde("run", "--tag", "v005", "--render-only", "--set", "no-equals")
  assert result.returncode != 0
  assert "--set" in result.stderr
