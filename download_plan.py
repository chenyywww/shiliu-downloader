"""Consume a resolver JSON file on the user's computer and download from the origin."""
import argparse
from dataclasses import replace
import json
from pathlib import Path
import subprocess
import tempfile

from download_router import dispatch
from resolution import validate_plan
from site_handlers.common import Options, OUTPUT_DIR


def download(value, options=None, allow_private=False):
    import download_hls as backend
    from site_handlers import yt_engine
    options = options or Options()
    value = validate_plan(value, allow_private)
    if value["strategy"] == "local" or value["status"] == "local_required":
        print("此任务需要在你的电脑重新解析，将使用本机下载流程。", flush=True)
        try:
            return dispatch(value["input_url"], options)
        except Exception:
            from download_router import select_route
            if select_route(value["input_url"])["handler"] in {"youtube", "douyin", "x"}:
                raise
            from site_handlers import browser
            print("本机直接读取未成功，将打开本机独立浏览器；如有网站验证，请手动完成。", flush=True)
            return browser.run(value["input_url"], replace(options, show_browser=True))
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".plan_", dir=OUTPUT_DIR) as name:
        work = Path(name).resolve()
        if work.parent != OUTPUT_DIR.resolve():
            raise ValueError("本机任务临时目录检查失败。")
        sources, saved = value["sources"], []
        tracks = len(sources) == 2 and {item["role"] for item in sources} == {"video", "audio"}
        for index, item in enumerate(sources):
            if tracks:
                output = work / f"track_{index}.mp4"
            elif options.output:
                output = Path(options.output)
                if len(sources) > 1:
                    output = output.with_name(output.stem + f"_{index+1:02d}" + output.suffix)
            else:
                from datetime import datetime
                output = OUTPUT_DIR / ("plan_" + datetime.now().strftime("%Y%m%d_%H%M%S") + f"_{index+1:02d}.mp4")
            headers = item["headers"]
            request_options = replace(options, output=str(output), referer=headers.get("Referer"))
            if item["kind"] == "hls" and not tracks:
                try:
                    path = backend.download(item["url"], output=str(output), referer=request_options.referer,
                        jobs=options.jobs, check=options.check, headers=headers)
                    if path:
                        saved.append(path)
                    continue
                except Exception as error:
                    print("普通 TS 流程未成功，在本机尝试其他媒体格式：" + str(error)[-200:], flush=True)
            path = yt_engine.run(item["url"], request_options, label="本机解析任务下载", headers=headers, format_selector="b" if item["kind"] == "file" else None)
            if path:
                saved.append(path)
        if options.check:
            return None
        if tracks:
            if len(saved) != 2:
                raise RuntimeError("没有取得完整的视频、音轨，停止合并。")
            video_index = next(i for i, item in enumerate(sources) if item["role"] == "video")
            audio_index = 1 - video_index
            durations = []
            for path in saved:
                track_info = json.loads(subprocess.check_output(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", str(path)]))
                durations.append(float(track_info.get("format", {}).get("duration", 0)))
            seconds = max(durations)
            if min(durations) <= 0 or max(durations) - min(durations) > max(2, seconds * .01):
                raise RuntimeError("视频与音轨时长无效或不匹配，已停止合并。")
            merged = work / "complete.mkv"
            backend.run_ffmpeg(["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "warning",
                "-i", str(saved[video_index]), "-i", str(saved[audio_index]),
                "-map", "0:v:0", "-map", "1:a:0", "-c", "copy", str(merged)], work, seconds)
            info = json.loads(subprocess.check_output(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type:format=duration", "-of", "json", str(merged)]))
            if not {"video", "audio"}.issubset({item["codec_type"] for item in info["streams"]}):
                raise RuntimeError("合并结果缺少视频或音轨。")
            if abs(float(info["format"]["duration"]) - seconds) > 2:
                raise RuntimeError("合并时长与轨道时长不一致，已停止保存。")
            output = Path(options.output).with_suffix(".mkv") if options.output else OUTPUT_DIR / ("plan_" + work.name.removeprefix(".plan_") + ".mkv")
            backend.save_output(merged, output)
            print("完成：" + str(output), flush=True)
            return output
        return saved[0] if len(saved) == 1 else saved


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("plan_file", nargs="?")
    parser.add_argument("-o", "--output")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--allow-private-test", action="store_true")
    parser.add_argument("--use-youtube-login", action="store_true")
    parser.add_argument("--use-x-login", action="store_true")
    args = parser.parse_args()
    filename = args.plan_file
    if not filename:
        import tkinter as tk
        from tkinter.filedialog import askopenfilename
        root = tk.Tk()
        root.withdraw()
        filename = askopenfilename(title="选择网页导出的解析任务", filetypes=[("解析任务", "*.json")])
        root.destroy()
    if not filename:
        return
    path = Path(filename)
    if path.stat().st_size > 1024 * 1024:
        raise ValueError("解析任务文件过大。")
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    download(value, Options(output=args.output, check=args.check, use_x_login=args.use_x_login,
                           use_youtube_login=args.use_youtube_login), args.allow_private_test)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("已取消。")
    except Exception as error:
        print("本机下载未完成：" + str(error))
        print("临时链接可能过期或绑定 IP。可将原视频页面粘贴到本机 download.cmd 重新解析。")
        raise SystemExit(1)
