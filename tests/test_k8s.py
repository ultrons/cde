"""Tests for the pure-functional bits of k8s.py."""

from __future__ import annotations

import json
from unittest.mock import patch

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
