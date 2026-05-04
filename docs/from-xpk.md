# For existing xpk users

cde and xpk are complementary at different layers, not competing. xpk is the
platform tool: cluster lifecycle, broad TPU/GPU surface, multi-user, run by a
team. cde is the personal iteration loop on top of a cluster that already
exists: narrower scope, agent-readable run history, `--inherit` chains, single
user.

The intended relationship: **use xpk for cluster ops, cde for the inner loop**
on a workload you're iterating on.

## When to reach for cde vs xpk

| Task | Tool |
|---|---|
| Create / delete / adapt a cluster | xpk |
| Manage nodepools, autoprovisioning, IAM | xpk |
| Add storage / RDMA / TCPX networking decorators to a workload | xpk (cde doesn't have these yet) |
| One-shot submission of a known-good workload | either; xpk's `workload create` is a fine choice |
| Iterate on a workload (tweak knobs across many runs, fork via `--inherit`, compare) | cde |
| Track what you tried for handoff to a future session or a coding agent | cde |
| Diagnose an admission failure | xpk has `xpk workload status` (PR #1180); cde has `cde status` — both work |

## Flag / concept mapping

When porting an existing `xpk workload create` invocation to cde:

| xpk | cde equivalent |
|---|---|
| `xpk workload create --workload=<name>` | `cde run --tag <name>` |
| `--cluster=<short-name>` | `cde run --context <full-kubectl-context>` (or rely on current-context — cde snapshots it at submit and records it on the run row) |
| `--team=<X>` | `team: X` in `cde.yaml` |
| `--value-class=<X>` | `defaults.value-class: X` in cde.yaml; `cde run --value-class=X` to override per-run |
| `--declared-duration-minutes=<N>` | `defaults.declared-duration-minutes: N`; `cde run --declared-minutes=N` to override |
| `--num-slices=<N>` | `defaults.num-slices: N`; `cde run --num-slices=N` to override |
| `--priority=<class>` | `defaults_overrides.priority_class: <class>` (or rely on cde's `<namespace>-priority` derivation) |
| `--script-dir=<dir>` + `--base-docker-image=<base>` | `image.base_image: <base>` in cde.yaml (or `cde build --base-image=<base>`). Same `crane mutate --append` shape; ~1-2s for source-only changes; no Docker daemon required. See "I don't have a Dockerfile" below. |
| `--use-pathways` | not a flag; the choice lives in your Jinja template (Pathways shape vs single-replicatedJob) — `cde init --from-yaml` preserves either |
| `xpk workload list` | `cde history` |
| `xpk workload status --workload=<name>` | `cde status <name>` |
| `xpk workload delete <name>` | `kubectl delete jobset <name>` (cde does not yet have a delete verb; `cde run --wait` auto-cleans on JobSet completion) |
| `xpk inspector --workload=<name>` | (cde delegates to plain `kubectl describe` / `kubectl logs`; no inspector-equivalent) |

## Migrating an existing xpk workload to cde

Cde was built with this CUJ in mind. The bridge is `cde init --from-yaml`:

```bash
# 1. Capture the rendered JobSet that xpk submitted last time.
kubectl get jobset <your-workload> -n <ns> -o yaml > baseline.yaml

# 2. Strip cluster-injected fields (status:, metadata.uid, resourceVersion, …)
#    so you're left with spec + the metadata you wrote.
$EDITOR baseline.yaml

# 3. Onboard. cde preserves: all custom env vars, kueue annotations,
#    resource limits, multi-replicatedJob structure (e.g. Pathways
#    head + worker), nodeSelectors, tolerations.
cde init --from-yaml baseline.yaml --project <project-name>

# 4. Edit cde.yaml — set image.registry to your AR/GCR path. Most other
#    fields (team, namespace, priority_class, num-slices, tpu-type) are
#    inferred automatically.
$EDITOR cde.yaml

# 5. Build + iterate.
cde build
cde run --tag v001 --note "first cde-managed submission"
cde run --tag v002 --inherit v001 --set per_device_batch_size=8 \
        --note "wider per-device batch — does it fit HBM?"
cde compare v001 v002
cde lineage v002
```

What you keep across the migration: the exact pod spec, env vars,
`nodeSelector`, kueue topology annotations, container images, resource
limits, multi-replicatedJob structure if you had one (Pathways).

What you gain: `--inherit` chains, `cde history` with full manifest fidelity,
`--set` / `--flag` Jinja overrides per run, hash-tagged images that auto-skip
rebuilds when context is unchanged, `cde lineage / compare / status / prune`.

## What you'll miss (today)

If your workflow depends on these, stay on xpk for those parts and use cde
alongside it (not as a replacement):

- **GPU workloads** — cde is TPU-shaped today.
- **Storage decorators** — gcsfuse, filestore, parallelstore, lustre,
  persistent disk, MTC. cde has no first-class equivalents; use xpk to deploy
  the storage, then reference the resulting volumes manually in your cde
  template.
- **Networking decorators** — RDMA, TCPX, TCPXO. Same story as storage.
- **Workload Identity / IAM binding helpers.** cde assumes the cluster's IAM
  is already wired correctly.
- **Recipe ecosystem.** xpk has ~50 golden-tested recipes covering cluster
  variants and workload patterns. cde has a small `examples/` directory with
  one substantive maxtext recipe and stubs for vLLM + kernel-benchmark.
- **Production telemetry / feedback loops.** xpk emits clearcut payloads; cde
  doesn't.
- **Multi-user / team-shared run history.** cde's SQLite is per-machine.
  GCS write-through is in PLAN.md but not shipped.

## What cde does that xpk doesn't

These are the differentiators that justify reaching for cde *in addition to*
xpk:

- **Run history with manifest fidelity.** Every `cde run` writes a row
  containing the rendered JobSet text, the full overrides dict, git SHA, image
  tag, k8s context, status, notes, hypothesis. Queryable via SQLite or
  `cde history --json`.
- **`--inherit <run>` for explicit lineage tracking.** Forks the parent's
  overrides as the base for the new run; layers your `--set` on top; records
  `parent_run` for `cde lineage`.
- **`cde compare a b` side-by-side delta + `cde lineage <run>`.**
- **Atomic kubectl context handling.** cde snapshots `kubectl config
  current-context` once at submit, prints it, passes `--context=<that>` to
  every kubectl call in that command, and records it on the run row. Follow-up
  verbs (`logs`, `reap`, `shell`, `status`) route via the recorded context, so
  drift in your shell's current-context after submit doesn't matter.
- **`cde prune`** with safety-by-default — keep tagged, annotated, recent
  rows; only delete obvious noise.
- **`cde init --from-yaml`** — the bridge that made you read this doc.
- **Agent-readable handoff.** The SQLite history is the canonical artifact a
  future coding-agent session reads to understand what was tried. cde was
  designed for that CUJ from day one.

## "I don't have a Dockerfile"

Many xpk users never write a project Dockerfile because xpk's
`--base-docker-image + --script-dir` does the build for you (it tars the
local source and crane-appends it onto a published base image). cde supports
the same pattern via `image.base_image` in cde.yaml or the `--base-image`
flag on `cde build`.

```yaml
# cde.yaml — no Dockerfile needed
image:
  registry: gcr.io/your-proj
  name: my-app
  context: ./src
  base_image: gcr.io/your-proj/jax-tpu-base:v1   # heavy deps already baked in
  workdir: /app                                  # where source lands in the image
```

Then:

```bash
cde build           # tars ./src, crane-appends onto base, pushes cde-<sha7>
cde run --tag v001  # uses the same cde-<sha7> tag
```

What `cde build` does on this path:
1. Resolves the base image to a digest via `crane digest <base>` (so the
   resulting cde-tag pins to a specific base, immune to `:latest` drift).
2. Tars your context (respecting `.dockerignore`), placing files at
   `<workdir>/` inside the tarball so they extract to `<workdir>` in the image.
3. Hashes the tarball + base digest → `cde-<sha7>`. Same source + same base
   = same tag = registry cache hit, no re-push.
4. `crane mutate <base@digest> --append <tar> --workdir <workdir> --tag
   <repo>:cde-<sha7>` to push.

Typical timing: ~1-2s for source-only changes (vs ~5-30s for `docker build`
even with cache hits). No Docker daemon required.

**Reproducibility:** the cde-tag includes both source and base digest, so
running `cde build` again with the same source AND the same base produces
the same tag. If the base moves under a moving tag (`:latest`, `:nightly`),
the new build gets a different cde-tag — old runs still resolve to their
recorded image, new runs pick up the new base.

**Requirement:** the `crane` binary must be on PATH. Install:
https://github.com/google/go-containerregistry/tree/main/cmd/crane

If you'd rather stay on `docker build`: leave `image.base_image` unset (or
remove your Dockerfile via `cde build --base-image=<ref>` in the opposite
direction — pass `--base-image=''` to force the docker path even when cde.yaml
has it set).

## Roadmap items relevant to xpk migrants

These are filed but not yet shipped — flagging here so you know they're
expected, not missing-by-design:

- `cde delete <run>`: 1:1 with `xpk workload delete`. Today, use
  `kubectl delete jobset <run>` directly.
- Multi-user history via GCS write-through (`history.gcs_uri` in cde.yaml is
  scaffolded; the write-through code is not).
