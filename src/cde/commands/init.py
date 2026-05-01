"""`cde init` — scaffold a project's cde.yaml + manifest template.

Refuses to overwrite existing files unless --force is passed. Bootstraps
the SQLite history DB at ~/.cde/history.sqlite (or wherever cde.yaml's
history.path resolves to) so subsequent verbs can write rows.

Substitutes a few template tokens at write time so the scaffolded
cde.yaml is closer to ready-to-edit than ready-to-replace:

  REPLACE-ME (project key)  → --project arg or basename of cwd
  REPLACE-ME (image.name)   → basename of cwd (good first guess)
"""

from __future__ import annotations

import argparse
import importlib.resources as ilr
from pathlib import Path

from cde import db, logging as log, paths


def register(subparsers: argparse._SubParsersAction) -> None:
  p = subparsers.add_parser(
      "init",
      help="Scaffold cde.yaml + manifest template in the current directory.",
  )
  p.add_argument(
      "--project",
      default=None,
      help=(
          "Logical project name (used to partition run history). "
          "Defaults to the basename of the project directory."
      ),
  )
  p.add_argument(
      "--force",
      action="store_true",
      help="Overwrite existing files (cde.yaml, manifests/jobset.yaml.j2).",
  )
  p.add_argument(
      "--no-history",
      action="store_true",
      help="Skip bootstrapping the SQLite history DB.",
  )
  p.set_defaults(func=run)


def _scaffold_cde_yaml(project_name: str) -> str:
  """Read the packaged template and substitute the obvious tokens."""
  pkg_templates = ilr.files("cde").joinpath("templates")
  text = pkg_templates.joinpath("cde.yaml").read_text(encoding="utf-8")
  # Replace just the *first two* occurrences of REPLACE-ME — `project:`
  # then `image.name`. The registry stays REPLACE-ME because we can't
  # reasonably guess it. The team also stays REPLACE-ME.
  text = text.replace("project: REPLACE-ME", f"project: {project_name}", 1)
  text = text.replace("name: REPLACE-ME", f"name: {project_name}", 1)
  return text


def run(args: argparse.Namespace) -> int:
  cwd = Path.cwd()
  project_name = args.project or cwd.name

  cde_yaml_dst = cwd / "cde.yaml"
  manifests_dir = cwd / "manifests"
  manifest_dst = manifests_dir / "jobset.yaml.j2"

  if cde_yaml_dst.exists() and not args.force:
    log.err(
        "cde.yaml already exists at %s. Pass --force to overwrite.",
        cde_yaml_dst,
    )
    return 1

  pkg_templates = ilr.files("cde").joinpath("templates")

  log.step("writing %s", cde_yaml_dst.relative_to(cwd))
  cde_yaml_dst.write_text(_scaffold_cde_yaml(project_name), encoding="utf-8")
  log.detail(
      "project name set to %r (basename of %s)",
      project_name, cwd,
  )
  log.detail("edit cde.yaml.project if you want a different grouping for history")

  manifests_dir.mkdir(exist_ok=True)
  if manifest_dst.exists() and not args.force:
    log.warn(
        "%s already exists; skipped. Pass --force to overwrite.",
        manifest_dst.relative_to(cwd),
    )
  else:
    log.step("writing %s", manifest_dst.relative_to(cwd))
    manifest_dst.write_text(
        pkg_templates.joinpath("jobset.yaml.j2").read_text(encoding="utf-8"),
        encoding="utf-8",
    )

  if not args.no_history:
    log.step("initialising history DB at %s", paths.history_db_path())
    paths.ensure_cde_home()
    with db.open_db(paths.history_db_path()):
      pass  # creating + migrating happens on connect

  log.ok("cde initialised. Next steps:")
  log.detail("1. Edit cde.yaml — set image.registry and team.")
  log.detail("2. Edit manifests/jobset.yaml.j2 to fit your workload.")
  log.detail("3. cde build && cde run --tag v001 --note 'first run'")
  return 0
