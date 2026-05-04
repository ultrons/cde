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

Filesystem layout: where cde keeps its state and looks for project config.

Two locations matter:

  per-user state    ~/.cde/                 — history.sqlite, logs, etc.
  per-project conf  ./cde.yaml              — project's cde config

Both can be overridden via environment variables for tests / sandboxes:

  CDE_HOME       — overrides ~/.cde
  CDE_CONFIG     — overrides ./cde.yaml lookup
"""
from __future__ import annotations

import os
from pathlib import Path


def cde_home() -> Path:
  override = os.environ.get("CDE_HOME")
  if override:
    return Path(override).expanduser().resolve()
  return Path.home() / ".cde"


def history_db_path() -> Path:
  return cde_home() / "history.sqlite"


def project_config_path(start: Path | None = None) -> Path:
  """Return the path to ./cde.yaml in the nearest enclosing directory.

  Walks up from `start` (default: cwd) looking for a `cde.yaml`. Returns
  the path even if the file does not exist (so callers can use it for
  `cde init`).

  Honors $CDE_CONFIG override.
  """
  override = os.environ.get("CDE_CONFIG")
  if override:
    return Path(override).expanduser().resolve()

  cur = (start or Path.cwd()).resolve()
  while True:
    candidate = cur / "cde.yaml"
    if candidate.is_file():
      return candidate
    if cur.parent == cur:
      # Hit filesystem root; fall back to cwd/cde.yaml (may not exist).
      return (start or Path.cwd()).resolve() / "cde.yaml"
    cur = cur.parent


def ensure_cde_home() -> Path:
  home = cde_home()
  home.mkdir(parents=True, exist_ok=True)
  return home
