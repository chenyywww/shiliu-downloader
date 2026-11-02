"""Persistent, sequential local jobs backed by the existing command-line router."""
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import queue
import re
import sqlite3
import subprocess
import sys
import threading
import uuid
import time

from download_events import PREFIX
from download_router import select_route
from site_handlers.common import ROOT, OUTPUT_DIR
from url_input import extract_url

ACTIVE = {"queued", "running", "waiting", "cancelling"}
ALLOWED_OPTIONS = {"check", "show_browser", "use_x_login", "use_youtube_login"}


def now():
    return datetime.now(timezone.utc).isoformat()


class JobStore:
    def __init__(self, directory):
        self.directory = Path(directory).resolve()
        self.directory.mkdir(parents=True, exist_ok=True)
        self.logs = self.directory / "logs"
        self.logs.mkdir(exist_ok=True)
        self.path = self.directory / "tasks.sqlite3"
        self.lock = threading.RLock()
        with self.connection() as db:
            db.execute("CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, data TEXT NOT NULL)")

    @contextmanager
    def connection(self):
        with self.lock:
            db = sqlite3.connect(self.path, timeout=10)
            try:
                with db:
                    yield db
            finally:
                db.close()

    def get(self, identifier):
        with self.connection() as db:
            row = db.execute("SELECT data FROM jobs WHERE id=?", (identifier,)).fetchone()
        if row is None:
            raise KeyError("任务不存在。")
        return json.loads(row[0])

    def put(self, job):
        with self.connection() as db:
            db.execute("INSERT INTO jobs VALUES (?, ?) ON CONFLICT(id) DO UPDATE SET data=excluded.data",
                       (job["id"], json.dumps(job, ensure_ascii=False, allow_nan=False)))

    def update(self, identifier, **values):
        with self.lock:
            job = self.get(identifier)
            job.update(values)
            self.put(job)
        return job

    def all(self):
        with self.connection() as db:
            rows = db.execute("SELECT data FROM jobs ORDER BY rowid DESC LIMIT 200").fetchall()
        return [json.loads(row[0]) for row in rows]

    def recover(self):
        with self.connection() as db:
            rows = db.execute("SELECT id, data FROM jobs").fetchall()
            for identifier, data in rows:
                job = json.loads(data)
                if job["status"] in ACTIVE:
                    job.update(status="interrupted", finished=now(),
                               error="上次服务已结束，任务没有完成。可以点击重试。")
                    db.execute("UPDATE jobs SET data=? WHERE id=?", (json.dumps(job, ensure_ascii=False), identifier))

    def expire_resolutions(self):
        if time.monotonic() - getattr(self, "_last_expiry", -60) < 60:
            return
        self._last_expiry = time.monotonic()
        with self.connection() as db:
            for identifier, raw in db.execute("SELECT id, data FROM jobs").fetchall():
                job = json.loads(raw)
                if job.get("resolution") and job.get("resolution_deadline", 0) < time.time():
                    job.update(resolution=None, resolution_expired=True, message="解析结果已过期，请重新解析。")
                    db.execute("UPDATE jobs SET data=? WHERE id=?", (json.dumps(job, ensure_ascii=False), identifier))

    def log_path(self, identifier):
        if not re.fullmatch(r"[a-f0-9]{32}", identifier):
            raise KeyError("任务不存在。")
        return self.logs / (identifier + ".log")

    def log_tail(self, identifier):
        path = self.log_path(identifier)
        if not path.is_file():
            return ""
        with path.open("rb") as stream:
            stream.seek(max(0, path.stat().st_size - 24000))
            return stream.read().decode("utf-8", "replace")


class JobManager:
    def __init__(self, directory=None, output_dir=None, command_builder=None, *, resolve_only=False, allow_private=False):
        self.resolve_only = resolve_only
        self.allow_private = allow_private
        self.store = JobStore(directory or ROOT / ".web")
        self.output_dir = Path(output_dir or OUTPUT_DIR).resolve()
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.command_builder = command_builder or self.command
        self.store.recover()
        self.pending = queue.Queue()
        self.stop_event = threading.Event()
        self.lock = threading.RLock()
        self.current = None
        self.process = None
        self.cancelled = set()
        self.worker = threading.Thread(target=self.work, name="local-download-queue", daemon=True)
        self.worker.start()

    def create(self, payload):
        kind = payload.get("kind", "resolve" if self.resolve_only else "download")
        if kind not in {"download", "resolve", "login_douyin", "login_x", "login_youtube"}:
            raise ValueError("不支持的任务类型。")
        if self.resolve_only and kind != "resolve":
            raise ValueError("当前是仅解析服务，不能在服务端下载视频或保存个人登录。")
        options = payload.get("options", {})
        if not isinstance(options, dict) or set(options) - ALLOWED_OPTIONS or any(type(v) is not bool for v in options.values()):
            raise ValueError("下载选项格式不正确。")
        if self.resolve_only and options:
            raise ValueError("仅解析服务不接受下载、检查片段或登录选项。")
        if kind in {"download", "resolve"}:
            text = payload.get("url", "")
            if not isinstance(text, str) or len(text) > 16000:
                raise ValueError("请粘贴一个视频链接或分享文案。")
            url = extract_url(text)
            if len(url) > 8000 or any(c in url for c in "\r\n\0"):
                raise ValueError("链接格式不正确或过长。")
            route = select_route(url)
            label = route["name"]
            if kind == "resolve":
                from resolve_net import validate_url
                validate_url(url, self.allow_private)
                options = {}
        else:
            url, label, options = "", {"login_douyin": "抖音登录", "login_x": "X 登录", "login_youtube": "YouTube 登录"}[kind], {}
            if kind == "login_youtube" and payload.get("url"):
                from site_handlers.youtube import video_url
                url = video_url(extract_url(payload["url"]))
        identifier = uuid.uuid4().hex
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        job = {"id": identifier, "kind": kind, "url": url, "route": label,
               "options": options, "status": "queued", "created": now(), "started": None,
               "finished": None, "progress": None, "message": "等待前面的任务完成。",
               "output": None, "outputs": [], "error": "",
               "requested_output": str(self.output_dir / f"video_{stamp}_{identifier[:6]}.mp4")}
        with self.lock:
            if self.stop_event.is_set():
                raise ValueError("服务正在退出，请重新启动后提交。")
            if sum(item["status"] in ACTIVE for item in self.store.all()) >= 30:
                raise ValueError("等待任务较多，请先完成或取消部分任务。")
            self.store.put(job)
            self.pending.put(identifier)
        return job

    def command(self, job):
        if job["kind"] == "resolve":
            return [sys.executable, "-X", "utf8", str(ROOT / "resolve_router.py"), job["url"]] + (["--allow-private-test"] if self.allow_private else [])
        command = [sys.executable, "-X", "utf8", str(ROOT / "download_router.py")]
        if job["kind"] != "download":
            flag = {"login_douyin": "--login-douyin", "login_x": "--login-x", "login_youtube": "--login-youtube"}[job["kind"]]
            return command + [flag] + ([job["url"]] if job["url"] else [])
        command += [job["url"], "-o", job["requested_output"]]
        flags = {"check": "--check", "show_browser": "--show-browser", "use_x_login": "--use-x-login", "use_youtube_login": "--use-youtube-login"}
        return command + [flag for key, flag in flags.items() if job["options"].get(key)]

    def retry(self, identifier, options=None):
        job = self.store.get(identifier)
        if job["status"] in ACTIVE:
            raise ValueError("任务仍在进行，请先等待或取消。")
        payload = {key: job[key] for key in ("kind", "url", "options")}
        if options is not None:
            if not isinstance(options, dict):
                raise ValueError("重试选项格式不正确。")
            payload["options"] = {**job["options"], **options}
        return self.create(payload)

    def cancel(self, identifier):
        with self.lock:
            job = self.store.get(identifier)
            if job["status"] not in ACTIVE:
                return job
            self.cancelled.add(identifier)
            queued = job["status"] == "queued"
            job = self.store.update(identifier, status="cancelled" if queued else "cancelling",
                                    message="已取消。" if queued else "正在停止当前任务…",
                                    finished=now() if queued else None)
            process = self.process if self.current == identifier else None
        if process and process.poll() is None:
            self.kill_tree(process)
        return job

    @staticmethod
    def kill_tree(process):
        if process.poll() is not None:
            return
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           creationflags=subprocess.CREATE_NO_WINDOW, timeout=15)
        else:
            import signal
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass

    def handle_event(self, identifier, event):
        kind = event.get("event")
        with self.lock:
            if identifier in self.cancelled:
                return
            if kind == "progress":
                progress = {key: event.get(key) for key in
                            ("label", "fraction", "downloaded", "total_bytes", "estimated", "detail", "speed", "eta")}
                for key in ("fraction", "downloaded", "total_bytes", "speed", "eta"):
                    value = progress[key]
                    if value is not None and (type(value) not in (int, float) or not math.isfinite(value)):
                        raise ValueError("无效的进度信息。")
                self.store.update(identifier, progress=progress, message=progress.get("label") or "正在处理…")
            elif kind == "route":
                self.store.update(identifier, route=str(event.get("name", "视频"))[:80], message="正在解析视频资源…")
            elif kind == "completed":
                value = event.get("output")
                outputs = event.get("outputs", [value] if value else [])
                if not isinstance(outputs, list) or len(outputs) > 100:
                    raise ValueError("下载结果列表无效。")
                checked = []
                expected_stem = Path(self.store.get(identifier)["requested_output"]).stem
                for output in outputs:
                    path = Path(output).resolve()
                    if (path.parent != self.output_dir or not path.is_file()
                            or not re.fullmatch(re.escape(expected_stem) + r"(?:_\d+)?", path.stem)
                            or path.suffix.lower() not in {".mp4", ".mkv", ".webm", ".mov", ".m4v", ".mp3", ".m4a", ".ogg", ".opus", ".flac", ".wav", ".ts"}):
                        raise ValueError("下载结果没有生成在指定目录中。")
                    checked.append(str(path))
                if checked:
                    self.store.update(identifier, output=checked[0], outputs=checked)
            elif kind == "interaction":
                self.store.update(identifier, status="waiting" if event.get("waiting") else "running",
                                  message=str(event.get("message") or "正在继续处理…")[:500])
            elif kind == "error":
                self.store.update(identifier, error=str(event.get("message", "处理失败。"))[-5000:])
            elif kind == "resolved":
                from resolution import validate_plan
                result = validate_plan(event.get("result"), self.allow_private)
                self.store.update(identifier, resolution=result, resolution_deadline=time.time() + 1800)

    def timeout(self, identifier, process):
        with self.lock:
            if self.current != identifier or process.poll() is not None:
                return
            self.store.update(identifier, error="解析超过 75 秒，已停止。可在本机重新解析。")
        self.kill_tree(process)

    def work(self):
        while not self.stop_event.is_set():
            try:
                identifier = self.pending.get(timeout=0.25)
            except queue.Empty:
                continue
            watchdog = None
            try:
                with self.lock:
                    if self.stop_event.is_set() or self.store.get(identifier)["status"] != "queued":
                        continue
                    self.current = identifier
                    job = self.store.update(identifier, status="running", started=now(), message="正在启动…")
                    env = os.environ.copy()
                    env.update(PYTHONUTF8="1", PYTHONIOENCODING="utf-8", VIDEO_DOWNLOADER_EVENTS="1")
                    kwargs = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {"start_new_session": True}
                    self.process = subprocess.Popen(self.command_builder(job), cwd=ROOT, env=env,
                                                    stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                                    stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                                                    errors="replace", **kwargs)
                    process = self.process
                    if job["kind"] == "resolve":
                        watchdog = threading.Timer(75, self.timeout, (identifier, process))
                        watchdog.daemon = True
                        watchdog.start()
                with process.stdout, self.store.log_path(identifier).open("w", encoding="utf-8") as log:
                    size = 0
                    for line in process.stdout:
                        if line.startswith(PREFIX):
                            if len(line) > 1024 * 1024:
                                raise ValueError("解析结果超出大小限制。")
                            self.handle_event(identifier, json.loads(line[len(PREFIX):]))
                        elif size < 2_000_000:
                            line = line[:20000]
                            log.write(line)
                            log.flush()
                            size += len(line)
                status = process.wait()
                with self.lock:
                    if identifier in self.cancelled:
                        self.store.update(identifier, status="cancelled", finished=now(), message="已取消。")
                    elif status:
                        error = self.store.get(identifier)["error"] or self.store.log_tail(identifier)[-4000:] or "下载未完成，请查看处理记录。"
                        self.store.update(identifier, status="failed", finished=now(), error=error, message="处理失败。")
                    else:
                        result = self.store.get(identifier)
                        if job["kind"] == "download" and not job["options"].get("check") and not result["output"]:
                            raise RuntimeError("程序已结束，但没有返回下载文件。请查看处理记录。")
                        if job["kind"] == "resolve":
                            if not result.get("resolution"):
                                raise RuntimeError("解析程序没有返回有效结果。")
                            message = "资源解析完成，等待在本机下载。" if result["resolution"]["status"] == "ready" else "需在本机重新解析。"
                        else:
                            message = "登录状态已保存。" if job["kind"] != "download" else (
                                "检查通过。" if job["options"].get("check") else "下载完成。")
                        self.store.update(identifier, status="completed", finished=now(), message=message)
            except Exception as error:
                if self.process and self.process.poll() is None:
                    self.kill_tree(self.process)
                    self.process.wait(timeout=15)
                with self.lock:
                    cancelled = identifier in self.cancelled
                    self.store.update(identifier, status="cancelled" if cancelled else "failed",
                                      finished=now(), error="" if cancelled else str(error), message="已取消。" if cancelled else "处理失败。")
            finally:
                if watchdog:
                    watchdog.cancel()
                with self.lock:
                    self.current = self.process = None
                    self.cancelled.discard(identifier)
                self.pending.task_done()

    def close(self):
        self.stop_event.set()
        with self.lock:
            current = self.current
        if current:
            self.cancel(current)
        self.worker.join(timeout=20)
