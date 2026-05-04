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

Sticky defaults — last-used values for a small allowlist of flags.

Lives at ~/.cde/recent.yaml (env-overridable via CDE_RECENT). Stores
*per-project* history so different projects don't leak defaults into
each other.

Format:
  version: 1
  projects:
    <project-name>:
      value_class: development
      team: alpha
      num_slices: 4
      declared_minutes: 60
      ts_updated: 2026-05-01T12:34:56+00:00

Why an allowlist? See PLAN.md. Briefly: --set values are per-experiment
knobs; making them sticky would silently propagate the very thing
you're iterating on. The four whitelisted fields don't change run-to-run.
"""
from __future__ import annotations

import datetime
import os
from dataclasses import dataclass
from pathlib import Path

import yaml

from cde import paths


_FILENAME = "recent.yaml"
_VERSION = 1
STICKY_FIELDS = ("value_class", "team", "num_slices", "declared_minutes")


@dataclass
class RecentDefaults:
  value_class: str | None = None
  team: str | None = None
  num_slices: int | None = None
  declared_minutes: int | None = None
  ts_updated: str | None = None

  def is_empty(self) -> bool:
    return all(getattr(self, f) is None for f in STICKY_FIELDS)


def _path() -> Path:
  override = os.environ.get("CDE_RECENT")
  if override:
    return Path(override).expanduser().resolve()
  return paths.cde_home() / _FILENAME


def load(project: str) -> RecentDefaults:
  p = _path()
  if not p.is_file():
    return RecentDefaults()
  try:
    raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
  except yaml.YAMLError:
    return RecentDefaults()
  projects = raw.get("projects") or {}
  d = projects.get(project) or {}
  if not isinstance(d, dict):
    return RecentDefaults()
  return RecentDefaults(
      value_class=d.get("value_class"),
      team=d.get("team"),
      num_slices=d.get("num_slices"),
      declared_minutes=d.get("declared_minutes"),
      ts_updated=d.get("ts_updated"),
  )


def save(project: str, defaults: RecentDefaults) -> None:
  """Write only the non-None fields, preserving other projects' entries."""
  p = _path()
  paths.ensure_cde_home()
  raw: dict = {}
  if p.is_file():
    try:
      raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError:
      raw = {}
  raw.setdefault("version", _VERSION)
  projects = raw.setdefault("projects", {})
  cur = projects.setdefault(project, {})
  for f in STICKY_FIELDS:
    val = getattr(defaults, f)
    if val is not None:
      cur[f] = val
  cur["ts_updated"] = datetime.datetime.now(datetime.timezone.utc).isoformat(
      timespec="seconds"
  )
  p.write_text(yaml.safe_dump(raw, sort_keys=True), encoding="utf-8")


def reset(project: str | None = None) -> None:
  """Clear the recent file for one project, or entirely."""
  p = _path()
  if not p.is_file():
    return
  if project is None:
    p.unlink()
    return
  try:
    raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
  except yaml.YAMLError:
    return
  projects = raw.get("projects") or {}
  if project in projects:
    del projects[project]
    p.write_text(yaml.safe_dump(raw, sort_keys=True), encoding="utf-8")
