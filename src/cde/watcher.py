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

Debounced multi-path file watcher used by `cde watch` and `cde sync`.

Wraps watchdog. Two design choices worth knowing:

  - Coalescing window: rapid-fire saves (e.g. four files saved in a row
    by an editor's "save all") fire one callback per debounce window
    (default 500 ms). The callback receives the *batch* of changed
    paths, not one per change.

  - Caller-controlled action: the watcher just notices changes and
    invokes a callback. It doesn't know whether you want to print a
    message (cde watch) or kubectl-cp (cde sync) or rebuild
    (hypothetical cde watch --auto). That's the caller's job.

Returns when the user Ctrl-C's. Robust to symlinks; ignores files
matched by a `.dockerignore` if one is found at the watched root.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

# watchdog is a real dependency in pyproject.toml, but cde is sometimes
# imported from a vendored / partially-installed environment (the failure
# mode another agent hit). Guard the import so unrelated verbs like
# `cde history` keep working; only `cde watch` / `cde sync` need watchdog
# at runtime.
try:
  from watchdog.events import FileSystemEvent, FileSystemEventHandler
  from watchdog.observers import Observer

  _WATCHDOG_IMPORT_ERROR: ImportError | None = None
except ImportError as _exc:
  _WATCHDOG_IMPORT_ERROR = _exc

  class FileSystemEventHandler:  # type: ignore[no-redef]
    pass

  FileSystemEvent = object  # type: ignore[assignment, misc]

  class Observer:  # type: ignore[no-redef]
    def __init__(self, *_args: object, **_kwargs: object) -> None:
      raise RuntimeError(
          "cde watch / cde sync require the 'watchdog' package; "
          "reinstall cde with: pip install -e ."
      ) from _WATCHDOG_IMPORT_ERROR


ChangeCallback = Callable[[list[Path]], None]


@dataclass
class WatchPath:
  """A local path to watch. `dest` is informational — used by sync to
  derive in-pod path when the watcher fires. Absent for cde watch."""

  src: Path
  dest: str = ""


class _DebouncedHandler(FileSystemEventHandler):
  """Coalesces rapid-fire fs events into one batch per debounce window."""

  def __init__(
      self,
      *,
      callback: ChangeCallback,
      debounce_ms: int,
      ignore_substrings: tuple[str, ...] = ("__pycache__/", ".pyc"),
  ):
    self._callback = callback
    self._debounce_s = max(0.05, debounce_ms / 1000.0)
    self._ignore = ignore_substrings
    self._pending: set[Path] = set()
    self._lock = threading.Lock()
    self._timer: threading.Timer | None = None

  def on_any_event(self, event: FileSystemEvent) -> None:
    if event.is_directory:
      return
    if event.event_type not in ("created", "modified", "moved"):
      return
    # watchdog types src_path as bytes | str depending on the platform.
    src_path = event.src_path
    if isinstance(src_path, bytes):
      src_path = src_path.decode()
    p = Path(src_path)
    s = str(p)
    if any(sub in s for sub in self._ignore):
      return
    with self._lock:
      self._pending.add(p)
      if self._timer is not None:
        self._timer.cancel()
      self._timer = threading.Timer(self._debounce_s, self._fire)
      self._timer.daemon = True
      self._timer.start()

  def _fire(self) -> None:
    with self._lock:
      batch = sorted(self._pending)
      self._pending.clear()
      self._timer = None
    if batch:
      try:
        self._callback(batch)
      except Exception as exc:  # pylint: disable=broad-except
        # Don't let a buggy callback kill the watcher.
        import traceback

        traceback.print_exc()
        del exc


class Watcher:
  """Holds an Observer + handler. Use `start()` then block, or use the
  context-manager form."""

  def __init__(
      self,
      paths: Iterable[WatchPath | Path | str],
      *,
      callback: ChangeCallback,
      debounce_ms: int = 500,
  ):
    self._paths: list[WatchPath] = []
    for p in paths:
      if isinstance(p, WatchPath):
        self._paths.append(p)
      else:
        self._paths.append(WatchPath(src=Path(p)))
    self._handler = _DebouncedHandler(
        callback=callback, debounce_ms=debounce_ms
    )
    self._observer = Observer()

  def start(self) -> None:
    for wp in self._paths:
      if wp.src.is_dir():
        self._observer.schedule(self._handler, str(wp.src), recursive=True)
      elif wp.src.exists():
        # Watch the parent dir; filter to this single file in callback.
        self._observer.schedule(
            self._handler, str(wp.src.parent), recursive=False
        )
    self._observer.start()

  def stop(self) -> None:
    self._observer.stop()
    self._observer.join(timeout=2.0)

  def __enter__(self) -> "Watcher":
    self.start()
    return self

  def __exit__(self, exc_type, exc, tb) -> None:
    self.stop()


def block_forever() -> None:
  """Block the current thread until Ctrl-C. The watcher runs on its own
  thread; this just keeps the process alive."""
  try:
    while True:
      time.sleep(60)
  except KeyboardInterrupt:
    pass
