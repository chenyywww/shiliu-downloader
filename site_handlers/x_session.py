"""An explicitly saved X session, encrypted for the current Windows user."""

from contextlib import contextmanager
import ctypes
from ctypes import wintypes
import http.cookiejar
import json
import os
from pathlib import Path
import sys
import tempfile
import time
from urllib.parse import urlsplit
import uuid

from .common import ROOT, VENDOR

MAGIC = b"XDLP1\0"
AUTH_DIR = ROOT / ".auth"
STATE_NAME = "x-session.bin"


class SessionError(RuntimeError):
    pass


def x_host(host):
    host = (host or "").lower().lstrip(".").rstrip(".")
    return any(host == domain or host.endswith("." + domain) for domain in ("x.com", "twitter.com"))


def filtered_state(state):
    return {
        "cookies": [
            cookie for cookie in state.get("cookies", [])
            if x_host(cookie.get("domain"))
        ],
        "origins": [
            origin for origin in state.get("origins", [])
            if x_host(urlsplit(origin.get("origin", "")).hostname)
        ],
    }


def has_login(state):
    active = {
        cookie.get("name")
        for cookie in filtered_state(state)["cookies"]
        if cookie.get("value")
        and (float(cookie.get("expires", -1)) <= 0 or float(cookie["expires"]) > time.time())
    }
    return {"auth_token", "ct0"}.issubset(active)


class Blob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]


def crypt(data, decrypt=False):
    if os.name != "nt":
        raise SessionError("此登录状态存储方式需要 Windows。")
    buffer = ctypes.create_string_buffer(data)
    source = Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    result = Blob()
    api = ctypes.WinDLL("crypt32", use_last_error=True)
    function = api.CryptUnprotectData if decrypt else api.CryptProtectData
    function.argtypes = [
        ctypes.POINTER(Blob), ctypes.c_void_p if decrypt else wintypes.LPCWSTR,
        ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD,
        ctypes.POINTER(Blob),
    ]
    function.restype = wintypes.BOOL
    description = None if decrypt else "Video downloader X session"
    if not function(ctypes.byref(source), description, None, None, None, 1, ctypes.byref(result)):
        raise SessionError("无法加密或读取本机登录状态，请使用当前 Windows 账号重新登录 X。")
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    try:
        return ctypes.string_at(result.pbData, result.cbData)
    finally:
        kernel.LocalFree(ctypes.cast(result.pbData, ctypes.c_void_p))


class SessionStore:
    def __init__(self, directory=AUTH_DIR):
        self.directory = Path(directory).resolve()
        self.path = self.directory / STATE_NAME

    def exists(self):
        return self.path.is_file()

    def save(self, state):
        state = filtered_state(state)
        if not has_login(state):
            raise SessionError("尚未检测到完整的 X 登录状态，请先在网页里完成登录。")
        payload = json.dumps({"version": 1, "state": state}, ensure_ascii=False).encode("utf-8")
        encrypted = MAGIC + crypt(payload)
        self.directory.mkdir(parents=True, exist_ok=True)
        staged = self.directory / ("session-" + uuid.uuid4().hex + ".tmp")
        try:
            with staged.open("xb") as stream:
                stream.write(encrypted)
            os.replace(staged, self.path)
        finally:
            staged.unlink(missing_ok=True)

    def load(self):
        if not self.exists():
            raise SessionError("还没有保存本工具的 X 登录会话。")
        try:
            encrypted = self.path.read_bytes()
            if not encrypted.startswith(MAGIC):
                raise ValueError("Invalid session format")
            document = json.loads(crypt(encrypted[len(MAGIC):], decrypt=True))
            state = filtered_state(document["state"])
        except SessionError:
            raise
        except Exception:
            raise SessionError("保存的 X 登录状态无法读取，请重新登录。") from None
        if not has_login(state):
            raise SessionError("保存的 X 登录状态已经过期，请重新登录。")
        return state


@contextmanager
def cookie_file(state, store=None):
    store = store or SessionStore()
    store.directory.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="cookies_", dir=store.directory) as name:
        directory = Path(name).resolve()
        if directory.parent != store.directory:
            raise SessionError("会话临时目录检查失败。")
        path = directory / "cookies.txt"
        jar = http.cookiejar.MozillaCookieJar(str(path))
        for item in filtered_state(state)["cookies"]:
            expires = item.get("expires", -1)
            expires = int(expires) if expires and expires > 0 else None
            domain = item["domain"]
            jar.set_cookie(http.cookiejar.Cookie(
                version=0, name=item["name"], value=item["value"],
                port=None, port_specified=False,
                domain=domain, domain_specified=True,
                domain_initial_dot=domain.startswith("."),
                path=item.get("path", "/"), path_specified=True,
                secure=bool(item.get("secure")), expires=expires,
                discard=expires is None, comment=None, comment_url=None,
                rest={}, rfc2109=False,
            ))
        jar.save(ignore_discard=True, ignore_expires=True)
        yield path


PANEL = """({message, hasTarget}) => {
    if (!/(^|\\.)(x\\.com|twitter\\.com)$/.test(location.hostname)) return;
    let box = document.getElementById('video-downloader-x-session');
    if (!box) {
        box = document.createElement('div');
        box.id = 'video-downloader-x-session';
        box.style.cssText = 'position:fixed;right:14px;bottom:14px;width:330px;' +
            'z-index:2147483647;background:#172554;color:white;padding:16px;' +
            'border:2px solid #60a5fa;border-radius:12px;font:14px/1.5 sans-serif;' +
            'box-shadow:0 4px 24px #0008;text-align:left';
        box.innerHTML = '<strong>视频下载工具 · X 登录</strong>' +
            '<p>请在 X 网页中手动登录，确认目标视频能播放后，再保存登录状态。</p>' +
            '<p id="video-downloader-x-message"></p>' +
            '<button data-action="target">打开目标视频</button> ' +
            '<button data-action="save">保存登录状态</button> ' +
            '<button data-action="cancel">取消</button>';
        for (const button of box.querySelectorAll('button')) {
            button.style.cssText = 'margin:4px 2px;padding:7px 10px;cursor:pointer;' +
                'background:#dbeafe;color:#172554;border:0;border-radius:5px';
            button.onclick = () => window.videoDownloaderXAction(button.dataset.action);
        }
        document.body.appendChild(box);
    }
    box.querySelector('[data-action="target"]').hidden = !hasTarget;
    box.querySelector('#video-downloader-x-message').textContent = message;
}"""


def login(target_url=None, store=None):
    store = store or SessionStore()
    if target_url and (
        urlsplit(target_url).scheme not in ("https", "http")
        or not x_host(urlsplit(target_url).hostname)
        or urlsplit(target_url).username is not None
        or urlsplit(target_url).password is not None
    ):
        raise ValueError("X 登录窗口只接受 X 或 Twitter 的目标网址。")
    if str(VENDOR) not in sys.path:
        sys.path.insert(0, str(VENDOR))
    from playwright.sync_api import sync_playwright, Error as BrowserError
    print("将打开独立 X 登录窗口。请手动登录，并在右下角点击“保存登录状态”。", flush=True)
    print("登录状态会在本机加密保存；不会读取你现有 Chrome 的登录信息。", flush=True)
    actions = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="chrome", headless=False)
        context = browser.new_context()
        page = context.new_page()

        def action(source, name):
            if source["frame"] == source["page"].main_frame and x_host(urlsplit(source["frame"].url).hostname):
                if name in ("save", "target", "cancel"):
                    actions.append(name)

        context.expose_binding("videoDownloaderXAction", action)
        message = "登录信息只保存在本机，账号密码请仅在 X 页面中输入。"
        try:
            try:
                page.goto("https://x.com/i/flow/login", wait_until="domcontentloaded", timeout=45000)
            except BrowserError:
                message = "页面加载尚未完成。可在地址栏打开 https://x.com/i/flow/login 后手动登录。"
            while browser.is_connected() and not page.is_closed():
                try:
                    page.evaluate(PANEL, {"message": message, "hasTarget": bool(target_url)})
                    if actions:
                        choice = actions.pop(0)
                        if choice == "cancel":
                            raise SessionError("已取消，没有保存新的登录状态。")
                        if choice == "target" and target_url:
                            page.goto(target_url, wait_until="domcontentloaded", timeout=45000)
                        elif choice == "save":
                            state = filtered_state(context.storage_state())
                            if not has_login(state):
                                message = "尚未检测到完整登录，请先在 X 网页中完成登录。"
                            else:
                                store.save(state)
                                print("X 登录状态已加密保存，现在可以重新下载。", flush=True)
                                return state
                    page.wait_for_timeout(250)
                except BrowserError:
                    if page.is_closed() or not browser.is_connected():
                        break
            raise SessionError("登录窗口已关闭，没有保存新的登录状态。")
        finally:
            if browser.is_connected():
                browser.close()
