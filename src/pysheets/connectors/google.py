"""Google Sheets connector.

Authentication options (any one is enough):

* **OAuth desktop client** - the same ``credentials.json`` / ``token.json`` pair used by
  Python scripts built on ``google-auth-oauthlib`` (e.g. the KU IR consolidator). Point
  PySheets at that folder and the existing token is reused; otherwise a browser sign-in runs.
* **Service account** JSON key (share the sheets with the service account e-mail).
* **No account** - sheets shared as "anyone with the link" are read through the public
  CSV export.

Everything here is read-only (``spreadsheets.readonly`` scope).
"""

from __future__ import annotations

import csv
import io
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

SCOPES = ["https://www.googleapis.com/auth/spreadsheets.readonly"]
API = "https://sheets.googleapis.com/v4/spreadsheets"


class GoogleError(Exception):
    pass


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
    """Thin REST client for the Sheets API with retries."""

    def __init__(self, session=None, public: bool = False):
        self.session = session  # google.auth AuthorizedSession, or None for public access
        self.public = public or session is None
        self.max_retries = 5

    # -- auth ---------------------------------------------------------------------

    @staticmethod
    def status() -> dict:
        d = google_dir()
        secret = d / "credentials.json"
        token = d / "token.json"
        service = d / "service_account.json"
        info: dict[str, Any] = {
            "folder": str(d),
            "client_secret": secret.exists(),
            "token": token.exists(),
            "service_account": service.exists(),
            "mode": "public",
        }
        if service.exists():
            info["mode"] = "service_account"
            try:
                info["account"] = json.loads(service.read_text(encoding="utf-8")).get("client_email")
            except (OSError, ValueError):
                pass
        elif token.exists():
            info["mode"] = "oauth"
        return info

    @staticmethod
    def setup(
        folder: str | None = None,
        client_secret: str | None = None,
        token: str | None = None,
        service_account: str | None = None,
    ) -> dict:
        """Copy credentials into PySheets' own folder (originals are left untouched)."""
        d = google_dir()
        if folder:
            f = Path(folder)
            if f.is_file():
                f = f.parent
            found = False
            for name in ("credentials.json", "token.json"):
                if (f / name).exists():
                    shutil.copyfile(f / name, d / name)
                    found = True
            if not found:
                raise GoogleError(f"No credentials.json or token.json found in {f}")
        if client_secret:
            shutil.copyfile(client_secret, d / "credentials.json")
        if token:
            shutil.copyfile(token, d / "token.json")
        if service_account:
            data = json.loads(Path(service_account).read_text(encoding="utf-8"))
            if data.get("type") != "service_account":
                raise GoogleError("That file is not a service-account key")
            shutil.copyfile(service_account, d / "service_account.json")
        return GoogleClient.status()

    @staticmethod
    def sign_out() -> dict:
        d = google_dir()
        for name in ("token.json", "service_account.json"):
            p = d / name
            if p.exists():
                p.unlink()
        return GoogleClient.status()

    @classmethod
    def connect(cls, interactive: bool = False) -> GoogleClient:
        """Build a client from saved credentials.  ``interactive`` allows a browser sign-in."""
        d = google_dir()
        service = d / "service_account.json"
        token = d / "token.json"
        secret = d / "credentials.json"
        try:
            from google.auth.transport.requests import AuthorizedSession, Request
        except ImportError:  # pragma: no cover - optional dependency
            return cls(None, public=True)
        if service.exists():
            from google.oauth2 import service_account

            creds = service_account.Credentials.from_service_account_file(str(service), scopes=SCOPES)
            return cls(AuthorizedSession(creds))
        creds = None
        if token.exists():
            from google.oauth2.credentials import Credentials

            try:
                creds = Credentials.from_authorized_user_file(str(token), SCOPES)
            except (ValueError, KeyError):
                creds = None
        if creds and not creds.valid and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
                token.write_text(creds.to_json(), encoding="utf-8")
            except Exception:  # noqa: BLE001 - revoked/expired refresh token
                creds = None
        if (creds is None or not creds.valid) and interactive:
            if not secret.exists():
                raise GoogleError("Add your OAuth client file (credentials.json) first")
            from google_auth_oauthlib.flow import InstalledAppFlow

            flow = InstalledAppFlow.from_client_secrets_file(str(secret), SCOPES)
            creds = flow.run_local_server(port=0, open_browser=True, timeout_seconds=300)
            token.write_text(creds.to_json(), encoding="utf-8")
        if creds is None or not creds.valid:
            return cls(None, public=True)
        return cls(AuthorizedSession(creds))

    # -- requests -----------------------------------------------------------------

    def _get(self, url: str, params: Any = None) -> Any:
        last: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                if self.session is not None:
                    resp = self.session.get(url, params=params, timeout=120)
                else:
                    import requests

                    resp = requests.get(url, params=params, timeout=120)
            except Exception as e:  # noqa: BLE001 - network errors are retried
                last = e
                time.sleep(min(30, 2**attempt))
                continue
            if resp.status_code in (429, 500, 502, 503, 504) and attempt < self.max_retries:
                time.sleep(min(60, 5 * (attempt + 1)))
                continue
            if resp.status_code == 403:
                raise GoogleError(
                    "Permission denied - share the sheet with your account (or make it link-viewable)"
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
        if self.public:
            raise GoogleError(
                "Listing tabs needs a Google account. Type the tab name instead, or connect an account."
            )
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
        if self.public:
            return {t: self._public_csv(spreadsheet_id, t) for t in tabs}
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

    def _public_csv(self, spreadsheet_id: str, tab: str) -> list[list] | None:
        url = f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}/gviz/tq"
        try:
            resp = self._get(url, params={"tqx": "out:csv", "sheet": tab})
        except GoogleError:
            return None
        text = resp.text
        if text.lstrip().startswith("<"):
            raise GoogleError("This sheet is private. Connect a Google account (Data > Google account).")
        return [list(r) for r in csv.reader(io.StringIO(text))]


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
