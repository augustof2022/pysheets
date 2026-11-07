# PyInstaller spec for the Windows desktop build.
#   pyinstaller packaging/pysheets.spec --noconfirm
# Produces dist/PySheets/PySheets.exe (one-folder build: starts fast, no unpacking).
# -*- mode: python ; coding: utf-8 -*-
import os

from PyInstaller.utils.hooks import collect_all, collect_submodules

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))
WEB = os.path.join(ROOT, "src", "pysheets", "web")
if not os.path.exists(os.path.join(WEB, "index.html")):
    raise SystemExit("Build the UI first: npm --prefix frontend ci && npm --prefix frontend run build")

datas = [(WEB, "pysheets/web")]
binaries = []
hiddenimports = collect_submodules("pysheets")
for pkg in ("duckdb", "python_calamine", "rustpy_xlsxwriter", "webview", "xlsxwriter", "sqlalchemy"):
    try:
        d, b, h = collect_all(pkg)
    except Exception:  # optional packages
        continue
    datas += d
    binaries += b
    hiddenimports += h
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
