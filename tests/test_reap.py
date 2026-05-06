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

Tests for `cde reap`. We monkeypatch kubectl by stubbing
k8s.get_jobset_status, since shelling kubectl in CI isn't viable.
"""
from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from cde import db, k8s
from cde.commands import reap as reap_cmd


@pytest.fixture
def env(tmp_path, monkeypatch):
  monkeypatch.setenv("CDE_HOME", str(tmp_path / ".cde"))
  monkeypatch.setenv("CDE_PREFERENCES", str(tmp_path / "prefs.yaml"))
  return tmp_path


def _seed(path: Path, *runs: db.Run) -> None:
  with db.open_db(path) as conn:
    for r in runs:
      db.insert_run(conn, r)


def test_reap_updates_terminal_runs(env, monkeypatch):
  hist_path = Path(env) / ".cde" / "history.sqlite"

  _seed(
      hist_path,
      db.Run(run_id="v001", project="p", k8s_namespace="ns",
             jobset_name="js-v001", status="running"),
      db.Run(run_id="v002", project="p", k8s_namespace="ns",
             jobset_name="js-v002", status="submitted"),
      db.Run(run_id="v003", project="p", k8s_namespace="ns",
             jobset_name="js-v003", status="ok"),  # already terminal
  )

  responses = {
      "js-v001": k8s.JobSetStatus(status="ok", reason="AllJobsCompleted",
                                   message=None, raw_phase=None),
      "js-v002": k8s.JobSetStatus(status="failed", reason="JobFailed",
                                   message="oom", raw_phase=None),
      "js-v003": None,                              # never queried
  }

  def fake_get(namespace, name, *, context=None):
    return responses[name]

  monkeypatch.setattr(reap_cmd.k8s, "get_jobset_status", fake_get)

  args = argparse.Namespace(all=True, limit=200)
  rc = reap_cmd.run(args)
  assert rc == 0

  with db.open_db(hist_path) as conn:
    a = db.get_run(conn, "v001")
    b = db.get_run(conn, "v002")
    c = db.get_run(conn, "v003")
  assert a.status == "ok"
  assert a.ts_finished is not None
  assert b.status == "failed"
  assert b.ts_finished is not None
  assert c.status == "ok"  # untouched


def test_reap_empty_message(env):
  hist_path = Path(env) / ".cde" / "history.sqlite"
  _seed(hist_path, db.Run(run_id="x", project="p", status="ok"))
  args = argparse.Namespace(all=True, limit=200)
  rc = reap_cmd.run(args)
  assert rc == 0


def test_reap_unknown_jobset_marks_evicted(env, monkeypatch):
  hist_path = Path(env) / ".cde" / "history.sqlite"
  _seed(
      hist_path,
      db.Run(run_id="v100", project="p", k8s_namespace="ns",
             jobset_name="js-gone", status="running"),
  )

  def fake_get(namespace, name, *, context=None):
    return k8s.JobSetStatus(
        status="unknown", reason="not-found", message="gone", raw_phase=None,
    )

  monkeypatch.setattr(reap_cmd.k8s, "get_jobset_status", fake_get)

  args = argparse.Namespace(all=True, limit=200)
  rc = reap_cmd.run(args)
  assert rc == 0

  with db.open_db(hist_path) as conn:
    r = db.get_run(conn, "v100")
  assert r.status == "evicted"
  assert r.ts_finished is not None


def test_reap_refreshes_pending_to_running(env, monkeypatch):
  # A row that previously refreshed to "pending" (Kueue suspended) must keep
  # being considered in-flight on the next reap, so that when Kueue admits and
  # the JobSet starts running we update the DB row. Regression for the bug
  # where the reap filter only included (submitted, running).
  hist_path = Path(env) / ".cde" / "history.sqlite"
  _seed(
      hist_path,
      db.Run(run_id="v200", project="p", k8s_namespace="ns",
             jobset_name="js-pending", status="pending"),
  )

  def fake_get(namespace, name, *, context=None):
    return k8s.JobSetStatus(
        status=k8s.STATUS_RUNNING, reason=None, message=None, raw_phase=None,
    )

  monkeypatch.setattr(reap_cmd.k8s, "get_jobset_status", fake_get)

  args = argparse.Namespace(all=True, limit=200)
  rc = reap_cmd.run(args)
  assert rc == 0
  with db.open_db(hist_path) as conn:
    r = db.get_run(conn, "v200")
  assert r.status == k8s.STATUS_RUNNING


def test_reap_skips_runs_without_namespace(env, monkeypatch):
  hist_path = Path(env) / ".cde" / "history.sqlite"
  _seed(
      hist_path,
      db.Run(run_id="x", project="p", k8s_namespace="", status="running"),
  )

  called = []

  def fake_get(namespace, name, *, context=None):
    called.append(name)
    return k8s.JobSetStatus("ok", None, None, None)

  monkeypatch.setattr(reap_cmd.k8s, "get_jobset_status", fake_get)

  args = argparse.Namespace(all=True, limit=200)
  rc = reap_cmd.run(args)
  assert rc == 0
  assert called == []  # didn't try to query
  with db.open_db(hist_path) as conn:
    r = db.get_run(conn, "x")
  assert r.status == "running"  # unchanged
