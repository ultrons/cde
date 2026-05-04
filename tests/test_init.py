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

Tests for `cde init`.
"""
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


_PATHWAYS_JOBSET = """\
apiVersion: jobset.x-k8s.io/v1alpha2
kind: JobSet
metadata:
  name: pathways-bench-001
  namespace: poc-ml-perf
  labels:
    kueue.x-k8s.io/queue-name: lq
    team: ml-perf
    value-class: benchmark
    declared-duration-minutes: "60"
spec:
  coordinator:
    replicatedJob: pathways-head
  network:
    enableDNSHostnames: true
    publishNotReadyAddresses: true
  failurePolicy:
    restartStrategy: Recreate
  replicatedJobs:
  - name: pathways-head
    replicas: 1
    template:
      spec:
        parallelism: 1
        completions: 1
        template:
          spec:
            priorityClassName: poc-ml-perf-priority
            nodeSelector:
              cloud.google.com/gke-nodepool: cpu-np
            containers:
            - name: pathways-proxy
              image: us-docker.pkg.dev/cloud-tpu-v2-images/pathways/proxy_server:vXYZ
            - name: pathways-rm
              image: us-docker.pkg.dev/cloud-tpu-v2-images/pathways/server:vXYZ
            - name: trainer
              image: gcr.io/my-proj/pathways-app:v3
              command: ["python3", "train.py"]
  - name: worker
    replicas: 4
    template:
      spec:
        parallelism: 16
        completions: 16
        template:
          spec:
            priorityClassName: poc-ml-perf-priority
            nodeSelector:
              cloud.google.com/gke-tpu-accelerator: tpu7x
              cloud.google.com/gke-tpu-topology: 4x4x4
            containers:
            - name: jax-worker
              image: gcr.io/my-proj/pathways-app:v3
              command: ["bash", "-c"]
              args: ["sleep infinity"]
              resources:
                limits:
                  google.com/tpu: "4"
"""


def test_from_yaml_preserves_multi_replicatedjob_pathways_shape(in_tmp):
  """Pathways JobSets have two replicatedJobs (head + worker). The
  scaffold must preserve both, template the user's image consistently,
  and template num_slices on the worker (not the head)."""
  src = in_tmp / "pathways.yaml"
  src.write_text(_PATHWAYS_JOBSET)

  result = _run_cde(
      "init", "--project", "pathways-bench",
      "--from-yaml", str(src),
      env_overrides={"CDE_HOME": str(in_tmp / ".cde")},
  )
  assert result.returncode == 0, result.stderr

  cde_yaml = (in_tmp / "cde.yaml").read_text()
  template = (in_tmp / "manifests" / "jobset.yaml.j2").read_text()

  # cde.yaml — inferred from the worker replicatedJob (replicas=4)
  assert "registry: gcr.io/my-proj" in cde_yaml
  assert "name: pathways-app" in cde_yaml
  assert "team: ml-perf" in cde_yaml
  assert "tpu-type: tpu7x" in cde_yaml
  assert "num-slices: 4" in cde_yaml
  # Non-derived namespace pinned (poc-ml-perf doesn't match team-<team>)
  assert "namespace: poc-ml-perf" in cde_yaml

  # Template — both replicatedJobs preserved
  assert "name: pathways-head" in template
  assert "name: worker" in template
  # head's replicas stays literal (replicas: 1, not templated)
  head_section = template.split("name: pathways-head")[1].split("name: worker")[0]
  assert "replicas: 1" in head_section
  assert "{{ num_slices }}" not in head_section
  # worker's replicas IS templated
  worker_section = template.split("name: worker")[1]
  assert "replicas: {{ num_slices }}" in worker_section
  # User's image is templated everywhere it appeared (head trainer + worker)
  assert "image: {{ image }}" in template
  # Pathways proxy/server images preserved literal
  assert "pathways/proxy_server:vXYZ" in template
  assert "pathways/server:vXYZ" in template
  # JobSet-level Pathways fields (coordinator, restartStrategy) preserved
  assert "coordinator:" in template
  assert "replicatedJob: pathways-head" in template
  assert "restartStrategy: Recreate" in template
  # cde.io/run-id label injected on the JobSet itself (pyyaml may quote
  # the placeholder; either form is fine — Jinja still expands)
  assert (
      "cde.io/run-id: {{ run_id }}" in template
      or "cde.io/run-id: '{{ run_id }}'" in template
  )


def test_from_yaml_pathways_template_renders(in_tmp):
  """End-to-end: imported Pathways YAML re-renders cleanly via cde run."""
  src = in_tmp / "pathways.yaml"
  src.write_text(_PATHWAYS_JOBSET)

  result = _run_cde(
      "init", "--project", "pathways-bench",
      "--from-yaml", str(src),
      env_overrides={"CDE_HOME": str(in_tmp / ".cde")},
  )
  assert result.returncode == 0, result.stderr

  text = (in_tmp / "cde.yaml").read_text()
  assert "REPLACE-ME" not in text
  (in_tmp / "Dockerfile").write_text("FROM scratch\n")
  (in_tmp / "main.py").write_text("print('ok')\n")

  rendered = _run_cde(
      "run", "--tag", "p001", "--render-only",
      env_overrides={"CDE_HOME": str(in_tmp / ".cde")},
  )
  assert rendered.returncode == 0, rendered.stderr

  import yaml as _yaml
  doc = _yaml.safe_load(rendered.stdout)
  assert doc["kind"] == "JobSet"
  rjs = doc["spec"]["replicatedJobs"]
  assert len(rjs) == 2
  assert rjs[0]["name"] == "pathways-head"
  assert rjs[0]["replicas"] == 1                  # literal, not templated
  assert rjs[1]["name"] == "worker"
  assert rjs[1]["replicas"] == 4                  # default num-slices
  assert "coordinator" in doc["spec"]
  # User image substituted; Pathways images preserved
  head_containers = (
      rjs[0]["template"]["spec"]["template"]["spec"]["containers"]
  )
  by_name = {c["name"]: c for c in head_containers}
  assert "pathways/proxy_server:vXYZ" in by_name["pathways-proxy"]["image"]
  assert "pathways/server:vXYZ" in by_name["pathways-rm"]["image"]
  # trainer's image resolves to the user's image (cde-tagged)
  assert by_name["trainer"]["image"].startswith("gcr.io/my-proj/pathways-app:cde-")
