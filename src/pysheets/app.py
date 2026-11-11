"""Desktop launcher: starts the engine + local server and opens a native window (WebView2 on Windows)."""

from __future__ import annotations

import argparse
import logging
import os
import sys
import webbrowser

from . import __version__
from .engine import io as IO
from .engine.workbook import Workbook
from .server import make_server, serve_in_thread, window_title

log = logging.getLogger("pysheets")


def build_workbook(files: list[str]) -> Workbook:
    wb = Workbook()
    opened = False
    for f in files:
        try:
            if f.lower().endswith(".pysheets"):
                IO.open_workbook(wb, f)
            else:
                IO.import_file(wb, f)
            opened = True
        except Exception as e:  # pragma: no cover - reported in the UI log
            log.error("Could not open %s: %s", f, e)
    if not wb.sheets:
        wb.add_sheet("Sheet1", record=False)
    elif opened and wb.path is None:
        wb.title = os.path.splitext(os.path.basename(files[0]))[0]
    wb.undo_stack.clear()
    wb.dirty = False
    return wb


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] == ["--run-python"]:
        # bundled interpreter for "file" scripts when no system Python is installed
        from .scripting import run_python_file

        return run_python_file(argv[1:])
    ap = argparse.ArgumentParser(
        prog="pysheets", description="PySheets: a fast desktop spreadsheet on DuckDB"
    )
    ap.add_argument(
        "files", nargs="*", help="files to open (csv, xlsx, parquet, json, sqlite, duckdb, .pysheets)"
    )
    ap.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    ap.add_argument(
        "--browser", action="store_true", help="open in the default web browser instead of a window"
    )
    ap.add_argument("--serve", action="store_true", help="only run the server (no window, no browser)")
    ap.add_argument("--port", type=int, default=0, help="port for the local server (default: random)")
    ap.add_argument(
        "--dev", action="store_true", help="development mode: no auth token (for the Vite dev server)"
    )
    ap.add_argument("--debug", action="store_true", help="enable webview devtools and verbose logs")
    args = ap.parse_args(argv)

    level = logging.DEBUG if args.debug else logging.INFO
    fmt = "%(asctime)s %(levelname)s %(message)s"
    if getattr(sys, "frozen", False) or sys.stderr is None:
        # windowed build: no console, so log next to the user's app data
        log_dir = os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"), "PySheets")
        os.makedirs(log_dir, exist_ok=True)
        logging.basicConfig(level=level, format=fmt, filename=os.path.join(log_dir, "pysheets.log"))
    else:
        logging.basicConfig(level=level, format=fmt)
    wb = build_workbook(args.files)
    server = make_server(wb, port=args.port or (8765 if args.dev else 0), token=None if args.dev else "auto")
    url = server.url
    if args.serve or args.dev:
        print(
            f"PySheets server running at {url}" + (f"?token={server.token}" if server.token else ""),
            flush=True,
        )
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        return 0
    serve_in_thread(server)
    if args.browser:
        webbrowser.open(url)
        print(f"PySheets running at {url} (Ctrl+C to quit)", flush=True)
        try:
            import time

            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            return 0
    try:
        import webview
    except ImportError:
        print("pywebview is not installed; opening in your browser instead.", file=sys.stderr)
        webbrowser.open(url)
        server.serve_forever()
        return 0
    window = webview.create_window(
        window_title(wb),
        url,
        width=1440,
        height=900,
        min_size=(960, 600),
        background_color="#FFFFFF",
        text_select=True,
        confirm_close=False,
        localization={"global.quitConfirmation": "You have unsaved changes. Quit PySheets anyway?"},
    )
    # The server flips window.confirm_close whenever the workbook becomes dirty/clean,
    # so pywebview's own close prompt only appears when there is something to lose.
    server.window = window
    server._window_title = window_title(wb)

    def _focus_after_google_sign_in():
        # the user finished signing in in their browser: bring PySheets back to the front
        try:
            window.restore()
            window.on_top = True
            window.on_top = False
        except Exception:  # noqa: BLE001 - cosmetic
            pass

    from .connectors.google import SIGN_IN

    SIGN_IN.on_done = _focus_after_google_sign_in
    webview.start(debug=args.debug, private_mode=False)
    server.shutdown()
    wb.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
