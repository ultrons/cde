# MaxText llama2-7b — a one-knob batch-size sweep with cde

This walkthrough shows the `init → build → run → compare → lineage`
loop applied to a small MaxText sweep on a single 4×4×4 TPU v7x slice.
The point isn't the result; it's that everything you need to read this
back later — manifest, image SHA, args, status, notes — lives in
`~/.cde/history.sqlite`, queryable in one command.

> Read this top-to-bottom rather than just copying the snippets — the
> commentary between commands is the part that's hard to reconstruct
> from `cde history` alone.

## Prerequisites

- A GKE cluster with TPU v7x nodes and Kueue topology-aware scheduling.
- Kubectl context already pointing at it (`cde run` will print the
  resolved context at submit, so a wrong context fails loud rather than
  silent).
- Write access to two GCS buckets: one for output (`base_output_directory`)
  and one for the JAX compilation cache (`jax_compilation_cache_dir`).
- Local Docker for the build step.

## Step 1 — point cde at the example

```bash
cd examples/maxtext-llama2-7b/

# Edit cde.yaml — at minimum, replace:
#   image.registry  → your gcr.io/AR path
#   team            → your team-quota team key
#   gs://REPLACE-ME → your buckets
$EDITOR cde.yaml
```

If you already have a hand-tuned MaxText JobSet from earlier work, you
don't need this scaffold at all — see [`cde init --from-yaml`](../../README.md):

```bash
cde init --from-yaml path/to/your/existing-maxtext.yaml --project maxtext-llama2-7b
```

That parses the existing manifest and emits a matching `cde.yaml` +
template, preserving custom env vars, kueue annotations, and resource
limits verbatim.

## Step 2 — build the image (once)

```bash
cde build
# step  hashing build context (./)
# detail image tag: gcr.io/your-proj/maxtext:cde-a1b2c3d
# step  building gcr.io/your-proj/maxtext:cde-a1b2c3d
# ok    pushed gcr.io/your-proj/maxtext:cde-a1b2c3d
```

The tag is the SHA-7 of the Docker context — same context, same tag, no
rebuild on subsequent runs unless your code changes.

## Step 3 — submit the baseline run

```bash
cde run --tag baseline-001 \
  --hypothesis "verify the maxtext path works end-to-end at default knobs" \
  --note "fresh start; per_device_batch_size=4, steps=100"
# step  applying to context=gke_... namespace=team-... priorityClass=...-priority
# ok    submitted baseline-001 as team-.../baseline-001
```

The `--hypothesis` and `--note` lands in the run's history row — that's
what makes the row legible to you (or an agent) later.

## Step 4 — fork the run with one knob changed

```bash
cde run --tag bs8-002 \
  --inherit baseline-001 \
  --set per_device_batch_size=8 \
  --hypothesis "wider per-device BS — does it fit in HBM at this seq len?" \
  --note "expected: ~2x throughput if it fits"
# detail  inheriting 8 override(s) from baseline-001: base_output_directory=...,
#         config=..., dataset_path=..., enable_checkpointing=False,
#         jax_compilation_cache_dir=..., model_name=llama2-7b,
#         per_device_batch_size=4, remat_policy=full, steps=100
# detail  defaulting --value-class=development from your last run
# step    applying to context=...
# ok      submitted bs8-002 as team-.../bs8-002
```

`--inherit` carries every override from the parent run as the base, then
layers this run's `--set` on top. The parent_run gets recorded so
`cde lineage` can walk the chain.

## Step 5 — watch them

```bash
# Default: pod 0, all containers, prefixed. Legible for a single-pod run.
cde logs baseline-001
# step  tailing team-.../baseline-001-slice-0-0 (replica 0/0, 1 total) (no follow)
# ...

# When the run has many pods (e.g. multi-slice), pick a specific replica:
cde logs baseline-001 -r 3

# Or fan out across all pods (the original kubectl-style behavior):
cde logs baseline-001 -a
```

## Step 6 — compare the two runs

```bash
cde compare baseline-001 bs8-002
# Run        baseline-001  bs8-002
# status     ok            ok
# image_tag  cde-a1b2c3d   cde-a1b2c3d   (same — cache hit)
# overrides  per_device_batch_size=4  per_device_batch_size=8
# notes      fresh start...  expected: ~2x throughput if it fits
```

## Step 7 — record the result

After the runs finish, annotate them so you (or a future session) can
read off what worked at a glance.

```bash
cde annotate bs8-002 -m "BS=8 fit; throughput +1.7x vs baseline-001."
cde tag bs8-002 bs-sweep
cde tag baseline-001 bs-sweep
```

The `-m` form is non-interactive and works without a TTY — important for
agent-driven sessions.

## Step 8 — reading the iteration log later

This is the payoff. After a few runs:

```bash
cde history --tag bs-sweep
# RUN_ID        STATUS  TEAM     VALUE-CLASS   OVERRIDES                                  AGE     TAGS      NOTES
# bs8-002       ok      maxtext  development   per_device_batch_size=8                    2h ago  bs-sweep  BS=8 fit; throughput +1.7x...
# baseline-001  ok      maxtext  development   per_device_batch_size=4                    4h ago  bs-sweep  fresh start; per_device...

cde lineage bs8-002
# bs8-002 → baseline-001

cde history bs8-002 --json | jq '.manifest_text' -r
# (full rendered JobSet YAML, exactly what kubectl applied)
```

`cde history --json` is the canonical way for a future coding-agent session
(Claude Code, Cursor, Codex, Aider, Gemini Code Assist, Copilot Workspace)
to pick up where you left off — the whole iteration log in one tool call.

## Notes on this example

- **Single slice (4×4×4 v7x).** Multi-slice training swaps `num-slices`
  (and the `replicas:` line) and adds the four kueue topology annotations
  (`podset-required-topology`, `podset-slice-required-topology`,
  `podset-slice-size`, slice-topology). See `cde init --from-yaml`'s
  output if you have a multi-slice JobSet already; the verbatim
  pass-through preserves those annotations.
- **MaxText calling convention.** MaxText takes `key=value` (no leading
  `--`) and accepts booleans literally as `key=true` / `key=false`.
  The args loop in [`manifests/jobset.yaml.j2`](./manifests/jobset.yaml.j2)
  reflects both: it drops the leading `--`, and renders Python
  `True`/`False` from `--flag` / `--no-flag` as `key=true` / `key=false`
  rather than bare flags. Contrast with the default scaffolded template,
  which uses `--key=value` and treats booleans as bare-flag presence.
- **XLA cache.** `jax_compilation_cache_dir` is set to a stable
  per-config prefix (`maxtext-llama2-7b-4x4x4`) so iterating on a knob
  that doesn't change the compilation key reuses the cache. Per-run
  prefixes (e.g. keyed off `run_id`) cold-compile every time and can
  swing throughput 5–10% on the same image+args.
