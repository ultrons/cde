"""cde.yaml schema, loader, and validator.

Pure stdlib + PyYAML. No pydantic — the validation we need is shallow
(check required fields, type-check a few scalars, fail loudly with a
clear message). Worth ~80 lines of code; not worth a 5MB dep.

Schema version 1. If we add fields later that change interpretation,
bump CONFIG_SCHEMA_VERSION and add a translator.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

CONFIG_SCHEMA_VERSION = 1


# ---------------------------------------------------------------------------
# Dataclasses (the in-memory shape after parsing)
# ---------------------------------------------------------------------------


@dataclass
class ImageConfig:
  registry: str                    # e.g. gcr.io/tpu-vm-gke-testing
  name: str                        # e.g. jaxgpt-tpu
  dockerfile: str = "./Dockerfile"
  context: str = "."

  @property
  def repo_path(self) -> str:
    """Full image path without the tag, e.g. gcr.io/.../jaxgpt-tpu."""
    return f"{self.registry.rstrip('/')}/{self.name}"


@dataclass
class SyncMapping:
  src: str                         # local path
  dest: str                        # in-pod path


@dataclass
class ProfileConfig:
  base_uri: str                    # e.g. gs://my-bucket/cde-profiles


@dataclass
class HistoryConfig:
  path: str = "~/.cde/history.sqlite"
  gcs_uri: str | None = None       # opt-in multi-machine write-through


@dataclass
class Defaults:
  value_class: str = "development"
  declared_duration_minutes: int = 60
  tpu_type: str | None = None
  num_slices: int = 1


@dataclass
class CdeConfig:
  """The parsed shape of cde.yaml.

  Required: image (registry + name), template (manifest path), team.
  Everything else has defaults.
  """

  image: ImageConfig
  template: str                    # e.g. ./manifests/jobset.yaml.j2
  team: str

  defaults: Defaults = field(default_factory=Defaults)
  sync: list[SyncMapping] = field(default_factory=list)
  profile: ProfileConfig | None = None
  history: HistoryConfig = field(default_factory=HistoryConfig)
  defaults_overrides: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class ConfigError(Exception):
  """Raised for any malformed cde.yaml. Caller should print and exit 1."""


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------


def load(path: Path) -> CdeConfig:
  if not path.is_file():
    raise ConfigError(f"cde.yaml not found at {path}")

  text = path.read_text(encoding="utf-8")
  try:
    raw = yaml.safe_load(text) or {}
  except yaml.YAMLError as exc:
    raise ConfigError(f"{path}: invalid YAML: {exc}") from exc

  if not isinstance(raw, dict):
    raise ConfigError(f"{path}: top-level must be a mapping, got {type(raw).__name__}")

  return _from_dict(raw, source=str(path))


def _require(d: dict[str, Any], key: str, source: str) -> Any:
  if key not in d:
    raise ConfigError(f"{source}: missing required field `{key}`")
  return d[key]


def _from_dict(raw: dict[str, Any], *, source: str) -> CdeConfig:
  # image
  image_raw = _require(raw, "image", source)
  if not isinstance(image_raw, dict):
    raise ConfigError(f"{source}: `image` must be a mapping")
  image = ImageConfig(
      registry=_require(image_raw, "registry", source + ":image"),
      name=_require(image_raw, "name", source + ":image"),
      dockerfile=image_raw.get("dockerfile", "./Dockerfile"),
      context=image_raw.get("context", "."),
  )

  # template
  template = _require(raw, "template", source)
  if not isinstance(template, str):
    raise ConfigError(f"{source}: `template` must be a string path")

  # team
  team = _require(raw, "team", source)
  if not isinstance(team, str) or not team.strip():
    raise ConfigError(f"{source}: `team` must be a non-empty string")

  # defaults
  d_raw = raw.get("defaults") or {}
  if not isinstance(d_raw, dict):
    raise ConfigError(f"{source}: `defaults` must be a mapping")
  defaults = Defaults(
      value_class=d_raw.get("value-class", "development"),
      declared_duration_minutes=int(d_raw.get("declared-duration-minutes", 60)),
      tpu_type=d_raw.get("tpu-type"),
      num_slices=int(d_raw.get("num-slices", 1)),
  )

  # sync
  sync_raw = raw.get("sync") or []
  if not isinstance(sync_raw, list):
    raise ConfigError(f"{source}: `sync` must be a list")
  sync: list[SyncMapping] = []
  for i, item in enumerate(sync_raw):
    if not isinstance(item, dict):
      raise ConfigError(f"{source}: sync[{i}] must be a mapping")
    sync.append(
        SyncMapping(
            src=_require(item, "src", f"{source}:sync[{i}]"),
            dest=_require(item, "dest", f"{source}:sync[{i}]"),
        )
    )

  # profile
  profile_raw = raw.get("profile")
  profile: ProfileConfig | None = None
  if profile_raw is not None:
    if not isinstance(profile_raw, dict):
      raise ConfigError(f"{source}: `profile` must be a mapping")
    profile = ProfileConfig(
        base_uri=_require(profile_raw, "base-uri", source + ":profile"),
    )

  # history
  hist_raw = raw.get("history") or {}
  if not isinstance(hist_raw, dict):
    raise ConfigError(f"{source}: `history` must be a mapping")
  history = HistoryConfig(
      path=hist_raw.get("path", "~/.cde/history.sqlite"),
      gcs_uri=hist_raw.get("gcs_uri"),
  )

  # defaults_overrides — free-form dict
  overrides = raw.get("defaults_overrides") or {}
  if not isinstance(overrides, dict):
    raise ConfigError(f"{source}: `defaults_overrides` must be a mapping")

  return CdeConfig(
      image=image,
      template=template,
      team=team,
      defaults=defaults,
      sync=sync,
      profile=profile,
      history=history,
      defaults_overrides=overrides,
  )
