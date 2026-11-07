"""Command-line entry point for pysheets."""

from __future__ import annotations


def main(argv: list[str] | None = None) -> int:
    from .app import main as app_main

    return app_main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
