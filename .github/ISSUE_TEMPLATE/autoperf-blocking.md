---
name: autoperf-blocking
about: Issue filed by the autoperf agent — blocks a perf-optimization iteration
title: "[autoperf] "
labels: ["autoperf-blocking"]
assignees: []
---

**Tool**: cde
**Filed by**: autoperf agent (jax-gpt commit <SHA>)
**Workload**: <name from autoperf/workloads/*.yaml>
**Iteration**: <N>

## Context

- Cluster context: `<gke_... context>`
- Run id (if applicable): `<tag>`
- cde subcommand: `<run | status | logs | profile path | history | build | ...>`
- Workload params (`--set`): `<flat list>`

## What I tried

```bash
<exact cde command, copy-pasteable>
```

## Expected (per `cde --help` / repo README)

<what the docs/--help say should happen>

## Got

```
<actual stdout/stderr/output, paste verbatim>
```

## Repro (minimum)

```bash
<smallest sequence of commands that reproduces from a clean state>
```

## Definition of done

<one concrete observable that confirms the fix>

For example: "After fix, `cde profile path <run_id>` returns the gs:// URI
within 30s of the run completing (currently times out / returns empty)."

## Workaround

<what the autoperf agent did instead, if any — including "halted iteration N">
