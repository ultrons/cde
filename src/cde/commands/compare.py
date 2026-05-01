"""`cde compare A B` — what differs between two runs?

Compares: overrides, image_tag, git_sha, team, value_class, declared_min,
num-slices is folded into overrides if present, plus notes / hypothesis
side-by-side.

Default output is a side-by-side terminal table; --json emits a structured
delta for downstream tooling (and Claude).
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from cde import config, db, logging as log, paths


def register(subparsers: argparse._SubParsersAction) -> None:
  p = subparsers.add_parser(
      "compare",
      help="Show what differs between two runs (overrides, image, notes).",
  )
  p.add_argument("a", help="run id A")
  p.add_argument("b", help="run id B")
  p.add_argument("--json", action="store_true", help="emit JSON delta")
  p.set_defaults(func=run)


def _resolve_db_path() -> Path:
  cfg_path = paths.project_config_path()
  if cfg_path.is_file():
    try:
      cfg = config.load(cfg_path)
      raw = cfg.history.path
      if raw:
        return Path(raw).expanduser()
    except config.ConfigError:
      pass
  return paths.history_db_path()


def _scalar_fields(r: db.Run) -> dict:
  return {
      "image_tag": r.image_tag,
      "git_sha": r.git_sha,
      "git_dirty": r.git_dirty,
      "team": r.team,
      "value_class": r.value_class,
      "declared_min": r.declared_min,
      "status": r.status,
      "ts_submitted": r.ts_submitted,
      "tags": r.tags,
  }


def _diff(a: db.Run, b: db.Run) -> dict:
  """Return a structured delta. Same scalars are omitted."""
  out: dict = {"a": a.run_id, "b": b.run_id}

  # Scalars
  scalars: dict = {}
  for k, va in _scalar_fields(a).items():
    vb = _scalar_fields(b)[k]
    if va != vb:
      scalars[k] = {"a": va, "b": vb}
  if scalars:
    out["scalars"] = scalars

  # Overrides (the most useful diff)
  ka, kb = set(a.overrides), set(b.overrides)
  ovr_diff: dict = {}
  for k in sorted(ka | kb):
    va = a.overrides.get(k, "(unset)")
    vb = b.overrides.get(k, "(unset)")
    if va != vb:
      ovr_diff[k] = {"a": va, "b": vb}
  if ovr_diff:
    out["overrides"] = ovr_diff

  # Notes / hypothesis side-by-side (don't try to diff prose)
  if a.notes != b.notes:
    out["notes"] = {"a": a.notes, "b": b.notes}
  if a.hypothesis != b.hypothesis:
    out["hypothesis"] = {"a": a.hypothesis, "b": b.hypothesis}
  return out


def _print_table(d: dict, a: db.Run, b: db.Run) -> None:
  print(f"  {a.run_id:>20}  {b.run_id:>20}")
  print(f"  {'-' * 20}  {'-' * 20}")

  def row(label, va, vb):
    print(f"{label:>30}  {str(va):>20}  {str(vb):>20}")

  for k, v in d.get("scalars", {}).items():
    row(k, v["a"], v["b"])
  if "overrides" in d:
    print(f"\n{'overrides':>30}")
    for k, v in d["overrides"].items():
      row(f"  {k}", v["a"], v["b"])
  if "notes" in d:
    print("\nnotes:")
    print(f"  [{a.run_id}]:")
    for line in (d["notes"]["a"] or "(empty)").splitlines() or ["(empty)"]:
      print(f"    {line}")
    print(f"  [{b.run_id}]:")
    for line in (d["notes"]["b"] or "(empty)").splitlines() or ["(empty)"]:
      print(f"    {line}")
  if "hypothesis" in d:
    print("\nhypothesis:")
    print(f"  [{a.run_id}]: {d['hypothesis']['a'] or '(empty)'}")
    print(f"  [{b.run_id}]: {d['hypothesis']['b'] or '(empty)'}")
  if list(d.keys()) == ["a", "b"]:
    print("(runs are identical on all tracked fields)")


def run(args: argparse.Namespace) -> int:
  with db.open_db(_resolve_db_path()) as conn:
    a = db.get_run(conn, args.a)
    b = db.get_run(conn, args.b)
  if a is None:
    log.err("no such run: %r", args.a)
    return 1
  if b is None:
    log.err("no such run: %r", args.b)
    return 1

  d = _diff(a, b)
  if args.json:
    print(json.dumps(d, indent=2, sort_keys=True))
  else:
    _print_table(d, a, b)
  return 0
