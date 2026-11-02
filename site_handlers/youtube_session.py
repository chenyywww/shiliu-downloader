"""Explicit local YouTube sessions; never read a personal Chrome profile."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import sys
import tempfile
import time
from urllib.parse import urlsplit
import uuid

from .common import ROOT, VENDOR
from .x_session import crypt

DIRECTORY = ROOT / ".auth"
MAGIC = b"YTLOGIN1\0"
AUTH_NAMES = {"SAPISID", "__Secure-1PAPISID", "__Secure-3PAPISID"}


class SessionError(RuntimeError):
    pass


def supported_host(host):
    host = (host or "").lower().strip(".")
    return host == "youtube.com" or host.endswith(".youtube.com")


def filtered_cookies(cookies):
    return [item for item in cookies if supported_host(item.get("domain"))]


def has_login(cookies):
    return any(item.get("name") in AUTH_NAMES and item.get("value")
               and (float(item.get("expires", -1)) <= 0 or float(item["expires"]) > time.time())
               for item in filtered_cookies(cookies))


class SessionStore:
    def __init__(self, directory=None):
        self.directory = Path(directory if directory is not None else DIRECTORY).resolve()
        self.path = self.directory / "youtube-session.bin"

    def save(self, cookies, user_agent=""):
        cookies = filtered_cookies(cookies)
        if not has_login(cookies):
            raise SessionError("尚未检测到 YouTube 登录会话，请先在 YouTube 网页手动登录。")
        if not isinstance(user_agent, str) or len(user_agent) > 4096 or any(c in user_agent for c in "\r\n\0"):
            raise SessionError("浏览器标识无效，未保存会话。")
        data = json.dumps({"version": 1, "cookies": cookies, "user_agent": user_agent}, ensure_ascii=False).encode()
        if len(data) > 1024 * 1024:
            raise SessionError("YouTube 会话文件过大，未保存。")
        try:
            blob = MAGIC + crypt(data)
        except Exception:
            raise SessionError("无法加密本机 YouTube 会话，未保存。") from None
        self.directory.mkdir(parents=True, exist_ok=True)
        staged = self.directory / ("youtube-" + uuid.uuid4().hex + ".tmp")
        try:
            with staged.open("xb") as stream:
                stream.write(blob)
            os.replace(staged, self.path)
        finally:
            staged.unlink(missing_ok=True)

    def load(self):
        if not self.path.is_file():
            return None
        try:
            data = self.path.read_bytes()
            if len(data) > 2 * 1024 * 1024 or not data.startswith(MAGIC):
                raise ValueError("Invalid session")
            record = json.loads(crypt(data[len(MAGIC):], decrypt=True))
            cookies = filtered_cookies(record["cookies"])
            agent = record.get("user_agent", "")
            if record.get("version") != 1 or not has_login(cookies) or not isinstance(agent, str) or any(c in agent for c in "\r\n\0"):
                raise ValueError("Expired session")
            return {"cookies": cookies, "user_agent": agent}
        except Exception:
            raise SessionError("本工具保存的 YouTube 会话已过期或无法读取，请重新登录或导入本人 Cookie。") from None

    def forget(self):
        self.path.unlink(missing_ok=True)


def import_cookie_text(text, store=None):
    """Parse only user-supplied Netscape cookies, with no plaintext disk copy."""
    if not isinstance(text, str) or len(text.encode("utf-8")) > 1024 * 1024:
        raise SessionError("请选择小于 1 MB 的 Netscape 格式 Cookie 文本文件。")
    lines = text.lstrip("\ufeff").splitlines()
    if not lines or not lines[0].strip().startswith(("# Netscape HTTP Cookie File", "# HTTP Cookie File")):
        raise SessionError("文件不是 Netscape 格式的 Cookie 文件；不要上传账号密码或其他文件。")
    cookies = []
    try:
        for line in lines[1:]:
            http_only = line.startswith("#HttpOnly_")
            if http_only:
                line = line[len("#HttpOnly_"):]
            elif not line or line.startswith("#"):
                continue
            parts = line.split("\t")
            if len(parts) != 7:
                raise ValueError("Invalid row")
            domain, include_subdomains, path, secure, expiry, name, value = parts
            if not supported_host(domain):
                continue
            if not name or include_subdomains not in {"TRUE", "FALSE"} or secure not in {"TRUE", "FALSE"}:
                raise ValueError("Invalid cookie")
            expires = int(expiry) if expiry else -1
            cookies.append({"name": name, "value": value, "domain": domain, "path": path or "/",
                            "expires": expires or -1, "secure": secure == "TRUE", "httpOnly": http_only,
                            "sameSite": "None"})
    except (ValueError, TypeError):
        raise SessionError("Cookie 文件格式不正确，未保存；请重新导出 YouTube 域名的 Cookie。") from None
    (store or SessionStore()).save(cookies)


@contextmanager
def cookie_file(record, directory=None):
    from .browser import write_cookies
    parent = Path(directory if directory is not None else DIRECTORY).resolve()
    parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="youtube_cookies_", dir=parent) as name:
        work = Path(name).resolve()
        if work.parent != parent:
            raise SessionError("YouTube 临时会话目录检查失败。")
        path = work / "cookies.txt"
        write_cookies(path, filtered_cookies(record["cookies"]))
        yield path


def login(target_url=None):
    from .browser import MANUAL_PAGE, wait_for_manual
    if target_url:
        from .youtube import video_url
        target_url = video_url(target_url)
    if str(VENDOR) not in sys.path:
        sys.path.insert(0, str(VENDOR))
    from playwright.sync_api import sync_playwright
    panel = MANUAL_PAGE.replace("下载工具 · 手动操作", "下载工具 · 保存 YouTube 登录")
    panel = panel.replace("请先在视频页面完成操作", "请手动登录 YouTube 后保存")
    panel = panel.replace("如有网站验证，请手动完成；然后点击播放目标视频。", "请用你自己的账号登录 YouTube；账号确认与年龄确认由你在网站自行完成。")
    panel = panel.replace("确认视频开始播放后，回到本页点击“开始检测”。", "确认目标作品能正常播放后，回到本页点击“保存并用于以后下载”。")
    panel = panel.replace("等待期间窗口会保持打开。无需在本页输入账号或密码。", "仅保存当前 Windows 用户在本机的 YouTube 会话。账号密码仅在 Google 官方页面输入。如果 Google 拒绝此浏览器，可取消后在设置中导入本人导出的 YouTube Cookie 文件。")
    panel = panel.replace(">开始检测</button>", ">保存并用于以后下载</button>")
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=False)
        context = browser.new_context()
        page = context.new_page()
        try:
            try:
                page.goto(target_url or "https://www.youtube.com/", wait_until="domcontentloaded", timeout=30000)
            except Exception:
                print("YouTube 页面加载尚未完成，可在独立窗口中手动打开 YouTube 后登录。", flush=True)
            wait_for_manual(page, context, lambda: "需要登录" if not has_login(context.cookies()) else "",
                panel_html=panel,
                ready_error="尚未检测到 YouTube 登录，请在旁边的 YouTube 网页完成登录，再保存。",
                instructions="请在独立 YouTube 窗口手动登录，确认目标作品可播放，再到“下载工具 · 保存 YouTube 登录”标签点击保存。",
                ready_message="已收到保存操作，正在加密保存 YouTube 会话。")
            SessionStore().save(context.cookies(), page.evaluate("navigator.userAgent"))
            print("YouTube 会话已在本机加密保存。下载时勾选“允许 YouTube 登录后备”即可在匿名失败后使用。", flush=True)
        finally:
            if browser.is_connected():
                browser.close()
