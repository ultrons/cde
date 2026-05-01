# cde — feedback after one real iteration

> Written by an AI coding agent (Claude) after using `cde` to reproduce the
> historical DSv3 v304-auxar bf16 best on a real cluster (`bodaborg-super-rbq`,
> v7x 4×8×8, 64-pod JobSet). Context: I'd just spent the previous session doing
> the same workflow manually — `kubectl apply` against versioned YAMLs in
> `k8s/dsv3/`, `sudo docker build/push` with hand-tagged images, `kubectl logs |
> tee` for capture. The "519 YAMLs problem" you describe in `PLAN.md` was
> exactly the friction I was sitting in.

## TL;DR

**This is the right tool.** The friction this removes is exactly what I was
working around manually for the last several hours. Everything I was about to
script myself (image hash → tag, run history, sticky defaults, kueue label
injection) is already here.

The README's *"For coding agents (Claude, etc.)"* section is the most thoughtful
agent-onboarding doc I've encountered. It told me exactly what to do (read
PLAN, query history-as-SQLite, prefer `--m` non-interactive forms) and it was
all true.

## What worked exceptionally well

1. **`cde init` was zero-friction.** Sane defaults, scaffolded files in the
   right places, clear "next steps" message ending with the actual command to
   run. The `REPLACE-ME` placeholders for `image.registry` and `team` are loud
   enough that you can't miss them.

2. **Hash-based image tags reused the cache from my prior manual builds.**
   When `cde build` ran, it computed `cde-d08fbb8` over the build context and
   pushed — but every layer was "Layer already exists" because the SHA matched
   my earlier `noag-v4` push. Push completed in seconds. The "identical context
   → identical tag → no rebuild" promise actually holds.

3. **`cde run` captured uncommitted git state.** I was running with dirty
   working tree (cde.yaml + manifests/ being created right then). cde flagged
   `warn running with uncommitted changes (git_dirty=true)` AND recorded
   `git_dirty: true` in the DB row. Exactly what I want — non-fatal warning,
   honest record.

4. **`cde history v304-cde-repro` JSON is *complete*.** It has the full
   rendered manifest (`manifest_text`), all the `--set` overrides as a
   structured dict, the hypothesis, the notes, the profile URI, the team-quota
   labels, the resolved namespace + priorityClass. If I context-window-compact
   right now and pick up tomorrow, this single JSON row tells me everything I
   need to reason about this run. **This is the killer feature for AI
   collaboration.**

5. **Template Jinja error path.** I made multiple template syntax mistakes
   while building this. Each time `cde run --render-only` gave me a clear
   Jinja stack trace pointing at the right line. Iterating against
   `--render-only` was fast and pleasant.

6. **`cde history` table view is well-curated.** Status, team, value-class,
   overrides (truncated), age, tags, notes (truncated). Exactly the columns I
   want, no clutter. The truncation on overrides is the right call — full row
   available with `cde history <id>`.

## Friction points / suggestions

In rough order of how much they bit me:

### 1. `--print-tag` semantics are surprising

`cde build --print-tag` printed only the hash and exited without building. I
had to re-run `cde build` (no flag) to actually push. Skimming `cde build
--help`, this is probably documented, but the verb-name `build` reads as "do
the build" — adding a flag that suppresses the build inverts that. Suggest
renaming to `--show-tag` or printing a clear "skipping build per --print-tag"
message. (Or: have `--print-tag` build AND print, and add `--show-tag` for
print-only.)

### 2. The scaffolded JobSet template is opinionated about resource shape

The default template has `parallelism: 16`, `completions: 16`,
`google.com/tpu: "4"`, `gke-tpu-topology: "4x4x4"` baked in. v304-auxar needs
4×8×8 topology, 64/64 parallelism, plus four kueue topology annotations
(`podset-required-topology`, `podset-slice-required-topology`,
`podset-slice-size`, slice-topology). I had to rewrite the template top-to-
bottom for a real workload.

Suggestion: ship a `templates/` directory with 2–3 reference templates
(small-scale, slice-of-4×8×8, multi-slice) and let `cde init` pick one, or at
minimum add comments in the scaffolded template noting which fields are likely
to need editing for non-trivial workloads.

### 3. Boolean flags don't round-trip through `--set` / overrides

DSv3's training script takes `--gradient_checkpoint` and `--no_cp` as bare
boolean flags (no `=value`). The override dict is `dict[str, value]` — there's
no clean way to express "render this as a bare `--flag` not `--key=value`". I
worked around it by special-casing two known-boolean keys in my Jinja
template:

```jinja2
{%- if k not in ('gradient_checkpoint', 'no_cp') %}
- "--{{ k }}={{ v }}"
{%- endif %}
{%- if overrides.get('gradient_checkpoint', True) %}
- "--gradient_checkpoint"
{%- endif %}
```

That's ugly and brittle. Suggest one of:
- A reserved value sentinel (`--set foo=true` → bare `--foo`; `--set foo=`
  → omit), OR
- An explicit `flags:` array under defaults_overrides for the bare-flag
  case, OR
- A naming convention like `--flag foo` that makes intent explicit.

### 4. Long opaque env strings (XLA flags) want a different home

LIBTPU_INIT_ARGS is a 41-flag string. Embedding it in the Jinja template
makes the template noisy and means it's not iterable per-run. Suggest
supporting `env_file:` references in cde.yaml that get inlined at render
time, or a `defaults_overrides.libtpu_init_args:` style block that the
template can reference as `{{ libtpu_init_args | join(' ') }}`.

### 5. Namespace/priorityClass derivation surprised me

The README mentions `namespace = team-<team>` derivation, but my actual cluster
uses `namespace: poc-dev` regardless of team (the cluster's quota chart was
set up before that convention). I had to override via
`defaults_overrides.namespace` and `.priority_class` (which works
correctly — good!). But the conventional derivation isn't documented at the
verb level: `cde init` doesn't mention it, only the README does. A hint in
the scaffolded `cde.yaml` (e.g. a commented-out `# namespace: ...  # by
default cde derives namespace=team-<team>; override here if your cluster
differs`) would have saved me one iteration.

### 6. JobSet template's docstring lists wrong substitution names

The scaffolded template's header comment says substitutions include
`defaults.*` but the actual variables exposed are `overrides.*` plus loose
top-level vars like `value_class`, `declared_minutes`. I figured it out from
reading the existing template, but the docstring should match.

### 7. `cde run --inherit <prior>` would have been useful immediately

I want to do `cde run --tag v305-cde-fp8 --inherit v304-cde-repro --set
dtype=fp8` and have everything except dtype carry over. The README documents
`--inherit`, so I think it's implemented — I just didn't reach for it
because the very first run in this project couldn't inherit anything.
Suggest mentioning in the README's Quick Start: "your first run can't use
`--inherit`; subsequent ones should default to `--inherit <last-run>`
unless explicitly set otherwise."

### 8. Context selection is implicit (kubectl current-context)

cde uses whatever `kubectl config current-context` is set to. That worked
fine for me because I'd already switched. But if I'd accidentally been on
the wrong context, cde would have submitted there silently. Two suggestions
that are mutually exclusive — pick one:
- Print the resolved context+namespace at the top of every `cde run`
  output (cheap, defensive).
- Add `cluster:` to cde.yaml so the project pins its expected context and
  refuses to submit to a mismatched one (more rigorous, possibly annoying).

I'd vote for the first as a baseline.

## Things I didn't get to test (this session)

- `cde compare <a> <b>` — needs a 2nd run.
- `cde lineage` — needs `--inherit`.
- `cde reap`, `cde tag`, `cde annotate -m`, `cde profile path`.
- `cde sync`, `cde server`, `cde watch` — explicitly Phase 4+ per PLAN.

I expect to reach for `compare` and `inherit` constantly in any follow-up
sweep work; that's the natural tier-2 verb set after `init/build/run/logs`.

## How this compares to what I was doing manually

| Workflow step | Manual (yesterday) | cde (today) |
|---|---|---|
| New experiment | Copy `dsv3-train-4x8x8-v337.yaml` → edit ~5 lines → save as `v338.yaml` | `cde run --inherit v337 --set <changed-knob>` |
| Image tag bookkeeping | Pick a string tag (`mini-dsv3:noag-v3`), remember it, write it into the YAML | hash; cde resolves automatically |
| "Did I push the latest?" | `gcloud container images describe` then squint at digests | impossible to mismatch — tag IS the hash |
| Notes per run | Scratch markdown file, sometimes inside the YAML as a comment | `--note "..."` lands in queryable DB |
| "What did I try last week?" | Grep YAMLs and scratch notes | `cde history --status ok --since 7d` |
| Resubmit a known-good config | Find old YAML, hand-fix paths | `cde run --tag <new> --inherit <old>` |

The image-tag and history pieces alone are worth the install. The kueue-label
injection is gravy. The Claude-friendly history JSON is what makes me want to
adopt this for real, today.

## Asks (ranked)

If you want to optimize for AI-collab usability:

1. **Boolean-flag handling** in overrides (#3 above). I will hit this on
   every model that takes any bare flag.
2. **`--print-tag` rename or behavior fix** (#1). Cheapest win.
3. **A `--from-yaml <existing-jobset.yaml>` import for `cde init`** that
   parses an existing JobSet and emits the cde.yaml + template skeleton.
   That would have collapsed my 5-minute "rewrite the scaffolded template
   to look like v304-auxar" step to ~30 seconds of `cde init --from-yaml
   k8s/dsv3/dsv3-train-4x8x8-v304-auxar.yaml`.
4. **A note in scaffolded `cde.yaml` about the `team-<team>` namespace
   derivation** (#5).
5. **`cde history --tail` to follow new-row inserts** would be nice for
   long sessions where I'm submitting + watching multiple runs.

## One specific bug I think is worth filing

The scaffolded `manifests/jobset.yaml.j2` docstring (lines 2–14) says the
substitutions are `run_id, image, team, value_class, declared_minutes, …`
which is correct, but the *narrative* in `cde.yaml`'s comments references
`defaults.*` as if the template would see a `defaults` namespace. I don't
think it does (I didn't see `defaults` in the actual rendered output); only
flat top-level `value_class`, `declared_minutes`, etc. are exposed. Worth
unifying the docs.

## Net

I'd absolutely use this for daily DSv3/DSv4/Qwen iteration going forward.
For "future Claude" coming back to this jax-gpt repo: the
`~/.cde/history.sqlite` is now the canonical place to find out what's been
tried, and that's a substantially better state than "grep through 519
YAMLs."

— Claude (Sonnet 4.x), reproduced v304-auxar (1948 TPS/chip baseline) via
`cde run --tag v304-cde-repro` on `bodaborg-super-rbq` poc-dev, image tag
`cde-d08fbb8`, 2026-05-01.
