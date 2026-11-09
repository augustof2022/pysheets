"""Check GitHub Releases for a newer PySheets and install it on request (never automatically).

Flow: :func:`check` compares this build's version with the latest release. :class:`Updater`
downloads the installer in the background (progress is polled by the UI), verifies its
SHA-256 against the digest GitHub publishes for the asset, then runs it silently and
closes PySheets so the files can be replaced. The installer relaunches the app.
"""

from __future__ import annotations

import hashlib
import os
import re
import sys
import tempfile
import threading
from pathlib import Path
from typing import Any

from . import __version__

REPO = "augustof2022/pysheets"
RELEASES_API = f"https://api.github.com/repos/{REPO}/releases/latest"
RELEASES_PAGE = f"https://github.com/{REPO}/releases"
DOWNLOAD_PREFIX = f"https://github.com/{REPO}/releases/download/"


class UpdateError(Exception):
    pass


def parse_version(v: str) -> tuple[int, ...]:
    """'v0.3.10' -> (0, 3, 10); anything after the numbers (e.g. '-beta') is ignored."""
    m = re.match(r"^\s*v?(\d+(?:\.\d+)*)", v or "")
    if not m:
        raise UpdateError(f"Unrecognised version: {v!r}")
    return tuple(int(x) for x in m.group(1).split("."))


def is_newer(latest: str, current: str) -> bool:
    a, b = parse_version(latest), parse_version(current)
    n = max(len(a), len(b))
    return a + (0,) * (n - len(a)) > b + (0,) * (n - len(b))


def install_kind() -> str:
    """'installer' (installed with the setup program), 'portable' (zip) or 'source'."""
    if not getattr(sys, "frozen", False):
        return "source"
    exe_dir = Path(sys.executable).resolve().parent
    if any(exe_dir.glob("unins*.exe")):
        return "installer"
    return "portable"


def _per_user_install() -> bool:
    """Installed for the current user only (no admin rights needed to update)."""
    local = os.environ.get("LOCALAPPDATA")
    exe = str(Path(sys.executable).resolve()).lower()
    return bool(local) and exe.startswith(str(Path(local)).lower())


def _session():
    import requests

    s = requests.Session()
    s.headers["User-Agent"] = f"PySheets/{__version__} (update check)"
    s.headers["Accept"] = "application/vnd.github+json"
    return s


def check(fetch=None) -> dict[str, Any]:
    """Compare this build with the latest GitHub release."""
    if fetch is None:
        import requests

        try:
            resp = _session().get(RELEASES_API, timeout=20)
        except requests.RequestException as e:
            raise UpdateError("Couldn't reach GitHub - check your internet connection") from e
        if resp.status_code == 404:
            raise UpdateError("No published releases found")
        if resp.status_code == 403:
            raise UpdateError("GitHub is rate-limiting update checks - try again in a few minutes")
        if not resp.ok:
            raise UpdateError(f"GitHub returned {resp.status_code}")
        data = resp.json()
    else:
        data = fetch()
    latest = str(data.get("tag_name") or "").lstrip("v")
    if not latest:
        raise UpdateError("The latest release has no version tag")
    setup = zipped = None
    for a in data.get("assets") or []:
        name = a.get("name", "")
        url = a.get("browser_download_url", "")
        if not url.startswith(DOWNLOAD_PREFIX):
            continue
        entry = {"name": name, "url": url, "size": a.get("size"), "sha256": _digest(a.get("digest"))}
        if re.fullmatch(r"PySheets-Setup-[\w.\-]+\.exe", name):
            setup = entry
        elif name.endswith("-windows-x64.zip"):
            zipped = entry
    notes = (data.get("body") or "").strip()
    return {
        "current": __version__,
        "latest": latest,
        "update_available": is_newer(latest, __version__),
        "published_at": data.get("published_at"),
        "notes": notes[:4000],
        "page": data.get("html_url") or RELEASES_PAGE,
        "installer": setup,
        "zip": zipped,
        "install_kind": install_kind(),
        "platform": sys.platform,
    }


def _digest(d: str | None) -> str | None:
    if d and d.startswith("sha256:"):
        return d.split(":", 1)[1].lower()
    return None


class Updater:
    """Background download + verification of the installer, then a silent install."""

    def __init__(self):
        self._lock = threading.Lock()
        self._state: dict[str, Any] = {
            "state": "idle",
            "done": 0,
            "total": None,
            "error": None,
            "version": None,
        }
        self._path: Path | None = None
        self._cancel = threading.Event()

    def state(self) -> dict:
        with self._lock:
            return dict(self._state)

    def _set(self, **kw: Any) -> None:
        with self._lock:
            self._state.update(kw)

    def start(self, info: dict, get=None) -> dict:
        """Start downloading the installer described by ``check()``'s ``installer`` entry."""
        if self.state()["state"] == "downloading":
            return self.state()
        asset = info.get("installer")
        if not asset:
            raise UpdateError("This release has no Windows installer")
        if not asset["url"].startswith(DOWNLOAD_PREFIX):
            raise UpdateError("Refusing to download an update from outside the PySheets releases")
        self._cancel = threading.Event()
        self._set(
            state="downloading", done=0, total=asset.get("size"), error=None, version=info.get("latest")
        )
        t = threading.Thread(target=self._download, args=(asset, get, self._cancel), daemon=True)
        t.start()
        return self.state()

    def cancel(self) -> dict:
        self._cancel.set()
        self._set(state="idle", done=0, error=None)
        return self.state()

    def _download(self, asset: dict, get, cancel: threading.Event) -> None:
        folder = Path(tempfile.gettempdir()) / "PySheets-update"
        folder.mkdir(parents=True, exist_ok=True)
        dest = folder / asset["name"]
        part = dest.with_suffix(dest.suffix + ".part")
        h = hashlib.sha256()
        try:
            if get is None:
                resp = _session().get(asset["url"], stream=True, timeout=60)
                resp.raise_for_status()
                total = int(resp.headers.get("Content-Length") or 0) or asset.get("size")
                chunks = resp.iter_content(1 << 16)
            else:
                total, chunks = get(asset["url"])
            self._set(total=total)
            done = 0
            with open(part, "wb") as f:
                for chunk in chunks:
                    if cancel.is_set():
                        return
                    f.write(chunk)
                    h.update(chunk)
                    done += len(chunk)
                    self._set(done=done)
            self._set(state="verifying")
            want = asset.get("sha256")
            if want and h.hexdigest() != want:
                raise UpdateError("The download is damaged (checksum mismatch) - try again")
            os.replace(part, dest)
            self._path = dest
            self._set(state="ready")
        except UpdateError as e:
            self._set(state="error", error=str(e))
        except Exception as e:  # noqa: BLE001 - network/disk errors are shown to the user
            self._set(state="error", error=f"Download failed: {e}")
        finally:
            if part.exists() and self.state()["state"] != "ready":
                try:
                    part.unlink()
                except OSError:
                    pass

    def install(self, launch=None) -> dict:
        """Run the verified installer silently; it closes PySheets, updates and relaunches it."""
        if self.state()["state"] != "ready" or self._path is None:
            raise UpdateError("Download the update first")
        args = ["/SILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/CLOSEAPPLICATIONS", "/relaunch=1"]
        args.append("/CURRENTUSER" if _per_user_install() else "/ALLUSERS")
        if launch is not None:
            launch(str(self._path), args)
        elif sys.platform == "win32":
            # ShellExecute so Windows can show the UAC prompt for an all-users install
            os.startfile(str(self._path), "open", " ".join(args))  # type: ignore[attr-defined]  # noqa: S606
        else:
            raise UpdateError("Automatic install is only available on Windows")
        self._set(state="installing")
        return self.state()


UPDATER = Updater()
