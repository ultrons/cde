"""`cde annotate` / `cde tag` / `cde untag` / `cde hypothesize`.

annotate / hypothesize have a three-way input fallthrough:

  -m "msg"      → use the literal string
  stdin pipe    → read message from stdin
  TTY+$EDITOR   → open $EDITOR with a pre-filled comment template

Tags are a JSON array on the run row; tag/untag append/remove uniquely.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from cde import config, db, logging as log, paths, suggest
from cde import preferences as prefs_mod


def _err_no_such_run(conn, run_id: str) -> None:
  ids = [r.run_id for r in db.list_runs(conn, limit=200)]
  log.err("no such run: %r.%s", run_id, suggest.hint(run_id, ids))


# ---------------------------------------------------------------------------
# Subparser registration
# ---------------------------------------------------------------------------


def register(subparsers: argparse._SubParsersAction) -> None:
  pa = subparsers.add_parser(
      "annotate",
      help="Replace the notes on a run (use $EDITOR if no -m and on a TTY).",
  )
  pa.add_argument("run_id")
  pa.add_argument("-m", "--message", default=None)
  pa.set_defaults(func=lambda args: _run_set_field(args, "notes"))

  ph = subparsers.add_parser(
      "hypothesize",
      help="Replace the hypothesis on a run.",
  )
  ph.add_argument("run_id")
  ph.add_argument("-m", "--message", default=None)
  ph.set_defaults(func=lambda args: _run_set_field(args, "hypothesis"))

  pt = subparsers.add_parser("tag", help="Add a tag to a run.")
  pt.add_argument("run_id")
  pt.add_argument("tag")
  pt.set_defaults(func=_run_tag)

  pu = subparsers.add_parser("untag", help="Remove a tag from a run.")
  pu.add_argument("run_id")
  pu.add_argument("tag")
  pu.set_defaults(func=_run_untag)


# ---------------------------------------------------------------------------
# Implementation
# ---------------------------------------------------------------------------


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


def _editor_message(run: db.Run, *, field: str) -> str | None:
  """Open $EDITOR with a pre-filled template; return the user's message,
  or None if they bailed (empty after stripping comments)."""
  prefs = prefs_mod.load()
  editor = prefs.cli.editor or os.environ.get("EDITOR") or os.environ.get("VISUAL")
  if not editor:
    log.err(
        "no -m provided and $EDITOR / preferences.cli.editor not set"
    )
    return None

  current = getattr(run, field) or ""
  template = (
      f"# {field.capitalize()} for run {run.run_id}\n"
      f"# Lines starting with # are stripped on save.\n"
      f"#\n"
      f"# Status:    {run.status}\n"
      f"# Submitted: {run.ts_submitted}\n"
      f"# Image:     {run.image_tag or '(unknown)'}\n"
      f"# Overrides: {', '.join(f'{k}={v}' for k,v in sorted(run.overrides.items())) or '(none)'}\n"
      f"#\n"
  )
  if current:
    template += "# Current contents below; edit or replace:\n"
    template += current
    if not current.endswith("\n"):
      template += "\n"

  with tempfile.NamedTemporaryFile(
      "w+", suffix=".cde-msg", delete=False, encoding="utf-8"
  ) as f:
    f.write(template)
    path = f.name

  try:
    rc = subprocess.run([editor, path], check=False).returncode
    if rc != 0:
      log.err("$EDITOR exited %d; no change made", rc)
      return None
    text = Path(path).read_text(encoding="utf-8")
  finally:
    os.unlink(path)

  # Strip comment lines + trailing whitespace
  msg_lines = [ln for ln in text.splitlines() if not ln.lstrip().startswith("#")]
  msg = "\n".join(msg_lines).strip()
  if not msg:
    log.warn("message is empty after stripping comments; no change made")
    return None
  return msg


def _resolve_message(args: argparse.Namespace, run: db.Run, *, field: str) -> str | None:
  if args.message is not None:
    return args.message
  if not sys.stdin.isatty():
    return sys.stdin.read().strip()
  return _editor_message(run, field=field)


def _run_set_field(args: argparse.Namespace, field: str) -> int:
  with db.open_db(_resolve_db_path()) as conn:
    run = db.get_run(conn, args.run_id)
    if run is None:
      _err_no_such_run(conn, args.run_id)
      return 1
    msg = _resolve_message(args, run, field=field)
    if msg is None:
      return 1
    db.update_run(conn, args.run_id, **{field: msg})
  log.ok("updated %s on %s", field, args.run_id)
  return 0


def _run_tag(args: argparse.Namespace) -> int:
  with db.open_db(_resolve_db_path()) as conn:
    if db.get_run(conn, args.run_id) is None:
      _err_no_such_run(conn, args.run_id)
      return 1
    db.add_tag(conn, args.run_id, args.tag)
  log.ok("tagged %s with %s", args.run_id, args.tag)
  return 0


def _run_untag(args: argparse.Namespace) -> int:
  with db.open_db(_resolve_db_path()) as conn:
    run = db.get_run(conn, args.run_id)
    if run is None:
      _err_no_such_run(conn, args.run_id)
      return 1
    if args.tag not in run.tags:
      log.warn("%s does not have tag %r", args.run_id, args.tag)
      return 0
    new_tags = [t for t in run.tags if t != args.tag]
    db.update_run(conn, args.run_id, tags=new_tags)
  log.ok("untagged %s from %s", args.tag, args.run_id)
  return 0
