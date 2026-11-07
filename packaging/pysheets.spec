# PyInstaller spec for the Windows desktop build.
#   pyinstaller packaging/pysheets.spec --noconfirm
# Produces dist/PySheets/PySheets.exe (one-folder build: starts fast, no unpacking).
# -*- mode: python ; coding: utf-8 -*-
import importlib.util
import os
import pkgutil
import sys

from PyInstaller.utils.hooks import collect_all, collect_submodules

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))
WEB = os.path.join(ROOT, "src", "pysheets", "web")
if not os.path.exists(os.path.join(WEB, "index.html")):
    raise SystemExit("Build the UI first: npm --prefix frontend ci && npm --prefix frontend run build")

datas = [(WEB, "pysheets/web")]
# optional OAuth client for "Sign in with Google" (written by CI from a repository secret)
_CLIENT = os.path.join(ROOT, "src", "pysheets", "connectors", "google_client.json")
if os.path.exists(_CLIENT):
    datas.append((_CLIENT, "pysheets/connectors"))
binaries = []
hiddenimports = collect_submodules("pysheets")
# app runtime + libraries that in-app Python scripts commonly import (Google auth, gspread, pandas, ...)
for pkg in ("duckdb", "python_calamine", "rustpy_xlsxwriter", "webview", "xlsxwriter", "sqlalchemy",
            "google.auth", "google.oauth2", "google_auth_oauthlib", "requests_oauthlib", "oauthlib", "requests",
            "gspread", "gspread_dataframe", "openpyxl", "tqdm"):
    try:
        d, b, h = collect_all(pkg)
    except Exception:  # optional packages
        continue
    datas += d
    binaries += b
    hiddenimports += h
# in-app Python scripts run on this interpreter: ship the whole standard library, not just what PySheets imports
_SKIP_STDLIB = {"tkinter", "turtle", "turtledemo", "idlelib", "test", "lib2to3", "ensurepip", "venv", "pydoc_data",
                "this", "antigravity", "distutils"}


def _stdlib_modules():
    out = []
    for name in sorted(sys.stdlib_module_names):
        if name in _SKIP_STDLIB or name.startswith("_"):
            continue
        try:
            spec = importlib.util.find_spec(name)
        except Exception:
            continue
        if spec is None:  # e.g. POSIX-only modules on Windows
            continue
        out.append(name)
        for info in pkgutil.walk_packages(spec.submodule_search_locations or [], name + ".", onerror=lambda _n: None):
            if not any(part in ("test", "tests", "idle_test") for part in info.name.split(".")):
                out.append(info.name)
    return out


hiddenimports += _stdlib_modules()
hiddenimports += ["pyarrow", "pyarrow.lib", "clr", "webview.platforms.edgechromium", "webview.platforms.winforms"]

a = Analysis(
    [os.path.join(SPECPATH, "launcher.py")],
    pathex=[os.path.join(ROOT, "src")],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["tkinter", "matplotlib", "IPython", "pytest", "pandas.tests", "numpy.tests", "pyarrow.tests",
              "pyarrow.flight", "pyarrow._flight"],
    noarchive=False,
)
# trim pieces of pyarrow the app never loads (Arrow Flight RPC, C++ headers, tests)
_DROP = ("arrow_flight", "pyarrow/include", "pyarrow\\include", "pyarrow/tests", "pyarrow\\tests")
a.binaries = [b for b in a.binaries if not any(x in b[0] for x in _DROP)]
a.datas = [d for d in a.datas if not any(x in d[0] for x in _DROP)]
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="PySheets",
    icon=os.path.join(SPECPATH, "pysheets.ico"),
    console=False,
    upx=False,
    version=None,
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="PySheets")
