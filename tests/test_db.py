"""Tests for the SQLite history store."""

from __future__ import annotations

from pathlib import Path

import pytest

from cde import db


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
  return tmp_path / "history.sqlite"


def test_connect_creates_db_and_runs_migrations(db_path):
  conn = db.connect(db_path)
  cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
  tables = {row[0] for row in cur.fetchall()}
  assert "runs" in tables
  assert "schema_migrations" in tables
  cur = conn.execute("SELECT version FROM schema_migrations ORDER BY version")
  versions = [row[0] for row in cur.fetchall()]
  # Update as new migrations are appended.
  assert versions == [1, 2, 3]
  conn.close()


def test_migrations_idempotent(db_path):
  expected = [1, 2, 3]
  db.connect(db_path).close()
  db.connect(db_path).close()  # second connect should be a no-op
  conn = db.connect(db_path)
  cur = conn.execute("SELECT version FROM schema_migrations ORDER BY version")
  versions = [row[0] for row in cur.fetchall()]
  assert versions == expected


def test_insert_and_get_run(db_path):
  with db.open_db(db_path) as conn:
    run = db.Run(
        run_id="v001",
        team="alpha",
        value_class="development",
        declared_min=60,
        overrides={"ep": 32, "fsdp": 16},
        notes="first run",
        tags=["exploratory"],
    )
    db.insert_run(conn, run)
    fetched = db.get_run(conn, "v001")
    assert fetched is not None
    assert fetched.run_id == "v001"
    assert fetched.team == "alpha"
    assert fetched.overrides == {"ep": 32, "fsdp": 16}
    assert fetched.tags == ["exploratory"]
    assert fetched.git_dirty is False  # bool conversion
    assert fetched.ts_submitted  # auto-populated


def test_get_unknown_returns_none(db_path):
  with db.open_db(db_path) as conn:
    assert db.get_run(conn, "v999") is None


def test_list_runs_orders_by_ts_desc(db_path):
  with db.open_db(db_path) as conn:
    db.insert_run(conn, db.Run(run_id="v001", ts_submitted="2026-05-01T10:00:00+00:00"))
    db.insert_run(conn, db.Run(run_id="v002", ts_submitted="2026-05-01T11:00:00+00:00"))
    db.insert_run(conn, db.Run(run_id="v003", ts_submitted="2026-05-01T09:00:00+00:00"))
    runs = db.list_runs(conn, limit=10)
  assert [r.run_id for r in runs] == ["v002", "v001", "v003"]


def test_list_runs_filters_by_status(db_path):
  with db.open_db(db_path) as conn:
    db.insert_run(conn, db.Run(run_id="a", status="ok"))
    db.insert_run(conn, db.Run(run_id="b", status="failed"))
    db.insert_run(conn, db.Run(run_id="c", status="ok"))
    runs = db.list_runs(conn, status="ok", limit=10)
  assert sorted(r.run_id for r in runs) == ["a", "c"]


def test_update_run_overrides_serialise(db_path):
  with db.open_db(db_path) as conn:
    db.insert_run(conn, db.Run(run_id="v001"))
    db.update_run(
        conn, "v001",
        overrides={"new": "value"},
        status="running",
    )
    r = db.get_run(conn, "v001")
  assert r is not None
  assert r.overrides == {"new": "value"}
  assert r.status == "running"


def test_add_tag_appends_unique(db_path):
  with db.open_db(db_path) as conn:
    db.insert_run(conn, db.Run(run_id="v001"))
    db.add_tag(conn, "v001", "best-so-far")
    db.add_tag(conn, "v001", "exploratory")
    db.add_tag(conn, "v001", "best-so-far")  # duplicate
    r = db.get_run(conn, "v001")
  assert r is not None
  assert sorted(r.tags) == ["best-so-far", "exploratory"]


def test_annotate_replaces_notes(db_path):
  with db.open_db(db_path) as conn:
    db.insert_run(conn, db.Run(run_id="v001", notes="initial"))
    db.annotate(conn, "v001", "regressed — see profile")
    r = db.get_run(conn, "v001")
  assert r is not None
  assert r.notes == "regressed — see profile"


def test_set_status_marks_finished(db_path):
  with db.open_db(db_path) as conn:
    db.insert_run(conn, db.Run(run_id="v001"))
    db.set_status(conn, "v001", "ok", finished=True)
    r = db.get_run(conn, "v001")
  assert r is not None
  assert r.status == "ok"
  assert r.ts_finished is not None


def test_composite_key_supports_multiple_submitters(db_path):
  """Two users can each have a run named v001 without collision."""
  with db.open_db(db_path) as conn:
    db.insert_run(conn, db.Run(run_id="v001", submitter="vaibhav", notes="mine"))
    db.insert_run(conn, db.Run(run_id="v001", submitter="renee", notes="hers"))
    a = db.get_run(conn, "v001", submitter="vaibhav")
    b = db.get_run(conn, "v001", submitter="renee")
  assert a is not None and a.notes == "mine"
  assert b is not None and b.notes == "hers"
