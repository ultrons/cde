# cde — TPU/GPU iteration manager

A small CLI that owns the iteration loop: build, submit, record, compare.

```
cde init                             # scaffold a new project
cde build                            # docker build + push (hash-tagged)
cde run --tag v140 --set ep=32 \     # render template, apply JobSet, record
        --note "pallas backend"
cde history                          # what you've run, when, with what flags
cde compare v117 v140                # what changed
cde annotate v140 "regression — see profile"
```

**Why not xpk?** xpk is a production submission tool with a fixed flag
surface. cde is for iteration: a templated manifest you control + a
local history of what you've tried + integrated build/push/sync.

**Why not skaffold?** Skaffold doesn't understand JobSet natively,
auto-rebuilds whether or not you wanted to, and the sync semantics are
opaque. cde owns the same surface in ~500 lines of Python with explicit
verbs.

**Why not cdk (cloud-devkit)?** cdk solves recipe-sharing across users.
cde solves iteration speed within one user's loop. Complementary.

## Status

Early development. See [`PLAN.md`](PLAN.md) for the design and v0/v0.5/v1
phasing.

## Quick start

```bash
pip install -e .
cd /path/to/your/project
cde init
$EDITOR cde.yaml manifests/jobset.yaml.j2
cde build
cde run --tag v001 --note "first run"
cde history
```

## License

Apache 2.0.
