"""Google sign-in gate: nothing Google-related works until the user signs in."""

import base64
import json
import time
import urllib.request
from types import SimpleNamespace

import pytest

from pysheets.api import dispatch
from pysheets.connectors import google as G
from pysheets.connectors.google import GoogleAuthRequired, GoogleClient, GoogleError
from pysheets.engine.workbook import Workbook


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("PYSHEETS_HOME", str(tmp_path))
    monkeypatch.delenv("PYSHEETS_GOOGLE_CLIENT", raising=False)
    G.SIGN_IN.cancel()
    yield tmp_path
    G.SIGN_IN.cancel()


def _jwt(claims: dict) -> str:
    enc = lambda d: base64.urlsafe_b64encode(json.dumps(d).encode()).decode().rstrip("=")  # noqa: E731
    return f"{enc({'alg': 'none'})}.{enc(claims)}.sig"


class FakeFlow:
    def __init__(self, redirect):
        self.redirect = redirect
        self.fetched = None

    def authorization_url(self, **kw):
        assert kw["access_type"] == "offline"
        return "https://accounts.example/auth?state=st123", "st123"

    def fetch_token(self, authorization_response):
        self.fetched = authorization_response
        self.oauth2session = SimpleNamespace(token={"id_token": _jwt({"email": "me@example.com"})})

    @property
    def credentials(self):
        data = {"token": "t", "refresh_token": "r", "client_id": "c", "client_secret": "s"}
        return SimpleNamespace(to_json=lambda: json.dumps(data))


def _browser_get(url: str) -> str:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(url, timeout=5) as resp:
        return resp.read().decode()


def _wait_done(timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        if not G.SIGN_IN.state()["pending"]:
            return G.SIGN_IN.state()
        time.sleep(0.05)
    raise AssertionError("sign-in still pending")


def _start():
    flows = []
    opened = []

    def factory(redirect):
        flows.append(FakeFlow(redirect))
        return flows[-1]

    st = G.SIGN_IN.start(open_browser=opened.append, flow_factory=factory)
    return st, flows[0], opened


def test_signed_out_blocks_everything(home):
    st = GoogleClient.status()
    assert st["signed_in"] is False and st["has_client"] is False
    with pytest.raises(GoogleAuthRequired):
        GoogleClient.connect()
    wb = Workbook()
    out = dispatch(
        wb, "gsheets.tabs", {"url": "https://docs.google.com/spreadsheets/d/abcdefghijklmnopqrstuvwxyz/edit"}
    )
    assert out["ok"] is False and out["code"] == "google_auth"
    out = dispatch(wb, "gsheets.import", {"url": "abcdefghijklmnopqrstuvwxyz", "tabs": ["x"]})
    assert out["code"] == "google_auth"
    wb.close()


def test_sign_in_needs_an_oauth_client(home):
    with pytest.raises(GoogleError, match="credentials.json"):
        G.SIGN_IN.start(open_browser=lambda u: None)
    bad = home / "bad.json"
    bad.write_text('{"type": "service_account"}')
    with pytest.raises(GoogleError, match="not an OAuth client"):
        GoogleClient.setup(client_secret=str(bad))
    good = home / "client.json"
    good.write_text(json.dumps({"installed": {"client_id": "x", "client_secret": "y"}}))
    st = GoogleClient.setup(client_secret=str(good))
    assert st["has_client"] and st["client_source"] == "file" and not st["signed_in"]


def test_browser_sign_in_completes(home):
    st, flow, opened = _start()
    assert st["pending"] and opened == ["https://accounts.example/auth?state=st123"]
    assert flow.redirect.startswith("http://127.0.0.1:")
    # stray requests (favicon) and stale links don't finish the flow
    with pytest.raises(urllib.error.HTTPError):
        _browser_get(flow.redirect + "favicon.ico")
    assert "out of date" in _browser_get(flow.redirect + "?state=old&code=zzz")
    assert G.SIGN_IN.state()["pending"]
    page = _browser_get(flow.redirect + "?state=st123&code=abc")
    assert "signed in" in page and "me@example.com" in page
    done = _wait_done()
    assert done["error"] is None and done["email"] == "me@example.com"
    assert flow.fetched.startswith("https://127.0.0.1:")  # oauthlib insists on https
    st = GoogleClient.status()
    assert st["signed_in"] and st["email"] == "me@example.com" and st["method"] == "oauth"
    assert json.loads((G.google_dir() / "token.json").read_text())["refresh_token"] == "r"
    # signing out forgets the account again
    st = GoogleClient.sign_out()
    assert not st["signed_in"] and not (G.google_dir() / "token.json").exists()


def test_browser_sign_in_denied(home):
    _st, flow, _ = _start()
    page = _browser_get(flow.redirect + "?state=st123&error=access_denied")
    assert "cancelled" in page
    done = _wait_done()
    assert done["error"] == "You cancelled the sign-in." and not GoogleClient.status()["signed_in"]


def test_cancel_stops_waiting(home):
    _st, flow, _ = _start()
    assert G.SIGN_IN.state()["pending"]
    st = G.SIGN_IN.cancel()
    assert not st["pending"] and st["error"] is None
    time.sleep(0.4)
    with pytest.raises(OSError):
        _browser_get(flow.redirect + "?state=st123&code=abc")
    assert not GoogleClient.status()["signed_in"]


def test_expired_token_signs_out(home, monkeypatch):
    from google.auth.exceptions import RefreshError
    from google.oauth2.credentials import Credentials

    d = G.google_dir()
    tok = {
        "token": "t",
        "refresh_token": "r",
        "client_id": "c",
        "client_secret": "s",
        "expiry": "2000-01-01T00:00:00Z",
    }
    (d / "token.json").write_text(json.dumps(tok))
    (d / "account.json").write_text(json.dumps({"email": "me@example.com", "method": "oauth"}))
    assert GoogleClient.status()["signed_in"]

    def revoked(self, request):
        raise RefreshError("invalid_grant: Token has been expired or revoked.")

    monkeypatch.setattr(Credentials, "refresh", revoked)
    st = GoogleClient.status(verify=True)
    assert not st["signed_in"] and "expired or was revoked" in st["expired"]
    assert not (d / "token.json").exists() and not (d / "account.json").exists()
    # a token that can't be parsed is also a sign-out, not a crash
    (d / "token.json").write_text("{}")
    with pytest.raises(GoogleAuthRequired, match="sign in again"):
        GoogleClient.connect()


def test_id_token_email():
    assert G._id_token_email(_jwt({"email": "a@b.co"})) == "a@b.co"
    assert G._id_token_email("garbage") is None
    assert G._id_token_email(None) is None


def test_401_from_google_signs_out(home):
    d = G.google_dir()
    (d / "token.json").write_text(
        json.dumps({"token": "t", "refresh_token": "r", "client_id": "c", "client_secret": "s"})
    )
    (d / "account.json").write_text(json.dumps({"email": "me@example.com", "method": "oauth"}))

    class Session:
        def get(self, url, params=None, timeout=None):
            return SimpleNamespace(status_code=401, text="", json=lambda: {})

    with pytest.raises(GoogleAuthRequired):
        GoogleClient(Session()).spreadsheet_info("abc")
    assert not GoogleClient.status()["signed_in"]  # so the sign-in screen shows instead of looping
