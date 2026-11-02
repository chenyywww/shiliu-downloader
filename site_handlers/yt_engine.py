from collections import deque
import json
from pathlib import Path
import shutil
import subprocess
import sys
from progress_display import ProgressBar

from .common import OUTPUT_DIR, VENDOR, child_env


def build_command(
    url, options, *, force_mkv=False, headers=None, cookiefile=None,
    format_selector=None, hls_vod=False,
):
    if not (VENDOR / "yt_dlp" / "__main__.py").is_file():
        raise RuntimeError("工具目录中的 yt-dlp 组件缺失。")
    if not shutil.which("ffmpeg"):
        raise RuntimeError("未找到 FFmpeg。")
    command = [
        sys.executable, "-X", "utf8", "-m", "yt_dlp",
        "--ignore-config", "--no-playlist", "--newline",
        "--socket-timeout", "15", "--retries", "2", "--extractor-retries", "1",
        "--fragment-retries", "3", "--windows-filenames", "--no-overwrites",
        "--abort-on-unavailable-fragments",
        "--concurrent-fragments", str(max(1, min(options.jobs, 16))),
        "--merge-output-format", "mkv",
        "--format", format_selector or "bv*+ba/b",
    ]
    runtime = shutil.which("node") or shutil.which("deno")
    if hls_vod:
        command += ["--extractor-args", "generic:is_live=false"]
    if runtime:
        name = "node" if Path(runtime).stem.lower() == "node" else "deno"
        command += ["--js-runtimes", name + ":" + runtime]
    if options.referer:
        command += ["--referer", options.referer]
    for key, value in (headers or {}).items():
        if key.lower() in ("user-agent", "referer", "origin") and value:
            if "\r" in value or "\n" in value:
                raise ValueError("请求信息含无效换行。")
            if key.lower() == "referer" and options.referer:
                continue
            command += ["--add-header", key + ":" + value]
    if cookiefile:
        command += ["--cookies", str(cookiefile)]
    if options.output:
        output = Path(options.output).expanduser().resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        command += ["--output", str(output.with_suffix("")) + ".%(ext)s"]
    else:
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        command += [
            "--paths", str(OUTPUT_DIR),
            "--output", "%(title).120s [%(id)s].%(ext)s",
        ]
    if options.check:
        command += [
            "--simulate", "--skip-download",
            "--print", "解析成功：%(title)s [%(id)s]；%(resolution)s",
        ]
    else:
        command += ["--no-simulate", "--print", "after_move:完成：%(filepath)s"]
        command += [
            "--progress", "--progress-delta", "0.2",
            "--progress-template",
            "download:__VD_PROGRESS__%(progress)j\t%(info.format_id)j"
            "\t%(info.vcodec)j\t%(info.acodec)j",
            "--progress-template", "postprocess:__VD_POST__%(progress)j",
        ]
        if force_mkv:
            command += ["--remux-video", "mkv"]
    command += ["--", url]
    return command


def run(
    url, options, *, label="yt-dlp", force_mkv=False, headers=None,
    cookiefile=None, format_selector=None, progress_label=None, hls_vod=False,
):
    command = build_command(
        url, options, force_mkv=force_mkv, headers=headers, cookiefile=cookiefile,
        format_selector=format_selector, hls_vod=hls_vod,
    )
    tail = deque(maxlen=40)
    bar = None
    key = None
    finished = True
    saved = None

    def number(value):
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    def field(value):
        try:
            return json.loads(value)
        except (ValueError, TypeError):
            return None if value == "NA" else value

    process = subprocess.Popen(
        command, env=child_env(), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace",
    )
    try:
        for line in process.stdout:
            # The provider adapter has already checked EXT-X-ENDLIST. Some
            # generic HLS extractions still carry an incorrect live flag.
            if hls_vod and line.startswith("WARNING: Live HLS streams are not supported"):
                continue
            if line.startswith("__VD_PROGRESS__"):
                try:
                    fields = line.removeprefix("__VD_PROGRESS__").strip().split("\t")
                    data = json.loads(fields[0])
                    identity = field(fields[1]) if len(fields) > 1 else data.get("filename")
                    vcodec = field(fields[2]) if len(fields) > 2 else None
                    acodec = field(fields[3]) if len(fields) > 3 else None
                    if bar is None or key != identity or finished:
                        if bar is not None and not finished:
                            bar.finish(False)
                        title = progress_label or ("下载音轨" if vcodec == "none" else (
                            "下载视频" if acodec == "none" else "下载媒体"
                        ))
                        bar, key, finished = ProgressBar(title), identity, False
                    received = number(data.get("downloaded_bytes")) or 0
                    known_total = number(data.get("total_bytes"))
                    total = known_total or number(
                        data.get("total_bytes_estimate")
                    )
                    fragment = number(data.get("fragment_index"))
                    fragments = number(data.get("fragment_count"))
                    fraction = received / known_total if known_total else (
                        (fragment or 0) / fragments if fragments else (
                            received / total if total else None
                        )
                    )
                    detail = f"{int(fragment)}/{int(fragments)}段" if fragments and fragment else ""
                    completed = data.get("status") == "finished"
                    if fraction is not None and not completed:
                        fraction = min(0.999, fraction)
                    bar.update(
                        fraction=1 if completed else fraction,
                        downloaded=received, detail=detail,
                        speed=number(data.get("speed")), eta=number(data.get("eta")),
                        total_bytes=total, estimated=not bool(known_total),
                    )
                    if completed:
                        bar.finish()
                        finished = True
                    else:
                        bar.draw()
                    continue
                except (ValueError, TypeError, KeyError):
                    pass
            if line.startswith("__VD_POST__"):
                try:
                    data = json.loads(line.removeprefix("__VD_POST__").strip())
                    if "MoveFiles" in data.get("postprocessor", ""):
                        continue
                    if bar is not None and key != "post" and not finished:
                        bar.finish(False)
                    if data.get("status") == "finished":
                        if bar is not None and key == "post":
                            bar.finish()
                        finished = True
                    else:
                        if key != "post" or finished:
                            bar, key = ProgressBar("合并/封装"), "post"
                        bar.update()
                        bar.draw(force=True)
                        finished = False
                    continue
                except (ValueError, TypeError):
                    pass
            if bar is not None and not finished:
                bar.finish(False)
            print(line, end="", flush=True)
            if line.startswith("完成："):
                saved = Path(line.removeprefix("完成：").strip())
            tail.append(line.rstrip())
        status = process.wait()
    except BaseException:
        process.terminate()
        process.wait()
        if bar is not None and not finished:
            bar.finish(False)
        raise
    finally:
        process.stdout.close()
    if bar is not None and not finished:
        bar.finish(status == 0)
    if status:
        raise RuntimeError(f"{label} 处理失败：\n" + "\n".join(tail)[-3000:])
    return saved
