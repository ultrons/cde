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

Tests for cde.yaml parsing.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from cde import config


def _write(p: Path, text: str) -> Path:
  p.write_text(text, encoding="utf-8")
  return p


def test_minimal_valid_config(tmp_path):
  cfg_path = _write(tmp_path / "cde.yaml", """
project: p
image:
  registry: gcr.io/example
  name: my-app
template: ./manifests/jobset.yaml.j2
team: alpha
""")
  cfg = config.load(cfg_path)
  assert cfg.image.registry == "gcr.io/example"
  assert cfg.image.name == "my-app"
  assert cfg.image.dockerfile == "./Dockerfile"  # default
  assert cfg.image.context == "."
  assert cfg.image.repo_path == "gcr.io/example/my-app"
  assert cfg.template == "./manifests/jobset.yaml.j2"
  assert cfg.team == "alpha"
  # Defaults
  assert cfg.defaults.value_class == "development"
  assert cfg.defaults.declared_duration_minutes == 60
  assert cfg.defaults.tpu_type is None
  assert cfg.defaults.num_slices == 1
  assert cfg.sync == []
  assert cfg.profile is None
  assert cfg.history.path == ""   # empty → caller falls back to paths.history_db_path()
  assert cfg.history.gcs_uri is None
  assert cfg.defaults_overrides == {}


def test_full_config_round_trip(tmp_path):
  cfg_path = _write(tmp_path / "cde.yaml", """
project: full-test
image:
  registry: gcr.io/proj
  name: trainer
  dockerfile: ./build/Dockerfile
  context: ./build
template: ./k8s/jobset.j2
team: ml-perf
defaults:
  value-class: benchmark
  declared-duration-minutes: 120
  tpu-type: tpu7x-128
  num-slices: 4
sync:
  - src: src/
    dest: /workspace/src/
  - src: configs/
    dest: /workspace/configs/
profile:
  base-uri: gs://my-bucket/profiles
history:
  path: /tmp/h.sqlite
  gcs_uri: gs://my-bucket/cde/runs.jsonl
defaults_overrides:
  batch_size: 1024
  ep: 32
""")
  cfg = config.load(cfg_path)
  assert cfg.image.dockerfile == "./build/Dockerfile"
  assert cfg.image.context == "./build"
  assert cfg.defaults.value_class == "benchmark"
  assert cfg.defaults.tpu_type == "tpu7x-128"
  assert cfg.defaults.num_slices == 4
  assert len(cfg.sync) == 2
  assert cfg.sync[0].src == "src/"
  assert cfg.sync[0].dest == "/workspace/src/"
  assert cfg.profile is not None
  assert cfg.profile.base_uri == "gs://my-bucket/profiles"
  assert cfg.history.gcs_uri == "gs://my-bucket/cde/runs.jsonl"
  assert cfg.defaults_overrides == {"batch_size": 1024, "ep": 32}


def test_missing_file_raises(tmp_path):
  with pytest.raises(config.ConfigError, match="not found"):
    config.load(tmp_path / "nope.yaml")


def test_missing_required_field_raises(tmp_path):
  cfg_path = _write(tmp_path / "cde.yaml", """
project: p
image:
  registry: gcr.io/example
  name: my-app
team: alpha
""")  # no `template`
  with pytest.raises(config.ConfigError, match="template"):
    config.load(cfg_path)


def test_top_level_must_be_mapping(tmp_path):
  cfg_path = _write(tmp_path / "cde.yaml", "- a list, not a mapping\n")
  with pytest.raises(config.ConfigError, match="mapping"):
    config.load(cfg_path)


def test_invalid_yaml_raises_clean(tmp_path):
  cfg_path = _write(tmp_path / "cde.yaml", "image: { unclosed\n")
  with pytest.raises(config.ConfigError, match="invalid YAML"):
    config.load(cfg_path)


def test_image_missing_subfield(tmp_path):
  cfg_path = _write(tmp_path / "cde.yaml", """
project: p
image:
  registry: gcr.io/example
template: ./manifests/jobset.yaml.j2
team: alpha
""")  # image.name missing
  with pytest.raises(config.ConfigError, match="image"):
    config.load(cfg_path)


def test_sync_must_be_list(tmp_path):
  cfg_path = _write(tmp_path / "cde.yaml", """
project: p
image:
  registry: gcr.io/example
  name: my-app
template: ./manifests/jobset.yaml.j2
team: alpha
sync: 'just a string'
""")
  with pytest.raises(config.ConfigError, match="sync"):
    config.load(cfg_path)


def test_empty_team_rejected(tmp_path):
  cfg_path = _write(tmp_path / "cde.yaml", """
project: p
image:
  registry: gcr.io/example
  name: my-app
template: ./manifests/jobset.yaml.j2
team: ""
""")
  with pytest.raises(config.ConfigError, match="team"):
    config.load(cfg_path)
