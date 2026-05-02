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


def test_init_writes_dockerignore_with_sensible_defaults(in_tmp):
  result = _run_cde("init", env_overrides={"CDE_HOME": str(in_tmp / ".cde")})
  assert result.returncode == 0, result.stderr
  di = (in_tmp / ".dockerignore")
  assert di.is_file(), "cde init should scaffold .dockerignore"
  text = di.read_text()
  # Things that shouldn't churn the build-context hash
  for pat in ("cde.yaml", "manifests/", ".cde/", ".git/", "__pycache__/", "*.md"):
    assert pat in text, f"missing {pat!r} in scaffolded .dockerignore"


def test_init_does_not_overwrite_existing_dockerignore(in_tmp):
  (in_tmp / ".dockerignore").write_text("# user content\nmy-secrets/\n")
  result = _run_cde("init", env_overrides={"CDE_HOME": str(in_tmp / ".cde")})
  assert result.returncode == 0, result.stderr
  text = (in_tmp / ".dockerignore").read_text()
  assert text == "# user content\nmy-secrets/\n", (
      "init should not clobber existing .dockerignore without --force"
  )


def test_init_force_overwrites_dockerignore(in_tmp):
  (in_tmp / ".dockerignore").write_text("# user content\n")
  result = _run_cde(
      "init", "--force",
      env_overrides={"CDE_HOME": str(in_tmp / ".cde")},
  )
  assert result.returncode == 0, result.stderr
  text = (in_tmp / ".dockerignore").read_text()
  assert "cde.yaml" in text  # the scaffolded content


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


# ---------------------------------------------------------------------------
# --from-yaml
# ---------------------------------------------------------------------------


_SAMPLE_JOBSET = """\
apiVersion: jobset.x-k8s.io/v1alpha2
kind: JobSet
metadata:
  name: dsv3-train-v304
  namespace: poc-dev
  labels:
    kueue.x-k8s.io/queue-name: lq
    team: dsv3-team
    value-class: benchmark
    declared-duration-minutes: "120"
spec:
  failurePolicy:
    maxRestarts: 0
  replicatedJobs:
  - name: slice
    replicas: 4
    template:
      spec:
        parallelism: 64
        completions: 64
        backoffLimit: 0
        template:
          metadata:
            labels:
              declared-duration-minutes: "120"
              custom.io/keep-me: yes-please
          spec:
            priorityClassName: poc-dev-priority
            restartPolicy: Never
            nodeSelector:
              cloud.google.com/gke-tpu-accelerator: tpu7x
              cloud.google.com/gke-tpu-topology: 4x8x8
            containers:
            - name: main
              image: gcr.io/my-proj/dsv3-train:noag-v3
              command: ["/bin/bash", "-c"]
              args: ["python train.py --batch_size=4096"]
              env:
              - name: LIBTPU_INIT_ARGS
                value: "--xla-flag-1 --xla-flag-2"
              resources:
                limits:
                  google.com/tpu: "4"
"""


def test_from_yaml_imports_existing_jobset(in_tmp):
  src = in_tmp / "existing.yaml"
  src.write_text(_SAMPLE_JOBSET)

  result = _run_cde(
      "init", "--project", "dsv3-onboard",
      "--from-yaml", str(src),
      env_overrides={"CDE_HOME": str(in_tmp / ".cde")},
  )
  assert result.returncode == 0, result.stderr

  cde_yaml = (in_tmp / "cde.yaml").read_text()
  template = (in_tmp / "manifests" / "jobset.yaml.j2").read_text()

  # cde.yaml inferred values
  assert "project: dsv3-onboard" in cde_yaml
  assert "registry: gcr.io/my-proj" in cde_yaml
  assert "name: dsv3-train" in cde_yaml
  assert "team: dsv3-team" in cde_yaml
  assert "value-class: benchmark" in cde_yaml
  assert "declared-duration-minutes: 120" in cde_yaml
  assert "tpu-type: tpu7x" in cde_yaml
  assert "num-slices: 4" in cde_yaml
  # Non-derived namespace pinned in defaults_overrides. priority_class
  # derives correctly from namespace (poc-dev-priority == <ns>-priority),
  # so it stays unpinned.
  assert "namespace: poc-dev" in cde_yaml

  # Template — Jinja placeholders for cde-owned bits. pyyaml may quote
  # placeholders in non-_UNQUOTE_KEYS positions with single quotes; match
  # either to avoid coupling tests to dump-style.
  assert "name: {{ run_id }}" in template
  assert "namespace: {{ namespace }}" in template
  assert "team: {{ team }}" in template
  assert "value-class: {{ value_class }}" in template
  assert (
      "declared-duration-minutes: '{{ declared_minutes }}'" in template
      or 'declared-duration-minutes: "{{ declared_minutes }}"' in template
  )
  assert "priorityClassName: {{ priority_class }}" in template
  assert "image: {{ image }}" in template
  assert "replicas: {{ num_slices }}" in template
  # cde.io/run-id label injected (quoted form is fine — Jinja still expands)
  assert (
      "cde.io/run-id: {{ run_id }}" in template
      or "cde.io/run-id: '{{ run_id }}'" in template
  )
  # Custom labels / env / nodeSelector / resources preserved verbatim
  assert "custom.io/keep-me: yes-please" in template
  assert "LIBTPU_INIT_ARGS" in template
  assert "--xla-flag-1 --xla-flag-2" in template
  assert "gke-tpu-topology: 4x8x8" in template
  assert "google.com/tpu: '4'" in template or 'google.com/tpu: "4"' in template


def test_from_yaml_template_renders_with_cde_run(in_tmp):
  """Round-trip: --from-yaml output must work as a real cde run template."""
  src = in_tmp / "existing.yaml"
  src.write_text(_SAMPLE_JOBSET)

  result = _run_cde(
      "init", "--project", "dsv3-onboard",
      "--from-yaml", str(src),
      env_overrides={"CDE_HOME": str(in_tmp / ".cde")},
  )
  assert result.returncode == 0, result.stderr

  # Patch any leftover REPLACE-ME (none expected for this rich source) and
  # exercise the render path, which loads cde.yaml + applies the template.
  text = (in_tmp / "cde.yaml").read_text()
  assert "REPLACE-ME" not in text, (
      f"Inference left REPLACE-ME tokens behind:\n{text}"
  )

  # Minimal Dockerfile so cde build's hash step works.
  (in_tmp / "Dockerfile").write_text("FROM scratch\n")
  (in_tmp / "main.py").write_text("print('hi')\n")

  rendered = _run_cde(
      "run", "--tag", "v001", "--render-only",
      env_overrides={"CDE_HOME": str(in_tmp / ".cde")},
  )
  assert rendered.returncode == 0, rendered.stderr

  import yaml as _yaml
  doc = _yaml.safe_load(rendered.stdout)
  assert doc["kind"] == "JobSet"
  assert doc["metadata"]["name"] == "v001"
  assert doc["metadata"]["namespace"] == "poc-dev"
  assert doc["metadata"]["labels"]["team"] == "dsv3-team"
  assert doc["metadata"]["labels"]["declared-duration-minutes"] == "120"
  pod_spec = (
      doc["spec"]["replicatedJobs"][0]["template"]["spec"]
      ["template"]["spec"]
  )
  assert pod_spec["priorityClassName"] == "poc-dev-priority"
  # Custom env preserved through render
  envs = {e["name"]: e["value"] for e in pod_spec["containers"][0]["env"]}
  assert envs["LIBTPU_INIT_ARGS"] == "--xla-flag-1 --xla-flag-2"


def test_from_yaml_no_jobset_errors(in_tmp):
  src = in_tmp / "not-a-jobset.yaml"
  src.write_text(
      "apiVersion: v1\nkind: ConfigMap\nmetadata:\n  name: foo\n"
  )
  result = _run_cde(
      "init", "--from-yaml", str(src),
      env_overrides={"CDE_HOME": str(in_tmp / ".cde")},
  )
  assert result.returncode == 1
  assert "no JobSet" in result.stderr
