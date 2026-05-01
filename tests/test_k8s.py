"""Tests for the pure-functional bits of k8s.py."""

from __future__ import annotations

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
