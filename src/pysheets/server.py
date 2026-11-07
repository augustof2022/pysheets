"""Local HTTP server: serves the UI and a token-protected JSON-RPC endpoint on 127.0.0.1."""

from __future__ import annotations

import json
import logging
import mimetypes
import os
import secrets
import shutil
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import api
from .engine.workbook import Workbook

log = logging.getLogger("pysheets")
WEB_DIR = Path(__file__).parent / "web"


class AppServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, addr, wb: Workbook, token: str | None, web_dir: Path = WEB_DIR):
        super().__init__(addr, Handler)
        self.wb = wb
        self.token = token
        self.web_dir = web_dir
        self.window = None  # pywebview window, when running as a desktop app
        self.upload_dir = Path(wb._tmpdir) / "uploads"
        self.upload_dir.mkdir(parents=True, exist_ok=True)

    @property
    def url(self) -> str:
        host, port = self.server_address[:2]
        return f"http://{host}:{port}/"


class Handler(BaseHTTPRequestHandler):
    server: AppServer
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):  # quiet
        log.debug("%s - %s", self.address_string(), fmt % args)

    # ------------------------------------------------------------ helpers

    def _send(self, status: int, body: bytes, ctype: str = "application/json", extra: dict | None = None):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header(
            "Cache-Control",
            "no-store"
            if ctype.startswith(("application/json", "text/html"))
            else "public, max-age=31536000, immutable",
        )
        self.send_header("X-Content-Type-Options", "nosniff")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, obj, status: int = 200):
        body = json.dumps(obj, default=str, separators=(",", ":"), allow_nan=False).encode()
        self._send(status, body)

    def _authorized(self, query: dict | None = None) -> bool:
        if self.server.token is None:
            return True
        tok = self.headers.get("X-PySheets-Token")
        if tok is None and query is not None:
            tok = (query.get("token") or [None])[0]
        return tok is not None and secrets.compare_digest(tok, self.server.token)

    def _read_body(self) -> bytes:
        n = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(n) if n else b""

    # --------------------------------------------------------------- GET

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        query = urllib.parse.parse_qs(parsed.query)
        path = parsed.path
        if path == "/api/progress":
            if not self._authorized(query):
                return self._json({"error": "unauthorized"}, 401)
            p = dict(self.server.wb.progress)
            if p.get("active"):
                try:
                    p["query"] = self.server.wb.con.query_progress()
                except Exception:
                    p["query"] = None
            return self._json(p)
        if path == "/api/download":
            if not self._authorized(query):
                return self._json({"error": "unauthorized"}, 401)
            return self._download(query)
        if path.startswith("/api/"):
            return self._json({"error": "not found"}, 404)
        return self._static(path)

    do_HEAD = do_GET

    def _static(self, path: str):
        web = self.server.web_dir.resolve()
        rel = path.lstrip("/") or "index.html"
        target = (web / rel).resolve()
        if not str(target).startswith(str(web)) or not target.is_file():
            target = web / "index.html"
            if not target.is_file():
                return self._send(404, b"UI not built. Run: npm --prefix frontend run build", "text/plain")
        data = target.read_bytes()
        ctype = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        if target.name == "index.html":
            cfg = json.dumps({"token": self.server.token, "desktop": self.server.window is not None})
            data = data.replace(b"<head>", f"<head><script>window.__PYSHEETS__={cfg}</script>".encode(), 1)
            ctype = "text/html; charset=utf-8"
        if ctype.startswith("text/") or ctype in ("application/javascript",):
            if "charset" not in ctype:
                ctype += "; charset=utf-8"
        self._send(200, data, ctype)

    def _download(self, query):
        path = (query.get("path") or [""])[0]
        p = Path(path).resolve()
        tmp = Path(self.server.wb._tmpdir).resolve()
        if not str(p).startswith(str(tmp)) or not p.is_file():
            return self._json({"error": "not found"}, 404)
        size = p.stat().st_size
        self.send_response(200)
        self.send_header("Content-Type", mimetypes.guess_type(p.name)[0] or "application/octet-stream")
        self.send_header("Content-Length", str(size))
        self.send_header("Content-Disposition", f'attachment; filename="{p.name}"')
        self.end_headers()
        with open(p, "rb") as fh:
            shutil.copyfileobj(fh, self.wfile, 1 << 20)

    # -------------------------------------------------------------- POST

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        query = urllib.parse.parse_qs(parsed.query)
        if not self._authorized():
            return self._json({"ok": False, "error": "unauthorized"}, 401)
        if parsed.path == "/api/rpc":
            try:
                req = json.loads(self._read_body() or b"{}")
            except json.JSONDecodeError:
                return self._json({"ok": False, "error": "bad json"}, 400)
            ctx = {"window": self.server.window}
            res = api.dispatch(self.server.wb, req.get("method", ""), req.get("params") or {}, ctx)
            if self.server.window is not None:
                try:
                    self.server.window.confirm_close = bool(self.server.wb.dirty)
                except Exception:  # pragma: no cover - window already closed
                    pass
            return self._json(_clean(res))
        if parsed.path == "/api/upload":
            name = os.path.basename((query.get("name") or ["upload.bin"])[0]) or "upload.bin"
            folder = self.server.upload_dir / secrets.token_hex(6)
            folder.mkdir(parents=True, exist_ok=True)
            dest = folder / name  # keep the original name: it becomes the sheet name
            remaining = int(self.headers.get("Content-Length") or 0)
            with open(dest, "wb") as fh:
                while remaining > 0:
                    chunk = self.rfile.read(min(remaining, 1 << 20))
                    if not chunk:
                        break
                    fh.write(chunk)
                    remaining -= len(chunk)
            return self._json({"ok": True, "path": str(dest)})
        return self._json({"ok": False, "error": "not found"}, 404)


def _clean(obj):
    """Replace NaN/inf (not valid JSON) recursively."""
    if isinstance(obj, float):
        return obj if obj == obj and obj not in (float("inf"), float("-inf")) else None
    if isinstance(obj, dict):
        return {k: _clean(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_clean(v) for v in obj]
    return obj


def make_server(
    wb: Workbook, port: int = 0, token: str | None = "auto", web_dir: Path = WEB_DIR
) -> AppServer:
    if token == "auto":
        token = secrets.token_urlsafe(24)
    return AppServer(("127.0.0.1", port), wb, token, web_dir)


def serve_in_thread(server: AppServer) -> threading.Thread:
    t = threading.Thread(target=server.serve_forever, name="pysheets-http", daemon=True)
    t.start()
    return t
