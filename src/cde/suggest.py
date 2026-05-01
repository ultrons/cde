"""Did-you-mean helpers — used in error paths to nudge typo'd inputs.

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
