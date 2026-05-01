"""Tiny coloured logger. Stderr only — stdout is reserved for actual data
(JSON output, etc.) so users can pipe `cde history --json | jq`."""

from __future__ import annotations

import os
import sys
from typing import Any


_USE_COLOR = sys.stderr.isatty() and os.environ.get("NO_COLOR") is None

_RESET = "\033[0m" if _USE_COLOR else ""
_BOLD = "\033[1m" if _USE_COLOR else ""
_BLUE = "\033[34m" if _USE_COLOR else ""
_GREEN = "\033[32m" if _USE_COLOR else ""
_YELLOW = "\033[33m" if _USE_COLOR else ""
_RED = "\033[31m" if _USE_COLOR else ""
_GREY = "\033[90m" if _USE_COLOR else ""


def info(msg: str, *args: Any) -> None:
  print(f"{_BLUE}info{_RESET} {msg % args if args else msg}", file=sys.stderr)


def step(msg: str, *args: Any) -> None:
  """A step in a multi-step verb (e.g. 'building', 'pushing', 'applying')."""
  print(
      f"{_BOLD}{_BLUE}→{_RESET} {msg % args if args else msg}", file=sys.stderr
  )


def ok(msg: str, *args: Any) -> None:
  print(
      f"{_GREEN}ok{_RESET}   {msg % args if args else msg}", file=sys.stderr
  )


def warn(msg: str, *args: Any) -> None:
  print(
      f"{_YELLOW}warn{_RESET} {msg % args if args else msg}", file=sys.stderr
  )


def err(msg: str, *args: Any) -> None:
  print(f"{_RED}err{_RESET}  {msg % args if args else msg}", file=sys.stderr)


def detail(msg: str, *args: Any) -> None:
  """Secondary information; muted color."""
  print(
      f"     {_GREY}{msg % args if args else msg}{_RESET}", file=sys.stderr
  )
