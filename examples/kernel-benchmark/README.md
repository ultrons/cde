# Pallas kernel benchmark (stub)

This directory is reserved for a from-scratch walkthrough of running a
Pallas / Mosaic kernel benchmark on cluster. **Not yet runnable.**

## Why this example matters

The maxtext and vllm examples are training- and inference-shaped — both
end in a checkpoint or a served model. Kernel benchmarks are different:

- **Iteration cadence is different.** Each run is short (a few minutes),
  often one or two parameter sweeps deep, with much faster turnaround
  than a training run.
- **No `--inherit` chains.** Each kernel variant is its own root rather
  than a fork of a previous run. `cde compare` becomes the dominant
  read-back verb instead of `cde lineage`.
- **Profiling-heavy.** `cde run --profile` lands per-run XPlanes under
  `<base-uri>/<run_id>/`, which is exactly the artifact you want for
  before/after comparisons of a kernel rewrite.
- **Pallas AOT-compile gates apply locally.** See
  `~/.claude/CLAUDE.md` (or your agent's equivalent rule file) — the
  three-gate pattern (AOT compile → EP=1 execution → EP=N execution)
  runs *before* anything goes through cde, and cde then records the
  EP=N cluster runs.

If cde's abstractions hold cleanly for this shape — different cadence,
different read-back patterns, different gating — that's a real signal
they're general-purpose, not just ML-training-specific.

## Tracking

Coming after maxtext and vllm have landed real recorded iteration logs.
