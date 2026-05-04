"""`cde init` — scaffold a project's cde.yaml + manifest template.

Refuses to overwrite existing files unless --force is passed. Bootstraps
the SQLite history DB at ~/.cde/history.sqlite (or wherever cde.yaml's
history.path resolves to) so subsequent verbs can write rows.

Substitutes a few template tokens at write time so the scaffolded
cde.yaml is closer to ready-to-edit than ready-to-replace:

  REPLACE-ME (project key)  → --project arg or basename of cwd
  REPLACE-ME (image.name)   → basename of cwd (good first guess)

`--from-yaml <path>` parses an existing JobSet manifest and emits a
matching cde.yaml + Jinja template, so onboarding a workload that
already has a hand-written YAML doesn't require rewriting.
"""

from __future__ import annotations

import argparse
import importlib.resources as ilr
import re
from pathlib import Path
from typing import Any

import yaml

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
  p.add_argument(
      "--from-yaml",
      dest="from_yaml",
      default=None,
      metavar="PATH",
      help=(
          "Scaffold cde.yaml + jinja template from an existing JobSet"
          " manifest at PATH instead of from the packaged defaults."
      ),
  )
  p.set_defaults(func=run)


_DOCKERIGNORE_TEMPLATE = """\
# cde reads .dockerignore when computing the build-context hash, so anything
# excluded here keeps the cde-<sha> image tag stable when only project
# metadata changes — no surprise rebuilds when you edit cde.yaml or notes.
#
# Add your own patterns below as the project grows.

cde.yaml
manifests/
.cde/
.git/
.gitignore

# Editor/OS clutter
*.swp
*.swo
.DS_Store
.idea/
.vscode/

# Python build artifacts
__pycache__/
*.pyc
*.pyo
.pytest_cache/
.mypy_cache/
.ruff_cache/
.tox/
*.egg-info/
build/
dist/

# Notes / docs that don't get baked in (override per-project as needed)
*.md
NOTES.md
"""


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


# ---------------------------------------------------------------------------
# --from-yaml: import an existing JobSet
# ---------------------------------------------------------------------------


# Keys whose value-position Jinja placeholder is safe to emit unquoted.
# (The post-pass below strips the quotes pyyaml adds around `'{{ x }}'`.)
# `declared-duration-minutes` is intentionally NOT in this set: K8s label
# values must be strings, so the rendered output needs `"60"`, not `60`.
_UNQUOTE_KEYS = {
    "name", "namespace", "team", "value-class",
    "priorityClassName", "image", "replicas",
    "parallelism", "completions",
}


def _split_image(image: str | None) -> tuple[str, str, str | None]:
  """`gcr.io/proj/myname:tag` → ('gcr.io/proj', 'myname', 'tag').
  Falls back to ('REPLACE-ME', <best guess>, None) on any unparseable form."""
  if not image:
    return "REPLACE-ME", "REPLACE-ME", None
  tag: str | None
  if ":" in image:
    base, _, tag_part = image.rpartition(":")
    tag = tag_part or None
  else:
    base, tag = image, None
  if "/" in base:
    registry, _, name = base.rpartition("/")
  else:
    registry, name = "REPLACE-ME", base
  return registry or "REPLACE-ME", name or "REPLACE-ME", tag


def _from_yaml_scaffold(
    yaml_path: Path, project_name: str
) -> tuple[str, str, dict[str, Any]]:
  """Parse a JobSet YAML; return (cde_yaml_text, jinja_template_text, info).

  `info` is a dict of inferred fields (used only to log what we found).
  Raises ValueError on an unusable input."""
  raw = yaml_path.read_text(encoding="utf-8")
  docs = [d for d in yaml.safe_load_all(raw) if d is not None]
  jobsets = [d for d in docs if isinstance(d, dict) and d.get("kind") == "JobSet"]
  if not jobsets:
    raise ValueError(
        f"no JobSet document found in {yaml_path} "
        f"(saw kinds: {sorted({d.get('kind') for d in docs})})"
    )
  if len(jobsets) > 1:
    raise ValueError(
        f"{yaml_path} contains {len(jobsets)} JobSet documents; "
        "pass one at a time"
    )
  js = jobsets[0]
  other_docs = [d for d in docs if d is not js]

  md = js.setdefault("metadata", {})
  labels = md.setdefault("labels", {})
  spec = js.setdefault("spec", {})
  rjs = spec.setdefault("replicatedJobs", [])

  def _pod_template(rj: dict) -> dict | None:
    """Return the pod template under a replicatedJob, or None."""
    pt = (rj.get("template") or {}).get("spec", {}).get("template")
    return pt if isinstance(pt, dict) else None

  def _pod_spec(rj: dict) -> dict:
    pt = _pod_template(rj) or {}
    return pt.get("spec", {}) or {}

  def _containers(rj: dict) -> list[dict]:
    return _pod_spec(rj).get("containers", []) or []

  # Pick a "worker" replicatedJob: the one with the largest `replicas`. This
  # gives us the user-relevant defaults (image, num_slices, nodeSelector) for
  # Pathways (head=1, worker=N) and is identical to rjs[0] for regular JobSets.
  def _replicas_int(rj: dict) -> int:
    try:
      return int(rj.get("replicas", 1))
    except (TypeError, ValueError):
      return 1

  worker_rj = max(rjs, key=_replicas_int) if rjs else {}
  worker_replicas = _replicas_int(worker_rj) if worker_rj else 0
  worker_pod_spec = _pod_spec(worker_rj) if worker_rj else {}
  worker_containers = _containers(worker_rj) if worker_rj else []
  user_image = worker_containers[0].get("image") if worker_containers else None

  inferred = {
      "team": labels.get("team"),
      "value_class": labels.get("value-class"),
      "declared_minutes": labels.get("declared-duration-minutes"),
      "namespace": md.get("namespace"),
      "priority_class": worker_pod_spec.get("priorityClassName"),
      "image": user_image,
      "num_slices": worker_rj.get("replicas") if worker_rj else None,
      "tpu_type": (worker_pod_spec.get("nodeSelector") or {}).get(
          "cloud.google.com/gke-tpu-accelerator"
      ),
      "tpu_topology": (worker_pod_spec.get("nodeSelector") or {}).get(
          "cloud.google.com/gke-tpu-topology"
      ),
  }

  registry, image_name, _ = _split_image(inferred["image"])

  # Substitute Jinja placeholders into the parsed structure.
  md["name"] = "{{ run_id }}"
  md["namespace"] = "{{ namespace }}"
  labels["team"] = "{{ team }}"
  if "value-class" in labels:
    labels["value-class"] = "{{ value_class }}"
  if "declared-duration-minutes" in labels:
    labels["declared-duration-minutes"] = "{{ declared_minutes }}"
  labels["cde.io/run-id"] = "{{ run_id }}"

  # Walk every replicatedJob: substitute on the user's image (only — leave
  # Pathways' proxy/server images literal), inject cde.io/run-id label,
  # template `replicas` only on the worker rj, template priorityClassName.
  for rj in rjs:
    pt = _pod_template(rj)
    if pt is None:
      continue
    pmd = pt.setdefault("metadata", {})
    plabels = pmd.setdefault("labels", {})
    plabels.setdefault("cde.io/run-id", "{{ run_id }}")
    if "declared-duration-minutes" in plabels:
      plabels["declared-duration-minutes"] = "{{ declared_minutes }}"
    pspec = pt.setdefault("spec", {})
    if "priorityClassName" in pspec:
      pspec["priorityClassName"] = "{{ priority_class }}"
    for c in pspec.get("containers", []) or []:
      if user_image and c.get("image") == user_image:
        c["image"] = "{{ image }}"
    # Replicas: only template the worker (largest replicas count) — leaves
    # head's `replicas: 1` literal in Pathways manifests.
    if rj is worker_rj and "replicas" in rj:
      rj["replicas"] = "{{ num_slices }}"

  # Dump and post-process. Use a default_flow_style=False block dump.
  dumped = yaml.safe_dump(js, default_flow_style=False, sort_keys=False)

  # Unquote `'{{ x }}'` for keys where unquoting is safe.
  def _unquote(text: str, key: str) -> str:
    pattern = (
        rf"^(?P<indent>\s*){re.escape(key)}:(?P<sp>\s+)"
        r"(?P<q>['\"])(?P<jinja>\{\{\s*\w+\s*\}\})(?P=q)(?P<tail>\s*)$"
    )
    return re.sub(
        pattern, r"\g<indent>" + key + r":\g<sp>\g<jinja>\g<tail>",
        text, flags=re.MULTILINE,
    )

  for k in _UNQUOTE_KEYS:
    dumped = _unquote(dumped, k)

  # If the input had sibling docs (PriorityClass etc.), keep them as-is —
  # cde owns only the JobSet. Emit them after the JobSet, separated by ---.
  if other_docs:
    extras = "\n---\n".join(
        yaml.safe_dump(d, default_flow_style=False, sort_keys=False)
        for d in other_docs
    )
    dumped = dumped + "---\n" + extras

  template_text = (
      "{# Scaffolded by `cde init --from-yaml`. Edit freely.\n"
      "   Substitutions provided by `cde run`:\n"
      "     run_id, image, team, value_class, declared_minutes,\n"
      "     namespace, priority_class, num_slices,\n"
      "     overrides (dict), env (list of {name, value} dicts).\n"
      "   Bool overrides (--flag/--no-flag) come through as Python True/False;\n"
      "   render True as a bare --flag, False as omitted.\n"
      "#}\n"
  ) + dumped

  # Build cde.yaml using inferred values where we found them.
  pkg_templates = ilr.files("cde").joinpath("templates")
  base = pkg_templates.joinpath("cde.yaml").read_text(encoding="utf-8")
  base = base.replace("project: REPLACE-ME", f"project: {project_name}", 1)
  base = base.replace(
      "registry: gcr.io/REPLACE-ME", f"registry: {registry}", 1,
  )
  base = base.replace("name: REPLACE-ME", f"name: {image_name}", 1)
  if inferred["team"]:
    base = base.replace("team: REPLACE-ME", f"team: {inferred['team']}", 1)
  if inferred["value_class"]:
    base = base.replace(
        "value-class: development",
        f"value-class: {inferred['value_class']}", 1,
    )
  if inferred["declared_minutes"] is not None:
    try:
      dm = int(inferred["declared_minutes"])
      base = base.replace(
          "declared-duration-minutes: 60",
          f"declared-duration-minutes: {dm}", 1,
      )
    except (TypeError, ValueError):
      pass
  if inferred["tpu_type"]:
    base = base.replace(
        "tpu-type: tpu7x-128", f"tpu-type: {inferred['tpu_type']}", 1,
    )
  if inferred["num_slices"] is not None:
    try:
      ns = int(inferred["num_slices"])
      base = base.replace("num-slices: 1", f"num-slices: {ns}", 1)
    except (TypeError, ValueError):
      pass

  # If the imported YAML used a non-derived namespace / priorityClass,
  # pin them in defaults_overrides so cde doesn't override them via
  # the team-<team> fallback.
  ns_override = inferred["namespace"]
  pc_override = inferred["priority_class"]
  team = inferred["team"] or ""
  derives_to_team_ns = ns_override == f"team-{team}" if team else False
  derives_to_team_pc = (
      pc_override == f"{ns_override}-priority"
      if ns_override and pc_override else False
  )
  override_lines: list[str] = []
  if ns_override and not derives_to_team_ns:
    override_lines.append(f"  namespace: {ns_override}")
  if pc_override and not derives_to_team_pc:
    override_lines.append(f"  priority_class: {pc_override}")
  if override_lines:
    base = base.replace(
        "defaults_overrides: {}",
        "defaults_overrides:\n" + "\n".join(override_lines),
        1,
    )

  return base, template_text, inferred


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

  imported_info: dict[str, Any] | None = None
  if args.from_yaml:
    src = Path(args.from_yaml).expanduser().resolve()
    if not src.is_file():
      log.err("--from-yaml: %s does not exist or is not a file", src)
      return 1
    try:
      cde_yaml_text, template_text, imported_info = _from_yaml_scaffold(
          src, project_name,
      )
    except (ValueError, yaml.YAMLError) as exc:
      log.err("--from-yaml: %s", exc)
      return 1
  else:
    cde_yaml_text = _scaffold_cde_yaml(project_name)
    template_text = pkg_templates.joinpath("jobset.yaml.j2").read_text(
        encoding="utf-8",
    )

  log.step("writing %s", cde_yaml_dst.relative_to(cwd))
  cde_yaml_dst.write_text(cde_yaml_text, encoding="utf-8")
  log.detail(
      "project name set to %r (basename of %s)",
      project_name, cwd,
  )
  log.detail("edit cde.yaml.project if you want a different grouping for history")

  if imported_info is not None:
    found = ", ".join(
        f"{k}={v}" for k, v in sorted(imported_info.items()) if v is not None
    ) or "(nothing inferable)"
    log.detail("imported from %s: %s", args.from_yaml, found)

  manifests_dir.mkdir(exist_ok=True)
  if manifest_dst.exists() and not args.force:
    log.warn(
        "%s already exists; skipped. Pass --force to overwrite.",
        manifest_dst.relative_to(cwd),
    )
  else:
    log.step("writing %s", manifest_dst.relative_to(cwd))
    manifest_dst.write_text(template_text, encoding="utf-8")

  # `.dockerignore` sets cde's build-context hash. Scaffold sensible
  # defaults so cde-tag stays stable when only project metadata changes
  # (cde.yaml, manifests/, scratch notes). Without this, every edit to
  # those files invalidates the hash and forces a rebuild — bug surfaced
  # in the vllm onboarding session.
  dockerignore_dst = cwd / ".dockerignore"
  if dockerignore_dst.exists() and not args.force:
    log.detail(
        ".dockerignore already exists; left as-is (pass --force to overwrite)."
    )
  else:
    log.step("writing %s", dockerignore_dst.relative_to(cwd))
    dockerignore_dst.write_text(_DOCKERIGNORE_TEMPLATE, encoding="utf-8")

  if not args.no_history:
    log.step("initialising history DB at %s", paths.history_db_path())
    paths.ensure_cde_home()
    with db.open_db(paths.history_db_path()):
      pass  # creating + migrating happens on connect

  log.ok("cde initialised. Next steps:")
  log.detail("1. Edit cde.yaml — set image.registry and team.")
  log.detail("2. Edit manifests/jobset.yaml.j2 to fit your workload.")
  log.detail("3. Review .dockerignore — extend it for your project's source layout.")
  log.detail("4. cde build && cde run --tag v001 --note 'first run'")
  log.detail(
      "5. Tab completion (per shell):"
      ' eval "$(register-python-argcomplete cde)"'
      " (add to ~/.bashrc to persist)"
  )
  return 0
