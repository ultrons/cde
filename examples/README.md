# cde examples

Each subdirectory here is a **recorded iteration log**, not a ready-to-run
demo. The cde.yaml + Jinja template + README capture the shape of a real
workload submitted via cde, so a future user (or coding agent) can read
the table, see the deltas, and understand the iteration without rerunning.

You will need to swap a few cluster-specific things before running any of
these against your own setup:

| Field | Why |
|---|---|
| `image.registry` in `cde.yaml` | Points at `gcr.io/REPLACE-ME` — change to your project's GCR / Artifact Registry path. |
| `team` in `cde.yaml` | Team key from your cluster's `team-quota` ConfigMap. |
| `defaults_overrides.namespace` / `priority_class` (if present) | Only needed if your cluster doesn't follow the `team-<team>` convention. |
| `defaults.tpu-type` and the `nodeSelector` in the template | Match your cluster's TPU SKU + topology. |
| `gs://REPLACE-ME/...` URIs | Output / dataset / cache buckets you have write access to. |

## Contents

| Example | Status | Demonstrates |
|---|---|---|
| [`maxtext-llama2-7b/`](./maxtext-llama2-7b/) | substantive | training-style sweep with `--inherit`, `cde compare`, `cde lineage` |
| [`vllm/`](./vllm/) | stub (in progress, owned by another agent) | inference: multi-container pod, `cde logs -c vllm-server` / `-c sidecar-bench` |
| [`kernel-benchmark/`](./kernel-benchmark/) | stub (later) | non-training workload: profile-heavy, no `--inherit` chain |

## CI gating

`tests/test_examples.py` runs `cde run --render-only` against every example
to catch cde-side drift (e.g. a config schema change that breaks an old
cde.yaml). It does **not** test that upstream tools (MaxText, vLLM) still
work — version pinning of those is on the human.
