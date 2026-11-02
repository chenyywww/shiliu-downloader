"""An explicitly saved, per-user Douyin session from the tool's own browser."""
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
MAGIC = b"DYLOGIN1\0"


class SessionError(RuntimeError):
    pass


def supported_host(host):
    host = (host or "").lower().strip(".")
    return any(host == name or host.endswith("." + name) for name in ("douyin.com", "iesdouyin.com"))


def filter_state(state):
    return {
        "cookies": [cookie for cookie in state.get("cookies", []) if supported_host(cookie.get("domain"))],
        "origins": [item for item in state.get("origins", []) if supported_host(urlsplit(item.get("origin", "")).hostname)],
    }


def has_login(cookies):
    return any(
        supported_host(cookie.get("domain"))
        and cookie.get("name") in {"sessionid", "sessionid_ss", "sid_tt"}
        and cookie.get("value")
        and (float(cookie.get("expires", -1)) <= 0 or float(cookie["expires"]) > time.time())
        for cookie in cookies
    )


def valid_user_agent(value):
    return isinstance(value, str) and 0 < len(value) <= 4096 and not any(c in value for c in "\r\n\0")


class SessionStore:
    def __init__(self, directory=None):
        self.directory = Path(directory if directory is not None else DIRECTORY).resolve()
        self.path = self.directory / "douyin-session.bin"

    def load(self):
        if not self.path.is_file():
            return None
        try:
            blob = self.path.read_bytes()
            if len(blob) > 8 * 1024 * 1024 or not blob.startswith(MAGIC):
                raise ValueError("Invalid saved session")
            record = json.loads(crypt(blob[len(MAGIC):], decrypt=True))
            state = filter_state(record["state"])
            if record.get("version") != 1 or not has_login(state["cookies"]) or not valid_user_agent(record.get("user_agent")):
                raise ValueError("Expired saved session")
            return {"state": state, "user_agent": record["user_agent"]}
        except Exception:
            raise SessionError("本工具保存的抖音登录状态已过期或无法读取，请重新运行 login_douyin.cmd。") from None

    def save(self, state, user_agent):
        state = filter_state(state)
        if not has_login(state["cookies"]):
            raise SessionError("尚未检测到抖音登录，请先在视频网页中手动登录。")
        if not valid_user_agent(user_agent):
            raise SessionError("浏览器标识无效，未保存抖音状态。")
        data = json.dumps({"version": 1, "state": state, "user_agent": user_agent}).encode("utf-8")
        if len(data) > 8 * 1024 * 1024:
            raise SessionError("抖音会话状态过大，未保存。")
        try:
            encrypted = MAGIC + crypt(data)
        except Exception:
            raise SessionError("无法加密本机抖音登录状态，未保存。") from None
        self.directory.mkdir(parents=True, exist_ok=True)
        staged = self.directory / ("douyin-" + uuid.uuid4().hex + ".tmp")
        try:
            with staged.open("xb") as stream:
                stream.write(encrypted)
            os.replace(staged, self.path)
        finally:
            staged.unlink(missing_ok=True)


@contextmanager
def cookie_file(record):
    from .browser import write_cookies
    DIRECTORY.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="douyin_cookies_", dir=DIRECTORY) as name:
        work = Path(name).resolve()
        if work.parent != DIRECTORY.resolve():
            raise SessionError("抖音会话临时目录检查失败。")
        path = work / "cookies.txt"
        write_cookies(path, filter_state(record["state"])["cookies"])
        yield path


def login(target_url=None):
    from .browser import MANUAL_PAGE, wait_for_manual
    if target_url:
        from .douyin import video_url
        target_url = video_url(target_url)
    if str(VENDOR) not in sys.path:
        sys.path.insert(0, str(VENDOR))
    from playwright.sync_api import sync_playwright

    panel = MANUAL_PAGE.replace("下载工具 · 手动操作", "下载工具 · 保存抖音登录")
    panel = panel.replace("请先在视频页面完成操作", "请手动登录抖音后保存")
    panel = panel.replace("如有网站验证，请手动完成；然后点击播放目标视频。", "请在抖音网页中手动登录，需要的验证由你完成。")
    panel = panel.replace("确认视频开始播放后，回到本页点击“开始检测”。", "回到本页点击“保存并用于以后下载”，授权工具在本机复用该抖音会话。")
    panel = panel.replace("等待期间窗口会保持打开。无需在本页输入账号或密码。", "登录状态仅供当前 Windows 账号在本机使用，并以加密形式保存。账号和密码请仅在抖音页面输入。")
    panel = panel.replace(">开始检测</button>", ">保存并用于以后下载</button>")
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=False)
        context = browser.new_context()
        page = context.new_page()
        try:
            try:
                page.goto(target_url or "https://www.douyin.com/", wait_until="domcontentloaded", timeout=30000)
            except Exception:
                print("页面尚未加载完成，可在这个独立窗口手动打开抖音后登录。", flush=True)
            wait_for_manual(
                page, context, lambda: "需要登录" if not has_login(context.cookies()) else "",
                panel_html=panel,
                ready_error="尚未检测到抖音登录，请先在旁边的抖音网页完成登录，再保存。",
                instructions="请在独立抖音窗口手动登录，再到“下载工具 · 保存抖音登录”标签点击“保存并用于以后下载”。窗口会保持打开。",
                ready_message="已收到保存操作，正在加密保存抖音登录状态。",
            )
            state = context.storage_state(indexed_db=True)
            SessionStore().save(state, page.evaluate("navigator.userAgent"))
            print("抖音登录状态已在本机加密保存；之后使用 download.cmd，匿名解析失败时会尝试这个会话。", flush=True)
        finally:
            if browser.is_connected():
                browser.close()
