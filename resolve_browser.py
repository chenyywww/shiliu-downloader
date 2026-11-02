"""Browser metadata discovery with response interception and no media-body reads."""
import base64
import json
import sys
import time
from urllib.parse import urlsplit

from site_handlers.common import VENDOR
from resolution import plan, source
from resolve_net import MAX_RESPONSE, MAX_TOTAL, is_media, validate_url


def discover(url, label, allow_private=False, seconds=12, _depth=0):
    if str(VENDOR) not in sys.path:
        sys.path.insert(0, str(VENDOR))
    from playwright.sync_api import sync_playwright
    candidates = {}
    seen_bytes = 0
    next_frame = None
    result = None
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=True)
        context = browser.new_context(service_workers="block")
        context.route_web_socket("**/*", lambda ws: ws.close())
        page = context.new_page()
        cdp = context.new_cdp_session(page)

        def failed(identifier):
            cdp.send("Fetch.failRequest", {"requestId": identifier, "errorReason": "Aborted"})

        def request_guard(route):
            try:
                validate_url(route.request.url, allow_private)
                # A cross-origin frame can have its own browser process/CDP target.
                # Resolve chosen players as top-level pages rather than allowing
                # an unprotected child frame to receive opaque media responses.
                if route.request.frame != page.main_frame:
                    return route.abort()
                path = urlsplit(route.request.url).path.lower()
                if (route.request.resource_type in {"image", "font", "media", "websocket"}
                        or is_media("", route.request.url) or path.endswith((".jpeg", ".jpg", ".png", ".webp"))):
                    return route.abort()
                route.continue_()
            except Exception:
                route.abort()

        context.route("**/*", request_guard)

        def paused(event):
            nonlocal seen_bytes
            identifier = event["requestId"]
            address = event["request"]["url"]
            try:
                validate_url(address, allow_private)
                if "responseStatusCode" not in event:
                    cdp.send("Fetch.continueRequest", {"requestId": identifier})
                    return
                status = event["responseStatusCode"]
                headers = event.get("responseHeaders", [])
                lookup = {entry["name"].lower(): entry["value"] for entry in headers}
                mime = lookup.get("content-type", "").lower()
                path = urlsplit(address).path.lower()
                if is_media(mime, address) or mime.startswith("image/"):
                    failed(identifier)
                    return
                length = lookup.get("content-length", "")
                if length.isdigit() and int(length) > MAX_RESPONSE:
                    failed(identifier)
                    return
                if status in {301, 302, 303, 307, 308}:
                    cdp.send("Fetch.continueResponse", {"requestId": identifier})
                    return
                if not (mime.startswith("text/") or any(part in mime for part in ("json", "javascript", "mpegurl", "xml")) or path.endswith((".js", ".css", ".m3u8"))):
                    failed(identifier)
                    return
                stream = cdp.send("Fetch.takeResponseBodyAsStream", {"requestId": identifier})["stream"]
                chunks, count = [], 0
                try:
                    while True:
                        item = cdp.send("IO.read", {"handle": stream, "size": 65536})
                        chunk = base64.b64decode(item["data"]) if item.get("base64Encoded") else item["data"].encode()
                        count += len(chunk)
                        seen_bytes += len(chunk)
                        if count > MAX_RESPONSE or seen_bytes > MAX_TOTAL:
                            raise ValueError("元数据量超出限制。")
                        chunks.append(chunk)
                        if item.get("eof"):
                            break
                finally:
                    cdp.send("IO.close", {"handle": stream})
                body = b"".join(chunks)
                if body.lstrip(b"\xef\xbb\xbf \r\n").startswith(b"#EXTM3U"):
                    request_headers = event["request"].get("headers", {})
                    candidates[address] = source(address, "hls", headers={key: value for key, value in request_headers.items() if key.lower() in {"user-agent", "referer", "origin"}})
                output_headers = [entry for entry in headers if entry["name"].lower() not in {"content-length", "content-encoding", "transfer-encoding"}]
                cdp.send("Fetch.fulfillRequest", {"requestId": identifier, "responseCode": status,
                          "responseHeaders": output_headers, "body": base64.b64encode(body).decode()})
            except Exception:
                try:
                    failed(identifier)
                except Exception:
                    pass

        cdp.on("Fetch.requestPaused", paused)
        cdp.send("Fetch.enable", {"patterns": [{"urlPattern": "*", "requestStage": "Response"}]})
        try:
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=18000)
            except Exception:
                pass
            deadline = time.monotonic() + seconds
            while time.monotonic() < deadline:
                if candidates:
                    page.wait_for_timeout(600)
                    break
                page.wait_for_timeout(200)
            if not candidates:
                import download_hls as backend
                frames = backend.embedded_players(page.content(), page.url)
                if frames and _depth < 2:
                    next_frame = frames[0]
                else:
                    raise ValueError("仅解析浏览器没有找到清单；可能需要网站验证，请在本机处理。")
            else:
                # Distinct masters are not combined into a fake single video.
                items = list(candidates.values())
                masters = [item for item in items if "master" in urlsplit(item["url"]).path.lower()]
                if len(masters) == 1:
                    items = masters
                if len(items) != 1:
                    raise ValueError("发现多个无法可靠区分的播放清单，请在本机选择正片。")
                result = plan(url, label, items)
                result["metadata_bytes"] = seen_bytes
        finally:
            browser.close()
    if next_frame:
        result = discover(next_frame, label, allow_private, seconds, _depth + 1)
        result["input_url"] = url
    return result
