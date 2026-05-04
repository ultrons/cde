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

Tests for `cde shell` — verifies argv construction without actually
spawning k9s / kubectl.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

import pytest

from cde.commands import shell as shell_cmd


@pytest.fixture
def cwd_with_yaml(tmp_path, monkeypatch):
  (tmp_path / "cde.yaml").write_text(
      "project: p\n"
      "image:\n  registry: gcr.io/x\n  name: y\n"
      "template: ./manifests/jobset.yaml.j2\n"
      "team: alpha\n"
  )
  monkeypatch.setenv("CDE_HOME", str(tmp_path / ".cde"))
  monkeypatch.chdir(tmp_path)
  return tmp_path


def test_shell_no_run_opens_k9s(cwd_with_yaml, monkeypatch):
  calls = []

  def fake_which(name):
    return "/usr/bin/k9s" if name == "k9s" else None

  def fake_call(argv, *a, **kw):
    calls.append(argv)
    return 0

  monkeypatch.setattr(shell_cmd.shutil, "which", fake_which)
  monkeypatch.setattr(shell_cmd.subprocess, "call", fake_call)

  args = argparse.Namespace(do_exec=False, run_id=None, cmd="/bin/bash")
  rc = shell_cmd.run(args)
  assert rc == 0
  assert calls == [["k9s", "-n", "team-alpha"]]


def test_shell_no_k9s(cwd_with_yaml, monkeypatch):
  monkeypatch.setattr(shell_cmd.shutil, "which", lambda _n: None)
  args = argparse.Namespace(do_exec=False, run_id=None, cmd="/bin/bash")
  rc = shell_cmd.run(args)
  assert rc == 127


def test_shell_exec_requires_run_id(cwd_with_yaml):
  args = argparse.Namespace(do_exec=True, run_id=None, cmd="/bin/bash")
  rc = shell_cmd.run(args)
  assert rc == 2
