"""Copyright 2026 Google LLC

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

     https://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.

`cde shell` — quick access to running pods.

Two modes:

  cde shell                    open k9s scoped to the current project's
                               namespace (-l team-quota.io/team=<team>)
  cde shell <run> --exec       kubectl exec -it into the run's first pod
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
from pathlib import Path

from cde import config, db, k8s, logging as log, paths, suggest


def register(subparsers: argparse._SubParsersAction) -> None:
  from cde import cli, completers

  p = subparsers.add_parser(
      "shell",
      help="k9s into the project's namespace, or kubectl exec into a run's pod.",
  )
  cli.set_completer(
      p.add_argument(
          "run_id",
          nargs="?",
          help="(with --exec) which run to exec into",
      ),
      completers.run_id_completer,
  )
  p.add_argument(
      "--exec",
      dest="do_exec",
      action="store_true",
      help="kubectl exec -it into the run's first pod (requires <run_id>)",
  )
  p.add_argument(
      "--cmd",
      default="/bin/bash",
      help="command to exec inside the pod (default: /bin/bash)",
  )
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


def _resolve_namespace_team() -> tuple[str | None, str | None]:
  cfg_path = paths.project_config_path()
  if not cfg_path.is_file():
    return None, None
  try:
    cfg = config.load(cfg_path)
  except config.ConfigError:
    return None, None
  ns = cfg.defaults_overrides.get("namespace") or f"team-{cfg.team}"
  return ns, cfg.team


def run(args: argparse.Namespace) -> int:
  if args.do_exec:
    return _exec_into_run(args)
  return _open_k9s()


def _open_k9s() -> int:
  if not shutil.which("k9s"):
    log.err("k9s not found on PATH. Install: https://k9scli.io/")
    return 127

  ns, team = _resolve_namespace_team()
  argv = ["k9s"]
  if ns:
    argv.extend(["-n", ns])
    log.detail("opening k9s scoped to %s", ns)
  else:
    log.detail("no project cde.yaml; opening k9s with default namespace")
  return subprocess.call(argv)


def _exec_into_run(args: argparse.Namespace) -> int:
  if not args.run_id:
    log.err("cde shell --exec requires a <run_id>")
    return 2

  with db.open_db(_resolve_db_path()) as conn:
    r = db.get_run(conn, args.run_id)
    if r is None:
      ids = [x.run_id for x in db.list_runs(conn, limit=200)]
      log.err("no such run: %r.%s", args.run_id, suggest.hint(args.run_id, ids))
      return 1

  if not r.k8s_namespace:
    log.err("run %s has no k8s_namespace recorded", r.run_id)
    return 1

  label = f"cde.io/run-id={r.run_id}"
  cmd = args.cmd.split() if isinstance(args.cmd, str) else list(args.cmd)
  try:
    return k8s.exec_into_first_pod(
        namespace=r.k8s_namespace, label=label, command=cmd,
        context=r.k8s_context or None,
    )
  except k8s.KubectlError as exc:
    log.err("%s", exc)
    return 1
