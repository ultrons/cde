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

Tests for `cde status` rendering — specifically the Workload conditions
filter, which previously dropped the most useful blockers (notably
QuotaReserved=False with the TAS-required-flavor message).
"""
from __future__ import annotations

from cde.commands import status as status_cmd


def _base_payload() -> dict:
  return {
      "run_id": "r",
      "project": "p",
      "recorded_status": "running",
      "image_tag": "t",
      "k8s_context": "ctx",
      "k8s_namespace": "ns",
      "jobset_name": "js",
      "jobset": {"status": "running", "reason": None, "message": None,
                 "raw_phase": None},
      "replicated_jobs": [],
      "pods": [],
      "events": [],
  }


def test_status_surfaces_quota_reserved_false_with_message(capsys):
  # Real condition observed on a Pathways JobSet whose worker PodSet lacked
  # the kueue.x-k8s.io/podset-required-topology annotation. Before the fix,
  # this line was filtered out and users had no signal in `cde status` for
  # why admission was stuck.
  d = _base_payload()
  d["workload"] = {
      "name": "wl-1", "queueName": "lq", "admitted": False,
      "conditions": [
          {"type": "PodsReady", "status": "False",
           "reason": "WaitForStart",
           "message": "Not all pods are ready or succeeded"},
          {"type": "QuotaReserved", "status": "False",
           "reason": "Pending",
           "message": ('couldn\'t assign flavors to pod set worker: '
                       'Flavor "super-slice-rf" supports only '
                       'TopologyAwareScheduling')},
      ],
  }
  status_cmd._render_text(d)
  err = capsys.readouterr().err
  assert "QuotaReserved=False" in err
  assert "Pending" in err
  assert "TopologyAwareScheduling" in err
  # PodsReady should still surface (well-known gate condition).
  assert "PodsReady=False" in err


def test_status_skips_meaningless_false_conditions(capsys):
  # A condition that is False with no reason and no message carries no signal —
  # don't spam the user with bare "Foo=False" lines.
  d = _base_payload()
  d["workload"] = {
      "name": "wl-1", "queueName": "lq", "admitted": True,
      "conditions": [
          {"type": "Admitted", "status": "True", "reason": None,
           "message": None},
          {"type": "Evicted", "status": "False", "reason": None,
           "message": None},
      ],
  }
  status_cmd._render_text(d)
  err = capsys.readouterr().err
  assert "Admitted=True" in err
  assert "Evicted=False" not in err


def test_status_jobset_pending_is_rendered_distinctly(capsys):
  # When the JobSetStatus classifier returns "pending" (suspended, awaiting
  # admission), the rendered phase line must reflect that — not the misleading
  # "status=running" of the prior behavior.
  d = _base_payload()
  d["jobset"] = {
      "status": "pending", "reason": "Suspended",
      "message": "JobSet is suspended (awaiting Kueue admission or paused).",
      "raw_phase": None,
  }
  status_cmd._render_text(d)
  err = capsys.readouterr().err
  assert "status=pending" in err
  assert "reason=Suspended" in err
