# cde — TPU/GPU iteration manager

A small CLI that owns the iteration loop for ML workloads on Kubernetes
(TPU/GPU). Replaces the typical sprawl of one-off YAML files + manual
`docker build` + scratch-markdown notes with explicit verbs and a
queryable run history.

```
cde init                            # scaffold a project (cde.yaml + JobSet template)
cde build                           # docker build + push, image tag = hash of context
cde run --tag v001 --note "first"   # render + apply + record in SQLite history
cde logs v001                       # tail kubectl logs; auto-update status on exit
cde history                         # see what you've run, when, with what flags
cde compare v001 v002               # what changed between two runs
```

## When to use cde

- You iterate on TPU/GPU workloads and currently maintain dozens of
  near-identical YAML files (e.g. `bench_v117.yaml`, `bench_v118.yaml`),
  each differing by a couple of flags.
- You forget what you tried last week.
- You want every job tagged + auto-routed to a Kueue team queue without
  hand-editing labels into every manifest.
- You want a future coding-agent session (Claude Code, Cursor, Codex,
  Aider, Gemini Code Assist, Copilot Workspace, …) to be able to see
  what you've already tried by reading a structured DB rather than
  asking you.

**Not the right fit if:** you only run jobs occasionally, or you need a
production submission tool with cluster lifecycle (use
[xpk](https://github.com/AI-Hypercomputer/xpk) for that), or you need
recipe-sharing across users (cdk fits).

## Status

Active development. **Phase 0–4 shipped**: init, build, run, logs,
shell, reap, watch, sync, server (up/down/wait-ready), history,
annotate, hypothesize, tag, untag, compare, lineage, defaults, profile,
plus shell tab completion. **93 tests passing.**

Not yet shipped: `cde profile pull/open` viewer integration, GCS
write-through for multi-machine history, metric extraction from runs.
See [`PLAN.md`](PLAN.md) for phasing and rationale.

### Tab completion (one-time setup)

```bash
# Bash / zsh:
eval "$(register-python-argcomplete cde)"
# Persist:
echo 'eval "$(register-python-argcomplete cde)"' >> ~/.bashrc
```

After that, `cde annotate v<TAB>` expands run IDs from history,
`cde history --tag <TAB>` lists tags you've used, `cde run --value-class
<TAB>` suggests recent classes, `cde compare <TAB> <TAB>` works on both
positionals.

## Install

```bash
git clone https://github.com/ultrons/cde.git
cd cde
pip install -e .
```

Requires Python 3.10+. Runtime deps: `pyyaml`, `jinja2`. Test deps add
`pytest`. Optional: `kubectl`, `docker` (or `podman` / `nerdctl`),
`k9s`, `gsutil` — cde shells out to whichever you have.

## The 30-second iteration loop

```bash
# 1. Once per project
cd ~/my-experiment
cde init --project my-bench

# 2. Edit cde.yaml — set image.registry, team. Keep the JobSet template
#    or replace it with your own.
$EDITOR cde.yaml manifests/jobset.yaml.j2

# Already have a JobSet you've been hand-editing? Onboard it instead:
#   cde init --from-yaml path/to/your/jobset.yaml --project my-bench
# This parses the existing manifest, infers cde.yaml fields (team,
# value-class, declared-min, namespace, priority class, image, num-slices,
# tpu-type), and emits a Jinja template with cde-owned bits substituted
# ({{ run_id }}, {{ image }}, {{ namespace }}, …) — your custom env vars,
# kueue annotations, and resource limits are preserved verbatim.

# 3. Build the image (hash-tagged from build context)
cde build

# 4. Submit a run. Every flag value lands in history.
cde run --tag v001 --note "baseline" --set ep=32 --set fsdp=16

# 5. Watch it. Status updates when kubectl logs exits.
cde logs v001

# 6. Iterate. Skip the build step if your context didn't change
#    (the hash-tag will be identical and we'll detect it).
cde run --tag v002 --inherit v001 --set ep=64 --note "wider EP"

# 7. See what's different between two runs
cde compare v001 v002
cde lineage v002
```

## Mental model

Three concepts, in order:

### 1. Hash-based image tags

`cde build` computes a 7-char hex hash over the build context (Dockerfile
+ tracked source + `.dockerignore`-respected tree). The image tag becomes
`<registry>/<name>:cde-<sha7>`. Identical context → identical tag → no
rebuild needed. The registry-existence check is best-effort; pass
`--force` to rebuild anyway.

### 2. Run history is in SQLite, queryable

`~/.cde/history.sqlite` (or `$CDE_HOME/history.sqlite`) carries one row
per `cde run`. Schema includes: tag, project, git SHA, image tag, full
manifest, overrides JSON, team/value-class/declared-minutes, status,
notes, hypothesis, tags, parent_run, profile_uri, log_uri. Always
written before kubectl-apply, so even failed submits leave a row.

`cde history --json` dumps the table. `cde history <id>` shows one full
row. **This is the canonical way for a future coding-agent session — or
future-you — to learn what you've been trying.**

### 3. Sticky defaults (per project, allowlisted)

After a successful submit, `~/.cde/recent.yaml` records that project's
last-used `value-class`, `team`, `num-slices`, `declared-minutes`. The
next `cde run` uses them as defaults if the corresponding flag isn't
explicitly passed, and **logs the inheritance** so it's never silent.
`--set` overrides are NOT sticky by design — those are the per-run
experimental knob. To carry `--set` values forward across runs, use
`--inherit <prior_run_id>` explicitly.

For training/inference scripts that take **bare boolean flags** (e.g.
vLLM's `--enforce-eager`, DSv3's `--gradient_checkpoint`), use
`--flag NAME` and `--no-flag NAME` instead of `--set`. Booleans land in
the `overrides` dict as Python `True` / `False`; the scaffolded
`jobset.yaml.j2` renders `True` as a bare flag, `False` as omitted, and
everything else as `--key=value`. Mixing `--flag X` and `--set X=...` on
the same run logs a warning — the `--flag` form wins, since that's the
more explicit shape.

## Verb reference

| Verb | What it does | Most-useful flags |
|---|---|---|
| `cde init` | Scaffold cde.yaml + manifest template + history DB | `--project`, `--force`, `--no-history`, `--from-yaml <path>` |
| `cde build` | Docker build + push, hash-tagged | `--show-tag` (alias `--print-tag`), `--no-push`, `--force` |
| `cde run` | Render template, apply, record run | `--tag` (required), `--note`, `--hypothesis`, `--set k=v`, `--flag NAME` / `--no-flag NAME`, `--inherit <run_id>`, `--profile`, `--wait`, `--render-only`, `--dry-run`, `--value-class`, `--declared-minutes`, `--num-slices` |
| `cde logs` | Tail kubectl logs; refresh status when done | `-a/--all-pods`, `-r N` (pick replica), `-c NAME` (pick container), `--no-follow`, `--since 5m` |
| `cde shell` | k9s scoped to project namespace | `--exec <run>` for kubectl exec |
| `cde reap` | Refresh status for in-flight runs | `--all` (cross-project), `--limit` |
| `cde history` | Table of recent runs in this project | `--json`, `--tag`, `--status`, `--since 7d`, `--all`, `--project`, `--limit`; positional `<run_id>` for one row |
| `cde annotate <id>` | Replace notes (uses $EDITOR if no `-m`) | `-m "..."` or pipe via stdin |
| `cde hypothesize <id>` | Replace hypothesis | `-m "..."` |
| `cde tag <id> <name>` / `cde untag <id> <name>` | Add/remove tags | — |
| `cde compare <a> <b>` | Side-by-side delta | `--json` for machine-readable |
| `cde lineage <id>` | Walk parent_run chain backwards | — |
| `cde defaults` | Show or reset sticky defaults | `--show`, `--reset`, `--reset-all` |
| `cde profile path <id>` | Print profile_uri (for `gsutil ls $(...)`) | — |

## Common workflows

### Training: iterate on backend / sharding / batch size

```bash
# Baseline
cde build
cde run --tag v100 --note "baseline" --set moe_backend=jax --set ep=32

# Try a different backend on the same other knobs
cde run --tag v101 --inherit v100 --set moe_backend=pallas

# Compare
cde compare v100 v101

# Annotate when you understand the result
cde tag v101 best-so-far
cde annotate v101 -m "pallas backend gives 1.2x at ep=32"
```

### Capture + replay a profile

```bash
# Add to cde.yaml first:
#   profile:
#     base-uri: gs://my-bucket/cde-profiles

cde run --tag v140 --profile --note "profile capture run"
# When done:
cde logs v140                           # blocks until terminal
gsutil ls $(cde profile path v140)      # find the profile files
xprof $(cde profile path v140)          # open in xprof (or whatever you use)
```

### Catch up on what's running

```bash
cde reap                                # refresh statuses across in-flight runs
cde history --status running            # see what's still active
cde history --tag best-so-far           # see your wins
```

## For coding agents

This section is for AI coding agents picking up an in-progress project —
Claude Code, Cursor, Codex, Aider, Gemini Code Assist, Copilot Workspace,
or any other tool/agent driving the shell. The interface is the same
regardless of which model is behind it: cde's design goal is that an
agent who has never seen the project before can reconstruct intent from
the SQLite history alone.

If you're that agent:

1. **Read `PLAN.md`** for design rationale that the code can't capture.
2. **Read `PREFERENCES` (if present)** for project-specific conventions.
3. **Run `cde history --json`** to see what's been tried. The notes /
   hypothesis / tags fields carry the human's mental state, which is
   especially useful after long context-window compaction.
4. **Don't ask "what changed between v117 and v118?"** — run
   `cde compare v117 v118 --json` and read the delta yourself.
5. **When you record annotations, use `cde annotate <id> -m "..."`** —
   the `-m` form is non-interactive and works without a TTY.

The SQLite DB at `~/.cde/history.sqlite` is also directly queryable:

```python
import sqlite3
conn = sqlite3.connect("/home/<user>/.cde/history.sqlite")
rows = conn.execute(
    "SELECT run_id, status, overrides, notes "
    "FROM runs WHERE project=? ORDER BY ts_submitted DESC LIMIT 10",
    ("my-bench",),
).fetchall()
```

Schema reference is in [`src/cde/db.py`](src/cde/db.py); migrations in
the same file. Composite key is `(submitter, run_id)`.

## Configuration

Two YAML files. Both are optional in the sense of having defaults; you
need at least `cde.yaml` for the verbs that touch a project.

### `<project>/cde.yaml` — per-project (committed to repo)

```yaml
project: my-bench                     # required, partitions history
image:
  registry: gcr.io/your-project       # required
  name: my-bench                      # auto-defaults to project basename
  dockerfile: ./Dockerfile            # optional, default
  context: .                          # optional, default
template: ./manifests/jobset.yaml.j2  # required
team: ml-perf                         # required, key in team-quota ConfigMap

defaults:
  value-class: development
  declared-duration-minutes: 60
  tpu-type: tpu7x-128
  num-slices: 1

# Auto-wire profile path for cde run --profile
profile:
  base-uri: gs://my-bucket/cde-profiles

# These appear as Jinja2 template variables. Override per-run via --set.
defaults_overrides:
  ep: 32
  fsdp: 16
```

### `~/.cde/preferences.yaml` — per-user, all projects

Build driver (docker/podman/nerdctl), sudo prefix, default registry,
sync behavior, etc. See [`preferences.example.yaml`](preferences.example.yaml)
for the full schema. Missing file = sensible defaults.

### Team-quota integration

cde inserts the four required Kueue labels into every JobSet:

```
kueue.x-k8s.io/queue-name: lq
team: <cfg.team>
value-class: <cfg.defaults.value-class>
declared-duration-minutes: "<cfg.defaults.declared-duration-minutes>"
```

…and derives the namespace + priorityClass per the team-quota chart's
convention (`namespace = team-<team>`, `priorityClass = <namespace>-priority`).
Override either via `cde.yaml.defaults_overrides.namespace` /
`.priority_class`. The cluster's `team-quota` ConfigMap (`team-quota-config`
in `kueue-system` by default) holds the source of truth; future versions
of cde will read it directly.

## Layout

```
cde/
  PLAN.md                      design doc + verb roadmap
  PREFERENCES                  (optional) project conventions
  preferences.example.yaml     ~/.cde/preferences.yaml schema reference
  src/cde/
    cli.py                     argparse dispatch
    config.py                  cde.yaml schema
    db.py                      SQLite + migrations + Run dataclass + CRUD
    paths.py                   ~/.cde/ resolution (env-overridable)
    preferences.py             ~/.cde/preferences.yaml loader
    recent.py                  sticky-defaults storage
    suggest.py                 did-you-mean wrapper
    git_info.py                capture (sha, dirty)
    context_hash.py            deterministic build-context hash
    driver.py                  docker/podman/nerdctl subprocess wrapper
    templating.py              Jinja2 strict-undefined wrapper
    k8s.py                     kubectl wrapper (apply / status / logs / exec)
    logging.py                 stderr-only colored logger
    commands/
      init.py build.py run.py logs.py shell.py reap.py
      history.py annotate.py compare.py lineage.py
      defaults.py profile.py
    templates/
      cde.yaml                 scaffolded by `cde init`
      jobset.yaml.j2           scaffolded by `cde init`
  tests/                       pytest unit + CLI integration tests (80 passing)
```

## License

Apache 2.0.
