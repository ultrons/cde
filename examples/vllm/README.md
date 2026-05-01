# vLLM inference (stub)

This directory is being populated by a separate agent for vLLM
inference workloads. **Not yet runnable.**

## Expected shape

The example will demonstrate:

- A multi-container pod (`vllm-server` + `sidecar-bench`) running on a
  TPU slice, so it stresses the multi-container case `cde logs` was
  redesigned for.
- `cde logs <run> -c vllm-server` and `cde logs <run> -c sidecar-bench`
  for legible single-container streaming.
- `cde server up` / `cde server down` / `cde server wait-ready` for
  port-forward + `/health` polling lifecycle (Phase 4 verbs in cde).
- Bool-flag handling via `cde run --flag enforce_eager` and
  `--flag enable_prefix_caching` — vLLM's CLI is heavy on bare flags.

When this lands, the cde.yaml + `manifests/jobset.yaml.j2` here will be
sibling to the maxtext example: a recorded inference iteration log with
`cde history` rows showing the knobs that were swept.

## Tracking

Coordination on the implementation lives outside this repo; check with
the maintainer if you're picking it up.
