"""The newer player's separate, byte-range video/audio HLS workflow."""

from contextlib import contextmanager
from dataclasses import replace
import datetime
import functools
import http.server
import json
from pathlib import Path
import subprocess
import tempfile
import threading
from urllib.parse import urljoin, urlsplit

from .common import OUTPUT_DIR


@contextmanager
def sample_playlists(work, sources, referer):
    """Serve two-fragment test playlists using the normal Range downloader."""
    import download_hls as backend
    public = work / "sample_playlists"
    public.mkdir()
    names, seconds = [], []
    for index, source in enumerate(sources):
        lines = backend.playlist(source, referer)
        rewritten, count, length = [], 0, 0.0
        for line in lines:
            if line.startswith("#EXTINF:"):
                length += float(line.split(":", 1)[1].split(",", 1)[0])
            if line and not line.startswith("#"):
                rewritten.append(urljoin(source, line))
                count += 1
                if count == 2:
                    break
            elif line != "#EXT-X-ENDLIST":
                rewritten.append(line)
        rewritten.append("#EXT-X-ENDLIST")
        name = f"stream_{index}.m3u8"
        (public / name).write_text("\n".join(rewritten) + "\n", encoding="utf-8")
        names.append(name)
        seconds.append(length)

    class Handler(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *args):
            pass

    server = http.server.ThreadingHTTPServer(
        ("127.0.0.1", 0), functools.partial(Handler, directory=str(public))
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        root = f"http://127.0.0.1:{server.server_port}/"
        yield [urljoin(root, name) for name in names], max(seconds)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def split_streams(master_url, referer):
    import download_hls as backend
    lines = backend.playlist(master_url, referer)
    variants = []
    for index, line in enumerate(lines):
        if line.startswith("#EXT-X-STREAM-INF:"):
            attr = backend.attributes(line)
            location = next(
                item for item in lines[index + 1:] if item and not item.startswith("#")
            )
            variants.append((int(attr.get("BANDWIDTH", 0)), urljoin(master_url, location), attr))
    if not variants:
        raise RuntimeError("新版播放器的主清单没有视频流。")
    _, video, chosen = max(variants, key=lambda item: item[0])
    group = chosen.get("AUDIO", "").strip('"')
    audio = []
    for line in lines:
        if line.startswith("#EXT-X-MEDIA:"):
            attr = backend.attributes(line)
            if attr.get("TYPE") == "AUDIO" and attr.get("GROUP-ID", "").strip('"') == group:
                if attr.get("URI"):
                    audio.append((attr.get("DEFAULT") == "YES", urljoin(master_url, attr["URI"].strip('"'))))
    if not audio:
        raise RuntimeError("新版播放器未提供匹配的独立音轨。")
    selected_audio = max(audio)[1]
    durations = []
    for stream in (video, selected_audio):
        text = backend.playlist(stream, referer)
        if "#EXT-X-ENDLIST" not in text:
            raise RuntimeError("播放器返回的是未结束的直播清单。")
        durations.append(sum(
            float(line.split(":", 1)[1].split(",", 1)[0])
            for line in text if line.startswith("#EXTINF:")
        ))
    return [video, selected_audio], max(durations)


def run(url, options):
    import download_hls as backend
    from .browser import capture, write_cookies
    from . import yt_engine
    print("检测到新版播放器，正在读取实际播放清单……", flush=True)
    items, cookies = capture(url, options)
    masters = [
        item for item in items if item["kind"] == "hls"
        and Path(urlsplit(item["url"]).path).name.lower() == "master.m3u8"
    ]
    if not masters:
        raise RuntimeError("新版播放器没有返回主播放清单，请稍后重试。")
    selected = masters[0]
    # Match the browser: this CDN rejects media requests carrying a Referer.
    request_referer = options.referer or selected["headers"].get("Referer") or None
    streams, expected_seconds = split_streams(selected["url"], request_referer or "")
    print("已找到视频及独立音轨，按字节范围分别下载。", flush=True)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".playerrecord_", dir=OUTPUT_DIR) as directory:
        work = Path(directory).resolve()
        if work.parent != OUTPUT_DIR.resolve():
            raise RuntimeError("播放器临时目录检查失败。")
        cookiefile = work / "cookies.txt"
        write_cookies(cookiefile, cookies)

        def download_and_merge(sources, seconds):
            files = []
            for index, source in enumerate(sources):
                label = "下载视频" if index == 0 else "下载音轨"
                request_options = replace(
                    options, output=str(work / f"track_{index}.mp4"),
                    referer=request_referer, check=False,
                )
                saved = yt_engine.run(
                    source, request_options, label="新版播放器",
                    headers=selected["headers"], cookiefile=cookiefile,
                    format_selector="b", progress_label=label, hls_vod=True,
                )
                if saved is None or not saved.is_file():
                    raise RuntimeError(f"{label}没有生成可用文件。")
                files.append(saved)
            merged = work / "complete.mkv"
            backend.run_ffmpeg([
                "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "warning",
                "-i", str(files[0]), "-i", str(files[1]),
                "-map", "0:v:0", "-map", "1:a:0", "-c", "copy", str(merged),
            ], work, seconds)
            info = json.loads(subprocess.check_output([
                "ffprobe", "-v", "error", "-show_entries",
                "format=duration:stream=codec_type,codec_name,width,height",
                "-of", "json", str(merged),
            ], timeout=30))
            kinds = {stream["codec_type"] for stream in info["streams"]}
            if not {"video", "audio"}.issubset(kinds):
                raise RuntimeError("合并文件缺少视频或音轨。")
            if abs(float(info["format"]["duration"]) - seconds) > 2:
                raise RuntimeError("合并时长与播放清单不一致，已停止以避免输出缺段视频。")
            return merged

        if options.check:
            with sample_playlists(work, streams, request_referer or "") as (samples, seconds):
                merged = download_and_merge(samples, seconds)
            decoded = subprocess.run([
                "ffmpeg", "-v", "error", "-xerror", "-i", str(merged),
                "-t", "1", "-f", "null", "-",
            ], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=30)
            if decoded.returncode:
                raise RuntimeError(decoded.stderr.decode("utf-8", "replace")[-2000:])
            print("测试通过：实际下载视频及音轨前两个分段，合并和解码均正常。", flush=True)
            return
        merged = download_and_merge(streams, expected_seconds)
        if options.output:
            output = Path(options.output).expanduser().resolve().with_suffix(".mkv")
        else:
            identifier = Path(urlsplit(url).path).name
            stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            output = OUTPUT_DIR / f"video_{stamp}_{identifier}.mkv"
        backend.save_output(merged, output)
        print(f"完成：{output}", flush=True)
        return output
