"""Normalize a single Douyin work; use native extraction then the shared browser."""
from dataclasses import replace
import re
from urllib.parse import parse_qs, urlsplit

from .common import OUTPUT_DIR


def video_url(url):
    parsed = urlsplit(url)
    host = (parsed.hostname or "").lower().rstrip(".")
    if parsed.scheme not in ("http", "https") or parsed.username or parsed.password:
        raise ValueError("请输入普通的抖音视频或分享短链接。")
    if host == "v.douyin.com":
        if not re.fullmatch(r"/[A-Za-z0-9_-]+/?", parsed.path):
            raise ValueError("抖音分享短链接格式不正确。")
        import download_hls
        _, resolved = download_hls.fetch(
            url, "https://www.douyin.com/", limit=1024 * 1024, return_url=True
        )
        if (urlsplit(resolved).hostname or "").lower() == host:
            raise RuntimeError("抖音短链接未返回具体作品，请重新复制该视频的分享链接。")
        return video_url(resolved)
    if host not in ("douyin.com", "www.douyin.com", "iesdouyin.com", "www.iesdouyin.com"):
        raise ValueError("抖音分享链接跳转到了不支持的域名。")
    match = re.fullmatch(r"/(?:share/)?video/(\d+)/?", parsed.path)
    identifier = match[1] if match else next(
        (value for value in parse_qs(parsed.query).get("modal_id", []) if value.isdigit()), None
    )
    if not identifier:
        raise ValueError(
            "这是抖音首页、推荐页或其他非作品地址，无法确定要下载哪条视频。"
            "请用目标视频的“分享 → 复制链接”；整段分享文案也可以直接粘贴。"
        )
    return "https://www.douyin.com/video/" + identifier


def run(url, options):
    from . import browser, yt_engine, douyin_session
    url = video_url(url)
    identifier = urlsplit(url).path.rsplit("/", 1)[-1]
    if not options.output:
        options = replace(options, output=str(OUTPUT_DIR / f"Douyin_{identifier}.mp4"))
    print(f"已定位抖音作品：{identifier}", flush=True)
    try:
        return yt_engine.run(url, options, label="抖音匿名解析")
    except RuntimeError as error:
        direct_error = str(error)
    session = None
    try:
        session = douyin_session.SessionStore().load()
    except douyin_session.SessionError as error:
        print(str(error), flush=True)
    if session:
        print("匿名接口未成功，尝试你在本工具中主动保存的抖音会话。", flush=True)
        print("正在使用你在本工具中手动保存的抖音登录状态。", flush=True)
        options = replace(options, browser_state=session["state"], browser_user_agent=session["user_agent"])
        try:
            with douyin_session.cookie_file(session) as cookies:
                return yt_engine.run(url, options, label="抖音直接解析", cookiefile=cookies,
                                     headers={"User-Agent": session["user_agent"]})
        except RuntimeError as error:
            direct_error += "\n\n已保存会话：" + str(error)
    print("抖音直接接口未成功，改用独立浏览器读取播放资源。", flush=True)
    try:
        return browser.run(url, replace(options, expected_video_id=identifier, passive_browser=True))
    except browser.BrowserInteractionCancelled:
        raise
    except Exception as error:
        hint = ("\n已尝试本工具保存的登录状态；重复登录不能保证解决请求签名或网站访问限制。"
                if session else "\n若同一作品在已登录的浏览器中能播放，可运行 login_douyin.cmd，在独立窗口手动登录并保存本机状态。")
        raise RuntimeError("抖音处理未成功：\n" + direct_error[-2000:] + "\n\n浏览器读取：" + str(error) + hint) from error
