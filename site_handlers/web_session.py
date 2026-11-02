"""Reuse only this tool's per-origin Cloudflare clearance, encrypted on Windows."""
from contextlib import contextmanager
import hashlib
import json
import math
import os
from pathlib import Path
import tempfile
import time
from urllib.parse import urlsplit
import uuid

from .common import ROOT

DIRECTORY = ROOT / ".auth" / "web"
MAGIC = b"VDWEB1\0"
COOKIE_NAMES = {"cf_clearance", "__cf_bm"}


def origin(url):
    parsed = urlsplit(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("网站会话需要普通的 http 或 https 地址。")
    host = parsed.hostname.lower().rstrip(".")
    authority = f"[{host}]" if ":" in host else host
    if parsed.port and parsed.port != (443 if parsed.scheme == "https" else 80):
        authority += f":{parsed.port}"
    return f"{parsed.scheme}://{authority}"


def clearance_cookies(url, cookies):
    host = urlsplit(origin(url)).hostname
    result = []
    fields = {"name", "value", "domain", "path", "expires", "httpOnly", "secure", "sameSite"}
    for cookie in cookies:
        domain = cookie.get("domain", "").lower().lstrip(".").rstrip(".")
        if not domain or not (host == domain or ("." in domain and host.endswith("." + domain))):
            continue
        if cookie.get("name") not in COOKIE_NAMES or not cookie.get("value"):
            continue
        try:
            expires = float(cookie.get("expires", 0))
            if not math.isfinite(expires) or expires <= time.time():
                continue
        except (TypeError, ValueError):
            continue
        result.append({key: value for key, value in cookie.items() if key in fields})
    return result


def crypt(data, decrypt=False):
    # Reuse the DPAPI primitive only; no X session is loaded or consulted.
    from .x_session import crypt as windows_crypt
    return windows_crypt(data, decrypt=decrypt)


class ClearanceStore:
    def __init__(self, directory=None):
        self.directory = Path(directory if directory is not None else DIRECTORY).resolve()

    def path(self, url):
        key = hashlib.sha256(origin(url).encode("utf-8")).hexdigest()
        return self.directory / (key + ".bin")

    def load(self, url):
        path = self.path(url)
        if not path.is_file():
            return None
        try:
            if path.stat().st_size > 512 * 1024:
                return None
            blob = path.read_bytes()
            if not blob.startswith(MAGIC):
                return None
            record = json.loads(crypt(blob[len(MAGIC):], decrypt=True))
            if record.get("version") != 1 or record.get("origin") != origin(url):
                return None
            cookies = clearance_cookies(url, record.get("cookies", []))
            if not any(cookie["name"] == "cf_clearance" for cookie in cookies):
                return None
            agent = record.get("user_agent", "")
            if not isinstance(agent, str) or not agent or "\r" in agent or "\n" in agent:
                return None
            return {"cookies": cookies, "user_agent": agent}
        except Exception:
            return None

    def save(self, url, cookies, user_agent):
        cookies = clearance_cookies(url, cookies)
        if not any(cookie["name"] == "cf_clearance" for cookie in cookies):
            return False
        if not user_agent or "\r" in user_agent or "\n" in user_agent:
            return False
        payload = json.dumps({"version": 1, "origin": origin(url),
                              "cookies": cookies, "user_agent": user_agent}).encode("utf-8")
        encrypted = MAGIC + crypt(payload)
        self.directory.mkdir(parents=True, exist_ok=True)
        staged = self.directory / (uuid.uuid4().hex + ".tmp")
        try:
            with staged.open("xb") as stream:
                stream.write(encrypted)
            os.replace(staged, self.path(url))
        finally:
            staged.unlink(missing_ok=True)
        return True


@contextmanager
def http_state(url, referer=None):
    from .browser import write_cookies
    state = ClearanceStore().load(url)
    page_origin = origin(referer or url)
    headers = {"Referer": referer or page_origin + "/", "Origin": page_origin}
    if not state:
        yield headers, None
        return
    headers["User-Agent"] = state["user_agent"]
    DIRECTORY.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="cookies_", dir=DIRECTORY) as name:
        work = Path(name).resolve()
        if work.parent != DIRECTORY.resolve():
            raise RuntimeError("网站会话临时目录检查失败。")
        cookiefile = work / "cookies.txt"
        write_cookies(cookiefile, state["cookies"])
        yield headers, cookiefile
