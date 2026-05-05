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

Tests for `cde delete <run>`.

The verb shells out to kubectl, so we monkeypatch `cde.k8s.delete_jobset`
to record what would be deleted instead of touching a real cluster.
"""
from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from cde import db
from cde.commands import delete as cmd_delete


@pytest.fixture
def env(tmp_path, monkeypatch) -> Path:
  cde_home = tmp_path / ".cde"
  cde_home.mkdir()
  monkeypatch.setenv("CDE_HOME", str(cde_home))
  monkeypatch.setenv("CDE_PREFERENCES", str(tmp_path / "prefs.yaml"))
  return tmp_path


def _seed(tmp_path: Path, run: db.Run) -> None:
  with db.open_db(tmp_path / ".cde" / "history.sqlite") as conn:
    db.insert_run(conn, run)


def _ids(tmp_path: Path) -> set[str]:
  with db.open_db(tmp_path / ".cde" / "history.sqlite") as conn:
    return {r.run_id for r in db.list_runs(conn, limit=1000)}


def _ns(**overrides) -> argparse.Namespace:
  defaults = dict(run_id="v001", purge=False, force=False)
  defaults.update(overrides)
  return argparse.Namespace(**defaults)


def test_delete_calls_kubectl_with_recorded_context(env, monkeypatch):
  _seed(
      env,
      db.Run(
          run_id="v001", project="p", status="failed",
          k8s_namespace="poc-ml-perf",
          k8s_context="gke_proj_region_cluster",
          jobset_name="v001",
      ),
  )
  calls = []

  def fake_delete(namespace, name, *, context=None, ignore_not_found=True):
    calls.append((namespace, name, context, ignore_not_found))
    return True

  monkeypatch.setattr("cde.commands.delete.k8s.delete_jobset", fake_delete)

  rc = cmd_delete.run(_ns(run_id="v001"))
  assert rc == 0
  assert calls == [
      ("poc-ml-perf", "v001", "gke_proj_region_cluster", True),
  ]
  # Row preserved by default
  assert "v001" in _ids(env)


def test_delete_purge_drops_history_row(env, monkeypatch):
  _seed(
      env,
      db.Run(
          run_id="v001", project="p", status="failed",
          k8s_namespace="poc-ml-perf",
          jobset_name="v001",
      ),
  )
  monkeypatch.setattr(
      "cde.commands.delete.k8s.delete_jobset", lambda *a, **kw: True,
  )

  rc = cmd_delete.run(_ns(run_id="v001", purge=True))
  assert rc == 0
  assert "v001" not in _ids(env)


def test_delete_refuses_active_run_without_force(env, monkeypatch):
  _seed(
      env,
      db.Run(
          run_id="v001", project="p", status="running",
          k8s_namespace="poc-ml-perf",
          jobset_name="v001",
      ),
  )
  called = []
  monkeypatch.setattr(
      "cde.commands.delete.k8s.delete_jobset",
      lambda *a, **kw: called.append(a) or True,
  )

  rc = cmd_delete.run(_ns(run_id="v001"))
  assert rc == 1
  assert called == []
  # Row still present — refusal must not cascade-delete
  assert "v001" in _ids(env)


def test_delete_force_proceeds_on_active_run(env, monkeypatch):
  _seed(
      env,
      db.Run(
          run_id="v001", project="p", status="running",
          k8s_namespace="poc-ml-perf",
          jobset_name="v001",
      ),
  )
  called = []
  monkeypatch.setattr(
      "cde.commands.delete.k8s.delete_jobset",
      lambda *a, **kw: called.append(a) or True,
  )

  rc = cmd_delete.run(_ns(run_id="v001", force=True))
  assert rc == 0
  assert called == [("poc-ml-perf", "v001")]


def test_delete_handles_missing_run(env, monkeypatch):
  monkeypatch.setattr(
      "cde.commands.delete.k8s.delete_jobset",
      lambda *a, **kw: pytest.fail("kubectl should not be called"),
  )
  rc = cmd_delete.run(_ns(run_id="nonexistent"))
  assert rc == 1


def test_delete_succeeds_when_jobset_already_gone(env, monkeypatch):
  _seed(
      env,
      db.Run(
          run_id="v001", project="p", status="failed",
          k8s_namespace="poc-ml-perf",
          jobset_name="v001",
      ),
  )
  monkeypatch.setattr(
      "cde.commands.delete.k8s.delete_jobset", lambda *a, **kw: False,
  )
  rc = cmd_delete.run(_ns(run_id="v001"))
  assert rc == 0


def test_delete_requires_recorded_namespace(env, monkeypatch):
  _seed(
      env,
      db.Run(
          run_id="v001", project="p", status="failed",
          k8s_namespace="",  # legacy / never-applied row
          jobset_name="v001",
      ),
  )
  called = []
  monkeypatch.setattr(
      "cde.commands.delete.k8s.delete_jobset",
      lambda *a, **kw: called.append(a) or True,
  )
  rc = cmd_delete.run(_ns(run_id="v001"))
  assert rc == 1
  assert called == []
