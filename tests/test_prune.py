"""Integration tests for `cde prune`."""

from __future__ import annotations

import datetime
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


def _ago(days: int) -> str:
  return (
      datetime.datetime.now(datetime.timezone.utc)
      - datetime.timedelta(days=days)
  ).isoformat(timespec="seconds")


def _read_run_ids(tmp_path: Path) -> set[str]:
  with db.open_db(Path(tmp_path / ".cde" / "history.sqlite")) as conn:
    return {r.run_id for r in db.list_runs(conn, limit=10000)}


# -- defaults --------------------------------------------------------------


def test_prune_dry_run_does_not_delete(env, tmp_path):
  _seed_db(
      tmp_path,
      db.Run(run_id="v001", project="p1", status="failed",
             ts_submitted=_ago(30)),
  )
  result = _cde("prune", "--all", env_overrides=env)
  assert result.returncode == 0, result.stderr
  assert "v001" in result.stderr
  assert "dry-run" in result.stderr
  # No deletion without --apply
  assert _read_run_ids(tmp_path) == {"v001"}


def test_prune_apply_deletes_failed_evicted(env, tmp_path):
  _seed_db(
      tmp_path,
      db.Run(run_id="v001", project="p1", status="ok", ts_submitted=_ago(30)),
      db.Run(run_id="v002", project="p1", status="failed",
             ts_submitted=_ago(30)),
      db.Run(run_id="v003", project="p1", status="evicted",
             ts_submitted=_ago(30)),
  )
  result = _cde("prune", "--all", "--apply", env_overrides=env)
  assert result.returncode == 0, result.stderr
  assert "pruned 2" in result.stderr
  assert _read_run_ids(tmp_path) == {"v001"}


def test_prune_keeps_ok_status_by_default(env, tmp_path):
  _seed_db(
      tmp_path,
      db.Run(run_id="v001", project="p1", status="ok", ts_submitted=_ago(30)),
  )
  result = _cde("prune", "--all", "--apply", env_overrides=env)
  assert result.returncode == 0, result.stderr
  assert _read_run_ids(tmp_path) == {"v001"}


# -- safety filters --------------------------------------------------------


def test_prune_keeps_tagged_runs_by_default(env, tmp_path):
  _seed_db(
      tmp_path,
      db.Run(run_id="v001", project="p1", status="failed",
             ts_submitted=_ago(30), tags=["bs-sweep"]),
      db.Run(run_id="v002", project="p1", status="failed",
             ts_submitted=_ago(30)),
  )
  result = _cde("prune", "--all", "--apply", env_overrides=env)
  assert result.returncode == 0, result.stderr
  assert _read_run_ids(tmp_path) == {"v001"}


def test_prune_include_tagged_overrides_safety(env, tmp_path):
  _seed_db(
      tmp_path,
      db.Run(run_id="v001", project="p1", status="failed",
             ts_submitted=_ago(30), tags=["bs-sweep"]),
  )
  result = _cde(
      "prune", "--all", "--apply", "--include-tagged",
      env_overrides=env,
  )
  assert result.returncode == 0, result.stderr
  assert _read_run_ids(tmp_path) == set()


def test_prune_keeps_annotated_runs_by_default(env, tmp_path):
  _seed_db(
      tmp_path,
      db.Run(run_id="v001", project="p1", status="failed",
             ts_submitted=_ago(30), notes="learned X here"),
      db.Run(run_id="v002", project="p1", status="failed",
             ts_submitted=_ago(30), hypothesis="trying Y"),
      db.Run(run_id="v003", project="p1", status="failed",
             ts_submitted=_ago(30)),
  )
  result = _cde("prune", "--all", "--apply", env_overrides=env)
  assert result.returncode == 0, result.stderr
  # Both notes-bearing and hypothesis-bearing rows survive; v003 deleted
  assert _read_run_ids(tmp_path) == {"v001", "v002"}


def test_prune_keeps_recent_runs_by_default(env, tmp_path):
  _seed_db(
      tmp_path,
      db.Run(run_id="v001", project="p1", status="failed", ts_submitted=_ago(1)),
      db.Run(run_id="v002", project="p1", status="failed", ts_submitted=_ago(30)),
  )
  result = _cde("prune", "--all", "--apply", env_overrides=env)
  assert result.returncode == 0, result.stderr
  assert _read_run_ids(tmp_path) == {"v001"}


def test_prune_keep_recent_zero_disables_recency(env, tmp_path):
  _seed_db(
      tmp_path,
      db.Run(run_id="v001", project="p1", status="failed", ts_submitted=_ago(1)),
  )
  result = _cde(
      "prune", "--all", "--apply", "--keep-recent", "0d",
      env_overrides=env,
  )
  assert result.returncode == 0, result.stderr
  assert _read_run_ids(tmp_path) == set()


# -- status / scope --------------------------------------------------------


def test_prune_status_narrows_to_one(env, tmp_path):
  _seed_db(
      tmp_path,
      db.Run(run_id="v001", project="p1", status="failed", ts_submitted=_ago(30)),
      db.Run(run_id="v002", project="p1", status="evicted", ts_submitted=_ago(30)),
  )
  result = _cde(
      "prune", "--all", "--apply", "--status", "failed",
      env_overrides=env,
  )
  assert result.returncode == 0, result.stderr
  # Only failed pruned; evicted survives
  assert _read_run_ids(tmp_path) == {"v002"}


def test_prune_does_not_touch_running_by_default(env, tmp_path):
  _seed_db(
      tmp_path,
      db.Run(run_id="v001", project="p1", status="running", ts_submitted=_ago(30)),
      db.Run(run_id="v002", project="p1", status="submitted", ts_submitted=_ago(30)),
  )
  result = _cde("prune", "--all", "--apply", env_overrides=env)
  assert result.returncode == 0, result.stderr
  assert _read_run_ids(tmp_path) == {"v001", "v002"}


def test_prune_include_running_lets_you_clear_zombies(env, tmp_path):
  _seed_db(
      tmp_path,
      db.Run(run_id="v001", project="p1", status="running", ts_submitted=_ago(30)),
  )
  result = _cde(
      "prune", "--all", "--apply", "--include-running",
      env_overrides=env,
  )
  assert result.returncode == 0, result.stderr
  assert _read_run_ids(tmp_path) == set()


def test_prune_lineage_chain_truncates_gracefully(env, tmp_path):
  """The vllm/maxtext-style fix-and-retry pattern: every intermediate
  failure has a child via --inherit. Pruning evicted rows breaks the
  parent_run chain on survivors. cde lineage should still walk as far
  as it can."""
  _seed_db(
      tmp_path,
      db.Run(run_id="v001", project="p1", status="evicted",
             ts_submitted=_ago(30)),
      db.Run(run_id="v002", project="p1", status="evicted",
             ts_submitted=_ago(30), parent_run="v001"),
      db.Run(run_id="v003", project="p1", status="ok",
             ts_submitted=_ago(30), parent_run="v002"),
  )
  result = _cde("prune", "--all", "--apply", env_overrides=env)
  assert result.returncode == 0, result.stderr
  # v003 is ok (kept). v001 + v002 are evicted, untagged, unannotated, old → pruned.
  assert _read_run_ids(tmp_path) == {"v003"}
  # Lineage on v003 must not crash even though parent_run="v002" no longer exists.
  lineage = _cde("lineage", "v003", env_overrides=env)
  assert lineage.returncode == 0, lineage.stderr
