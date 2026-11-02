"""Resolve metadata only. Never invoke the downloader or FFmpeg here."""
import argparse
from contextlib import redirect_stdout
import json
import os
from pathlib import Path
import shutil
import sys
from unittest.mock import patch
from urllib.parse import urlsplit

from download_events import emit
from download_router import select_route
from site_handlers.common import VENDOR
from url_input import extract_url
from resolve_net import MetadataClient, native_transport, validate_url
from resolution import plan, source, clean_headers


def resolve_hls(url, label, allow_private=False):
    import download_hls as backend
    client = MetadataClient(allow_private)
    try:
        with patch.object(backend, "fetch", client.fetch):
            master, lines, referer = backend.resolve_input(url, None)
            variants = []
            for line in lines:
                if line.startswith("#EXT-X-STREAM-INF:"):
                    dims = backend.attributes(line).get("RESOLUTION", "0x0").split("x")
                    if len(dims) == 2 and all(part.isdigit() for part in dims):
                        variants.append(tuple(map(int, dims)))
            width, height = max(variants, key=lambda item: item[0] * item[1], default=(None, None))
            value = plan(url, label, [source(master, "hls", headers={"User-Agent": client_agent(), "Referer": referer or ""}, width=width, height=height)])
            value["metadata_bytes"] = client.received
            return value
    except Exception:
        if len(client.media) == 1:
            return plan(url, label, [source(client.media[0], headers={"User-Agent": client_agent()})])
        raise


def client_agent():
    from resolve_net import AGENT
    return AGENT


def resolve_native(url, label, allow_private=False):
    if str(VENDOR) not in sys.path:
        sys.path.insert(0, str(VENDOR))
    import yt_dlp
    params = {"quiet": True, "no_warnings": True, "skip_download": True, "simulate": True,
              "noplaylist": True, "playlistend": 1, "cachedir": False, "socket_timeout": 12,
              "retries": 0, "extractor_retries": 0, "postprocessors": [], "format": "bv*+ba/b"}
    if shutil.which("node"):
        params["js_runtimes"] = {"node": {"path": shutil.which("node")}}
    with yt_dlp.YoutubeDL(params) as ydl:
        with native_transport(ydl, allow_private) as client:
            info = ydl.extract_info(url, download=False)
        if info.get("entries"):
            info = next(item for item in info["entries"] if item)
        chosen = info.get("requested_formats") or [info]
        sources = []
        for item in chosen:
            address = item.get("url")
            if not address:
                continue
            protocol = item.get("protocol", "")
            kind = "hls" if "m3u8" in protocol else ("dash" if urlsplit(address).path.endswith(".mpd") else "file")
            role = "audio" if item.get("vcodec") == "none" else ("video" if item.get("acodec") == "none" else "combined")
            headers = {**info.get("http_headers", {}), **item.get("http_headers", {})}
            sources.append(source(address, kind, role, headers, item.get("width"), item.get("height")))
        if not sources:
            raise ValueError("解析器没有返回可下载资源。")
        value = plan(url, label, sources, info.get("title"))
        value["metadata_bytes"] = client.received
        return value


def resolve(url, allow_private=False, browser=True):
    url = extract_url(url)
    validate_url(url, allow_private)
    route = select_route(url)
    label, handler = route["name"], route["handler"]
    emit("route", name=label, handler="resolve", url=url)
    emit("progress", label="仅解析网页与播放清单", fraction=None)
    if urlsplit(url).path.lower().endswith((".mp4", ".webm", ".mkv", ".m4a")):
        return plan(url, label, [source(url)])
    if handler == "douyin":
        from site_handlers.douyin import video_url
        import download_hls as backend
        client = MetadataClient(allow_private)
        with patch.object(backend, "fetch", client.fetch):
            url = video_url(url)
    elif handler == "youtube":
        from site_handlers.youtube import video_url
        url = video_url(url)
    elif handler == "x":
        from site_handlers.x import canonical_url
        url = canonical_url(url)
    errors = []
    strategies = [resolve_native] if handler in {"youtube", "douyin", "x"} else [resolve_hls, resolve_native]
    for operation in strategies:
        try:
            return operation(url, label, allow_private)
        except Exception as error:
            errors.append(str(error)[-1500:])
    if handler == "x":
        try:
            import re
            from site_handlers import x_public
            # This service receives only the public post id; no personal session.
            items = x_public.resolve(re.search(r"/status/(\d+)", url)[1])
            return plan(url, label, [source(item["url"]) for item in items])
        except Exception as error:
            errors.append(str(error)[-500:])
    if browser and handler not in {"youtube", "x", "douyin"}:
        try:
            from resolve_browser import discover
            return discover(url, label, allow_private)
        except Exception as error:
            errors.append(str(error)[-1000:])
    return plan(url, label, reason="云端未取得可直接交付的资源，请在本机重新解析。" + "\n" + "\n".join(errors)[-2500:])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("url")
    parser.add_argument("--allow-private-test", action="store_true")
    args = parser.parse_args()
    with redirect_stdout(sys.stderr):
        result = resolve(args.url, args.allow_private_test)
    emit("resolved", result=result)
    if os.environ.get("VIDEO_DOWNLOADER_EVENTS") == "1":
        print("仅解析完成。" if result["status"] == "ready" else "需要在本机重新解析。", flush=True)
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        emit("error", message=str(error))
        print("解析失败：" + str(error), file=sys.stderr)
        raise SystemExit(1)
