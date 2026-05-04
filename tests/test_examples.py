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

Smoke test: every committed example renders via `cde run --render-only`.

Examples rot when cde's contract changes (config schema, template
variables, default flags). This gates the cde side of that drift.
Upstream version drift (MaxText, vLLM SHAs) is on the human.

A "runnable example" here is any examples/<name>/cde.yaml that has a
sibling manifests/jobset.yaml.j2. Stub examples without those files are
ignored.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
EXAMPLES_ROOT = REPO_ROOT / "examples"


def _runnable_examples() -> list[Path]:
  if not EXAMPLES_ROOT.is_dir():
    return []
  out = []
  for d in sorted(EXAMPLES_ROOT.iterdir()):
    if not d.is_dir():
      continue
    if (d / "cde.yaml").is_file() and (d / "manifests" / "jobset.yaml.j2").is_file():
      out.append(d)
  return out


@pytest.mark.parametrize(
    "example_dir", _runnable_examples(), ids=lambda d: d.name,
)
def test_example_renders(example_dir: Path, tmp_path: Path, monkeypatch):
  """`cde run --render-only` against the example's cde.yaml + template
  must produce a valid JobSet manifest.

  We patch the REPLACE-ME tokens (registry, team, GCS URIs) with
  benign placeholders so render-only succeeds without polluting the
  example with real cluster identifiers."""
  work = tmp_path / "work"
  shutil.copytree(example_dir, work)

  cfg_text = (work / "cde.yaml").read_text()
  cfg_text = (
      cfg_text
      .replace("gcr.io/REPLACE-ME", "gcr.io/example-project")
      .replace("gs://REPLACE-ME", "gs://example-bucket")
      .replace("REPLACE-ME", "example-team")
  )
  (work / "cde.yaml").write_text(cfg_text)

  # cde build's context-hash needs at least a Dockerfile + one tracked file.
  if not (work / "Dockerfile").exists():
    (work / "Dockerfile").write_text("FROM scratch\n")
  if not list(work.glob("*.py")):
    (work / "placeholder.py").write_text("# placeholder for context-hash\n")

  monkeypatch.chdir(work)
  monkeypatch.setenv("CDE_HOME", str(tmp_path / ".cde"))
  monkeypatch.setenv("CDE_PREFERENCES", str(tmp_path / "prefs.yaml"))

  proc = subprocess.run(
      [sys.executable, "-m", "cde", "run", "--tag", "smoke-001", "--render-only"],
      capture_output=True, text=True, env=os.environ.copy(), check=False,
  )
  assert proc.returncode == 0, (
      f"{example_dir.name}: cde run --render-only failed\n"
      f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
  )

  doc = yaml.safe_load(proc.stdout)
  assert doc is not None, f"{example_dir.name}: empty render output"
  assert doc.get("kind") == "JobSet", (
      f"{example_dir.name}: expected kind=JobSet, got {doc.get('kind')!r}"
  )
  assert doc["metadata"]["name"] == "smoke-001"
  # cde always injects the run-id label so cde logs / reap can find pods.
  assert doc["metadata"]["labels"]["cde.io/run-id"] == "smoke-001"
