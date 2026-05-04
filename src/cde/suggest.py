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

Did-you-mean helpers — used in error paths to nudge typo'd inputs.

Pulls candidate values from the run history (via cde.db.list_runs) and
ranks them with difflib.get_close_matches.
"""
from __future__ import annotations

import difflib
from typing import Iterable


def closest(value: str, candidates: Iterable[str], *, n: int = 3) -> list[str]:
  """Return up to `n` closest matches to `value` among `candidates`."""
  return difflib.get_close_matches(value, list(candidates), n=n, cutoff=0.5)


def hint(value: str, candidates: Iterable[str]) -> str:
  """Return a ' Did you mean: a, b?' suffix string, or empty if nothing close.

  Designed to append onto error messages directly.
  """
  m = closest(value, candidates)
  if not m:
    return ""
  return f" Did you mean: {', '.join(m)}?"
