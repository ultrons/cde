# cde — design and implementation plan

This document is the canonical reference for `cde`'s design. Read it cold
to understand what this tool is, why it exists, and what's in v0 vs
later.

## What cde is

A **TPU/GPU iteration manager**. It owns:

1. **The build pipeline** — explicit `cde build`; opt-in `cde watch`.
   Hash-based image tagging; identical contexts never rebuild.
2. **Run submission** — render a Jinja2-templated JobSet manifest, inject
   the four team-quota labels (team, value-class, declared-duration,
   queue-name) + priorityClass, `kubectl apply`, wait, stream logs.
3. **Run history** — local SQLite at `~/.cde/history.sqlite`; every run
   recorded with overrides, image hash, git SHA, profile URI, log URI,
   notes, tags. Queryable.
4. **File sync into running pods** — `cde sync on` enables a
   kubectl-cp-on-change watcher for fast no-rebuild iteration.
5. **Server lifecycle** — for inference, separate verbs to bring up a
   serving JobSet, wait for `/health`, run a bench/eval against it
   without restarting the server.
6. **Profile management** — auto-wire `JAX_PROFILER_DIR` to a per-run
   GCS path; `cde profile open <run>` fetches and opens.

What cde does **not** do:

- Cluster lifecycle (use gcloud / xpk).
- Image building beyond a docker subprocess (no BuildKit gymnastics).
- Profile viewing (open in browser/xprof; we just fetch).
- Submission UX for production batch (use xpk for that).
- Recipe sharing across users (cdk does that; we do iteration).
- Cluster-wide observability / dashboards.
- Quota policy (team-quota chart does that; we just inject labels).

## Why cde exists

Two specific pain points the existing tools don't solve:

1. **519-YAML-files problem.** The user's jax-gpt repo today has 519 YAML
   files (431 in `k8s/dsv3/` alone), 348 distinct image tags. Adjacent
   versions differ by 3–5 lines; the rest is copy-paste tax. The actual
   iteration is "change one flag, restart" but the workflow forces a
   full image-rebuild + new-YAML-file pattern.
2. **Run-to-run cognitive load.** Today, "what did I change between
   v117 and v118?" requires `diff` of two 100-line YAMLs. Notes are
   scattered in scratch markdown files that get lost. With structured
   run history + notes + tags, this becomes one command.

The coding-agent collab angle: when run history is in SQLite, any agent
(Claude Code, Cursor, Codex, Aider, Gemini Code Assist, Copilot
Workspace, …) can read the whole iteration log in a single tool call
and reason about what's been tried. That's a different tier of
assistance than "ask the user to find the YAML files."

## Iteration tiers (the unlock)

Today every iteration pays the same image-rebuild tax. cde exposes
three explicit tiers:

| Change type | Tier | Verb | Time |
|---|---|---|---|
| XLA flag, env var, CLI arg, slice shape, num slices | **restart-only** | `cde run --mode=restart` | ~30s |
| Python source change (with hot-reload-capable process) | **sync** | `cde run --mode=sync` | ~5s |
| Python source change (no hot reload) | **sync-then-restart** | `cde run --mode=sync-restart` | ~15s |
| Library / dependency change | **build** | `cde run --mode=build` | ~3-10min |

Default `cde run` auto-detects:
- If `git diff` since last run touches only the manifest template or
  `cde.yaml` overrides → `restart`.
- If `git diff` touches sync-watched paths only → `sync` (assuming
  sync-on).
- Otherwise → `build`.

Auto-detection is a hint; user can always override with `--mode`.

## Repository layout

```
cde/
  README.md                  # what cde is, quick start
  PLAN.md                    # this file
  LICENSE                    # Apache-2.0
  pyproject.toml             # PEP 621 package config; entry point cde=cde.cli:main
  .gitignore
  src/cde/
    __init__.py              # __version__
    __main__.py              # python -m cde
    cli.py                   # argparse setup, dispatch
    config.py                # cde.yaml schema (dataclasses + PyYAML)
    db.py                    # SQLite schema, migrations, CRUD
    paths.py                 # XDG-style paths (~/.cde/, ./cde.yaml, etc.)
    logging.py               # tiny coloured logger
    docker.py                # docker subprocess (later)
    k8s.py                   # kubectl wrapper, JobSet ops (later)
    templating.py            # jinja2 substitution (later)
    profiling.py             # GCS profile path conventions (later)
    commands/
      __init__.py
      init.py                # cde init
      build.py               # cde build (later)
      run.py                 # cde run (later)
      history.py             # cde history (later)
      annotate.py            # cde annotate / tag (later)
      compare.py             # cde compare (later)
      sync.py                # cde sync on/off (later)
      watch.py               # cde watch (later)
      server.py              # cde server up/down/reload (later)
      profile.py             # cde profile pull/open (later)
  templates/                 # baked-in templates copied by cde init
    cde.yaml                 # commented sample config
    jobset.yaml.j2           # JobSet template
    server.yaml.j2           # (later) server JobSet template
  tests/
    test_config.py
    test_db.py
    test_init.py
  docs/
    iteration-tiers.md       # the tier model in detail
    server-loop.md           # inference workflow guide
```

## Configuration layers

Two YAML files, both loaded by cde at startup, with project-level
overriding user-level:

| File | Scope | What lives here |
|---|---|---|
| `~/.cde/preferences.yaml` | Per-user, all cde projects | Build driver (docker/podman), `sudo` prefix, color, editor, default GCS bucket for profiles, etc. See [`preferences.example.yaml`](./preferences.example.yaml) for the full schema. |
| `<project>/cde.yaml` | Per-project | image, team, template, sync paths, project-specific defaults. The required field every project must set. |

The `~/.cde/preferences.yaml` is the migration target for things that
were previously human-prose instructions in agent rule files
(`~/.claude/CLAUDE.md`, `.cursorrules`, `AGENTS.md`, `GEMINI.md`,
`.aider.conf.yml`, etc. — every coding agent has its own format, e.g.
"always use local docker build"). With cde owning the build pipeline,
those preferences become machine-readable config that cde enforces,
not free-form instructions an agent has to remember to follow.

## Data model

### cde.yaml (per-project config)

```yaml
# cde.yaml — committed to the project's repo
# All fields have sensible defaults; only image and team are required.

image:
  registry: gcr.io/your-project
  name: jaxgpt-tpu                       # final tag is auto: <name>:cde-<sha7>
  dockerfile: ./Dockerfile               # relative to repo root
  context: .                             # build context

template: ./manifests/jobset.yaml.j2     # rendered into a JobSet

# Team-quota integration (cluster-side)
team: ml-perf
defaults:
  value-class: development
  declared-duration-minutes: 60
  tpu-type: tpu7x-128
  num-slices: 1

# Skaffold-replacement: paths cde sync mirrors
sync:
  - src: jax_gpt/                        # local
    dest: /workspace/jax_gpt/            # in-pod

# Auto-wired profile location
profile:
  base-uri: gs://your-bucket/cde-profiles
  # actual path: <base-uri>/<run_id>/

# History config
history:
  # Default path: $CDE_HOME/history.sqlite (CDE_HOME defaults to ~/.cde).
  # Override only when you genuinely need a non-default location.
  path: ""
  # gcs_uri: gs://my-bucket/cde/runs.jsonl  # opt-in, multi-machine

# Defaults the user can override per-run via --set
defaults_overrides:
  ep: 32
  fsdp: 16
  batch_size: 1024
```

### SQLite schema

```sql
CREATE TABLE schema_migrations (
  version INTEGER PRIMARY KEY,
  applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE runs (
  run_id        TEXT NOT NULL,                -- user-supplied tag, e.g. 'v140'
  submitter     TEXT NOT NULL,                -- user@domain; () for solo
  ts_submitted  TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  ts_started    TIMESTAMP,                    -- pod first Running
  ts_finished   TIMESTAMP,                    -- terminal status reached
  status        TEXT NOT NULL DEFAULT 'submitted',
                                             -- submitted|running|ok|failed|evicted|killed

  -- what was built
  git_sha       TEXT,
  git_dirty     INTEGER DEFAULT 0,            -- BOOL
  image_tag     TEXT,                         -- 'jaxgpt:cde-abc1234'

  -- what was applied
  manifest_text TEXT,                         -- rendered YAML, full
  overrides     TEXT,                         -- JSON of --set values
  template_path TEXT,                         -- which template was used
  team          TEXT,
  value_class   TEXT,
  declared_min  INTEGER,
  k8s_namespace TEXT,
  jobset_name   TEXT,                         -- the actual JobSet name

  -- artifacts
  log_uri       TEXT,
  profile_uri   TEXT,
  output_uri    TEXT,                         -- checkpoints / outputs

  -- annotations (the part humans and coding agents care about most)
  notes         TEXT DEFAULT '',
  tags          TEXT DEFAULT '[]',            -- JSON array
  hypothesis    TEXT DEFAULT '',
  parent_run    TEXT,                         -- (submitter, run_id) of forked-from run
  parent_submitter TEXT,

  PRIMARY KEY (submitter, run_id)
);

CREATE INDEX idx_runs_ts ON runs(ts_submitted DESC);
CREATE INDEX idx_runs_status ON runs(status);
CREATE INDEX idx_runs_team ON runs(team);
```

Migrations: a tiny `apply_migrations(db)` reads `schema_migrations`,
applies any in `src/cde/migrations/0NN_*.sql` not yet present.

## Shipped verb list

```
cde init [--from-yaml <path>]  # scaffold cde.yaml + manifest template; --from-yaml
                                # accepts existing JobSet/Pathways YAML and preserves
                                # multi-replicatedJob structure
cde build [--base-image <ref>] # docker build + push, tag = cde-<context-hash>;
                                # --base-image switches to crane-append fast-path
                                # (~1-2s, no Docker daemon)
cde watch                       # opt-in: rebuild-on-save; print "ready to run"
cde run [--mode=…] [--set k=v] [--tag X] [--note "..."] [--profile]
        [--inherit <run>] [--context <ctx>]
                                # render + apply + record history; --inherit forks
                                # overrides from a prior run; --context snapshots
                                # the kubectl context onto the run row
cde history                     # last 20 runs, table view
cde history <run_id>            # full row, JSON
cde history --json              # all rows, JSON (for humans + coding agents)
cde status <tag>                # live cluster view of a run (rolls up all
                                # replicatedJobsStatus entries for Pathways)
cde annotate <run> "..."        # update notes
cde tag <run> <tag>             # add tag
cde compare <a> <b>             # diff overrides + notes + (optional) manifest
cde logs <run> [-r <name>]      # tail the JobSet's pods; -r selects a named
                                # replicatedJob for Pathways
cde shell                       # k9s shortcut filtered to current namespace
cde sync on/off                 # toggle sync mode for the current run
cde prune                       # delete failed/evicted runs from local
                                # history (keep-tagged, keep-annotated,
                                # keep-recent 7d by default)
cde delete <run> [--purge]      # kubectl delete the on-cluster JobSet
                                # routed via the run's recorded context;
                                # --purge also drops the history row
cde quota                       # show team-quota status (read team-quota ConfigMap)
cde server up/down/reload/wait-ready
                                # inference lifecycle
cde profile pull/open <run>     # auto-wired GCS paths
cde lineage <run>               # walk parent_run chain
```

## Not yet shipped

```
cde sync history                # GCS write-through (multi-machine run history)
```

## v1 backlog

```
cde xla <run>               # drop into xla_shell with the run's env
cde sweep <grid.yaml>       # parametric runs
cde share <run>             # one-line URL/snippet
cde fork <run> [--tag …]    # base a new run on another's overrides
cde eval <suite>            # eval clients separate from bench
```

## Implementation order (code-first)

Phases 0–6 are shipped. The repo runs `pytest -q` (135 tests) and
`python -m mypy` green on Python 3.10/3.11/3.12 in GitHub Actions CI.

1. **Phase 0 — bootstrap:** PLAN.md, project skeleton, `config.py`,
   `db.py`, `paths.py`, `cli.py`, `commands/init.py`.
2. **Phase 1 — build + run:** `docker.py`, `templating.py`, `crane.py`,
   `commands/build.py` (with `--base-image` crane-append fast-path),
   `commands/run.py` (with `--inherit` + atomic `--context`).
3. **Phase 2 — history surface:** `commands/history.py`,
   `commands/annotate.py`, `commands/compare.py`, `commands/lineage.py`,
   `commands/prune.py`, `commands/status.py`.
4. **Phase 3 — k8s ops:** `k8s.py` (JobSet wait-ready, log streaming,
   replicatedJob status rollup), `commands/logs.py` (with `-r <name>`
   for Pathways), `commands/shell.py`.
5. **Phase 4 — sync loop:** `commands/sync.py` (kubectl-cp watcher) +
   `commands/watch.py`.
6. **Phase 5 — server lifecycle:** `commands/server.py` (up/down/reload/
   wait-ready for inference).
7. **Phase 6 — profile hand-off:** `commands/profile.py` (GCS pull,
   viewer hand-off).
8. **Phase 7 (not yet shipped):** GCS write-through + multi-machine
   history sync.

## Scope discipline

The user explicitly chose to own build + sync + history rather than
delegating to skaffold. The reason this stays sustainable:

- **Each `cde` subcommand is a thin wrapper.** docker subprocess for
  build, kubectl subprocess for apply/cp, sqlite3 stdlib for storage,
  jinja2 for templating. No reinvention.
- **Hard size budget: 2000 lines of Python at v1.0.** If a feature
  proposal would push past that, it goes in the v2 bucket and probably
  gets cut.
- **Anything not in the verb list above is "use the underlying tool
  directly."** No `cde docker push`, no `cde kubectl get`, no
  `cde gcs ls`. Those are subprocess-callable in shell scripts.

## Testing strategy

- **Unit tests** for everything pure-functional: config parsing, schema
  validation, template rendering, history CRUD, override merging.
- **Integration tests** that exercise CLI verbs against a tmp directory
  + an in-memory SQLite. No real cluster needed for v0.
- **Real-cluster smoke tests** (manual, not CI): one bench run on a
  development TPU cluster, one inference run on a small cluster.
  Documented in `docs/smoke-testing.md`.

## Decisions worth re-checking

| Decision | Rationale | Reconsider when |
|---|---|---|
| Python over Go | Matches xpk, team-quota, ecosystem. `pip install` distribution. | Single-binary distribution becomes a hard requirement. |
| dataclasses + PyYAML over pydantic | Zero third-party deps for v0; pydantic is ~5MB+ wheel. | Schema validation errors get unwieldy. |
| SQLite over Postgres/Cloud SQL | Personal-productivity tool; no daemon; one-file backup. | Team-wide concurrent writes >20 users. |
| argparse over click/typer | Stdlib; minimal deps. | Verb tree gets >30 commands or we want help-text auto-generation. |
| Hash-based image tags | Eliminates the "what tag did I push?" cognitive load + redundant rebuilds. | Multiple parallel writers to the same image registry. |
| `~/.cde/history.sqlite` (XDG-ish) | One DB across all projects on a machine. Lets `cde history --all` show cross-project. | Per-project isolation becomes desired. Move to `<project>/.cde/history.sqlite`. |

## Future-agent orientation

If a future coding-agent session (Claude Code, Cursor, Codex, Aider,
Gemini Code Assist, Copilot Workspace, …) works on this repo:

- **Read this file first.** It captures the design rationale that the
  code can't.
- **Run history is queryable via `cde history --json`** (or directly
  against `~/.cde/history.sqlite`). Use it. Don't ask the user "what
  changed between v117 and v118" — the database knows.
- **Stick to the v0/v0.5/v1 phasing.** Adding a verb out of order is
  rarely worth the scope creep.
- **The 2000-line budget is real.** If a feature requires more, it's
  probably wrong-shaped.
