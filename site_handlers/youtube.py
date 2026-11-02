import re
from urllib.parse import parse_qs, urlsplit


def video_url(url):
    parsed = urlsplit(url)
    host = (parsed.hostname or "").lower().rstrip(".")
    parts = [part for part in parsed.path.split("/") if part]
    identifier = None
    if host == "youtu.be" or host.endswith(".youtu.be"):
        identifier = parts[0] if parts else None
    elif parsed.path.rstrip("/") == "/watch":
        identifier = parse_qs(parsed.query).get("v", [None])[0]
    elif len(parts) >= 2 and parts[0] in ("shorts", "live", "embed"):
        identifier = parts[1]
    if not identifier or not re.fullmatch(r"[A-Za-z0-9_-]{11}", identifier):
        raise ValueError("请输入具体的 YouTube 视频链接，例如 watch、Shorts 或 youtu.be 链接。")
    return "https://www.youtube.com/watch?v=" + identifier


def run(url, options):
    from .yt_engine import run as download
    from . import youtube_session
    url = video_url(url)
    try:
        return download(url, options, label="YouTube", force_mkv=True)
    except RuntimeError as error:
        anonymous_error = str(error)
    if not options.use_youtube_login:
        if any(word in anonymous_error.lower() for word in ("confirm your age", "sign in", "private video", "members-only")):
            raise RuntimeError(anonymous_error + "\n\n此作品需要有效的 YouTube 登录或访问资格。请在设置中保存本人 YouTube 会话，再勾选“允许 YouTube 登录后备”创建重试任务。") from None
        raise RuntimeError(anonymous_error) from None
    record = youtube_session.SessionStore().load()
    if not record:
        raise RuntimeError("已允许 YouTube 登录后备，但尚未保存会话。请在设置中手动登录或导入本人 YouTube Cookie 后再试。\n" + anonymous_error) from None
    print("匿名解析未成功，使用你在本工具中主动保存的 YouTube 会话。", flush=True)
    headers = {"User-Agent": record["user_agent"]} if record["user_agent"] else None
    try:
        with youtube_session.cookie_file(record) as cookies:
            return download(url, options, label="YouTube（已登录）", force_mkv=True,
                            cookiefile=cookies, headers=headers)
    except RuntimeError as error:
        raise RuntimeError(str(error) + "\n已使用保存的 YouTube 会话。请确认同一账号能在网站播放该作品；会话过期时需更新。") from error
