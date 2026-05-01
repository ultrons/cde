"""Tests for recent.py (sticky defaults storage)."""

from __future__ import annotations

from pathlib import Path

import pytest

from cde import recent


@pytest.fixture
def env(tmp_path, monkeypatch) -> Path:
  monkeypatch.setenv("CDE_HOME", str(tmp_path / ".cde"))
  monkeypatch.setenv("CDE_RECENT", str(tmp_path / "recent.yaml"))
  return tmp_path


def test_load_missing_returns_empty(env):
  d = recent.load("any-project")
  assert d.is_empty()


def test_save_and_load_per_project(env):
  recent.save("p1", recent.RecentDefaults(value_class="benchmark", num_slices=4))
  recent.save("p2", recent.RecentDefaults(value_class="development", team="beta"))

  a = recent.load("p1")
  assert a.value_class == "benchmark"
  assert a.num_slices == 4
  assert a.team is None        # not set for p1

  b = recent.load("p2")
  assert b.value_class == "development"
  assert b.team == "beta"


def test_save_preserves_other_projects(env):
  recent.save("p1", recent.RecentDefaults(value_class="a"))
  recent.save("p2", recent.RecentDefaults(value_class="b"))
  recent.save("p1", recent.RecentDefaults(value_class="c"))   # update p1

  assert recent.load("p1").value_class == "c"
  assert recent.load("p2").value_class == "b"   # preserved


def test_reset_one_project(env):
  recent.save("p1", recent.RecentDefaults(value_class="a"))
  recent.save("p2", recent.RecentDefaults(value_class="b"))
  recent.reset(project="p1")
  assert recent.load("p1").is_empty()
  assert recent.load("p2").value_class == "b"


def test_reset_all(env):
  recent.save("p1", recent.RecentDefaults(value_class="a"))
  recent.save("p2", recent.RecentDefaults(value_class="b"))
  recent.reset()                                 # all
  assert recent.load("p1").is_empty()
  assert recent.load("p2").is_empty()
