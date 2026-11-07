"""Entry point used by PyInstaller (keeps `pysheets` importable as a package)."""

from pysheets.app import main

if __name__ == "__main__":
    raise SystemExit(main())
