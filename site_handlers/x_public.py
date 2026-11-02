"""Resolve public X media through the user-selected public download service."""

from collections import OrderedDict
from dataclasses import replace
import http.cookiejar
import json
from pathlib import Path
import re
import subprocess
import urllib.error
import urllib.parse
import urllib.request

from .common import OUTPUT_DIR

SERVICE = "https://xunlangbot.com"
AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


class PublicResolverError(RuntimeError):
    pass


def read_response(opener, request, limit=3 * 1024 * 1024):
    try:
        with opener.open(request, timeout=30) as response:
            body = response.read(limit + 1)
    except urllib.error.HTTPError as error:
        if error.code == 429:
            raise PublicResolverError("公开解析服务暂时限流，请稍后重试。") from None
        raise PublicResolverError(f"公开解析服务返回 HTTP {error.code}。") from None
    except urllib.error.URLError:
        raise PublicResolverError("暂时无法连接公开解析服务。") from None
    if len(body) > limit:
        raise PublicResolverError("公开解析服务的响应超过预期大小。")
    return body


def csrf_token(page):
    match = re.search(
        r"""http\.send\(\s*(['"])(csrf_test_name=[^'"]+)\1\s*\)""",
        page,
    )
    if not match:
        raise PublicResolverError("解析站页面结构发生变化，未找到有效的网页校验参数。")
    values = urllib.parse.parse_qs(match.group(2), max_num_fields=4)
    token = values.get("csrf_test_name", [""])[0]
    if not re.fullmatch(r"[A-Za-z0-9_-]{16,256}", token):
        raise PublicResolverError("解析站返回的网页校验参数无效。")
    return token


def valid_media_url(url):
    if not isinstance(url, str):
        return False
    try:
        parsed = urllib.parse.urlsplit(url)
        host = (parsed.hostname or "").lower().rstrip(".")
        port = parsed.port
    except ValueError:
        return False
    return (
        parsed.scheme == "https"
        and port in (None, 443)
        and parsed.username is None and parsed.password is None
        and (host == "video.twimg.com" or host.endswith(".video.twimg.com"))
        and parsed.path.lower().endswith(".mp4")
    )


def variant_rank(item):
    dimensions = re.search(r"/(\d{2,5})x(\d{2,5})/", urllib.parse.urlsplit(item["url"]).path)
    if dimensions:
        width, height = map(int, dimensions.groups())
        quality, area = max(width, height), width * height
    else:
        numbers = re.findall(r"\d+", str(item.get("resolution", "")))
        quality, area = (max(map(int, numbers)), 0) if numbers else (0, 0)
    try:
        bitrate = int(item.get("size", 0))
    except (ValueError, TypeError):
        bitrate = 0
    return quality, area, bitrate


def choose_variants(result, media_index=None):
    if result.get("success") not in (True, 1, "true", "1"):
        message = str(result.get("message") or "未取得可下载结果。")
        raise PublicResolverError("公开解析服务未成功：" + message[:500])
    variants = result.get("variants")
    if not isinstance(variants, list):
        raise PublicResolverError("公开解析服务返回的数据结构不受支持。")
    groups = OrderedDict()
    for item in variants:
        if not isinstance(item, dict) or not valid_media_url(item.get("url")):
            continue
        url = item["url"]
        asset = re.search(r"/(?:ext_tw_video|amplify_video)/(\d+)/", urllib.parse.urlsplit(url).path)
        identity = asset.group(1) if asset else urllib.parse.urlsplit(url).path
        old = groups.get(identity)
        if old is None or variant_rank(item) > variant_rank(old):
            groups[identity] = item
    if not groups:
        raise PublicResolverError("解析站未返回可验证的 X 视频 MP4 地址。")
    # This service accepts a tweet id, not an original /video/N index. Never
    # silently substitute a different clip in a multi-video tweet.
    if media_index is not None and (media_index != 1 or len(groups) != 1):
        raise PublicResolverError(
            "该链接指定了视频编号，但解析站不能可靠对应原帖中的编号。"
            "请使用原帖链接获取可用视频，或显式选择登录会话处理。"
        )
    return list(groups.values())


def resolve(post_id, media_index=None):
    if not re.fullmatch(r"\d{1,25}", str(post_id)):
        raise ValueError("X 帖子编号无效。")
    # A fresh service-only cookie jar provides CSRF state. No personal X session
    # is loaded, attached to this opener, or sent to the resolver.
    opener = urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar())
    )
    page = read_response(
        opener, urllib.request.Request(SERVICE + "/download", headers={"User-Agent": AGENT})
    ).decode("utf-8", "replace")
    token = csrf_token(page)
    request = urllib.request.Request(
        SERVICE + "/twitter_download?" + urllib.parse.urlencode({"id": str(post_id)}),
        data=urllib.parse.urlencode({"csrf_test_name": token}).encode("ascii"),
        headers={
            "User-Agent": AGENT, "Origin": SERVICE, "Referer": SERVICE + "/download",
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        },
    )
    try:
        result = json.loads(read_response(opener, request))
    except (ValueError, UnicodeDecodeError):
        raise PublicResolverError("公开解析服务没有返回有效 JSON，请稍后重试。") from None
    if not isinstance(result, dict):
        raise PublicResolverError("公开解析服务没有返回有效结果。")
    return choose_variants(result, media_index)


def run(post_id, options, media_index=None):
    from . import yt_engine
    print("正在使用你指定的公开解析服务：xunlangbot.com", flush=True)
    videos = resolve(post_id, media_index)
    print(f"已取得 {len(videos)} 个视频的可用版本，开始从 X 媒体服务器下载。", flush=True)
    saved_files = []
    for index, video in enumerate(videos, start=1):
        if options.output:
            output = Path(options.output).expanduser().resolve().with_suffix(".mp4")
            if len(videos) > 1:
                output = output.with_name(output.stem + f"_{index:02d}.mp4")
        else:
            suffix = f"_{index:02d}" if len(videos) > 1 else ""
            output = OUTPUT_DIR / f"X_{post_id}{suffix}.mp4"
        media_options = replace(options, output=str(output), referer=None)
        saved = yt_engine.run(
            video["url"], media_options, label="X 公开媒体下载",
            format_selector="b", progress_label="下载 X 视频",
            headers={"User-Agent": AGENT},
        )
        if options.check:
            continue
        if saved is None or not saved.is_file():
            raise PublicResolverError("未生成可验证的视频文件。")
        try:
            info = json.loads(subprocess.check_output([
                "ffprobe", "-v", "error", "-show_entries",
                "stream=codec_type,width,height:format=duration,size",
                "-of", "json", str(saved),
            ], timeout=30))
        except (subprocess.SubprocessError, ValueError):
            raise PublicResolverError("文件已保存，但媒体完整性检查未通过。") from None
        if not any(stream.get("codec_type") == "video" for stream in info.get("streams", [])):
            raise PublicResolverError("返回的文件不包含可识别的视频流。")
        if float(info.get("format", {}).get("duration", 0)) <= 0:
            raise PublicResolverError("返回的视频没有有效时长。")
        print("视频格式与时长检查通过。", flush=True)
        saved_files.append(saved)
    return saved_files
