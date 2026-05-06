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

Tests for the pure-functional bits of k8s.py.
"""
from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from cde import k8s


def test_classify_completed():
  obj = {"status": {
      "conditions": [
          {"type": "Completed", "status": "True", "reason": "AllJobsCompleted"},
      ],
  }}
  s = k8s.classify_jobset(obj)
  assert s.status == k8s.STATUS_OK
  assert s.reason == "AllJobsCompleted"


def test_classify_failed():
  obj = {"status": {
      "conditions": [
          {"type": "Failed", "status": "True", "reason": "JobFailed", "message": "pod oom"},
      ],
  }}
  s = k8s.classify_jobset(obj)
  assert s.status == k8s.STATUS_FAILED
  assert s.reason == "JobFailed"
  assert s.message == "pod oom"


def test_classify_only_false_conditions_is_running():
  obj = {"status": {
      "conditions": [
          {"type": "Completed", "status": "False"},
          {"type": "Failed", "status": "False"},
      ],
  }}
  s = k8s.classify_jobset(obj)
  assert s.status == k8s.STATUS_RUNNING


def test_classify_no_conditions_is_running():
  s = k8s.classify_jobset({"status": {}})
  assert s.status == k8s.STATUS_RUNNING


def test_classify_succeeded_alias():
  obj = {"status": {"conditions": [
      {"type": "Succeeded", "status": "True"},
  ]}}
  s = k8s.classify_jobset(obj)
  assert s.status == k8s.STATUS_OK


def test_classify_picks_first_terminal():
  # Both Completed and Failed True (shouldn't happen, but defensive)
  obj = {"status": {"conditions": [
      {"type": "Completed", "status": "True"},
      {"type": "Failed", "status": "True"},
  ]}}
  s = k8s.classify_jobset(obj)
  # Either is fine; assert it's one of them, not "running"
  assert s.status in (k8s.STATUS_OK, k8s.STATUS_FAILED)


def test_classify_passes_raw_phase():
  obj = {"status": {"phase": "Running", "conditions": []}}
  s = k8s.classify_jobset(obj)
  assert s.raw_phase == "Running"


def test_classify_suspended_via_spec_is_pending():
  # Authoritative signal: spec.suspend == True (Kueue toggles this until admit).
  # Real failure that motivated this test: a JobSet on a TAS-required Kueue flavor
  # that never gets admitted shows zero pods + spec.suspend=true; previously this
  # was misclassified as "running" because no terminal condition was set.
  obj = {
      "spec": {"suspend": True},
      "status": {"conditions": []},
  }
  s = k8s.classify_jobset(obj)
  assert s.status == k8s.STATUS_PENDING
  assert s.reason == "Suspended"


def test_classify_suspended_via_condition_is_pending():
  # Fallback: older JobSet controllers may not toggle spec.suspend on the
  # observed object but emit a Suspended condition.
  obj = {
      "spec": {"suspend": False},
      "status": {"conditions": [
          {"type": "Suspended", "status": "True", "reason": "QueuePaused",
           "message": "queue is paused"},
      ]},
  }
  s = k8s.classify_jobset(obj)
  assert s.status == k8s.STATUS_PENDING
  assert s.reason == "QueuePaused"
  assert s.message == "queue is paused"


def test_classify_unsuspended_no_terminal_is_running():
  # Once Kueue admits, spec.suspend flips to false and there's no terminal
  # condition yet — must classify as running, not lingering pending.
  obj = {
      "spec": {"suspend": False},
      "status": {"conditions": []},
  }
  s = k8s.classify_jobset(obj)
  assert s.status == k8s.STATUS_RUNNING


def test_classify_terminal_takes_priority_over_suspend():
  # Defensive: if both Completed=True and spec.suspend=True somehow appear,
  # terminal wins. A successful JobSet shouldn't get masked as pending.
  obj = {
      "spec": {"suspend": True},
      "status": {"conditions": [
          {"type": "Completed", "status": "True", "reason": "AllJobsCompleted"},
      ]},
  }
  s = k8s.classify_jobset(obj)
  assert s.status == k8s.STATUS_OK


# ---------- get_replicated_jobs_status ----------


class _FakeProc:
  def __init__(self, returncode: int, stdout: str = "", stderr: str = ""):
    self.returncode = returncode
    self.stdout = stdout
    self.stderr = stderr


def test_replicated_jobs_status_pathways_shape():
  """Gap 3: Pathways JobSet has multiple replicatedJobsStatus entries
  (pathways-head + worker). The helper must surface all of them."""
  obj = {
      "status": {
          "replicatedJobsStatus": [
              {"name": "pathways-head", "active": 1, "ready": 1,
               "succeeded": 0, "failed": 0},
              {"name": "worker", "active": 4, "ready": 4,
               "succeeded": 0, "failed": 0},
          ],
      },
  }
  with patch(
      "cde.k8s.subprocess.run",
      return_value=_FakeProc(0, json.dumps(obj)),
  ):
    out = k8s.get_replicated_jobs_status("ns", "pathways-bench-001")
  assert len(out) == 2
  assert out[0]["name"] == "pathways-head"
  assert out[0]["active"] == 1
  assert out[1]["name"] == "worker"
  assert out[1]["active"] == 4
  assert out[1]["ready"] == 4


def test_replicated_jobs_status_returns_empty_on_kubectl_error():
  with patch(
      "cde.k8s.subprocess.run",
      return_value=_FakeProc(1, "", "not found"),
  ):
    assert k8s.get_replicated_jobs_status("ns", "bogus") == []


def test_replicated_jobs_status_returns_empty_when_no_status_yet():
  """Newly-submitted JobSet has no replicatedJobsStatus until the
  controller fills it in. Helper must handle that gracefully."""
  obj = {"status": {}}  # no replicatedJobsStatus
  with patch(
      "cde.k8s.subprocess.run",
      return_value=_FakeProc(0, json.dumps(obj)),
  ):
    assert k8s.get_replicated_jobs_status("ns", "fresh") == []


# ---------- delete_jobset ----------


def test_delete_jobset_returns_true_when_kubectl_deleted():
  with patch(
      "cde.k8s.subprocess.run",
      return_value=_FakeProc(
          0, "jobset.jobset.x-k8s.io/foo deleted\n",
      ),
  ) as mock_run:
    out = k8s.delete_jobset("poc-dev", "foo", context="ctx")
  assert out is True
  args = mock_run.call_args[0][0]
  assert "--context=ctx" in args
  assert "delete" in args and "jobset" in args
  assert "--ignore-not-found" in args


def test_delete_jobset_returns_false_when_already_gone():
  # With --ignore-not-found, kubectl exits 0 with empty stdout.
  with patch(
      "cde.k8s.subprocess.run",
      return_value=_FakeProc(0, "", ""),
  ):
    assert k8s.delete_jobset("ns", "gone") is False


def test_delete_jobset_raises_on_other_failures():
  with patch(
      "cde.k8s.subprocess.run",
      return_value=_FakeProc(1, "", "Error: forbidden"),
  ):
    with pytest.raises(k8s.KubectlError, match="forbidden"):
      k8s.delete_jobset("ns", "x")
