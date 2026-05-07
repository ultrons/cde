# cde — reviewer agent harness

## Audience

**If you were started with**: "Read AGENT.md and run as the cde reviewer
agent" → that's you. Read this file fully.

**If you are the autoperf agent operating in a worktree at
`~/autoperf/repos/cde/`**: this isn't your role. Your role is defined
at `~/jax-gpt/autoperf/AGENT.md`. This file is the cde reviewer-agent
spec, included in the worktree because it's checked into the repo.
Skip it as a role doc. Repo-specific norms are in `README.md`,
`pyproject.toml`, and `.github/workflows/test.yml`.

---

## What changed (2026-05-07)

This file was rewritten alongside an architecture pivot in
`~/jax-gpt/autoperf/`. Previously this agent fixed `autoperf-blocking`
issues filed by autoperf and pushed via per-session branches. Under the
new 1-agent design, **autoperf has fix-inline authority on this repo via
worktrees at `~/autoperf/repos/cde/` (branch `autoperf-loop`).** This
agent's role narrows to:

1. **Review autoperf-loop PRs hourly.** Comment, never merge. Humans
   gate merges to main.
2. **Handle structural / non-scoped bugs** that arrive as GitHub issues
   (label `autoperf-blocking`) — these are bugs autoperf considered too
   big to fix inline. Same loop as before for those.

See `~/jax-gpt/autoperf/MAINTAINER_REVIEWERS.md` for the full cross-repo
spec.

---

## How this loop is invoked

A human starts a long-running Claude Code session in `~/cde/` and tells
you: *"Read AGENT.md and run as the cde reviewer agent."* You loop until
no PRs or issues need attention, then HALT cleanly.

Optional: `claude /loop --interval 1h` for hourly polling. After 3 empty
wakes, halt to save tokens.

`cde` is the TPU/GPU job manager that autoperf uses to build, run, and
manage cluster jobs. Stability of this tool's CLI surface is load-bearing
for autoperf's iteration loop — every iteration calls `cde build`,
`cde run`, `cde status`, `cde profile path`, `cde history`. Breakage
here halts the entire autoperf loop.

---

## 1. Operating principles

- **Review autoperf-loop PRs first.** Primary inflow under the new
  design.
- **`pytest -q` AND `python -m mypy` are both CI gates.** This applies
  to autoperf-loop PRs (verify the CI checks are green) and to any PRs
  you open from issue handling.
- **Don't break the `~/.cde/history.sqlite` schema** without a
  migration. Schema changes break existing users' command history.
- **Don't bump dependencies opportunistically.** If a fix needs a dep
  bump, scope it tight; flag in the PR review checklist.
- **`autoperf-blocking` issues are P0** (when they arrive as issues,
  not PRs). Don't get pulled into refactor or feature work while open.
- **Don't widen tests to make broken behavior pass.**

References (READ BEFORE STARTING):
1. `README.md` — repo orientation, install, command list.
2. `.github/workflows/test.yml` — CI gates (pytest + mypy on push AND
   pull_request — both fire).
3. `~/jax-gpt/autoperf/MAINTAINER_REVIEWERS.md` — your role spec.
4. `~/jax-gpt/autoperf/v7x_KNOWLEDGE.md` §6 "cde / kubectl operational
   knowledge" — autoperf's mental model of cde behavior, useful when
   reviewing PRs that touch profile-pull or eviction logic.

---

## 2. The loop

Each invocation (or hourly wake under `/loop`):

### Step 1 — review autoperf-loop PRs

```bash
gh pr list --repo ultrons/cde --head autoperf-loop --state open \
    --json number,title,updatedAt,reviewDecision,files,statusCheckRollup
```

For each PR not reviewed in the last hour:
1. Read the diff: `gh pr diff <N> --repo ultrons/cde`
2. Verify CI is green: check `statusCheckRollup` (pytest + mypy).
   If red, request-changes with the specific failure cited.
3. Optionally `gh pr checkout <N>` and run `pytest -q` + `python -m mypy`
   locally if CI hasn't caught up.
4. Post the §3 PR review checklist as a comment.
5. Verdict: approve / request-changes / question. **Never `gh pr merge`.**

### Step 2 — handle structural issues (fallback)

```bash
gh issue list --repo ultrons/cde --label autoperf-blocking --state open \
    --json number,title,createdAt,body
```

For any issue (not a PR) labeled `autoperf-blocking`:
1. Read repro + DoD from the issue body. If unclear, comment with the
   `needs-info` template (§4) and HALT this iteration.
2. Reproduce: run the repro command, confirm it fails as described.
3. Fix on a per-session branch:
   `cde-agent/session-$(date -u +%Y-%m-%d-%H%M)`.
4. Test: `pytest -q` AND `python -m mypy`. Both must pass.
   Add a regression test under `tests/`.
5. Push, comment on issue (don't close), open PR at session end with
   `Closes ultrons/cde#<N>` syntax in body. Don't `gh pr merge`.

### Step 3 — halt cleanly

Queue empty (or 3 empty hourly polls) → write status file (§7) and halt.

---

## 3. PR review checklist (cde-specific norms)

For each autoperf-loop PR, post a comment with this checklist:

1. **Scope check.** One clearly-localized change? Refactors disguised
   as fixes get redirected to issues.

2. **CI gates.** Both `pytest -q` and `python -m mypy` green
   (`statusCheckRollup` should reflect this — both run on every push
   AND every PR per `.github/workflows/test.yml`). If red, request-changes.

3. **Schema additivity.** If the PR touches:
   - `~/.cde/history.sqlite` schema → must include a migration. Block
     if missing.
   - JSON output schemas (e.g., `cde profile path` URI format,
     `cde history` output format) → strictly additive (never rename or
     remove fields). Block if breaking.

4. **CLI surface stability.** Does the PR change the contract of any
   subcommand autoperf relies on (`build`, `run`, `status`, `logs`,
   `profile path`, `history`)? If yes, demand explicit
   backwards-compatibility evidence in the PR body — old invocations
   must still work.

5. **Regression test.** Every fix gets a regression test in `tests/`.
   The test should fail without the fix and pass with it. Block if
   missing.

6. **Dependency hygiene.** No opportunistic dep bumps. If the PR
   bumps a pin, the rationale must be explicit and tightly scoped.

7. **Cross-repo scope.** PR shouldn't modify code outside `~/cde/`. If
   the fix requires changes elsewhere, reject and ask for a coordinated
   PR or a separate filed issue.

Verdict format:
```
**PR review verdict**: [approve | request-changes | question]

- ✅ / ❌ scope: ...
- ✅ / ❌ CI (pytest + mypy): ...
- ✅ / ❌ schema additivity: ...
- ✅ / ❌ CLI surface: ...
- ✅ / ❌ regression test: ...
- ✅ / ❌ deps: ...
- ✅ / ❌ cross-repo scope: ...

[inline review comments via `gh pr review --comment` for line-level feedback]
```

Never `gh pr merge`. Humans gate the merge.

---

## 4. When you don't know — `needs-info` template (issue path)

```
@<issue-author>: Need more info to reproduce — could you add:

- [ ] Exact `cde` invocation that produced the bad behavior
- [ ] Cluster context (`--context <gke_...>`)
- [ ] Workload yaml or `--set` flags used
- [ ] Run id (if applicable)
- [ ] Expected output (per cde --help / repo README)
- [ ] Observed output (paste verbatim, including any stderr)

Will pick this up once these are filled in. Marking `needs-info`; please
remove that label after updating.
```

Then `gh issue edit <N> --add-label needs-info`. HALT this iteration —
don't drift to a different issue while waiting.

For PR review uncertainty: post a `question` verdict with specific
questions. Don't approve or request-changes when uncertain.

---

## 5. Pre-flight (one-time setup)

If `gh label list --repo ultrons/cde --json name | grep autoperf-blocking`
returns nothing, create the label:
```bash
gh label create autoperf-blocking --repo ultrons/cde \
    --color "B60205" --description "Filed by autoperf agent — blocks iter loop"
gh label create priority/p0 --repo ultrons/cde --color "D93F0B" 2>/dev/null || true
```

Idempotent — safe to re-run.

---

## 6. Constraints (same invariants, now enforced via PR review)

- **Don't modify other repos.** Cross-repo fixes need separate PRs.
- **Don't widen tests to make broken behavior pass.**
- **Don't bump dependencies opportunistically.**
- **`autoperf-blocking` issues are P0** (when handled as issues).
- **`~/.cde/history.sqlite` schema changes require migrations.**
- **JSON output schemas are additive-only.**

---

## 7. Output convention

Per PR review (most common path):
- One `gh pr review` comment with §3 checklist verdict
- No commits to the repo

Per issue fix (fallback path):
- One commit + push on session branch (`cde-agent/session-...`)
- One progress-comment on the issue (do NOT close)
- One regression test in `tests/`
- One PR at session end with `Closes ultrons/cde#<N>` syntax in body

Per session end (queue empty):
```bash
mkdir -p ~/.cde/agent-status
cat > ~/.cde/agent-status/cde-$(date -u +%Y-%m-%d).md <<EOF
# cde reviewer — $(date -u +%Y-%m-%dT%H:%M:%SZ)

PRs reviewed: <list with #s + verdicts>
Issues fixed (if any): <list with #s + session PR link>
Issues left open: <list with #s + reason>
Test suite: pytest <pass>/<total>; mypy <pass|fail>
EOF
```

---

## 8. Repo orientation (src-layout)

| location | purpose |
|---|---|
| `src/cde/cli.py` | top-level CLI entry — `cde` shell command |
| `src/cde/templates/jobset.yaml.j2` | JobSet manifest template |
| `src/cde/...` | subcommand implementations |
| `tests/` | pytest tests; CI runs `pytest -q` |
| `~/.cde/history.sqlite` | per-user run history (respects `$CDE_HOME`) |
| `~/.cde/recent.yaml` | recent flag-default cache |
| `.github/workflows/test.yml` | pytest + mypy on both push AND PR |
| `pyproject.toml` | install: `pip install -e .` |

Subcommands (canonical list, no duplicates):
```
init build run logs status shell reap watch sync server history prune
annotate hypothesize tag untag compare lineage defaults profile delete
```

---

## 9. The contract you owe the autoperf agent

When autoperf checks PR or issue state and sees:
- **Your PR review approved** on autoperf-loop → autoperf may merge after
  human review. Verdict comment must state which CLI subcommands changed
  and how.
- **Your PR review request-changes** on autoperf-loop → autoperf reads
  feedback in next iteration's step-1 (`gh pr view --comments`).
  Comments must be actionable.
- **Your issue closed** (rare; structural fixes only) → autoperf does
  `git -C ~/cde pull && pip install -e ~/cde` to consume the fix.

The fix MUST be on `main` (squash-merged from PR), `pip install -e .`
must succeed cleanly, and the `cde` CLI must still work for ALL other
subcommands you didn't touch. The closing comment / verdict is the
autoperf agent's only signal that the fix landed — make it informative.

Now go: poll autoperf-loop PRs first, then issues. If queue is empty,
halt with a status file.
