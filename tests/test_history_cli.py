"""Integration tests for cde history / annotate / tag / compare / lineage
against an isolated SQLite DB."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from cde import db


@pytest.fixture
def env(tmp_path, monkeypatch) -> dict:
  cde_home = tmp_path / ".cde"
  cde_home.mkdir()
  monkeypatch.setenv("CDE_HOME", str(cde_home))
  monkeypatch.setenv("CDE_PREFERENCES", str(tmp_path / "prefs.yaml"))
  return {"CDE_HOME": str(cde_home), "CDE_PREFERENCES": str(tmp_path / "prefs.yaml")}


def _cde(*args, env_overrides=None) -> subprocess.CompletedProcess:
  e = os.environ.copy()
  if env_overrides:
    e.update(env_overrides)
  return subprocess.run(
      [sys.executable, "-m", "cde", *args],
      capture_output=True, text=True, env=e, check=False,
  )


def _seed_db(tmp_path: Path, *runs: db.Run) -> None:
  with db.open_db(Path(tmp_path / ".cde" / "history.sqlite")) as conn:
    for r in runs:
      db.insert_run(conn, r)


def test_history_empty_message(env, tmp_path):
  result = _cde("history", env_overrides=env)
  assert result.returncode == 0, result.stderr
  assert "no runs" in result.stderr


def test_history_table(env, tmp_path):
  _seed_db(
      tmp_path,
      db.Run(run_id="v001", project="p1", team="alpha", value_class="benchmark",
             status="ok", overrides={"ep": 32}, notes="first run"),
      db.Run(run_id="v002", project="p1", team="alpha", value_class="benchmark",
             status="failed", overrides={"ep": 64}, notes="oops"),
  )
  result = _cde("history", "--all", env_overrides=env)
  assert result.returncode == 0, result.stderr
  assert "v001" in result.stdout
  assert "v002" in result.stdout
  assert "first run" in result.stdout
  assert "ep=32" in result.stdout


def test_history_json(env, tmp_path):
  _seed_db(tmp_path, db.Run(run_id="v001", project="p1", team="alpha"))
  result = _cde("history", "--all", "--json", env_overrides=env)
  assert result.returncode == 0, result.stderr
  data = json.loads(result.stdout)
  assert isinstance(data, list)
  assert len(data) == 1
  assert data[0]["run_id"] == "v001"


def test_history_one_run_json(env, tmp_path):
  _seed_db(tmp_path, db.Run(run_id="v001", project="p1", notes="abc"))
  result = _cde("history", "v001", env_overrides=env)
  assert result.returncode == 0, result.stderr
  data = json.loads(result.stdout)
  assert data["run_id"] == "v001"
  assert data["notes"] == "abc"


def test_history_filter_by_status(env, tmp_path):
  _seed_db(
      tmp_path,
      db.Run(run_id="v001", project="p1", status="ok"),
      db.Run(run_id="v002", project="p1", status="failed"),
  )
  result = _cde("history", "--all", "--status", "failed", env_overrides=env)
  assert result.returncode == 0, result.stderr
  assert "v002" in result.stdout
  assert "v001" not in result.stdout


def test_history_did_you_mean(env, tmp_path):
  _seed_db(tmp_path, db.Run(run_id="v001", project="p1"))
  result = _cde("history", "v0001", env_overrides=env)
  assert result.returncode == 1
  assert "Did you mean" in result.stderr
  assert "v001" in result.stderr


def test_annotate_with_message(env, tmp_path):
  _seed_db(tmp_path, db.Run(run_id="v001", project="p1"))
  result = _cde("annotate", "v001", "-m", "regression — see profile",
                env_overrides=env)
  assert result.returncode == 0, result.stderr
  with db.open_db(Path(tmp_path / ".cde" / "history.sqlite")) as conn:
    r = db.get_run(conn, "v001")
  assert r.notes == "regression — see profile"


def test_annotate_via_stdin(env, tmp_path):
  _seed_db(tmp_path, db.Run(run_id="v001", project="p1"))
  e = os.environ.copy(); e.update(env)
  result = subprocess.run(
      [sys.executable, "-m", "cde", "annotate", "v001"],
      input="from\nstdin\n",
      capture_output=True, text=True, env=e, check=False,
  )
  assert result.returncode == 0, result.stderr
  with db.open_db(Path(tmp_path / ".cde" / "history.sqlite")) as conn:
    r = db.get_run(conn, "v001")
  assert r.notes == "from\nstdin"


def test_tag_and_untag(env, tmp_path):
  _seed_db(tmp_path, db.Run(run_id="v001", project="p1"))
  assert _cde("tag", "v001", "best-so-far", env_overrides=env).returncode == 0
  assert _cde("tag", "v001", "regression", env_overrides=env).returncode == 0

  with db.open_db(Path(tmp_path / ".cde" / "history.sqlite")) as conn:
    r = db.get_run(conn, "v001")
  assert "best-so-far" in r.tags
  assert "regression" in r.tags

  assert _cde("untag", "v001", "regression", env_overrides=env).returncode == 0
  with db.open_db(Path(tmp_path / ".cde" / "history.sqlite")) as conn:
    r = db.get_run(conn, "v001")
  assert "regression" not in r.tags


def test_compare(env, tmp_path):
  _seed_db(
      tmp_path,
      db.Run(run_id="v001", project="p1", overrides={"ep": 32, "fsdp": 16},
             team="alpha", value_class="benchmark"),
      db.Run(run_id="v002", project="p1", overrides={"ep": 64, "fsdp": 16},
             team="alpha", value_class="benchmark", notes="more EP"),
  )
  result = _cde("compare", "v001", "v002", "--json", env_overrides=env)
  assert result.returncode == 0, result.stderr
  d = json.loads(result.stdout)
  assert d["a"] == "v001"
  assert d["b"] == "v002"
  assert "overrides" in d
  assert d["overrides"]["ep"] == {"a": 32, "b": 64}
  # fsdp same → not in delta
  assert "fsdp" not in d["overrides"]
  assert "notes" in d


def test_compare_table(env, tmp_path):
  _seed_db(
      tmp_path,
      db.Run(run_id="a", project="p1", overrides={"x": 1}),
      db.Run(run_id="b", project="p1", overrides={"x": 2}),
  )
  result = _cde("compare", "a", "b", env_overrides=env)
  assert result.returncode == 0, result.stderr
  assert "x" in result.stdout
  assert "1" in result.stdout and "2" in result.stdout


def test_lineage(env, tmp_path):
  _seed_db(
      tmp_path,
      db.Run(run_id="v001", project="p1", overrides={"a": 1}),
      db.Run(run_id="v002", project="p1", parent_run="v001", parent_submitter="",
             overrides={"a": 2}),
      db.Run(run_id="v003", project="p1", parent_run="v002", parent_submitter="",
             overrides={"a": 3}),
  )
  result = _cde("lineage", "v003", env_overrides=env)
  assert result.returncode == 0, result.stderr
  out = result.stdout
  # All three appear, in tip-first order
  assert out.index("v003") < out.index("v002") < out.index("v001")
