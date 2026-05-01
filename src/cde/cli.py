# PYTHON_ARGCOMPLETE_OK
"""cde CLI entry point.

argparse with one subparser per verb. Each verb module exposes a
register(subparsers) function that wires up its subparser. New verbs
get added to _COMMANDS below — that's the only edit cli.py needs.

Tab completion: install once via
  eval "$(register-python-argcomplete cde)"
The PYTHON_ARGCOMPLETE_OK marker on line 1 is intentional.
"""

from __future__ import annotations

import argparse
import sys
from importlib import import_module
from typing import Any, Callable

from cde import __version__


# (subcommand-module-name, ) — keep alphabetised for readability.
# Each module must expose `register(subparsers)`.
_COMMANDS = [
    "init",
    "build",
    "run",
    "logs",
    "shell",
    "reap",
    "watch",
    "sync",
    "server",
    "history",
    "annotate",   # registers annotate / hypothesize / tag / untag
    "compare",
    "lineage",
    "defaults",
    "profile",
]


def set_completer(action: argparse.Action, completer: Callable[..., Any]) -> None:
  """Attach an argcomplete completer to an action.

  argcomplete monkey-patches argparse.Action with a `completer`
  attribute at runtime. mypy can't see that attribute through the
  static argparse stubs, so a plain `action.completer = ...` trips
  attr-defined. Using setattr keeps the assignment explicit and avoids
  scattering `# type: ignore` everywhere. The pattern is the same as
  xpk's _set_completer in src/xpk/parser/workload.py.
  """
  setattr(action, "completer", completer)


def _build_parser() -> argparse.ArgumentParser:
  parser = argparse.ArgumentParser(
      prog="cde",
      description="TPU/GPU iteration manager — build, submit, record, compare.",
  )
  parser.add_argument(
      "--version", action="version", version=f"cde {__version__}",
  )
  subparsers = parser.add_subparsers(
      dest="cmd",
      title="subcommands",
      required=True,
  )
  for name in _COMMANDS:
    mod = import_module(f"cde.commands.{name}")
    mod.register(subparsers)
  return parser


def main(argv: list[str] | None = None) -> int:
  parser = _build_parser()

  # Install argcomplete hook. Cheap when running normally; intercepts
  # if argcomplete is invoking us in completion mode.
  try:
    import argcomplete

    argcomplete.autocomplete(parser)
  except ImportError:
    pass

  args = parser.parse_args(argv)
  return args.func(args)


if __name__ == "__main__":
  sys.exit(main())
