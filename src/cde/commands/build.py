"""`cde build` — build + push the project's image with a hash-based tag.

The tag is `<registry>/<name>:cde-<sha7>` where sha7 is computed from
the build context (Dockerfile + tracked files). Same context = same
tag = no rebuild needed (we skip the build entirely if the image is
already in the registry, unless --force is passed).
"""

from __future__ import annotations

import argparse
from pathlib import Path

from cde import config, context_hash, driver as driver_mod, logging as log, paths
from cde import preferences as prefs_mod


def register(subparsers: argparse._SubParsersAction) -> None:
  p = subparsers.add_parser(
      "build",
      help="Build and push the project's container image (hash-tagged).",
  )
  p.add_argument(
      "--force",
      action="store_true",
      help="Build and push even if the registry already has this tag.",
  )
  p.add_argument(
      "--no-push",
      action="store_true",
      help="Build locally only; skip push.",
  )
  p.add_argument(
      "--show-tag", "--print-tag",
      dest="show_tag",
      action="store_true",
      help="Print the resolved tag and exit without building or pushing.",
  )
  p.set_defaults(func=run)


def run(args: argparse.Namespace) -> int:
  cfg_path = paths.project_config_path()
  if not cfg_path.is_file():
    log.err("no cde.yaml found (run `cde init` first)")
    return 1
  cfg = config.load(cfg_path)
  prefs = prefs_mod.load()

  project_root = cfg_path.parent
  ctx_dir = (project_root / cfg.image.context).resolve()
  dockerfile = (project_root / cfg.image.dockerfile).resolve()
  if not ctx_dir.is_dir():
    log.err("build context not found: %s", ctx_dir)
    return 1
  if not dockerfile.is_file():
    log.err("Dockerfile not found: %s", dockerfile)
    return 1

  log.step("hashing build context (%s)", ctx_dir)
  sha7 = context_hash.context_hash(ctx_dir, dockerfile=dockerfile)
  tag = f"{cfg.image.repo_path}:cde-{sha7}"
  log.detail("image tag: %s", tag)

  if args.show_tag:
    log.detail("(--show-tag: skipping build and push)")
    print(tag)
    return 0

  drv = driver_mod.Driver(prefs)

  # Skip if already in registry, unless --force
  if not args.force and drv.image_exists(tag):
    log.ok("%s already exists in registry — skipping build (use --force to rebuild)", tag)
    return 0

  log.step("building %s", tag)
  rc = drv.build(
      context=ctx_dir,
      dockerfile=dockerfile,
      tag=tag,
  )
  if rc != 0:
    log.err("%s build failed (exit %d)", prefs.build.driver, rc)
    return rc

  if args.no_push or not prefs.build.push_after_build:
    log.ok("built %s (push skipped)", tag)
    return 0

  log.step("pushing %s", tag)
  rc = drv.push(tag)
  if rc != 0:
    log.err("%s push failed (exit %d)", prefs.build.driver, rc)
    return rc
  log.ok("pushed %s", tag)
  return 0
