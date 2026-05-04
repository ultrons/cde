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

Per-user cde preferences.

Loaded from ~/.cde/preferences.yaml (env-overridable via CDE_PREFERENCES).
Missing file = use defaults; missing keys within file = use defaults.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from cde import paths


PREFERENCES_FILENAME = "preferences.yaml"

# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------


@dataclass
class BuildPrefs:
  driver: str = "docker"           # docker | podman | nerdctl
  sudo: bool = False
  builder: str = "local"           # local | gcb (gcb deferred to v0.5+)
  use_buildkit: bool = True
  push_after_build: bool = True


@dataclass
class DockerPrefs:
  registry_default: str = ""


@dataclass
class GitPrefs:
  detect_dirty: bool = True
  fail_on_dirty: bool = False


@dataclass
class CliPrefs:
  color: str = "auto"              # auto | always | never
  editor: str = ""                 # "" → fall back to $EDITOR


@dataclass
class HistoryPrefs:
  default_limit: int = 20
  gcs_uri: str | None = None       # opt-in multi-machine write-through


@dataclass
class SyncPrefs:
  delete_extras: bool = False
  watch_debounce_ms: int = 500


@dataclass
class ProfilePrefs:
  default_base_uri: str = ""


@dataclass
class TeamQuotaPrefs:
  configmap_name: str = "team-quota-config"
  configmap_namespace: str = "kueue-system"


@dataclass
class Preferences:
  schema_version: int = 1
  build: BuildPrefs = field(default_factory=BuildPrefs)
  docker: DockerPrefs = field(default_factory=DockerPrefs)
  git: GitPrefs = field(default_factory=GitPrefs)
  cli: CliPrefs = field(default_factory=CliPrefs)
  history: HistoryPrefs = field(default_factory=HistoryPrefs)
  sync: SyncPrefs = field(default_factory=SyncPrefs)
  profile: ProfilePrefs = field(default_factory=ProfilePrefs)
  team_quota: TeamQuotaPrefs = field(default_factory=TeamQuotaPrefs)


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class PreferencesError(Exception):
  """Raised on a malformed preferences.yaml."""


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------


_VALID_DRIVERS = {"docker", "podman", "nerdctl"}
_VALID_COLORS = {"auto", "always", "never"}
_VALID_BUILDERS = {"local", "gcb"}


def preferences_path() -> Path:
  override = os.environ.get("CDE_PREFERENCES")
  if override:
    return Path(override).expanduser().resolve()
  return paths.cde_home() / PREFERENCES_FILENAME


def load(path: Path | None = None) -> Preferences:
  """Read and validate preferences.yaml. Missing file → all defaults."""
  p = path or preferences_path()
  if not p.is_file():
    return Preferences()

  try:
    raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
  except yaml.YAMLError as exc:
    raise PreferencesError(f"{p}: invalid YAML: {exc}") from exc
  if not isinstance(raw, dict):
    raise PreferencesError(
        f"{p}: top-level must be a mapping, got {type(raw).__name__}"
    )

  return _from_dict(raw, source=str(p))


def _section(raw: dict, key: str, source: str) -> dict[str, Any]:
  val = raw.get(key) or {}
  if not isinstance(val, dict):
    raise PreferencesError(f"{source}: `{key}` must be a mapping")
  return val


def _from_dict(raw: dict[str, Any], *, source: str) -> Preferences:
  build_raw = _section(raw, "build", source)
  driver = build_raw.get("driver", "docker")
  if driver not in _VALID_DRIVERS:
    raise PreferencesError(
        f"{source}: build.driver must be one of {sorted(_VALID_DRIVERS)},"
        f" got {driver!r}"
    )
  builder = build_raw.get("builder", "local")
  if builder not in _VALID_BUILDERS:
    raise PreferencesError(
        f"{source}: build.builder must be one of {sorted(_VALID_BUILDERS)},"
        f" got {builder!r}"
    )
  build = BuildPrefs(
      driver=driver,
      sudo=bool(build_raw.get("sudo", False)),
      builder=builder,
      use_buildkit=bool(build_raw.get("use_buildkit", True)),
      push_after_build=bool(build_raw.get("push_after_build", True)),
  )

  docker_raw = _section(raw, "docker", source)
  docker = DockerPrefs(registry_default=docker_raw.get("registry_default", ""))

  git_raw = _section(raw, "git", source)
  git = GitPrefs(
      detect_dirty=bool(git_raw.get("detect_dirty", True)),
      fail_on_dirty=bool(git_raw.get("fail_on_dirty", False)),
  )

  cli_raw = _section(raw, "cli", source)
  color = cli_raw.get("color", "auto")
  if color not in _VALID_COLORS:
    raise PreferencesError(
        f"{source}: cli.color must be one of {sorted(_VALID_COLORS)},"
        f" got {color!r}"
    )
  cli = CliPrefs(color=color, editor=cli_raw.get("editor", ""))

  history_raw = _section(raw, "history", source)
  history = HistoryPrefs(
      default_limit=int(history_raw.get("default_limit", 20)),
      gcs_uri=history_raw.get("gcs_uri"),
  )

  sync_raw = _section(raw, "sync", source)
  sync = SyncPrefs(
      delete_extras=bool(sync_raw.get("delete_extras", False)),
      watch_debounce_ms=int(sync_raw.get("watch_debounce_ms", 500)),
  )

  profile_raw = _section(raw, "profile", source)
  profile = ProfilePrefs(
      default_base_uri=profile_raw.get("default_base_uri", ""),
  )

  tq_raw = _section(raw, "team_quota", source)
  tq = TeamQuotaPrefs(
      configmap_name=tq_raw.get("configmap_name", "team-quota-config"),
      configmap_namespace=tq_raw.get("configmap_namespace", "kueue-system"),
  )

  return Preferences(
      schema_version=int(raw.get("schema_version", 1)),
      build=build,
      docker=docker,
      git=git,
      cli=cli,
      history=history,
      sync=sync,
      profile=profile,
      team_quota=tq,
  )
