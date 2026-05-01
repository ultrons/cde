"""Allow running cde via `python -m cde`."""

from cde.cli import main

if __name__ == "__main__":
  raise SystemExit(main())
