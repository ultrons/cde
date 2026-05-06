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

Post-render manifest mutators that fix admission-gate footguns.

Today this module only injects Kueue topology-aware-scheduling
annotations on JobSet pod templates that lack them. The motivating
failure: clusters whose ResourceFlavor has spec.topologyName set
silently reject every JobSet that doesn't declare a podset topology
request, leaving the Workload sitting forever with QuotaReserved=False,
reason=Pending, message="couldn't assign flavors to pod set ...:
Flavor "..." supports only TopologyAwareScheduling".

The TAS annotation is a cluster-side admission gate, not a user
preference. Default-inject is safe because Kueue ignores the annotation
when the assigned flavor has no topologyName.
"""
from __future__ import annotations

import yaml


REQUIRED_TOPOLOGY_KEY = "kueue.x-k8s.io/podset-required-topology"
SLICE_REQUIRED_TOPOLOGY_KEY = "kueue.x-k8s.io/podset-slice-required-topology"


def maybe_inject_tas_annotations(
    manifest: str, *, topology_label: str,
) -> tuple[str, list[str]]:
  """Inject TAS pod-template annotations on JobSet replicatedJobs that lack them.

  Returns (possibly-modified manifest, list of replicatedJob names that were
  mutated). When no mutation is needed, returns the input string byte-for-byte
  unchanged — important so unaffected templates don't lose comments or
  block-scalar style during a YAML round-trip.

  We detect by *key presence*, not value match: if a user has set
  kueue.x-k8s.io/podset-required-topology to a non-default label, leave it
  alone. We only inject when the key is missing entirely.
  """
  try:
    docs = list(yaml.safe_load_all(manifest))
  except yaml.YAMLError:
    # Don't fail run() over our injection — better to apply the user's
    # template as-is and let kubectl's own validator surface any issue.
    return manifest, []

  modified: list[str] = []
  any_mutation = False
  for doc in docs:
    if not isinstance(doc, dict):
      continue
    if doc.get("kind") != "JobSet":
      continue
    spec = doc.get("spec") or {}
    for rj in spec.get("replicatedJobs", []) or []:
      if not isinstance(rj, dict):
        continue
      pt = ((rj.get("template") or {}).get("spec") or {}).get("template")
      if not isinstance(pt, dict):
        continue
      md = pt.get("metadata")
      if not isinstance(md, dict):
        md = {}
        pt["metadata"] = md
      anns = md.get("annotations")
      if not isinstance(anns, dict):
        anns = {}

      need_required = REQUIRED_TOPOLOGY_KEY not in anns
      need_slice = SLICE_REQUIRED_TOPOLOGY_KEY not in anns
      if not (need_required or need_slice):
        continue

      if need_required:
        anns[REQUIRED_TOPOLOGY_KEY] = topology_label
      if need_slice:
        anns[SLICE_REQUIRED_TOPOLOGY_KEY] = topology_label
      md["annotations"] = anns
      any_mutation = True
      modified.append(str(rj.get("name") or "<unnamed>"))

  if not any_mutation:
    return manifest, []

  out = yaml.safe_dump_all(docs, default_flow_style=False, sort_keys=False)
  return out, modified
