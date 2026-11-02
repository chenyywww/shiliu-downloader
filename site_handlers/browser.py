from dataclasses import replace
import math
from pathlib import Path
import sys
import tempfile
import time
from urllib.parse import parse_qs, urljoin, urlsplit

from .common import OUTPUT_DIR, VENDOR


class BrowserChallengeError(RuntimeError):
    pass


class BrowserInteractionCancelled(RuntimeError):
    pass


def hls_metadata(text, url):
    if not text.lstrip("\ufeff \r\n\t").startswith("#EXTM3U"):
        return {}
    lines = [line.strip() for line in text.splitlines()]
    master = any(line.startswith("#EXT-X-STREAM-INF:") for line in lines)
    children = []
    if master:
        for index, line in enumerate(lines):
            if line.startswith("#EXT-X-STREAM-INF:"):
                child = next((part for part in lines[index + 1:] if part and not part.startswith("#")), None)
                if child:
                    children.append(urljoin(url, child))
    durations = []
    for line in lines:
        if line.startswith("#EXTINF:"):
            try:
                value = float(line.split(":", 1)[1].split(",", 1)[0])
                if math.isfinite(value) and value >= 0:
                    durations.append(value)
            except ValueError:
                pass
    return {"master": master, "children": children,
            "vod": not master and "#EXT-X-ENDLIST" in lines,
            "duration": sum(durations), "valid": True}


MANUAL_PAGE = """<!doctype html>
<html lang="zh-CN"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>下载工具 · 手动操作</title>
<style>
body{max-width:660px;margin:70px auto;padding:24px;font:18px/1.8 sans-serif;
background:#f5f7fb;color:#172554}main{background:white;padding:32px;border-radius:16px}
button{font:inherit;padding:10px 18px;margin:8px 8px 0 0;cursor:pointer;
border:1px solid #cad4e8;border-radius:8px}#ready{background:#1d4ed8;color:white}
#status{color:#9a3412}
</style><main>
<h1>请先在视频页面完成操作</h1>
<ol><li>切换到旁边的视频网页标签。</li>
<li>如有网站验证，请手动完成；然后点击播放目标视频。</li>
<li>确认视频开始播放后，回到本页点击“开始检测”。</li></ol>
<p>等待期间窗口会保持打开。无需在本页输入账号或密码。</p>
<p id="status" role="status"></p>
<button id="target" onclick="window.videoDownloaderAction('target')">返回视频页</button>
<button id="ready" onclick="window.videoDownloaderAction('ready')">开始检测</button>
<button id="cancel" onclick="window.videoDownloaderAction('cancel')">取消</button>
</main></html>"""


def wait_for_manual(page, context, challenge_reason, *, auto_resume=False,
                    panel_html=None, ready_error="视频网页仍停留在验证页，请完成验证并播放后再试。",
                    instructions=None, ready_message="开始检测当前网页的媒体请求。"):
    """Keep the event loop and network listener alive while the user operates the site."""
    from playwright.sync_api import Error as BrowserError
    from download_events import emit
    emit("interaction", waiting=True, message=instructions or (
        "请在独立浏览器中完成网站验证，通过后自动继续。" if auto_resume
        else "请在独立浏览器中播放正片，再点击工具页里的开始检测。"))

    actions = []
    panel = context.new_page()

    def action(source, name):
        # Only the tool's own blank control page can confirm or cancel the operation.
        if (
            source["page"] == panel
            and source["frame"] == panel.main_frame
            and source["frame"].url == "about:blank"
            and name in ("target", "ready", "cancel")
        ):
            actions.append(name)

    try:
        panel.expose_binding("videoDownloaderAction", action)
        panel.set_content(panel_html or MANUAL_PAGE)
        if auto_resume:
            panel.evaluate("""() => {
                document.title = '下载工具 · 等待网站验证';
                document.querySelector('h1').textContent = '请完成视频网站的验证';
                document.querySelector('ol').innerHTML = '<li>切换到旁边的视频网页。</li>' +
                    '<li>如有验证，请手动完成。通过后工具会自动继续。</li>';
                document.querySelector('main > p').textContent = '无需播放视频，也无需点击开始检测。';
                document.querySelector('#ready').hidden = true;
            }""")
            print("请在视频网页中手动完成网站验证；通过后自动继续，无需播放或点击检测。", flush=True)
        else:
            print(
                instructions or "请在新开的浏览器中手动播放目标视频，再切换到"
                "“下载工具 · 手动操作”标签，点击“开始检测”。窗口会保持打开。",
                flush=True,
            )
        page.bring_to_front()
        while not page.is_closed() and not panel.is_closed():
            if auto_resume and not challenge_reason():
                print("网站验证已通过，自动读取播放器配置。", flush=True)
                return
            if actions:
                choice = actions.pop(0)
                if choice == "cancel":
                    raise BrowserInteractionCancelled("已取消本次浏览器检测。")
                if choice == "target":
                    page.bring_to_front()
                elif choice == "ready":
                    if challenge_reason():
                        panel.locator("#status").evaluate(
                            "(element, text) => element.textContent = text", ready_error
                        )
                    else:
                        print(ready_message, flush=True)
                        return
            panel.wait_for_timeout(250)
        raise BrowserInteractionCancelled("浏览器或手动操作标签已关闭，已取消本次检测。")
    except BrowserError as error:
        raise BrowserInteractionCancelled("浏览器手动操作已中断，请重新运行。") from error
    finally:
        emit("interaction", waiting=False)
        if not panel.is_closed():
            try:
                panel.close()
            except BrowserError:
                pass


def capture(url, options):
    try:
        candidates, videos, cookies = capture_session(url, options)
    except BrowserChallengeError:
        if options.show_browser or options.check or not sys.stdin.isatty():
            raise
        print(
            "检测到网站验证，改为打开独立浏览器，等待你手动操作。", flush=True
        )
        candidates, videos, cookies = capture_session(
            url, replace(options, show_browser=True)
        )
    return rank_candidates(candidates, videos), cookies


def playable_media_seen(candidates, videos):
    """A loading animation alone must not end network discovery."""
    if any(item.get("configured") for item in candidates.values()):
        return True
    for frame, items in videos.items():
        for video in items:
            if not video.get("src") or video.get("ready", 0) < 2:
                continue
            if video.get("tag") == "video" and video.get("area", 0) <= 0:
                continue
            for candidate in candidates.values():
                if candidate["url"] == video["src"]:
                    return True
                if candidate["frame"] == frame and candidate["kind"] in ("hls", "dash"):
                    return True
    return False


def media_matches_video_id(url, identifier):
    query = parse_qs(urlsplit(url).query)
    return any(identifier in query.get(key, []) for key in ("__vid", "aweme_id", "item_id"))


def probe_configured_sources(page, candidates, attempted, deadline):
    """Read literal player settings and request manifests without starting playback."""
    import download_hls as backend
    for frame in list(page.frames)[:6]:
        if time.monotonic() >= deadline:
            return False
        try:
            sources = backend.player_candidates(frame.content(), frame.url)
        except Exception:
            continue
        for source in sources[:3]:
            if source in attempted or time.monotonic() >= deadline:
                continue
            attempted.add(source)

            def read_manifest(location):
                remaining = min(5000, max(250, int((deadline - time.monotonic()) * 1000)))
                return frame.evaluate("""async ({url, timeout}) => {
                    const controller = new AbortController();
                    const timer = setTimeout(() => controller.abort(), timeout);
                    try {
                        const response = await fetch(url, {
                            credentials: 'same-origin', signal: controller.signal
                        });
                        if (!response.ok) return null;
                        const text = await response.text();
                        return text.length <= 2097152 ? {url: response.url, text} : null;
                    } finally { clearTimeout(timer); }
                }""", {"url": location, "timeout": remaining})

            try:
                result = read_manifest(source)
                if not result:
                    continue
                metadata = hls_metadata(result["text"], result["url"])
                if not metadata.get("valid"):
                    continue
                if metadata["master"]:
                    try:
                        chosen = backend.select_variant(result["url"], result["text"].splitlines())
                        child_url = chosen[1] if chosen else None
                    except ValueError:
                        child_url = next(iter(metadata["children"]), None)
                    if not child_url or time.monotonic() >= deadline:
                        continue
                    child = read_manifest(child_url)
                    child_meta = hls_metadata(child["text"], child["url"]) if child else {}
                    if not child_meta.get("vod"):
                        continue
                    metadata.update(vod=True, duration=child_meta["duration"])
                if not metadata.get("vod"):
                    continue
                item = candidates.get(result["url"])
                if item:
                    item.update(configured=True, manifest=metadata)
                    print("已从网页配置读取有效点播清单，无需播放视频。", flush=True)
                    return True
            except Exception:
                continue
    return False


def capture_session(url, options):
    if str(VENDOR) not in sys.path:
        sys.path.insert(0, str(VENDOR))
    from playwright.sync_api import sync_playwright, TimeoutError as BrowserTimeout
    from .web_session import ClearanceStore

    candidates = {}
    videos = {}
    documents = {}
    access_errors = []
    expected_id = getattr(options, "expected_video_id", None)
    store = ClearanceStore()
    saved = store.load(url)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            channel="chrome", headless=not options.show_browser
        )
        context_options = {}
        if getattr(options, "browser_state", None):
            context_options["storage_state"] = options.browser_state
            if options.browser_user_agent:
                context_options["user_agent"] = options.browser_user_agent
        elif saved:
            context_options = {"user_agent": saved["user_agent"],
                               "storage_state": {"cookies": saved["cookies"], "origins": []}}
        context = browser.new_context(**context_options)
        context.set_default_timeout(1000)
        page = context.new_page()

        def response_received(response):
            if expected_id and response.status == 403 and "/aweme/detail/" in response.url:
                try:
                    if "ArgusSecurityPlugin Sign Invalid" in response.text()[:1000]:
                        access_errors.append("抖音作品接口返回 403：ArgusSecurityPlugin Sign Invalid（请求签名校验未通过）。")
                except Exception:
                    pass
            if response.request.resource_type == "document":
                try:
                    documents[response.request.frame] = (
                        response.status, response.headers.get("cf-mitigated", "")
                    )
                except Exception:
                    pass
            if not 200 <= response.status < 300 or response.request.method != "GET":
                return
            media_url = response.url
            parsed = urlsplit(media_url)
            if parsed.scheme not in ("http", "https"):
                return
            mime = response.headers.get("content-type", "").lower()
            if "text/html" in mime:
                return
            path = parsed.path.lower()
            if path.endswith(".m3u8") or "mpegurl" in mime:
                kind = "hls"
            elif path.endswith(".mpd") or "dash+xml" in mime:
                kind = "dash"
            elif mime.startswith(("video/", "audio/")) or path.endswith((".mp4", ".webm")):
                kind = "file"
            else:
                return
            if expected_id and not media_matches_video_id(media_url, expected_id):
                return
            try:
                frame_url = response.request.frame.url
            except Exception:
                frame_url = page.url
            request_headers = response.request.headers
            headers = {
                "User-Agent": request_headers.get("user-agent", ""),
                "Referer": request_headers.get("referer", ""),
                "Origin": request_headers.get("origin", ""),
            }
            old = candidates.get(media_url)
            item = {
                "url": media_url, "kind": kind, "frame": frame_url,
                "headers": headers, "order": old["order"] if old else len(candidates),
            }
            if old and old.get("configured"):
                item["configured"] = True
            if expected_id:
                item["configured"] = True
            if kind == "hls":
                try:
                    length = response.headers.get("content-length", "")
                    if not length.isdigit() or int(length) <= 2 * 1024 * 1024:
                        text = response.text()
                        if len(text) <= 2 * 1024 * 1024:
                            item["manifest"] = hls_metadata(text, media_url)
                except Exception:
                    pass
            candidates[media_url] = item

        context.on("response", response_received)

        def challenge_reason():
            for frame in page.frames:
                status, mitigation = documents.get(frame, (0, ""))
                if mitigation.lower() == "challenge":
                    return f"Cloudflare 人机验证（HTTP {status}）"
            return ""

        try:
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=30000)
            except BrowserTimeout:
                print("页面加载较慢，继续等待媒体请求。", flush=True)
            if getattr(options, "manual_browser", False):
                wait_for_manual(page, context, challenge_reason)
            clicked = set()
            attempted = set()
            last_probe = 0
            start = time.monotonic()
            last_count = 0
            changed = start
            while time.monotonic() - start < options.browser_wait:
                reason = challenge_reason()
                if reason:
                    # Give non-interactive challenges time to finish before asking the user.
                    if time.monotonic() - start >= 3:
                        if not options.show_browser:
                            raise BrowserChallengeError(
                                f"网页被{reason}拦截，尚未加载到视频。"
                                "请在交互窗口重试，或添加 --show-browser，"
                                "手动完成验证后会自动继续，无需播放或点击检测。"
                            )
                        wait_for_manual(page, context, challenge_reason, auto_resume=True)
                        attempted.clear()
                        start = changed = time.monotonic()
                    page.wait_for_timeout(400)
                    continue
                if time.monotonic() - last_probe >= 1:
                    last_probe = time.monotonic()
                    if probe_configured_sources(page, candidates, attempted, start + options.browser_wait):
                        break
                for frame in list(page.frames):
                    try:
                        if not getattr(options, "passive_browser", False):
                            frame.evaluate("""() => {
                            for (const video of document.querySelectorAll('video,audio')) {
                                video.muted = true;
                                if (video.paused) {
                                    try {
                                        const pending = video.play();
                                        if (pending && typeof pending.catch === 'function')
                                            pending.catch(() => {});
                                    } catch (_) {}
                                }
                            }
                        }""")
                        if not getattr(options, "passive_browser", False) and frame not in clicked:
                            button = frame.locator(
                                ".jw-display-icon-container .jw-icon-display,"
                                ".vjs-big-play-button,.plyr__control--overlaid,"
                                "button[aria-label='Play']"
                            ).first
                            if button.count() and button.is_visible():
                                button.click(timeout=1000)
                                clicked.add(frame)
                        items = frame.eval_on_selector_all(
                            "video,audio",
                            """items => items.map(v => ({
                                src: v.currentSrc || v.src,
                                ready: v.readyState,
                                duration: Number.isFinite(v.duration) ? v.duration : 0,
                                tag: v.tagName.toLowerCase(),
                                area: v.tagName === 'VIDEO' &&
                                    getComputedStyle(v).visibility !== 'hidden' ?
                                    v.getBoundingClientRect().width *
                                    v.getBoundingClientRect().height : 0
                            }))""",
                        )
                        if items:
                            videos[frame.url] = items
                    except Exception:
                        continue
                if len(candidates) != last_count:
                    last_count = len(candidates)
                    changed = time.monotonic()
                if (playable_media_seen(candidates, videos)
                        and time.monotonic() - start >= 5 and time.monotonic() - changed >= 3):
                    break
                page.wait_for_timeout(400)
            reason = challenge_reason()
            if reason:
                raise BrowserChallengeError(f"网页仍被{reason}拦截，尚未加载到视频。请手动完成验证后重试。")
            cookies = context.cookies()
            if not reason:
                try:
                    agent = page.evaluate("navigator.userAgent")
                    if store.save(url, cookies, agent):
                        print("已保存本工具的网站验证状态，下次优先直接读取网页。", flush=True)
                except Exception:
                    print("本次网站验证状态未能保存，下次可能需要重新验证。", flush=True)
            if not candidates:
                if access_errors:
                    raise RuntimeError(
                        access_errors[-1] + "没有取得指定作品的媒体资源；"
                        "这不等于必须登录账号，需要进一步核对正常浏览器的访问状态。"
                    )
                statuses = sorted({status for frame, (status, _) in documents.items()
                                   if frame in page.frames and status >= 400})
                detail = f"网页或播放器返回 HTTP {', '.join(map(str, statuses))}。" if statuses else ""
                raise RuntimeError(
                    detail + "浏览器没有发现可用媒体请求。"
                    "可添加 --manual-browser，在独立浏览器中手动播放后点击“开始检测”。"
                )
            return candidates, videos, cookies
        finally:
            if browser.is_connected():
                browser.close()


def rank_candidates(candidates, videos):
    configured = {item["url"] for item in candidates.values() if item.get("configured")}
    players = [
        (frame, video) for frame, items in videos.items() for video in items
        if video.get("tag") == "video" and video.get("area", 0) > 0 and video.get("src")
    ]
    primary = max(players, key=lambda item: item[1]["area"], default=None)
    if primary is not None:
        primary_frame, primary_video = primary
        associated = configured | {
            item["url"] for item in candidates.values()
            if item["frame"] == primary_frame or item["url"] == primary_video["src"]
        }
        if not associated:
            raise RuntimeError(
                "已发现主播放器，但捕获的媒体请求均无法对应这个播放器，"
                "可能只是广告。请确认正片开始播放后再检测。"
            )
    else:
        associated = configured
        if not configured and candidates and all(item["kind"] == "file" for item in candidates.values()):
            if not any(
                item["url"] == video.get("src")
                for item in candidates.values() for items in videos.values() for video in items
            ):
                raise RuntimeError(
                    "只发现无法对应当前播放器的媒体文件，可能是广告。"
                    "请使用 --manual-browser 手动播放目标视频后再检测。"
                )
    by_url = {item["url"]: item for item in candidates.values()}
    child_urls = set()
    for item in candidates.values():
        manifest = item.get("manifest", {})
        for child_url in manifest.get("children", []):
            child_urls.add(child_url)
            child = by_url.get(child_url, {}).get("manifest", {})
            if child.get("vod"):
                manifest["vod"] = True
                manifest["duration"] = max(manifest.get("duration", 0), child.get("duration", 0))
    # Retain the master rather than separately listing its already captured variants.
    groups = {}
    for item in candidates.values():
        if item["url"] in child_urls:
            continue
        parsed = urlsplit(item["url"])
        score = 30 if item["kind"] in ("hls", "dash") else 20
        if item.get("configured"):
            score += 600
        if item["url"] in associated:
            score += 200
        manifest = item.get("manifest", {})
        if manifest.get("master") or "master" in Path(parsed.path).name.lower():
            score += 30
        if primary is not None and primary_video.get("duration", 0) > 0 and manifest.get("valid"):
            duration = primary_video["duration"]
            if manifest.get("vod"):
                if abs(manifest.get("duration", 0) - duration) <= max(2, duration * 0.01):
                    score += 300
            elif not manifest.get("master"):
                score -= 300
        for video in videos.get(item["frame"], []):
            duration = video.get("duration", 0)
            if math.isfinite(duration) and duration > 60:
                score += 30
            if video.get("src") == item["url"]:
                score += 100
        item["score"] = score
        if item["kind"] in ("hls", "dash"):
            key = (item["kind"], parsed.netloc, str(Path(parsed.path).parent))
        else:
            key = (item["kind"], parsed.netloc, parsed.path)
        old = groups.get(key)
        if old is None or (score, -item["order"]) > (old["score"], -old["order"]):
            groups[key] = item
    return sorted(groups.values(), key=lambda item: (-item["score"], item["order"]))


def write_cookies(path, cookies):
    rows = ["# Netscape HTTP Cookie File"]
    for cookie in cookies:
        expires = cookie.get("expires", -1)
        values = [
            cookie.get("domain", ""),
            "TRUE" if cookie.get("domain", "").startswith(".") else "FALSE",
            cookie.get("path", "/"),
            "TRUE" if cookie.get("secure") else "FALSE",
            str(int(expires)) if expires > 0 else "",
            cookie.get("name", ""), cookie.get("value", ""),
        ]
        if any("\t" in value or "\n" in value or "\r" in value for value in values):
            continue
        rows.append("\t".join(values))
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")


def run(url, options):
    from . import yt_engine
    import download_hls as backend
    items, cookies = capture(url, options)
    selected = items[0]
    if len(items) > 1 and items[0]["score"] - items[1]["score"] < 20:
        print("发现多个候选视频：", flush=True)
        for index, item in enumerate(items, start=1):
            print(f"{index}. {item['kind']} · {urlsplit(item['url']).hostname}", flush=True)
        if not sys.stdin.isatty():
            raise RuntimeError("候选视频不唯一，请在交互窗口中选择目标视频。")
        answer = input("请输入目标视频编号：").strip()
        if not answer.isdigit() or not 1 <= int(answer) <= len(items):
            raise ValueError("视频编号无效。")
        selected = items[int(answer) - 1]
    print(f"浏览器已识别 {selected['kind']} 媒体资源。", flush=True)
    request_options = replace(
        options, referer=options.referer or selected["headers"].get("Referer")
    )
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".browser_", dir=OUTPUT_DIR) as directory:
        work = Path(directory).resolve()
        if work.parent != OUTPUT_DIR.resolve():
            raise RuntimeError("浏览器会话临时目录检查失败。")
        cookiefile = work / "cookies.txt"
        write_cookies(cookiefile, cookies)
        headers = dict(selected["headers"])
        if options.referer:
            headers["Referer"] = options.referer
        if selected["kind"] == "hls":
            try:
                return backend.download(
                    selected["url"], output=options.output,
                    referer=request_options.referer, jobs=options.jobs, check=options.check,
                    headers=headers, cookiefile=cookiefile,
                )
            except Exception as error:
                print(f"TS 分段处理未成功，改用标准媒体处理：{str(error)[-300:]}", flush=True)
        return yt_engine.run(
            selected["url"], request_options, label="浏览器发现的资源",
            headers=headers, cookiefile=cookiefile,
            hls_vod=selected.get("manifest", {}).get("vod", False),
        )
