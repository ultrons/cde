# Preferences — durable choices for cde

Read this before making a decision that's already been settled. If a
question feels like "haven't we answered this?" — yes, probably here.

For cross-project preferences that apply to *every* repo Vaibhav owns
(always-local docker build, no `gcloud builds submit`, etc.), see the
global `~/.claude/CLAUDE.md`. This file is for cde-specific things only.

## Architecture

- **Single-user-then-team trajectory.** v0 is solo (Vaibhav + Claude).
  v0.5 may add multi-machine sync. v1+ may add team sharing. Don't
  build for hypothetical team users today, but don't paint into a
  corner either — `(submitter, run_id)` composite key is the
  forward-compat seam.
- **Iteration is the product.** Build / run / record / compare. If a
  feature doesn't directly improve the inner loop, it goes in the v1
  backlog or gets cut.
- **2000-line budget at v1.0.** Real ceiling. If a feature would push
  past it, the feature is wrong-shaped or out of scope.

## Build pipeline

- **Always local docker build + push.** No `gcloud builds submit`. No
  buildkit gymnastics for v0.
- **Hash-based image tagging.** `<name>:cde-<sha7>` where the hash is
  over the build context (Dockerfile + tracked source). Identical
  context = no rebuild.
- **Explicit verbs over auto.** `cde build` is manual. `cde watch` is
  opt-in. We never auto-rebuild because the user saved a file unless
  watch is on.

## Schema and history

- **Never edit existing entries in `_MIGRATIONS`.** Append only. A
  bug in migration 001 gets fixed by migration 002.
- **Composite key `(submitter, run_id)`.** Solo users get
  `submitter=""`; team-shared usage gets a real submitter. Schema
  doesn't change between modes.
- **Timestamps are ISO-8601 UTC TEXT.** No `PARSE_DECLTYPES`. We
  serialize/deserialize in the application layer.
- **Every `cde run` writes a history row, even on failure.** A
  crashed run is still data. The row + status='failed' is more
  useful than a missing row.

## Adding a verb

1. Drop a module at `src/cde/commands/<verb>.py`.
2. Module exposes `register(subparsers)` (wires its argparse
   subparser) and an entry function the parser dispatches to.
3. Add the verb name to `_COMMANDS` in `cli.py`. That's the only
   touch outside the new module.
4. Update PLAN.md's verb list (move from "v0.5" to "v0" if relevant).
5. Add tests at `tests/test_<verb>.py`.

## Code style

- **Stdlib first.** New runtime deps require justification in the PR.
  Today's runtime: PyYAML only.
- **Subprocess directly.** `subprocess.run(["docker", "build", ...])`
  is fine — don't reach for a docker-py / kubernetes-asyncio / gitpython
  wrapper. We're shelling out to mature CLIs; there's nothing to abstract.
- **2-space indent for Python.** Matches xpk and the rest of the user's
  codebases.
- **`from __future__ import annotations`** at the top of every module.
- **Logging to stderr.** stdout is reserved for actual data
  (`cde history --json | jq`).

## Run tracking and Claude collaboration

- **`cde history --json` is the canonical machine-readable interface.**
  Future Claude sessions should query it before asking the user.
- **Notes are first-class.** `cde run --note "..."` and `cde annotate`
  are not convenience features — they're how the user records
  hypotheses for future-them and for Claude.
- **Tags are free-form strings.** No enum. The user decides what's
  meaningful (`best-so-far`, `regression`, `exploratory`).

## Tests

- **Unit + integration only.** No real-cluster CI. Manual smoke tests
  for cluster-side verbs are documented in PLAN.md, not automated.
- **Tmp dirs and `CDE_HOME`/`CDE_CONFIG` env overrides.** Tests never
  touch the real `~/.cde/`.
- **Run all tests pre-commit.** `pytest -q` from the repo root. Green
  before push, no exceptions.

## Communication

- **Terse responses.** No "Here's a comprehensive overview…" prefaces.
  Code is the explanation. Comments cover the *why*, not the *what*.
- **State the result, not the journey.** "Done in <sha>" beats
  "I wrote a function that does X and then I tested it and then…".

## Decisions consciously deferred

- pydantic for config validation: deferred (dataclasses + handwritten
  errors are 80 lines and avoid a dep).
- click/typer for CLI: deferred (argparse is stdlib).
- alembic / proper migration tool: deferred (3-line `_apply_migrations`
  loop covers our case).
- async / aiosqlite: not needed; the workload is single-threaded.
