"""Google Sheets connector.

Every Google feature requires a sign-in first. Ways to sign in:

* **Sign in with Google** - the browser OAuth flow (desktop "installed app" client).  The
  client comes from the user's ``credentials.json`` (the same file Python scripts built on
  ``google-auth-oauthlib`` use) or from a client bundled with the build.
* **Token from existing scripts** - reuse a ``token.json`` created by those scripts.
* **Service account** JSON key (share the sheets with the service account e-mail).

Access is read-only (``spreadsheets.readonly``) plus the account e-mail for display.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import threading
import time
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

SHEETS_SCOPE = "https://www.googleapis.com/auth/spreadsheets.readonly"
SCOPES = [SHEETS_SCOPE]
SIGN_IN_SCOPES = ["openid", "https://www.googleapis.com/auth/userinfo.email", SHEETS_SCOPE]
API = "https://sheets.googleapis.com/v4/spreadsheets"
SIGN_IN_TIMEOUT = 300  # seconds to finish the browser sign-in

# Google may return the scopes in another form (e.g. "email" for userinfo.email)
os.environ.setdefault("OAUTHLIB_RELAX_TOKEN_SCOPE", "1")


class GoogleError(Exception):
    pass


class GoogleAuthRequired(GoogleError):
    """Raised when a Google feature is used before signing in (or after the sign-in expired)."""

    code = "google_auth"

    def __init__(self, message: str = "Sign in to Google first (Data > Google Sheets > Sign in with Google)"):
        super().__init__(message)


def app_data_dir() -> Path:
    base = (
        os.environ.get("PYSHEETS_HOME")
        or os.environ.get("LOCALAPPDATA")
        or os.path.join(os.path.expanduser("~"), ".local", "share")
    )
    d = Path(base) / (
        "PySheets" if "LOCALAPPDATA" in os.environ and "PYSHEETS_HOME" not in os.environ else "pysheets"
    )
    d.mkdir(parents=True, exist_ok=True)
    return d


def google_dir() -> Path:
    d = app_data_dir() / "google"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _read_json(path: Path) -> dict | None:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8-sig"))  # Notepad may add a BOM
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _write_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _forget_sign_in(d: Path) -> None:
    for name in ("token.json", "service_account.json", "account.json"):
        try:
            (d / name).unlink()
        except FileNotFoundError:
            pass


def _revoke(token: str) -> None:
    try:
        import requests

        requests.post("https://oauth2.googleapis.com/revoke", params={"token": token}, timeout=10)
    except Exception:  # noqa: BLE001 - best effort
        pass


def _is_client(data: dict | None) -> bool:
    return bool(data) and bool({"installed", "web"} & set(data or {}))


def oauth_client_config() -> tuple[dict | None, str | None]:
    """The OAuth client used by "Sign in with Google": the user's file, else one bundled with the build."""
    data = _read_json(google_dir() / "credentials.json")
    if _is_client(data):
        return data, "file"
    env = os.environ.get("PYSHEETS_GOOGLE_CLIENT", "").strip()
    if env:
        try:
            data = json.loads(env) if env.startswith("{") else _read_json(Path(env))
        except ValueError:
            data = None
        if _is_client(data):
            return data, "bundled"
    data = _read_json(Path(__file__).with_name("google_client.json"))
    if _is_client(data):
        return data, "bundled"
    return None, None


def _id_token_email(id_token: str | None) -> str | None:
    """E-mail claim of an ID token received directly from Google's token endpoint (over TLS)."""
    if not id_token or id_token.count(".") != 2:
        return None
    import base64

    part = id_token.split(".")[1]
    try:
        claims = json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))
    except (ValueError, TypeError):
        return None
    return claims.get("email")


# ------------------------------------------------------------------- browser sign-in

_PAGE = """<!doctype html><html><head><meta charset="utf-8"><title>PySheets</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
body{{margin:0;height:100vh;display:grid;place-items:center;background:#f5f6fa;color:#111827;
font:15px/1.5 "Segoe UI",system-ui,sans-serif}}
@media (prefers-color-scheme:dark){{body{{background:#0f1117;color:#e5e7eb}}.c{{background:#181b24}}}}
.c{{background:#fff;border-radius:16px;padding:36px 40px;max-width:420px;text-align:center;
box-shadow:0 10px 40px rgba(0,0,0,.08)}}
.i{{width:52px;height:52px;border-radius:50%;display:grid;place-items:center;margin:0 auto 14px;
font-size:26px;color:#fff;background:{color}}}
h1{{font-size:20px;margin:0 0 6px}}p{{margin:0;opacity:.75}}
</style></head><body><div class="c"><div class="i">{icon}</div><h1>{title}</h1><p>{text}</p></div>
</body></html>"""


def _page(ok: bool, text: str) -> bytes:
    import html

    return _PAGE.format(
        color="#16a34a" if ok else "#dc2626",
        icon="&#10003;" if ok else "!",
        title="You're signed in" if ok else "Sign-in didn't finish",
        text=html.escape(text),
    ).encode("utf-8")


class SignIn:
    """ "Sign in with Google": an OAuth loopback flow run in the background so the UI can poll or cancel.

    The browser is sent to Google's consent page; Google redirects to ``http://127.0.0.1:<port>/``
    where a one-shot server exchanges the code for a token (PKCE) and saves it.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._cancel = threading.Event()
        self._state: dict[str, Any] = {"pending": False, "auth_url": None, "error": None, "email": None}
        self.on_done: Callable[[], None] | None = None  # e.g. bring the app window to the front

    def state(self) -> dict:
        with self._lock:
            return dict(self._state)

    def _set(self, **kw: Any) -> None:
        with self._lock:
            self._state.update(kw)

    def start(
        self, open_browser: Callable[[str], Any] | None = None, flow_factory: Callable | None = None
    ) -> dict:
        with self._lock:
            if self._state["pending"] and self._thread is not None and self._thread.is_alive():
                return dict(self._state)
        from http.server import HTTPServer

        if flow_factory is None:
            cfg, _ = oauth_client_config()
            if cfg is None:
                raise GoogleError("Choose your OAuth client file (credentials.json) first")
        server = HTTPServer(("127.0.0.1", 0), _CallbackHandler)
        redirect = f"http://127.0.0.1:{server.server_address[1]}/"
        try:
            if flow_factory is not None:
                flow = flow_factory(redirect)
            else:
                from google_auth_oauthlib.flow import Flow

                flow = Flow.from_client_config(cfg, scopes=SIGN_IN_SCOPES, redirect_uri=redirect)
            auth_url, expected = flow.authorization_url(
                access_type="offline", prompt="consent select_account"
            )
        except Exception:
            server.server_close()
            raise
        server.flow = flow  # type: ignore[attr-defined]
        server.expected_state = expected  # type: ignore[attr-defined]
        server.owner = self  # type: ignore[attr-defined]
        server.finished = False  # type: ignore[attr-defined]
        self._cancel = threading.Event()
        self._set(pending=True, auth_url=auth_url, error=None, email=None)
        self._thread = threading.Thread(target=self._serve, args=(server, self._cancel), daemon=True)
        self._thread.start()
        (open_browser or _open_browser)(auth_url)
        return self.state()

    def reopen(self) -> dict:
        st = self.state()
        if st["pending"] and st["auth_url"]:
            _open_browser(st["auth_url"])
        return st

    def cancel(self) -> dict:
        self._cancel.set()
        self._set(pending=False, error=None)
        return self.state()

    def _serve(self, server, cancel: threading.Event) -> None:
        deadline = time.monotonic() + SIGN_IN_TIMEOUT
        server.timeout = 0.25
        try:
            while not server.finished and not cancel.is_set():
                if time.monotonic() > deadline:
                    self._set(pending=False, error="Sign-in timed out - try again")
                    return
                server.handle_request()
        finally:
            server.server_close()

    def _complete(self, flow, authorization_response: str) -> str | None:
        flow.fetch_token(authorization_response=authorization_response)
        creds = flow.credentials
        token = getattr(getattr(flow, "oauth2session", None), "token", None) or {}
        email = _id_token_email(token.get("id_token"))
        d = google_dir()
        _forget_sign_in(d)
        (d / "token.json").write_text(creds.to_json(), encoding="utf-8")
        stamp = datetime.now().isoformat(timespec="seconds")
        _write_json(d / "account.json", {"email": email, "method": "oauth", "signed_in_at": stamp})
        self._set(pending=False, error=None, email=email)
        if self.on_done:
            try:
                self.on_done()
            except Exception:  # noqa: BLE001
                pass
        return email


def _open_browser(url: str) -> None:
    import webbrowser

    webbrowser.open(url, new=1, autoraise=True)


def _make_handler():
    from http.server import BaseHTTPRequestHandler
    from urllib.parse import parse_qs, urlparse

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):  # keep the console quiet
            pass

        def _reply(self, body: bytes, status: int = 200) -> None:
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):  # noqa: N802
            srv = self.server
            q = parse_qs(urlparse(self.path).query)
            if "code" not in q and "error" not in q:
                return self._reply(b"", 404)  # favicon and friends
            owner: SignIn = srv.owner  # type: ignore[attr-defined]
            if q.get("state", [None])[0] != srv.expected_state:  # type: ignore[attr-defined]
                return self._reply(
                    _page(False, "This sign-in link is out of date. Start again from PySheets.")
                )
            if "error" in q:
                err = q["error"][0]
                msg = "You cancelled the sign-in." if err == "access_denied" else f"Google said: {err}"
                owner._set(pending=False, error=msg)
                srv.finished = True  # type: ignore[attr-defined]
                return self._reply(_page(False, msg))
            port = srv.server_address[1]
            try:
                email = owner._complete(srv.flow, f"https://127.0.0.1:{port}{self.path}")  # type: ignore[attr-defined]
            except Exception as e:  # noqa: BLE001
                msg = f"Couldn't finish signing in: {e}"
                owner._set(pending=False, error=msg)
                srv.finished = True  # type: ignore[attr-defined]
                return self._reply(_page(False, msg))
            srv.finished = True  # type: ignore[attr-defined]
            who = f" as {email}" if email else ""
            self._reply(
                _page(True, f"PySheets is connected{who}. You can close this tab and go back to the app.")
            )

    return Handler


_CallbackHandler = _make_handler()
SIGN_IN = SignIn()


def extract_sheet_id(text: str) -> str:
    """Accept a spreadsheet URL, an ``/d/<id>`` fragment or a bare ID."""
    t = str(text or "").strip()
    for pat in (r"/spreadsheets/d/([a-zA-Z0-9-_]+)", r"/d/([a-zA-Z0-9-_]+)", r"[?&]id=([a-zA-Z0-9-_]+)"):
        m = re.search(pat, t)
        if m:
            return m.group(1)
    return t


def clean_header(value: Any) -> str:
    """Header key used for matching: case-, whitespace- and newline-insensitive."""
    return " ".join(str(value if value is not None else "").split()).upper()


def _a1_tab(tab: str) -> str:
    return "'" + tab.replace("'", "''") + "'"


# --------------------------------------------------------------------------- client


class GoogleClient:
    """Thin REST client for the Sheets API with retries.  Build one with :meth:`connect`."""

    def __init__(self, session=None):
        self.session = session  # google.auth AuthorizedSession
        self.max_retries = 5

    # -- account state ------------------------------------------------------------

    @staticmethod
    def status(verify: bool = False) -> dict:
        """Sign-in state for the UI.  ``verify`` checks the saved token with Google."""
        d = google_dir()
        account = _read_json(d / "account.json") or {}
        service = d / "service_account.json"
        token = d / "token.json"
        cfg, source = oauth_client_config()
        info: dict[str, Any] = {
            "folder": str(d),
            "signed_in": False,
            "email": None,
            "method": None,
            "has_client": cfg is not None,
            "client_source": source,
            "sign_in": SIGN_IN.state(),
        }
        if service.exists():
            data = _read_json(service) or {}
            info.update(signed_in=True, method="service_account", email=data.get("client_email"))
        elif token.exists():
            info.update(signed_in=True, method=account.get("method") or "oauth", email=account.get("email"))
        if verify and info["signed_in"]:
            try:
                GoogleClient.connect()
            except GoogleAuthRequired as e:
                info.update(signed_in=False, method=None, email=None, expired=str(e))
            except GoogleError as e:
                info["warning"] = str(e)  # offline etc.: keep the saved sign-in
        return info

    @staticmethod
    def setup(
        folder: str | None = None,
        client_secret: str | None = None,
        token: str | None = None,
        service_account: str | None = None,
    ) -> dict:
        """Copy credentials into PySheets' own folder (originals are left untouched).

        A ``token.json`` or service-account key signs in immediately (after a check with
        Google); an OAuth client file (``credentials.json``) enables "Sign in with Google".
        """
        d = google_dir()
        if folder:
            f = Path(folder)
            if f.is_file():
                f = f.parent
            if not (f / "credentials.json").exists() and not (f / "token.json").exists():
                raise GoogleError(f"No credentials.json or token.json found in {f}")
            if (f / "credentials.json").exists():
                client_secret = str(f / "credentials.json")
            if (f / "token.json").exists():
                token = str(f / "token.json")
        if client_secret:
            data = _read_json(Path(client_secret))
            if not data or not ({"installed", "web"} & set(data)):
                raise GoogleError("That file is not an OAuth client (credentials.json from Google Cloud)")
            shutil.copyfile(client_secret, d / "credentials.json")
        if service_account:
            data = _read_json(Path(service_account)) or {}
            if data.get("type") != "service_account":
                raise GoogleError("That file is not a service-account key")
            _forget_sign_in(d)
            shutil.copyfile(service_account, d / "service_account.json")
            _write_json(d / "account.json", {"email": data.get("client_email"), "method": "service_account"})
            GoogleClient.connect()  # fails fast on a revoked key
        elif token:
            data = _read_json(Path(token)) or {}
            if "refresh_token" not in data and "token" not in data:
                raise GoogleError("That token.json has no Google credentials")
            _forget_sign_in(d)
            shutil.copyfile(token, d / "token.json")
            client = GoogleClient.connect()
            _write_json(d / "account.json", {"email": client.account_email(), "method": "token"})
        return GoogleClient.status()

    @staticmethod
    def sign_out() -> dict:
        d = google_dir()
        tok = _read_json(d / "token.json") or {}
        secret = tok.get("refresh_token") or tok.get("token")
        if secret and (_read_json(d / "account.json") or {}).get("method") == "oauth":
            # revoke tokens PySheets created itself (not ones shared with the user's scripts)
            threading.Thread(target=_revoke, args=(secret,), daemon=True).start()
        _forget_sign_in(d)
        return GoogleClient.status()

    # -- connecting ---------------------------------------------------------------

    @classmethod
    def connect(cls, interactive: bool = False) -> GoogleClient:
        """Client for the signed-in account.  Raises :class:`GoogleAuthRequired` when signed out."""
        _ = interactive  # sign-in happens through SIGN_IN (the UI), never implicitly
        d = google_dir()
        service = d / "service_account.json"
        token = d / "token.json"
        from google.auth.exceptions import RefreshError, TransportError
        from google.auth.transport.requests import AuthorizedSession, Request

        if service.exists():
            from google.oauth2 import service_account

            try:
                creds = service_account.Credentials.from_service_account_file(str(service), scopes=SCOPES)
                creds.refresh(Request())
            except (ValueError, KeyError) as e:
                _forget_sign_in(d)
                raise GoogleAuthRequired("The saved service-account key is invalid - sign in again") from e
            except RefreshError as e:
                _forget_sign_in(d)
                raise GoogleAuthRequired("Google rejected the service-account key - sign in again") from e
            except TransportError as e:
                raise GoogleError("Can't reach Google - check your internet connection") from e
            return cls(AuthorizedSession(creds))
        if not token.exists():
            raise GoogleAuthRequired()
        from google.oauth2.credentials import Credentials

        try:
            # keep the token's own scopes: tokens from the user's scripts may have broader ones
            creds = Credentials.from_authorized_user_file(str(token))
        except (ValueError, KeyError) as e:
            _forget_sign_in(d)
            raise GoogleAuthRequired("The saved Google sign-in is unreadable - sign in again") from e
        if not creds.valid:
            if not creds.refresh_token:
                _forget_sign_in(d)
                raise GoogleAuthRequired("Your Google sign-in expired - sign in again")
            try:
                creds.refresh(Request())
            except RefreshError as e:
                _forget_sign_in(d)
                raise GoogleAuthRequired("Your Google sign-in expired or was revoked - sign in again") from e
            except TransportError as e:
                raise GoogleError("Can't reach Google - check your internet connection") from e
            token.write_text(creds.to_json(), encoding="utf-8")
        return cls(AuthorizedSession(creds))

    def account_email(self) -> str | None:
        """E-mail of the signed-in account when the token allows it (best effort)."""
        creds = getattr(self.session, "credentials", None)
        tok = getattr(creds, "token", None)
        if not tok:
            return None
        try:
            import requests

            resp = requests.get(
                "https://oauth2.googleapis.com/tokeninfo", params={"access_token": tok}, timeout=15
            )
            return resp.json().get("email") if resp.ok else None
        except Exception:  # noqa: BLE001
            return None

    # -- requests -----------------------------------------------------------------

    def _get(self, url: str, params: Any = None) -> Any:
        last: Exception | None = None
        for attempt in range(self.max_retries + 1):
            if self.session is None:
                raise GoogleAuthRequired()
            try:
                resp = self.session.get(url, params=params, timeout=120)
            except GoogleError:
                raise
            except Exception as e:  # noqa: BLE001 - network errors are retried
                if type(e).__name__ == "RefreshError":
                    _forget_sign_in(google_dir())
                    raise GoogleAuthRequired(
                        "Your Google sign-in expired or was revoked - sign in again"
                    ) from e
                last = e
                time.sleep(min(30, 2**attempt))
                continue
            if resp.status_code in (429, 500, 502, 503, 504) and attempt < self.max_retries:
                time.sleep(min(60, 5 * (attempt + 1)))
                continue
            if resp.status_code == 401:
                # AuthorizedSession already tried a token refresh: the sign-in is no longer valid
                _forget_sign_in(google_dir())
                raise GoogleAuthRequired("Google asked you to sign in again")
            if resp.status_code == 403:
                raise GoogleError(
                    "Permission denied - this account can't open that spreadsheet (ask for access)"
                )
            if resp.status_code == 404:
                raise GoogleError("Spreadsheet or tab not found")
            if resp.status_code >= 400:
                try:
                    msg = resp.json().get("error", {}).get("message")
                except ValueError:
                    msg = resp.text[:200]
                raise GoogleError(f"Google returned {resp.status_code}: {msg}")
            return resp
        raise GoogleError(f"Network error talking to Google: {last}")

    def spreadsheet_info(self, spreadsheet_id: str) -> dict:
        """Title and tabs (name, rows, columns)."""
        resp = self._get(
            f"{API}/{spreadsheet_id}",
            params={"fields": "properties.title,sheets.properties(title,sheetId,gridProperties)"},
        )
        data = resp.json()
        tabs = []
        for s in data.get("sheets", []):
            p = s.get("properties", {})
            g = p.get("gridProperties", {})
            tabs.append(
                {
                    "title": p.get("title"),
                    "gid": p.get("sheetId"),
                    "rows": g.get("rowCount"),
                    "cols": g.get("columnCount"),
                }
            )
        return {"id": spreadsheet_id, "title": data.get("properties", {}).get("title"), "tabs": tabs}

    def get_values(self, spreadsheet_id: str, tabs: list[str], typed: bool = True) -> dict[str, list[list]]:
        """Values of whole tabs.  Missing tabs map to ``None``."""
        params: list[tuple[str, str]] = [("ranges", _a1_tab(t)) for t in tabs]
        params.append(("valueRenderOption", "UNFORMATTED_VALUE" if typed else "FORMATTED_VALUE"))
        params.append(("dateTimeRenderOption", "FORMATTED_STRING"))
        try:
            data = self._get(f"{API}/{spreadsheet_id}/values:batchGet", params=params).json()
        except GoogleError as e:
            if "Unable to parse range" in str(e) and len(tabs) > 1:
                # one missing tab fails the batch: fetch individually
                out = {}
                for t in tabs:
                    try:
                        out.update(self.get_values(spreadsheet_id, [t], typed))
                    except GoogleError:
                        out[t] = None
                return out
            if "Unable to parse range" in str(e):
                return {tabs[0]: None}
            raise
        out = {}
        for t, vr in zip(tabs, data.get("valueRanges", []), strict=False):
            out[t] = vr.get("values", [])
        return out


# ------------------------------------------------------------------------ shaping


def values_to_rows(values: list[list] | None, header: bool = True) -> tuple[list[str], list[list]]:
    """Rectangular rows + unique headers (handles ragged rows, blank and duplicate headers)."""
    if not values:
        return [], []
    raw_headers = list(values[0]) if header else []
    data = values[1:] if header else values
    width = max([len(raw_headers)] + [len(r) for r in data]) if data else len(raw_headers)
    names: list[str] = []
    seen: dict[str, int] = {}
    for i in range(width):
        h = str(raw_headers[i]).strip() if i < len(raw_headers) and raw_headers[i] not in (None, "") else ""
        if header and not h:
            h = f"Column {i + 1}"
        key = h.lower()
        if h and key in seen:
            seen[key] += 1
            h = f"{h} ({seen[key]})"
        else:
            seen[key] = 1
        names.append(h)
    rows = [[(r[i] if i < len(r) and r[i] != "" else None) for i in range(width)] for r in data]
    return names, rows


@dataclass
class SourceResult:
    label: str
    spreadsheet_id: str
    tab: str
    status: str = "Done"
    rows: int = 0
    error: str = ""
    missing_columns: list[str] = field(default_factory=list)
    extra_columns: list[str] = field(default_factory=list)


def consolidate(
    client: GoogleClient,
    sources: Iterable[tuple[str, str]],
    tabs: list[str],
    *,
    typed: bool = False,
    drop_blank: bool = True,
    add_metadata: bool = True,
    expected: dict[str, list[str]] | None = None,
    workers: int = 4,
    progress: Callable[[int, int, str], None] | None = None,
) -> tuple[dict[str, tuple[list[str], list[list]]], list[SourceResult]]:
    """Pull ``tabs`` from every (label, spreadsheet_id) and stack them per tab.

    Headers are matched ignoring case, spaces and line breaks; the union of columns is kept
    (or the ``expected`` columns first, in order).  Adds ``_source_label``, ``_source_spreadsheet_id``,
    ``_source_tab``, ``_source_row_number`` and ``_extract_timestamp`` columns.
    """
    sources = [(str(lbl or sid), extract_sheet_id(sid)) for lbl, sid in sources if extract_sheet_id(sid)]
    stamp = datetime.now().isoformat(timespec="seconds")
    results: list[SourceResult] = []
    pulled: list[tuple[str, str, dict[str, list[list] | None]]] = []
    lock = threading.Lock()
    done = 0

    def fetch(label: str, sid: str):
        return label, sid, client.get_values(sid, tabs, typed=typed)

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futs = {pool.submit(fetch, lbl, sid): (lbl, sid) for lbl, sid in sources}
        for fut in as_completed(futs):
            lbl, sid = futs[fut]
            try:
                pulled.append(fut.result())
            except GoogleAuthRequired:
                for f in futs:
                    f.cancel()
                raise
            except Exception as e:  # noqa: BLE001 - recorded per source
                for t in tabs:
                    results.append(SourceResult(lbl, sid, t, "Error", error=str(e)))
            with lock:
                done += 1
                if progress:
                    progress(done, len(sources), lbl)
    order = {sid: i for i, (_lbl, sid) in enumerate(sources)}
    pulled.sort(key=lambda x: order.get(x[1], 0))

    out: dict[str, tuple[list[str], list[list]]] = {}
    for tab in tabs:
        exp = (expected or {}).get(tab) or []
        col_keys: list[str] = [clean_header(c) for c in exp]
        col_names: dict[str, str] = {clean_header(c): c for c in exp}
        per_source: list[tuple[str, str, list[str], list[list]]] = []
        for label, sid, vals in pulled:
            v = vals.get(tab)
            if v is None:
                results.append(SourceResult(label, sid, tab, "MissingTab", error=f"Tab '{tab}' not found"))
                continue
            names, rows = values_to_rows(v)
            keys = [clean_header(n) for n in names]
            for k, n in zip(keys, names, strict=True):
                if k and k not in col_names:
                    col_names[k] = n
                    col_keys.append(k)
            res = SourceResult(label, sid, tab)
            if exp:
                have = set(keys)
                res.missing_columns = [c for c in exp if clean_header(c) not in have]
                expk = {clean_header(c) for c in exp}
                res.extra_columns = [n for n, k in zip(names, keys, strict=True) if k and k not in expk]
            per_source.append((label, sid, keys, rows))
            results.append(res)
        header = [col_names[k] for k in col_keys]
        meta = [
            "_source_label",
            "_source_spreadsheet_id",
            "_source_tab",
            "_source_row_number",
            "_extract_timestamp",
        ]
        all_rows: list[list] = []
        for label, sid, keys, rows in per_source:
            idx = {}
            for i, k in enumerate(keys):
                if k and k not in idx:
                    idx[k] = i
            positions = [idx.get(k) for k in col_keys]
            kept = 0
            for n, r in enumerate(rows):
                vals = [r[p] if p is not None and p < len(r) else None for p in positions]
                if drop_blank and all(v is None or (isinstance(v, str) and not v.strip()) for v in vals):
                    continue
                if add_metadata:
                    vals += [label, sid, tab, n + 2, stamp]
                all_rows.append(vals)
                kept += 1
            for res in results:
                if res.spreadsheet_id == sid and res.tab == tab and res.status == "Done":
                    res.rows = kept
        out[tab] = (header + (meta if add_metadata else []), all_rows)
    return out, results
