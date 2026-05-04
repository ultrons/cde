"""`cde build` — build + push the project's image with a hash-based tag.

The tag is `<registry>/<name>:cde-<sha7>`. Two build paths:

  Default (docker build):  sha7 hashes context + Dockerfile.
                            Equivalent to `docker build -t <tag>` + push.

  Crane-append (when `image.base_image` is set in cde.yaml or
  `--base-image` is passed): sha7 hashes context tarball + base digest.
  Equivalent to `crane mutate <base@digest> --append <ctx.tar.gz>
  --workdir <wd> --tag <tag>`. No Docker daemon required; ~1-2s for
  source-only changes. Same shape as xpk's `--base-docker-image +
  --script-dir` fast-path.

Both paths skip the build entirely if the resulting tag already exists
in the registry, unless `--force` is passed.
"""

from __future__ import annotations

import argparse
import tempfile
from pathlib import Path

from cde import (
    config,
    context_hash,
    crane,
    driver as driver_mod,
    logging as log,
    paths,
)
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
  p.add_argument(
      "--base-image",
      dest="base_image",
      default=None,
      help=(
          "Use crane-append fast-path on top of this base image (overrides"
          " image.base_image in cde.yaml). Equivalent to xpk's"
          " --base-docker-image; ~1-2s for source-only changes; no Docker"
          " daemon required. Pass --base-image='' to force the docker-build"
          " path even when cde.yaml has base_image set."
      ),
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
  if not ctx_dir.is_dir():
    log.err("build context not found: %s", ctx_dir)
    return 1

  # Resolve which build path to take. CLI flag overrides cde.yaml; an
  # explicit empty --base-image='' forces the docker path.
  if args.base_image is None:
    base_image = cfg.image.base_image
  elif args.base_image == "":
    base_image = None
  else:
    base_image = args.base_image

  if base_image:
    return _build_crane(args, cfg, ctx_dir, base_image)
  return _build_docker(args, cfg, prefs, project_root, ctx_dir)


def _build_docker(args, cfg, prefs, project_root: Path, ctx_dir: Path) -> int:
  dockerfile = (project_root / cfg.image.dockerfile).resolve()
  if not dockerfile.is_file():
    log.err("Dockerfile not found: %s", dockerfile)
    log.detail(
        "(or set image.base_image in cde.yaml / pass --base-image=<ref>"
        " to use the crane-append fast-path instead)"
    )
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


def _build_crane(args, cfg, ctx_dir: Path, base_image: str) -> int:
  if not crane.is_available():
    log.err(
        "crane not found on PATH — install via: "
        "https://github.com/google/go-containerregistry/tree/main/cmd/crane"
    )
    log.detail(
        "(or unset image.base_image in cde.yaml to use docker build instead)"
    )
    return 1

  workdir = cfg.image.workdir
  log.step("resolving base image %s", base_image)
  try:
    base_digest = crane.resolve_digest(base_image)
  except crane.CraneError as exc:
    log.err("%s", exc)
    return 1
  log.detail("base digest: %s", base_digest)

  with tempfile.TemporaryDirectory(prefix="cde-build-") as tmp:
    tarball = Path(tmp) / "context.tar.gz"
    log.step(
        "tarring context (%s) to land at %s in image", ctx_dir, workdir,
    )
    try:
      crane.make_context_tarball(ctx_dir, workdir=workdir, out=tarball)
    except crane.CraneError as exc:
      log.err("%s", exc)
      return 1

    sha7 = crane.context_sha7(tarball, base_digest)
    tag = f"{cfg.image.repo_path}:cde-{sha7}"
    log.detail("image tag: %s", tag)

    if args.show_tag:
      log.detail("(--show-tag: skipping append and push)")
      print(tag)
      return 0

    if not args.force and crane.image_exists(tag):
      log.ok(
          "%s already exists in registry — skipping append (use --force to redo)",
          tag,
      )
      return 0

    if args.no_push:
      log.warn(
          "--no-push is ignored on the crane-append path — crane mutate"
          " always pushes the resulting image"
      )

    log.step("appending source layer onto %s and pushing %s", base_image, tag)
    try:
      crane.append_and_push(
          base_image=base_image,
          base_digest=base_digest,
          tarball=tarball,
          workdir=workdir,
          tag=tag,
      )
    except crane.CraneError as exc:
      log.err("%s", exc)
      return 1
  log.ok("pushed %s", tag)
  return 0
