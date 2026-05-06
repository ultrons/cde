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

Tests for cde.manifest_inject — render-time mutation of JobSet manifests.
"""
from __future__ import annotations

import textwrap

import yaml

from cde import manifest_inject


_LABEL = "cloud.google.com/gke-tpu-topology"


def _stripped_scaffold_render() -> str:
  """A realistic Pathways-shaped JobSet whose pod templates lack Kueue TAS
  annotations. Mirrors the failure observed in production (`sb-64-64-ep10`)."""
  return textwrap.dedent("""\
      apiVersion: jobset.x-k8s.io/v1alpha2
      kind: JobSet
      metadata:
        name: test-run
        namespace: poc-dev
        labels:
          kueue.x-k8s.io/queue-name: lq
      spec:
        replicatedJobs:
        - name: pathways-head
          replicas: 1
          template:
            spec:
              parallelism: 1
              template:
                metadata:
                  labels:
                    cde.io/run-id: test-run
                spec:
                  restartPolicy: Never
                  containers:
                  - name: jax-tpu
                    image: gcr.io/example/img:tag
                    command: ["/bin/bash", "-c"]
                    args:
                      - |
                        echo "multi
                        line
                        heredoc"
                        python train.py
        - name: worker
          replicas: 1
          template:
            spec:
              parallelism: 32
              template:
                metadata:
                  labels:
                    cde.io/run-id: test-run
                spec:
                  restartPolicy: OnFailure
                  containers:
                  - name: pathways-worker
                    image: gcr.io/example/worker:tag
                    resources:
                      limits:
                        google.com/tpu: "4"
      """)


def test_inject_adds_both_annotations_when_missing():
  manifest = _stripped_scaffold_render()
  out, mutated = manifest_inject.maybe_inject_tas_annotations(
      manifest, topology_label=_LABEL,
  )
  assert mutated == ["pathways-head", "worker"]
  doc = yaml.safe_load(out)
  for rj in doc["spec"]["replicatedJobs"]:
    pt_md = rj["template"]["spec"]["template"]["metadata"]
    anns = pt_md["annotations"]
    assert anns["kueue.x-k8s.io/podset-required-topology"] == _LABEL
    assert anns["kueue.x-k8s.io/podset-slice-required-topology"] == _LABEL


def test_inject_byte_identical_noop_when_all_present():
  # When every replicatedJob already has both keys, do not re-serialize. This
  # preserves block-scalar style (heredocs in args), comments, and key order
  # in the user's rendered manifest. Only the heredoc concern is observable in
  # this test, but it's the most important one — PyYAML round-trip would
  # convert `|`-block scalars to quoted multi-line strings.
  manifest = textwrap.dedent("""\
      apiVersion: jobset.x-k8s.io/v1alpha2
      kind: JobSet
      metadata:
        name: x
      spec:
        replicatedJobs:
        - name: only
          template:
            spec:
              template:
                metadata:
                  annotations:
                    kueue.x-k8s.io/podset-required-topology: cloud.google.com/gke-tpu-topology
                    kueue.x-k8s.io/podset-slice-required-topology: cloud.google.com/gke-tpu-topology
                spec:
                  containers:
                  - name: c
                    args:
                      - |
                        line one
                        line two
      """)
  out, mutated = manifest_inject.maybe_inject_tas_annotations(
      manifest, topology_label=_LABEL,
  )
  assert mutated == []
  assert out == manifest  # byte-for-byte identical


def test_inject_preserves_user_chosen_label_value():
  # Detection is by key presence, not value match. If the user has set the
  # required-topology annotation to a non-default label (e.g. for a non-TPU
  # cluster), leave it alone — only inject the slice variant if missing.
  manifest = textwrap.dedent("""\
      apiVersion: jobset.x-k8s.io/v1alpha2
      kind: JobSet
      spec:
        replicatedJobs:
        - name: r
          template:
            spec:
              template:
                metadata:
                  annotations:
                    kueue.x-k8s.io/podset-required-topology: my-custom-label
                spec:
                  containers: []
      """)
  out, mutated = manifest_inject.maybe_inject_tas_annotations(
      manifest, topology_label=_LABEL,
  )
  assert mutated == ["r"]
  doc = yaml.safe_load(out)
  anns = doc["spec"]["replicatedJobs"][0]["template"]["spec"][
      "template"]["metadata"]["annotations"]
  assert anns["kueue.x-k8s.io/podset-required-topology"] == "my-custom-label"
  assert anns["kueue.x-k8s.io/podset-slice-required-topology"] == _LABEL


def test_inject_preserves_heredoc_args_after_mutation():
  # Discriminating test: even when we DO round-trip, the bash args must parse
  # back to the same string content (kubectl-equivalent), regardless of YAML
  # formatting choices PyYAML makes on dump.
  manifest = _stripped_scaffold_render()
  before = yaml.safe_load(manifest)
  out, _ = manifest_inject.maybe_inject_tas_annotations(
      manifest, topology_label=_LABEL,
  )
  after = yaml.safe_load(out)
  rj_idx = 0  # pathways-head
  before_args = before["spec"]["replicatedJobs"][rj_idx]["template"]["spec"][
      "template"]["spec"]["containers"][0]["args"][0]
  after_args = after["spec"]["replicatedJobs"][rj_idx]["template"]["spec"][
      "template"]["spec"]["containers"][0]["args"][0]
  assert before_args == after_args


def test_inject_skips_non_jobset_documents():
  manifest = textwrap.dedent("""\
      apiVersion: v1
      kind: ConfigMap
      metadata:
        name: cm
      data:
        x: "1"
      """)
  out, mutated = manifest_inject.maybe_inject_tas_annotations(
      manifest, topology_label=_LABEL,
  )
  assert mutated == []
  assert out == manifest


def test_inject_returns_input_on_invalid_yaml():
  manifest = "this is: : not valid: yaml: ["  # malformed
  out, mutated = manifest_inject.maybe_inject_tas_annotations(
      manifest, topology_label=_LABEL,
  )
  assert mutated == []
  assert out == manifest
