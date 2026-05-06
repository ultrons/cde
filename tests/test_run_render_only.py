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

Smoke test: `cde run --render-only` produces a valid YAML manifest
without touching docker / kubectl / the cluster.
"""
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

  # Kueue topology-aware-scheduling annotations on the pod template. Without
  # these, JobSets on a TAS-required ResourceFlavor sit forever with
  # QuotaReserved=False. The scaffold ships them so users get a working
  # default; users can delete them if their cluster doesn't need TAS.
  pt_annotations = (
      doc["spec"]["replicatedJobs"][0]["template"]["spec"]
      ["template"]["metadata"]["annotations"]
  )
  assert pt_annotations["kueue.x-k8s.io/podset-required-topology"] == (
      "cloud.google.com/gke-tpu-topology"
  )
  assert pt_annotations["kueue.x-k8s.io/podset-slice-required-topology"] == (
      "cloud.google.com/gke-tpu-topology"
  )


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


def test_namespace_priority_class_filtered_from_overrides(project):
  """defaults_overrides.namespace + priority_class configure cde itself.
  They get exposed to the template as {{ namespace }} / {{ priority_class }},
  but must NOT leak into the `overrides` dict that user templates iterate
  over for args. Otherwise `python train.py {% for k,v in overrides %}` ends
  up with `--namespace=...` in the args line."""
  cfg_path = project / "cde.yaml"
  text = cfg_path.read_text()
  text = text.replace(
      "defaults_overrides: {}",
      "defaults_overrides:\n  namespace: my-ns\n  priority_class: my-pc",
      1,
  )
  cfg_path.write_text(text)

  result = _cde("run", "--tag", "v100", "--render-only")
  assert result.returncode == 0, result.stderr
  import yaml
  doc = yaml.safe_load(result.stdout)
  # namespace + priority_class still flow through their first-class slots
  assert doc["metadata"]["namespace"] == "my-ns"
  pod_spec = (
      doc["spec"]["replicatedJobs"][0]["template"]["spec"]
      ["template"]["spec"]
  )
  assert pod_spec["priorityClassName"] == "my-pc"
  # …but NOT into the args line that iterates `overrides`
  args_str = pod_spec["containers"][0]["args"][0]
  assert "namespace=" not in args_str
  assert "priority_class=" not in args_str


def test_flag_renders_as_bare_flag(project):
  result = _cde(
      "run", "--tag", "v006", "--render-only",
      "--flag", "gradient_checkpoint",
      "--flag", "enforce_eager",
      "--no-flag", "use_v2_block_manager",
      "--set", "ep=32",
  )
  assert result.returncode == 0, result.stderr
  import yaml
  doc = yaml.safe_load(result.stdout)
  args_str = (
      doc["spec"]["replicatedJobs"][0]["template"]["spec"]
      ["template"]["spec"]["containers"][0]["args"][0]
  )
  assert "--gradient_checkpoint " in args_str
  assert "--enforce_eager " in args_str
  assert "--ep=32" in args_str
  # --no-flag must NOT emit a key=value or a bare flag
  assert "use_v2_block_manager" not in args_str


def test_run_deletes_history_row_on_failed_apply(project):
  # Regression for #5: a failed `kubectl apply` must NOT leave a row in
  # history.sqlite. Previously, every transient apply failure (bogus context,
  # webhook rejection, API timeout) forced the user to `cde delete --purge
  # --force <tag>` before they could retry with the same --tag — exactly the
  # iteration-loop friction Susan reported in #4.
  #
  # We trigger the failure with a deliberately bogus --context: kubectl will
  # exit non-zero with "context ... does not exist", which becomes a
  # KubectlError the apply branch catches. The row inserted just before the
  # apply must be deleted, not just status-updated to "failed".
  result = _cde(
      "run", "--tag", "v_apply_fail",
      "--context", "bogus-context-does-not-exist",
  )
  assert result.returncode != 0
  hist_db = Path(os.environ["CDE_HOME"]).expanduser() / "history.sqlite"
  if not hist_db.exists():
    return  # nothing to check; row certainly absent
  import sqlite3
  rows = sqlite3.connect(hist_db).execute(
      "SELECT run_id, status FROM runs WHERE run_id=?", ("v_apply_fail",),
  ).fetchall()
  assert rows == [], (
      f"row from failed apply should have been deleted; still in DB: {rows}"
  )
