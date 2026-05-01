"""Tests for the argcomplete completer functions."""

from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from cde import completers, db


@pytest.fixture
def env(tmp_path, monkeypatch) -> Path:
  cde_home = tmp_path / ".cde"
  cde_home.mkdir()
  monkeypatch.setenv("CDE_HOME", str(cde_home))
  monkeypatch.setenv("CDE_PREFERENCES", str(tmp_path / "prefs.yaml"))
  return tmp_path


def _seed(env: Path, *runs: db.Run) -> None:
  with db.open_db(env / ".cde" / "history.sqlite") as conn:
    for r in runs:
      db.insert_run(conn, r)


def test_run_id_completer_filters_by_prefix(env):
  _seed(
      env,
      db.Run(run_id="v001", project="p"),
      db.Run(run_id="v002", project="p"),
      db.Run(run_id="bench-001", project="p"),
  )
  out = completers.run_id_any_project_completer(prefix="v", parsed_args=None)
  assert sorted(out) == ["v001", "v002"]
  out = completers.run_id_any_project_completer(prefix="bench-", parsed_args=None)
  assert out == ["bench-001"]


def test_tag_completer(env):
  _seed(
      env,
      db.Run(run_id="a", project="p", tags=["best-so-far", "regression"]),
      db.Run(run_id="b", project="p", tags=["best-so-far", "exploratory"]),
  )
  # Without project filter (no cde.yaml in cwd → falls back to project=None)
  out = completers.tag_completer(prefix="", parsed_args=None)
  assert sorted(out) == sorted(["best-so-far", "exploratory", "regression"])

  out = completers.tag_completer(prefix="best", parsed_args=None)
  assert out == ["best-so-far"]


def test_value_class_completer_seeds_defaults_when_history_empty(env):
  out = completers.value_class_completer(prefix="b", parsed_args=None)
  assert "benchmark" in out


def test_value_class_completer_unions_history_and_defaults(env):
  _seed(env, db.Run(run_id="v001", project="p", value_class="custom-class"))
  out = completers.value_class_completer(prefix="", parsed_args=None)
  assert "custom-class" in out
  assert "development" in out


def test_project_completer(env):
  _seed(
      env,
      db.Run(run_id="a", project="proj-one"),
      db.Run(run_id="b", project="proj-two"),
      db.Run(run_id="c", project="proj-one"),  # dup
  )
  out = completers.project_completer(prefix="proj", parsed_args=None)
  assert sorted(out) == ["proj-one", "proj-two"]


def test_completer_robust_to_missing_db(env, monkeypatch):
  # CDE_HOME points at an empty dir; no DB file. Completer should
  # not raise — it should return [] gracefully.
  monkeypatch.setenv("CDE_HOME", str(env / "nope"))
  out = completers.run_id_any_project_completer(prefix="v", parsed_args=None)
  assert isinstance(out, list)  # empty or partial, never an exception
