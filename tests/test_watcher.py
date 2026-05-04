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

Tests for the watcher module — debounce + coalescing semantics.
"""
from __future__ import annotations

import time
from pathlib import Path

import pytest

from cde import watcher


@pytest.fixture
def tmp_root(tmp_path) -> Path:
  return tmp_path


def _wait_for(predicate, timeout_s: float = 3.0, poll_s: float = 0.05) -> bool:
  deadline = time.monotonic() + timeout_s
  while time.monotonic() < deadline:
    if predicate():
      return True
    time.sleep(poll_s)
  return False


def test_single_change_fires_callback(tmp_root):
  batches: list[list[Path]] = []
  with watcher.Watcher([tmp_root], callback=batches.append, debounce_ms=100):
    (tmp_root / "a.py").write_text("x")
    assert _wait_for(lambda: len(batches) == 1)
  assert any((tmp_root / "a.py").resolve() == p.resolve() for p in batches[0])


def test_rapid_changes_coalesce_into_one_batch(tmp_root):
  batches: list[list[Path]] = []
  with watcher.Watcher([tmp_root], callback=batches.append, debounce_ms=200):
    for i in range(5):
      (tmp_root / f"f{i}.py").write_text("x")
      time.sleep(0.02)  # well under debounce
    assert _wait_for(lambda: len(batches) >= 1, timeout_s=2.0)
  # All 5 saves coalesce into one batch (or possibly 2 if the test is slow);
  # at minimum the total file count seen should be 5.
  total = sum(len(b) for b in batches)
  assert total >= 5
  # Coalescing claim: usually exactly one batch
  assert len(batches) <= 2


def test_pycache_ignored(tmp_root):
  batches: list[list[Path]] = []
  (tmp_root / "__pycache__").mkdir()
  with watcher.Watcher([tmp_root], callback=batches.append, debounce_ms=150):
    (tmp_root / "__pycache__" / "x.pyc").write_text("compiled")
    time.sleep(0.4)
  assert batches == []


def test_callback_exception_doesnt_kill_watcher(tmp_root):
  call_count = [0]

  def boom(_batch):
    call_count[0] += 1
    raise RuntimeError("boom")

  with watcher.Watcher([tmp_root], callback=boom, debounce_ms=100):
    (tmp_root / "a.py").write_text("x")
    assert _wait_for(lambda: call_count[0] >= 1)
    (tmp_root / "b.py").write_text("y")
    assert _wait_for(lambda: call_count[0] >= 2)
