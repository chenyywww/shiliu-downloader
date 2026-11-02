"""Anonymous X extraction, a public resolver, and explicitly optional login."""

import re
import sys
from urllib.parse import urlsplit

from . import yt_engine, x_public
from .x_session import SessionError, SessionStore, cookie_file, login, x_host


def canonical_url(url):
    parsed = urlsplit(url)
    if not x_host(parsed.hostname):
        raise ValueError("请输入 X 或 Twitter 的帖子链接。")
    match = re.fullmatch(
        r"/(?P<user>[^/]+)/status/(?P<id>\d+)(?P<media>/(?:video|photo)/\d+)?/?",
        parsed.path,
    )
    if match and match["user"] != "i":
        return "https://x.com/" + match["user"] + "/status/" + match["id"] + (match["media"] or "")
    match = re.fullmatch(r"/i/(?:web/)?status/(?P<id>\d+)(?P<media>/(?:video|photo)/\d+)?/?", parsed.path)
    if match:
        return "https://x.com/i/web/status/" + match["id"] + (match["media"] or "")
    raise ValueError("请输入具体的 X 帖子或视频链接，主页地址不能直接下载。")


def run(url, options):
    url = canonical_url(url)
    if "/photo/" in url:
        raise ValueError("这是图片编号链接，请复制包含目标视频的帖子或视频链接。")
    errors = []
    try:
        print("X 流程 1/2：直接匿名解析。", flush=True)
        return yt_engine.run(url, options, label="X 匿名解析")
    except RuntimeError as error:
        errors.append("直接匿名解析：" + str(error))
        print("直接解析未成功，尝试已验证的公开解析服务。", flush=True)
    post_id = re.search(r"/status/(\d+)", url).group(1)
    selected = re.search(r"/video/(\d+)$", url)
    media_index = int(selected.group(1)) if selected else None
    if "/photo/" not in url:
        try:
            print("X 流程 2/2：公开解析服务。", flush=True)
            return x_public.run(post_id, options, media_index)
        except Exception as error:
            errors.append("公开解析服务：" + str(error))
    if not options.use_x_login:
        raise RuntimeError(
            "两种无需个人账号的 X 解析方式均未成功。\n"
            + "\n\n".join(errors)[-5000:]
            + "\n如要使用自己的 X 登录会话，可显式添加 --use-x-login；默认不会打开登录窗口。"
        )
    store = SessionStore()
    state = None
    if store.exists():
        try:
            state = store.load()
            print("正在使用本工具保存的 X 登录会话。", flush=True)
        except SessionError as error:
            print(str(error), flush=True)
    if state is None:
        if not sys.stdin.isatty() or options.check:
            raise RuntimeError(
                "你已选择使用个人登录会话，但尚未保存有效登录。"
                "请双击 login_x.cmd 手动登录并保存后再试。\n"
                + "\n\n".join(errors)[-3000:]
            )
        print("你已选择登录后备方式，请在独立窗口中手动登录并保存。", flush=True)
        state = login(target_url=url, store=store)
    try:
        with cookie_file(state, store) as path:
            return yt_engine.run(url, options, label="X（已登录）", cookiefile=path)
    except RuntimeError as error:
        raise RuntimeError(
            "已使用显式选择的 X 登录会话，但仍未取得该视频。请确认同一账号能查看内容；"
            "可用 login_x.cmd 更新会话。\n" + str(error)
        ) from error
