"""Command-line entry point for pysheets."""

from __future__ import annotations

import argparse

from pysheets import __version__


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="pysheets", description=__doc__)
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.parse_args(argv)
    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
