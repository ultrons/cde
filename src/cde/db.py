"""SQLite history store.

Schema lives in `_MIGRATIONS` as raw SQL strings. Adding a new migration
means appending one to the list (and never editing the existing entries).

Composite primary key is (submitter, run_id) so future team-shared
history (multiple submitters into one DB) is supported without a schema
change. Solo users get `submitter=""`.
"""

from __future__ import annotations

import datetime
import json
import sqlite3
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterator


# ---------------------------------------------------------------------------
# Migrations
# ---------------------------------------------------------------------------

_MIGRATIONS: list[str] = [
    # 001_initial.sql — schema_migrations is created by _apply_migrations
    # itself before this runs.
    """
    CREATE TABLE runs (
      run_id           TEXT NOT NULL,
      submitter        TEXT NOT NULL DEFAULT '',
      ts_submitted     TIMESTAMP NOT NULL,
      ts_started       TIMESTAMP,
      ts_finished      TIMESTAMP,
      status           TEXT NOT NULL DEFAULT 'submitted',

      git_sha          TEXT,
      git_dirty        INTEGER NOT NULL DEFAULT 0,
      image_tag        TEXT,

      manifest_text    TEXT,
      overrides        TEXT NOT NULL DEFAULT '{}',
      template_path    TEXT,
      team             TEXT,
      value_class      TEXT,
      declared_min     INTEGER,
      k8s_namespace    TEXT,
      jobset_name      TEXT,

      log_uri          TEXT,
      profile_uri      TEXT,
      output_uri       TEXT,

      notes            TEXT NOT NULL DEFAULT '',
      tags             TEXT NOT NULL DEFAULT '[]',
      hypothesis       TEXT NOT NULL DEFAULT '',
      parent_run       TEXT,
      parent_submitter TEXT,

      PRIMARY KEY (submitter, run_id)
    );

    CREATE INDEX idx_runs_ts ON runs(ts_submitted DESC);
    CREATE INDEX idx_runs_status ON runs(status);
    CREATE INDEX idx_runs_team ON runs(team);
    """,
]


def _apply_migrations(conn: sqlite3.Connection) -> None:
  cur = conn.cursor()
  cur.execute(
      "CREATE TABLE IF NOT EXISTS schema_migrations ("
      "  version INTEGER PRIMARY KEY,"
      "  applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
  )
  cur.execute("SELECT version FROM schema_migrations")
  applied = {row[0] for row in cur.fetchall()}

  for i, sql in enumerate(_MIGRATIONS, start=1):
    if i in applied:
      continue
    cur.executescript(sql)
    cur.execute("INSERT INTO schema_migrations (version) VALUES (?)", (i,))
  conn.commit()


# ---------------------------------------------------------------------------
# Connection
# ---------------------------------------------------------------------------


def connect(db_path: Path) -> sqlite3.Connection:
  """Open the DB, ensure parent dir exists, run migrations, return connection.

  Timestamps are kept as TEXT (ISO-8601 UTC) — we deliberately don't enable
  PARSE_DECLTYPES so SQLite doesn't try to parse them as the dot-separated
  format. Application code reads/writes ISO-8601 strings end-to-end.
  """
  db_path.parent.mkdir(parents=True, exist_ok=True)
  conn = sqlite3.connect(db_path)
  conn.row_factory = sqlite3.Row
  conn.execute("PRAGMA journal_mode=WAL")
  conn.execute("PRAGMA foreign_keys=ON")
  _apply_migrations(conn)
  return conn


@contextmanager
def open_db(db_path: Path) -> Iterator[sqlite3.Connection]:
  conn = connect(db_path)
  try:
    yield conn
    conn.commit()
  finally:
    conn.close()


# ---------------------------------------------------------------------------
# Run dataclass + serialization
# ---------------------------------------------------------------------------


@dataclass
class Run:
  run_id: str
  submitter: str = ""
  ts_submitted: str = ""           # ISO-8601 UTC; populated on insert if empty
  ts_started: str | None = None
  ts_finished: str | None = None
  status: str = "submitted"

  git_sha: str | None = None
  git_dirty: bool = False
  image_tag: str | None = None

  manifest_text: str | None = None
  overrides: dict[str, Any] = field(default_factory=dict)
  template_path: str | None = None
  team: str | None = None
  value_class: str | None = None
  declared_min: int | None = None
  k8s_namespace: str | None = None
  jobset_name: str | None = None

  log_uri: str | None = None
  profile_uri: str | None = None
  output_uri: str | None = None

  notes: str = ""
  tags: list[str] = field(default_factory=list)
  hypothesis: str = ""
  parent_run: str | None = None
  parent_submitter: str | None = None

  def to_row(self) -> dict[str, Any]:
    """Convert to the column dict used by SQLite."""
    d = asdict(self)
    d["overrides"] = json.dumps(d["overrides"], sort_keys=True)
    d["tags"] = json.dumps(d["tags"])
    d["git_dirty"] = 1 if d["git_dirty"] else 0
    return d


def _row_to_run(row: sqlite3.Row) -> Run:
  d = dict(row)
  d["overrides"] = json.loads(d.get("overrides") or "{}")
  d["tags"] = json.loads(d.get("tags") or "[]")
  d["git_dirty"] = bool(d.get("git_dirty", 0))
  return Run(**d)


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------

_NOW = lambda: datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def insert_run(conn: sqlite3.Connection, run: Run) -> Run:
  if not run.ts_submitted:
    run.ts_submitted = _NOW()
  row = run.to_row()
  cols = list(row.keys())
  placeholders = ",".join(f":{c}" for c in cols)
  conn.execute(
      f"INSERT INTO runs ({','.join(cols)}) VALUES ({placeholders})",
      row,
  )
  conn.commit()
  return run


def get_run(
    conn: sqlite3.Connection, run_id: str, *, submitter: str = ""
) -> Run | None:
  cur = conn.execute(
      "SELECT * FROM runs WHERE submitter=? AND run_id=?",
      (submitter, run_id),
  )
  row = cur.fetchone()
  return _row_to_run(row) if row else None


def list_runs(
    conn: sqlite3.Connection,
    *,
    submitter: str | None = None,
    limit: int = 20,
    status: str | None = None,
) -> list[Run]:
  q = "SELECT * FROM runs"
  conds: list[str] = []
  args: list[Any] = []
  if submitter is not None:
    conds.append("submitter=?")
    args.append(submitter)
  if status is not None:
    conds.append("status=?")
    args.append(status)
  if conds:
    q += " WHERE " + " AND ".join(conds)
  q += " ORDER BY ts_submitted DESC LIMIT ?"
  args.append(limit)
  cur = conn.execute(q, args)
  return [_row_to_run(r) for r in cur.fetchall()]


def update_run(
    conn: sqlite3.Connection, run_id: str, *, submitter: str = "", **fields: Any
) -> None:
  if not fields:
    return
  # Encode complex types
  if "overrides" in fields and isinstance(fields["overrides"], dict):
    fields["overrides"] = json.dumps(fields["overrides"], sort_keys=True)
  if "tags" in fields and isinstance(fields["tags"], list):
    fields["tags"] = json.dumps(fields["tags"])
  if "git_dirty" in fields and isinstance(fields["git_dirty"], bool):
    fields["git_dirty"] = 1 if fields["git_dirty"] else 0

  set_clause = ",".join(f"{k}=:{k}" for k in fields)
  args = dict(fields, run_id=run_id, submitter=submitter)
  conn.execute(
      f"UPDATE runs SET {set_clause} WHERE submitter=:submitter AND run_id=:run_id",
      args,
  )
  conn.commit()


def add_tag(
    conn: sqlite3.Connection, run_id: str, tag: str, *, submitter: str = ""
) -> list[str]:
  run = get_run(conn, run_id, submitter=submitter)
  if run is None:
    raise KeyError(f"no such run ({submitter!r}, {run_id!r})")
  if tag not in run.tags:
    run.tags.append(tag)
    update_run(conn, run_id, submitter=submitter, tags=run.tags)
  return run.tags


def annotate(
    conn: sqlite3.Connection, run_id: str, note: str, *, submitter: str = ""
) -> None:
  update_run(conn, run_id, submitter=submitter, notes=note)


def set_status(
    conn: sqlite3.Connection,
    run_id: str,
    status: str,
    *,
    submitter: str = "",
    finished: bool = False,
) -> None:
  fields: dict[str, Any] = {"status": status}
  if finished:
    fields["ts_finished"] = _NOW()
  update_run(conn, run_id, submitter=submitter, **fields)
