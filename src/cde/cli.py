"""cde CLI entry point.

argparse with one subparser per verb. Each verb module exposes a
register(subparsers) function that wires up its subparser. New verbs
get added to _COMMANDS below — that's the only edit cli.py needs.
"""

from __future__ import annotations

import argparse
import sys
from importlib import import_module

from cde import __version__


# (subcommand-module-name, ) — keep alphabetised for readability.
# Each module must expose `register(subparsers)`.
_COMMANDS = [
    "init",
    # later: build, run, history, annotate, compare, sync, watch, server, profile
]


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
  args = parser.parse_args(argv)
  return args.func(args)


if __name__ == "__main__":
  sys.exit(main())
