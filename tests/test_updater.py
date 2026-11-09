"""Update check: version comparison, release parsing, verified download."""

import hashlib
import time

import pytest

from pysheets import __version__, updater
from pysheets.updater import DOWNLOAD_PREFIX, UpdateError, Updater, check, is_newer, parse_version


def release(tag, payload=b"setup-bytes", digest=None):
    return {
        "tag_name": tag,
        "html_url": f"https://github.com/augustof2022/pysheets/releases/tag/{tag}",
        "published_at": "2026-11-09T01:33:00Z",
        "body": "What's new",
        "assets": [
            {
                "name": f"PySheets-Setup-{tag.lstrip('v')}.exe",
                "browser_download_url": f"{DOWNLOAD_PREFIX}{tag}/PySheets-Setup-{tag.lstrip('v')}.exe",
                "size": len(payload),
                "digest": digest or "sha256:" + hashlib.sha256(payload).hexdigest(),
            },
            {"name": "evil.exe", "browser_download_url": "https://example.com/evil.exe", "size": 1},
        ],
    }


def test_versions():
    assert parse_version("v0.3.10") == (0, 3, 10)
    assert is_newer("0.3.10", "0.3.9") and is_newer("v1.0", "0.9.9") and is_newer("0.4", "0.3.99")
    assert not is_newer("0.3.4", "0.3.4") and not is_newer("0.3", "0.3.0") and not is_newer("0.3.3", "0.3.4")
    with pytest.raises(UpdateError):
        parse_version("latest")


def test_check_up_to_date_and_newer():
    same = check(fetch=lambda: release(f"v{__version__}"))
    assert same["update_available"] is False and same["latest"] == __version__
    newer = check(fetch=lambda: release("v99.0.0"))
    assert newer["update_available"] is True
    assert newer["installer"]["name"] == "PySheets-Setup-99.0.0.exe"
    assert newer["installer"]["url"].startswith(DOWNLOAD_PREFIX)  # assets from elsewhere are ignored
    assert newer["install_kind"] == "source"  # tests don't run frozen


def _wait(u, states=("ready", "error")):
    end = time.time() + 5
    while time.time() < end:
        if u.state()["state"] in states:
            return u.state()
        time.sleep(0.02)
    raise AssertionError(u.state())


def test_download_is_verified(tmp_path, monkeypatch):
    monkeypatch.setattr(updater.tempfile, "gettempdir", lambda: str(tmp_path))
    payload = b"x" * 200_000
    info = check(fetch=lambda: release("v99.0.0", payload))
    u = Updater()
    u.start(
        info, get=lambda url: (len(payload), [payload[i : i + 65536] for i in range(0, len(payload), 65536)])
    )
    st = _wait(u)
    assert st["state"] == "ready" and st["done"] == len(payload)
    launched = []
    u.install(launch=lambda path, args: launched.append((path, args)))
    assert launched[0][0].endswith("PySheets-Setup-99.0.0.exe")
    assert "/SILENT" in launched[0][1] and "/relaunch=1" in launched[0][1]
    assert u.state()["state"] == "installing"


def test_tampered_download_is_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(updater.tempfile, "gettempdir", lambda: str(tmp_path))
    info = check(fetch=lambda: release("v99.0.0", b"genuine"))
    u = Updater()
    u.start(info, get=lambda url: (8, [b"tampered"]))
    st = _wait(u)
    assert st["state"] == "error" and "checksum" in st["error"]
    with pytest.raises(UpdateError):
        u.install(launch=lambda *a: None)
    assert not list((tmp_path / "PySheets-update").glob("*.exe"))


def test_refuses_foreign_urls():
    u = Updater()
    with pytest.raises(UpdateError):
        u.start({"installer": {"name": "x.exe", "url": "https://example.com/x.exe"}, "latest": "9"})
