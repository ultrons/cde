"""argcomplete-driven tab completers.

Each completer is a callable(prefix, parsed_args, **kwargs) returning a
list of completion candidates. argcomplete invokes them in a special
shell-side process — kept fast and side-effect-free.

Data sources:
  - Run history (filtered to the current project) for run_id, tag, value-class.
  - cfg.defaults.value_class as a fallback seed for value-class.
"""

from __future__ import annotations

from pathlib import Path

from cde import config, db, paths


def _project_filter() -> str | None:
  cfg_path = paths.project_config_path()
  if not cfg_path.is_file():
    return None
  try:
    return config.load(cfg_path).project
  except Exception:  # pylint: disable=broad-except
    return None


def _db_path() -> Path:
  cfg_path = paths.project_config_path()
  if cfg_path.is_file():
    try:
      cfg = config.load(cfg_path)
      if cfg.history.path:
        return Path(cfg.history.path).expanduser()
    except Exception:  # pylint: disable=broad-except
      pass
  return paths.history_db_path()


def _safe_runs(limit: int = 200, project: str | None = None) -> list[db.Run]:
  try:
    with db.open_db(_db_path()) as conn:
      return db.list_runs(conn, project=project, limit=limit)
  except Exception:  # pylint: disable=broad-except
    return []


# ---------------------------------------------------------------------------
# Completers — public callables for argcomplete `.completer = ...`
# ---------------------------------------------------------------------------


def run_id_completer(prefix: str, parsed_args, **_kwargs) -> list[str]:
  """Complete a run id from history, filtered to the current project."""
  project = _project_filter()
  rows = _safe_runs(project=project)
  return [r.run_id for r in rows if r.run_id.startswith(prefix or "")]


def run_id_any_project_completer(prefix: str, parsed_args, **_kwargs) -> list[str]:
  """Complete a run id from history, no project filter."""
  rows = _safe_runs(project=None)
  return [r.run_id for r in rows if r.run_id.startswith(prefix or "")]


def tag_completer(prefix: str, parsed_args, **_kwargs) -> list[str]:
  """Complete a tag from history (union across all rows in this project)."""
  project = _project_filter()
  rows = _safe_runs(project=project)
  tags: set[str] = set()
  for r in rows:
    tags.update(r.tags)
  return sorted(t for t in tags if t.startswith(prefix or ""))


def value_class_completer(prefix: str, parsed_args, **_kwargs) -> list[str]:
  """Complete --value-class from history + cfg defaults."""
  project = _project_filter()
  rows = _safe_runs(project=project)
  candidates: set[str] = {r.value_class for r in rows if r.value_class}
  # Add common defaults so the very first run also gets a completion list.
  candidates.update({"development", "benchmark", "regression", "scale-test"})
  return sorted(c for c in candidates if c.startswith(prefix or ""))


def project_completer(prefix: str, parsed_args, **_kwargs) -> list[str]:
  """Complete --project from distinct projects seen in history."""
  rows = _safe_runs(project=None, limit=500)
  projects: set[str] = {r.project for r in rows if r.project}
  return sorted(p for p in projects if p.startswith(prefix or ""))
