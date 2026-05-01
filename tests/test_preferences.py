"""Tests for the preferences loader."""

from __future__ import annotations

from pathlib import Path

import pytest

from cde import preferences as prefs


def _write(p: Path, text: str) -> Path:
  p.write_text(text, encoding="utf-8")
  return p


def test_missing_file_returns_defaults(tmp_path):
  cfg = prefs.load(tmp_path / "nope.yaml")
  assert cfg == prefs.Preferences()
  assert cfg.build.driver == "docker"
  assert cfg.build.sudo is False
  assert cfg.history.default_limit == 20


def test_partial_overrides_keep_defaults(tmp_path):
  p = _write(tmp_path / "p.yaml", """
build:
  sudo: true
  driver: podman
""")
  cfg = prefs.load(p)
  assert cfg.build.driver == "podman"
  assert cfg.build.sudo is True
  # Untouched defaults still apply
  assert cfg.build.use_buildkit is True
  assert cfg.build.push_after_build is True
  assert cfg.history.default_limit == 20


def test_full_override(tmp_path):
  p = _write(tmp_path / "p.yaml", """
schema_version: 1
build:
  driver: nerdctl
  sudo: false
  builder: local
  use_buildkit: false
  push_after_build: false
docker:
  registry_default: gcr.io/example
git:
  detect_dirty: false
  fail_on_dirty: true
cli:
  color: never
  editor: vim
history:
  default_limit: 50
sync:
  delete_extras: true
  watch_debounce_ms: 100
profile:
  default_base_uri: gs://my-bucket/profiles
team_quota:
  configmap_name: custom-name
  configmap_namespace: custom-ns
""")
  cfg = prefs.load(p)
  assert cfg.build.driver == "nerdctl"
  assert cfg.build.use_buildkit is False
  assert cfg.docker.registry_default == "gcr.io/example"
  assert cfg.git.fail_on_dirty is True
  assert cfg.cli.color == "never"
  assert cfg.history.default_limit == 50
  assert cfg.sync.delete_extras is True
  assert cfg.profile.default_base_uri == "gs://my-bucket/profiles"
  assert cfg.team_quota.configmap_name == "custom-name"


def test_invalid_driver_rejected(tmp_path):
  p = _write(tmp_path / "p.yaml", """
build:
  driver: dockerd-the-typo
""")
  with pytest.raises(prefs.PreferencesError, match="driver"):
    prefs.load(p)


def test_invalid_color_rejected(tmp_path):
  p = _write(tmp_path / "p.yaml", """
cli:
  color: technicolor
""")
  with pytest.raises(prefs.PreferencesError, match="color"):
    prefs.load(p)


def test_section_must_be_mapping(tmp_path):
  p = _write(tmp_path / "p.yaml", "build: 'just a string'\n")
  with pytest.raises(prefs.PreferencesError, match="build"):
    prefs.load(p)


def test_invalid_yaml_clean_error(tmp_path):
  p = _write(tmp_path / "p.yaml", "build: { unclosed\n")
  with pytest.raises(prefs.PreferencesError, match="invalid YAML"):
    prefs.load(p)
