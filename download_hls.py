"""Download finite MPEG-TS HLS playlists, including PNG-prefixed segments."""

import argparse
import ast
import concurrent.futures
from contextlib import contextmanager
from contextvars import ContextVar
import datetime
import html
import http.cookiejar
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import urllib.parse
import urllib.request
import uuid
import warnings
from progress_display import ProgressBar, SegmentProgress


HTTP_SESSION = ContextVar("hls_http_session", default=None)


@contextmanager
def request_session(headers=None, cookiefile=None):
    supplied = {}
    names = {"user-agent": "User-Agent", "referer": "Referer", "origin": "Origin"}
    for name, value in (headers or {}).items():
        if name.lower() not in names:
            continue
        if not isinstance(value, str) or "\r" in value or "\n" in value:
            raise ValueError("浏览器请求信息含无效字符。")
        supplied[names[name.lower()]] = value
    jar = http.cookiejar.CookieJar()
    if cookiefile:
        jar = http.cookiejar.MozillaCookieJar(str(cookiefile))
        jar.load(ignore_discard=True, ignore_expires=False)
    token = HTTP_SESSION.set((supplied, urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))))
    try:
        yield
    finally:
        HTTP_SESSION.reset(token)


def fetch(url, referer, limit=32 * 1024 * 1024, return_url=False, on_progress=None):
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/131.0.0.0 Safari/537.36"
        ),
        "Referer": referer,
        "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }
    session = HTTP_SESSION.get()
    if session:
        for name, value in session[0].items():
            if value:
                headers[name] = value
            else:
                headers.pop(name, None)
    opener = session[1].open if session else urllib.request.urlopen
    for attempt in range(3):
        try:
            request = urllib.request.Request(url, headers=headers)
            with opener(request, timeout=30) as response:
                final_url = response.geturl()
                if on_progress is None:
                    data = response.read(limit + 1)
                else:
                    length = response.headers.get("Content-Length", "")
                    length = int(length) if length.isdigit() else None
                    chunks, received = [], 0
                    on_progress(0, length)
                    while received <= limit:
                        chunk = response.read(min(128 * 1024, limit + 1 - received))
                        if not chunk:
                            break
                        chunks.append(chunk)
                        received += len(chunk)
                        on_progress(received, length)
                    data = b"".join(chunks)
            if len(data) > limit:
                raise ValueError("单个响应超出预期大小。")
            return (data, final_url) if return_url else data
        except Exception:
            if attempt == 2:
                raise
            time.sleep(0.5 * (attempt + 1))


def playlist(url, referer):
    text = fetch(url, referer, 2 * 1024 * 1024).decode("utf-8-sig")
    if not text.lstrip().startswith("#EXTM3U"):
        raise ValueError("地址返回的不是播放清单，请重新复制当前的 m3u8 请求地址。")
    return [line.strip() for line in text.splitlines()]


JS_STRING = r"""(?:'(?:\\.|[^'\\])*'|"(?:\\.|[^"\\])*")"""
PACKED_SCRIPT = re.compile(
    r"\}\(\s*(" + JS_STRING + r")\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*("
    + JS_STRING + r""")\.split\(\s*['"]\|['"]\s*\)"""
)


def decode_js_string(literal):
    # Parse only a quoted string literal; never execute downloaded JavaScript.
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", SyntaxWarning)
        value = ast.literal_eval(literal)
    if not isinstance(value, str):
        raise ValueError("Expected a quoted string")
    return html.unescape(value.replace("\\/", "/"))


def unpack_scripts(page):
    decoded = []
    digits = "0123456789abcdefghijklmnopqrstuvwxyz"
    for match in PACKED_SCRIPT.finditer(page):
        try:
            payload = decode_js_string(match.group(1))
            radix, count = int(match.group(2)), int(match.group(3))
            words = decode_js_string(match.group(4)).split("|")
            if not 2 <= radix <= 36 or count > 10000:
                continue

            def encode(number):
                if number == 0:
                    return "0"
                result = ""
                while number:
                    result = digits[number % radix] + result
                    number //= radix
                return result

            mapping = {
                encode(index): words[index]
                for index in range(min(count, len(words)))
                if words[index]
            }
            decoded.append(re.sub(
                r"\b[0-9a-z]+\b",
                lambda token: mapping.get(token.group(0), token.group(0)),
                payload,
            ))
        except (ValueError, SyntaxError, IndexError):
            continue
    return decoded


def player_candidates(page, page_url):
    documents = unpack_scripts(page) + [html.unescape(page)]
    candidates = []
    for document in documents:
        for match in re.finditer(
            r"\b(?:var|let|const)\s+links\s*=\s*(\{[^{}]*\})\s*;",
            document,
        ):
            try:
                links = json.loads(match.group(1))
            except json.JSONDecodeError:
                continue
            for key in ("hls4", "hls3", "hls2", "hls", "file"):
                value = links.get(key)
                if isinstance(value, str) and value:
                    candidates.append(urllib.parse.urljoin(page_url, value))
        for match in re.finditer(
            r"\b(?:file|src|source(?:\d+)?)\s*[:=]\s*(" + JS_STRING + ")",
            document, re.IGNORECASE,
        ):
            try:
                value = decode_js_string(match.group(1))
                if ".m3u8" in value.lower():
                    candidates.append(urllib.parse.urljoin(page_url, value))
            except (ValueError, SyntaxError):
                continue
    return list(dict.fromkeys(
        url for url in candidates
        if urllib.parse.urlsplit(url).scheme in ("http", "https")
    ))


class EmbeddedPlayers(HTMLParser):
    def __init__(self, page_url):
        super().__init__(convert_charrefs=True)
        self.page_url = page_url
        self.frames = []

    def handle_starttag(self, tag, attributes):
        if tag.lower() != "iframe":
            return
        attrs = dict(attributes)
        for name in ("src", "data-src", "data-lazy-src"):
            location = attrs.get(name)
            if not location:
                continue
            url = urllib.parse.urljoin(self.page_url, location.strip())
            parsed = urllib.parse.urlsplit(url)
            if parsed.scheme not in ("http", "https") or not parsed.netloc:
                continue
            host = (parsed.hostname or "").lower().removeprefix("www.")
            known = host in {"recordplay.biz", "playrecord.biz"}
            known = known or host == "playerrecord.cfd" or host.endswith(".playerrecord.cfd")
            embed_path = bool(re.match(r"^/(?:embed|e)/[\w-]+/?$", parsed.path))
            fullscreen = "allowfullscreen" in attrs
            # Small tracking/ad frames and JavaScript redirects are not players.
            if not known and not (embed_path or fullscreen):
                continue
            width = re.match(r"\d+", attrs.get("width") or "")
            height = re.match(r"\d+", attrs.get("height") or "")
            if not known and width and height:
                if int(width.group()) < 320 or int(height.group()) < 180:
                    continue
            score = (100 if known else 0) + (20 if embed_path else 0)
            score += 10 if fullscreen else 0
            self.frames.append((score, url))


def embedded_players(page, page_url):
    parser = EmbeddedPlayers(page_url)
    parser.feed(page)
    ranked = sorted(enumerate(parser.frames), key=lambda item: (-item[1][0], item[0]))
    return list(dict.fromkeys(frame[1] for _, frame in ranked))


def resolve_input(url, referer):
    seen = set()
    explicit_referer = referer

    def visit(page_url, request_referer=None, depth=0):
        if depth > 3 or page_url in seen:
            raise ValueError("播放器嵌入层级过多，或页面存在循环引用。")
        seen.add(page_url)
        parsed = urllib.parse.urlsplit(page_url)
        initial_referer = explicit_referer or request_referer or urllib.parse.urlunsplit(
            (parsed.scheme, parsed.netloc, "/", "", "")
        )
        data, actual_url = fetch(
            page_url, initial_referer, 2 * 1024 * 1024, return_url=True
        )
        seen.add(actual_url)
        text = data.decode("utf-8-sig", errors="replace")
        if text.lstrip().startswith("#EXTM3U"):
            return actual_url, [line.strip() for line in text.splitlines()], initial_referer

        page_referer = explicit_referer or actual_url
        candidates = player_candidates(text, actual_url)
        errors = []
        if candidates:
            print("正在从播放器配置查找播放清单……", flush=True)
        for candidate in candidates:
            try:
                body, resolved = fetch(
                    candidate, page_referer, 2 * 1024 * 1024, return_url=True
                )
                content = body.decode("utf-8-sig", errors="replace")
                if content.lstrip().startswith("#EXTM3U"):
                    print("已找到有效播放清单。", flush=True)
                    return (
                        resolved, [line.strip() for line in content.splitlines()],
                        page_referer,
                    )
            except Exception as error:
                errors.append(str(error))

        frames = embedded_players(text, actual_url)
        if frames:
            print(f"已从网页找到 {len(frames)} 个候选播放器，正在自动读取……", flush=True)
        for frame in frames[:4]:
            try:
                return visit(frame, page_referer, depth + 1)
            except Exception as error:
                errors.append(str(error))

        if errors:
            raise ValueError(f"已经识别到播放器，但读取失败：{errors[-1]}")
        raise ValueError(
            "未能从这个网页识别可用播放器或播放清单。"
            "可以改贴播放器页面地址，或浏览器 Network 中实际的 m3u8 地址。"
        )

    return visit(url)


def attributes(line):
    return dict(re.findall(r'([A-Z0-9-]+)=("[^"]*"|[^,]*)', line))


def select_variant(url, lines):
    variants = []
    for index, line in enumerate(lines):
        if not line.startswith("#EXT-X-STREAM-INF:"):
            continue
        attr = attributes(line)
        if attr.get("AUDIO") and any(
            item.startswith("#EXT-X-MEDIA:")
            and "TYPE=AUDIO" in item
            and "URI=" in item
            for item in lines
        ):
            raise ValueError("这个清单含独立音轨，暂不适用此工具。")
        width, height = map(int, attr.get("RESOLUTION", "0x0").split("x"))
        rank = (width * height, int(attr.get("BANDWIDTH", 0)))
        location = next(
            item for item in lines[index + 1:] if item and not item.startswith("#")
        )
        variants.append((rank, urllib.parse.urljoin(url, location), width, height))
    return max(variants) if variants else None


def clean_segment(data):
    # Probe several consecutive 188-byte TS packets instead of assuming 70 bytes.
    end = min(65536, len(data) - 188 * 4)
    for offset in range(max(0, end)):
        if data[offset] != 0x47:
            continue
        if all(data[offset + 188 * index] == 0x47 for index in range(1, 5)):
            payload = data[offset:]
            if len(payload) % 188:
                raise ValueError("分段末尾有不完整的数据包，已停止以避免输出损坏文件。")
            return payload, offset
    raise ValueError("分段中未找到可识别的视频数据，可能链接已失效或格式不同。")


def media_entries(lines, url):
    if "#EXT-X-ENDLIST" not in lines:
        raise ValueError("这是直播或未结束的清单，此工具适用于已录制完成的视频。")
    entries = []
    duration = None
    for line in lines:
        if line.startswith("#EXT-X-KEY:") and attributes(line).get("METHOD") != "NONE":
            raise ValueError("此工具不支持该清单的加密方式。")
        if line.startswith(("#EXT-X-MAP:", "#EXT-X-BYTERANGE:")):
            raise ValueError("此工具适用于普通 TS 分段；这个清单的分段方式不同。")
        if line.startswith("#EXTINF:"):
            duration = float(line.split(":", 1)[1].split(",", 1)[0])
        elif line and not line.startswith("#"):
            if duration is None:
                raise ValueError("分段缺少时长信息。")
            entries.append((urllib.parse.urljoin(url, line), duration))
            duration = None
    if not entries:
        raise ValueError("清单中没有视频分段。")
    return entries


def local_playlist(lines, count):
    result = []
    written = 0
    for line in lines:
        if line and not line.startswith("#"):
            result.append(f"segment_{written:06d}.ts")
            written += 1
            if written == count:
                break
        elif line != "#EXT-X-ENDLIST":
            result.append(line)
    result.append("#EXT-X-ENDLIST")
    return "\n".join(result) + "\n"


def run_ffmpeg(command, work, seconds, check=False):
    progress = ProgressBar("验证" if check else "合并")
    progress.update(fraction=0)
    progress.draw(force=True)
    error_log = work / "ffmpeg.log"
    with error_log.open("wb") as errors:
        process = subprocess.Popen(
            command[:1] + ["-progress", "pipe:1", "-stats_period", "0.2", "-nostats"]
            + command[1:],
            stdout=subprocess.PIPE, stderr=errors, text=True, encoding="utf-8",
            errors="replace",
        )

        def read_progress():
            with process.stdout:
                for line in process.stdout:
                    key, _, value = line.strip().partition("=")
                    if key == "out_time_us":
                        try:
                            position = max(0, int(value)) / 1000000
                            progress.update(
                                fraction=min(0.999, position / max(seconds, 0.001)),
                                detail=f"{duration_text(position)}/{duration_text(seconds)}",
                            )
                        except ValueError:
                            pass

        reader = threading.Thread(target=read_progress, daemon=True)
        reader.start()
        deadline = time.monotonic() + 300
        try:
            while process.poll() is None:
                if time.monotonic() > deadline:
                    raise TimeoutError("合并或验证超过预期时间。")
                progress.draw()
                time.sleep(0.15)
            reader.join(timeout=2)
        except BaseException:
            process.kill()
            process.wait()
            reader.join(timeout=2)
            progress.finish(False)
            raise
    progress.finish(process.returncode == 0)
    text = error_log.read_text(encoding="utf-8", errors="replace").strip()
    if process.returncode:
        raise RuntimeError(text[-3000:])
    if text:
        print("合并提示：\n" + text[-2000:], flush=True)


def duration_text(value):
    from progress_display import duration
    return duration(value)


def save_output(source, output):
    source, output = Path(source), Path(output)
    if output.exists():
        raise FileExistsError(f"文件已经存在：{output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    staged = output.with_name(f".{output.name}.{uuid.uuid4().hex}.partial")
    created, copied = False, 0
    copying = ProgressBar("保存文件")
    total_size = source.stat().st_size
    copying.update(fraction=0, downloaded=0, total_bytes=total_size)
    copying.draw(force=True)
    try:
        with staged.open("xb") as destination:
            created = True
            with source.open("rb") as reader:
                while chunk := reader.read(1024 * 1024):
                    destination.write(chunk)
                    copied += len(chunk)
                    copying.update(
                        fraction=copied / max(1, total_size), downloaded=copied,
                        total_bytes=total_size,
                    )
                    copying.draw()
        staged.rename(output)
        copying.finish()
    except BaseException:
        copying.finish(False)
        if created:
            staged.unlink(missing_ok=True)
        raise


def download(url, output=None, referer=None, jobs=8, check=False, *, headers=None, cookiefile=None):
    with request_session(headers, cookiefile):
        return download_with_session(url, output, referer, jobs, check)


def download_with_session(url, output=None, referer=None, jobs=8, check=False):
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError("请粘贴以 https:// 开头的文章、播放器页面或播放清单地址。")
    for name in ("ffmpeg", "ffprobe"):
        if not shutil.which(name):
            raise ValueError(f"未找到 {name}，请先安装并确认它能在终端中运行。")
    url, lines, referer = resolve_input(url, referer)
    variant = select_variant(url, lines)
    label = "best"
    if variant:
        _, url, width, height = variant
        label = f"{width}x{height}"
        print(f"清单标注的最高分辨率：{width} × {height}", flush=True)
        lines = playlist(url, referer)
    entries = media_entries(lines, url)
    expected_duration = sum(duration for _, duration in entries)
    if check:
        entries = entries[:3]
    processed_duration = sum(item[1] for item in entries)
    if output is None:
        stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        output = Path(__file__).resolve().parent.parent / f"video_{stamp}_{label}.mp4"
    output = Path(output).expanduser().resolve()
    if output.exists():
        raise FileExistsError(f"文件已经存在，请选择另一个保存名称：{output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    print(f"共 {len(entries)} 个分段，按原清单顺序处理。", flush=True)
    removed_counts = {}
    progress = SegmentProgress(len(entries))
    with tempfile.TemporaryDirectory(prefix=".hls_", dir=output.parent) as directory:
        work = Path(directory).resolve()
        if work.parent != output.parent:
            raise RuntimeError("临时目录位置检查失败。")

        def job(index, segment_url):
            raw = fetch(
                segment_url, referer,
                on_progress=lambda current, total: progress.received(index, current, total),
            )
            payload, removed = clean_segment(raw)
            (work / f"segment_{index:06d}.ts").write_bytes(payload)
            return len(raw), removed

        pool = concurrent.futures.ThreadPoolExecutor(
            max_workers=max(1, min(jobs, 16)),
            initializer=HTTP_SESSION.set, initargs=(HTTP_SESSION.get(),),
        )
        futures = {
            pool.submit(job, index, segment_url): index
            for index, (segment_url, _) in enumerate(entries)
        }
        pending = set(futures)
        progress.draw(force=True)
        try:
            while pending:
                done, pending = concurrent.futures.wait(
                    pending, timeout=0.2,
                    return_when=concurrent.futures.FIRST_COMPLETED,
                )
                for future in done:
                    size, removed = future.result()
                    progress.done(futures[future], size)
                    removed_counts[removed] = removed_counts.get(removed, 0) + 1
                progress.draw()
            progress.finish()
        except BaseException:
            progress.finish(False)
            for future in futures:
                future.cancel()
            raise
        finally:
            pool.shutdown(wait=True, cancel_futures=True)

        clean_list = work / "local.m3u8"
        clean_list.write_text(local_playlist(lines, len(entries)), encoding="utf-8")
        command = [
            "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "warning",
            "-i", str(clean_list), "-map", "0:v:0", "-map", "0:a:0?",
        ]
        if check:
            command += ["-xerror", "-f", "null", "-"]
        else:
            command += [
                "-c", "copy", "-bsf:a", "aac_adtstoasc",
                "-movflags", "+faststart", str(work / "output.mp4"),
            ]
        print("正在验证音视频……" if check else "正在按原清单合并视频……", flush=True)
        run_ffmpeg(command, work, processed_duration, check)
        if check:
            print("测试通过：前三个分段可正常解码。", flush=True)
            return
        info = json.loads(subprocess.check_output([
            "ffprobe", "-v", "error", "-show_entries",
            "format=duration,size:stream=codec_type,codec_name,width,height,r_frame_rate",
            "-of", "json", str(work / "output.mp4"),
        ], timeout=30))
        actual_duration = float(info["format"]["duration"])
        if abs(actual_duration - expected_duration) > 2:
            raise RuntimeError(
                f"合并时长异常：清单 {expected_duration:.3f} 秒，"
                f"文件 {actual_duration:.3f} 秒。"
            )
        # The final file inherits the output folder's normal access permissions.
        save_output(work / "output.mp4", output)
        print(f"完成：{output}", flush=True)
        print(json.dumps({
            "output": str(output),
            "expected_seconds": expected_duration,
            "prefix_counts": removed_counts,
            "probe": info,
        }, ensure_ascii=False), flush=True)
        return output


def report_error(error):
    print(f"\n下载失败：{error}", file=sys.stderr, flush=True)
    log = Path(__file__).resolve().parent / "download-error.log"
    try:
        with log.open("a", encoding="utf-8") as stream:
            stream.write(datetime.datetime.now().isoformat() + "\n")
            stream.write(traceback.format_exc() + "\n")
        print(f"详细错误已保存在：{log}", flush=True)
    except OSError:
        pass


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("url", nargs="?")
    parser.add_argument("-o", "--output")
    parser.add_argument("--referer")
    parser.add_argument("--jobs", type=int, default=8)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    if args.url:
        download(
            args.url.strip().strip("\"'"), args.output, args.referer,
            args.jobs, args.check,
        )
        return
    print("支持文章页面、播放器页面，也支持直接粘贴 m3u8 地址。", flush=True)
    print("成功或失败后均可继续输入；输入空行退出。", flush=True)
    while True:
        try:
            url = input("\n请粘贴网址，然后按回车：\n").strip().strip("\"'")
        except EOFError:
            return
        if not url:
            return
        try:
            download(url, args.output, args.referer, args.jobs, args.check)
        except Exception as error:
            report_error(error)
            print("窗口保持打开，可以直接粘贴另一个地址重试。", flush=True)


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    try:
        main()
    except KeyboardInterrupt:
        print("\n已取消。", file=sys.stderr)
        sys.exit(130)
    except Exception as error:
        report_error(error)
        sys.exit(1)
