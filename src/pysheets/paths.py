"""Where PySheets keeps the user's files: ``Documents\\PySheets`` (exports in ``Documents\\PySheets\\Exports``)."""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

FOLDER_NAME = "PySheets"


def _windows_documents() -> Path | None:
    """The real Documents folder (follows OneDrive / redirected folders)."""
    try:
        import ctypes
        from ctypes import wintypes

        class GUID(ctypes.Structure):
            _fields_ = [
                ("Data1", wintypes.DWORD),
                ("Data2", wintypes.WORD),
                ("Data3", wintypes.WORD),
                ("Data4", ctypes.c_ubyte * 8),
            ]

        folderid_documents = GUID(
            0xFDD39AD0, 0x238F, 0x46AF, (ctypes.c_ubyte * 8)(0xAD, 0xB4, 0x6C, 0x85, 0x48, 0x03, 0x69, 0xC7)
        )
        out = ctypes.c_wchar_p()
        shell32 = ctypes.windll.shell32  # type: ignore[attr-defined]
        if shell32.SHGetKnownFolderPath(ctypes.byref(folderid_documents), 0, None, ctypes.byref(out)) != 0:
            return None
        try:
            return Path(out.value) if out.value else None
        finally:
            ctypes.windll.ole32.CoTaskMemFree(out)  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001 - fall back to ~/Documents
        return None


def documents_dir() -> Path:
    override = os.environ.get("PYSHEETS_DOCUMENTS")
    if override:
        return Path(override)
    if sys.platform == "win32":
        found = _windows_documents()
        if found:
            return found
    if sys.platform.startswith("linux"):
        # XDG user dirs ("Documents" may be localised)
        cfg = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "user-dirs.dirs"
        try:
            m = re.search(r'^XDG_DOCUMENTS_DIR="?([^"\n]+)"?', cfg.read_text(encoding="utf-8"), re.M)
            if m:
                return Path(m.group(1).replace("$HOME", str(Path.home())))
        except OSError:
            pass
    return Path.home() / "Documents"


def workbooks_dir() -> Path:
    """``Documents/PySheets`` - created on first use."""
    d = documents_dir() / FOLDER_NAME
    d.mkdir(parents=True, exist_ok=True)
    return d


def exports_dir() -> Path:
    d = workbooks_dir() / "Exports"
    d.mkdir(parents=True, exist_ok=True)
    return d


def safe_name(name: str, default: str = "Untitled spreadsheet") -> str:
    name = re.sub(r'[\\/:*?"<>|\x00-\x1f]+', "_", (name or "").strip()).strip(" .")
    return name or default


def unique_path(folder: Path, name: str) -> Path:
    """``folder/name``, or ``name (2)``, ``name (3)``… if it exists."""
    p = folder / name
    stem, suffix = p.stem, p.suffix
    n = 2
    while p.exists():
        p = folder / f"{stem} ({n}){suffix}"
        n += 1
    return p


def reveal(path: Path) -> None:
    """Open a folder in the file manager (or select a file in it)."""
    path = Path(path)
    if sys.platform == "win32":
        if path.is_file():
            subprocess.Popen(["explorer", "/select,", str(path)])  # noqa: S603,S607
        else:
            os.startfile(str(path))  # type: ignore[attr-defined]  # noqa: S606
    elif sys.platform == "darwin":
        subprocess.Popen(["open", "-R", str(path)] if path.is_file() else ["open", str(path)])  # noqa: S603,S607
    else:
        subprocess.Popen(["xdg-open", str(path if path.is_dir() else path.parent)])  # noqa: S603,S607
