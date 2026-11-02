"""Local web interface. Bind exclusively to loopback; reuse the existing downloader."""
import argparse
from datetime import datetime
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import threading
import time
from urllib.error import URLError
from urllib.parse import urlsplit
from urllib.request import ProxyHandler, build_opener
import webbrowser

from site_handlers.common import ROOT
from web_jobs import JobManager

APP_ID = "local-video-downloader-v1"
INSTALLATION = hashlib.sha256(str(ROOT.resolve()).lower().encode()).hexdigest()[:16]
STATIC = ROOT / "web_ui"


class LocalServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = os.name != "nt"

    def server_bind(self):
        if os.name == "nt":
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()

    def __init__(self, port=8765, manager=None, opener=None, *, resolve_only=False):
        self.resolve_only = resolve_only or bool(manager and manager.resolve_only)
        self.token = secrets.token_urlsafe(32)
        self.instance = secrets.token_hex(12)
        self.opener = opener or self.open_path
        # Bind first: a duplicate launch must never recover or modify live jobs.
        super().__init__(("127.0.0.1", port), Handler)
        try:
            self.manager = manager or JobManager(resolve_only=self.resolve_only)
        except Exception:
            self.server_close()
            raise
        self.url = f"http://127.0.0.1:{self.server_port}"

    @staticmethod
    def open_path(path):
        if os.name == "nt":
            os.startfile(str(path))
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path)])


class Handler(BaseHTTPRequestHandler):
    server_version = "LocalDownloader"
    sys_version = ""

    def log_message(self, *args):
        pass

    def reply(self, status, data, content_type="application/json; charset=utf-8"):
        if isinstance(data, (dict, list)):
            data = json.dumps(data, ensure_ascii=False, allow_nan=False).encode()
        elif isinstance(data, str):
            data = data.encode()
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    def local_request(self):
        valid = {f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"}
        if self.headers.get("Host", "") not in valid:
            self.reply(403, {"error": "请通过本机地址打开工具。"})
            return False
        origin = self.headers.get("Origin")
        if origin and origin not in {"http://" + host for host in valid}:
            self.reply(403, {"error": "只接受本工具页面发起的操作。"})
            return False
        if self.headers.get("Sec-Fetch-Site") == "cross-site":
            self.reply(403, {"error": "请在本工具页面操作。"})
            return False
        return True

    @staticmethod
    def public_job(job):
        return {key: value for key, value in job.items() if key != "requested_output"}

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        if not self.local_request():
            return
        path = urlsplit(self.path).path
        try:
            if path == "/api/health":
                return self.reply(200, {"app": APP_ID, "installation": INSTALLATION, "instance": self.server.instance, "resolve_only": self.server.resolve_only})
            if path == "/api/config":
                return self.reply(200, {"token": self.server.token, "instance": self.server.instance, "resolve_only": self.server.resolve_only,
                    "output_dir": str(self.server.manager.output_dir),
                    "sessions": {"douyin": not self.server.resolve_only and (ROOT / ".auth/douyin-session.bin").is_file(),
                                 "x": not self.server.resolve_only and (ROOT / ".auth/x-session.bin").is_file(),
                                 "youtube": not self.server.resolve_only and (ROOT / ".auth/youtube-session.bin").is_file()}})
            if path == "/api/tasks":
                self.server.manager.store.expire_resolutions()
                return self.reply(200, {"tasks": [self.public_job(job) for job in self.server.manager.store.all()],
                                        "instance": self.server.instance})
            if path.startswith("/api/tasks/"):
                if path.endswith("/plan"):
                    self.server.manager.store.expire_resolutions()
                    identifier = path.strip("/").split("/")[-2]
                    job = self.server.manager.store.get(identifier)
                    if job["status"] != "completed" or not job.get("resolution"):
                        return self.reply(404, {"error": "解析结果不存在或已过期，请重新解析。"})
                    data = json.dumps(job["resolution"], ensure_ascii=False, indent=2).encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json; charset=utf-8")
                    self.send_header("Content-Disposition", 'attachment; filename="download-plan-' + identifier[:8] + '.json"')
                    self.send_header("Content-Length", str(len(data)))
                    self.send_header("Cache-Control", "no-store")
                    self.send_header("X-Content-Type-Options", "nosniff")
                    self.end_headers()
                    if self.command != "HEAD":
                        self.wfile.write(data)
                    return
                identifier = path.rsplit("/", 1)[-1]
                job = self.public_job(self.server.manager.store.get(identifier))
                job["log"] = self.server.manager.store.log_tail(identifier)
                return self.reply(200, job)
            if path == "/guide":
                import html
                content = html.escape((ROOT / ("仅解析说明.md" if self.server.resolve_only else "使用说明.md")).read_text(encoding="utf-8"))
                return self.reply(200, '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>下载工具 · 使用说明</title><link rel="stylesheet" href="/styles.css"><main class="guide"><a href="/">← 返回下载工具</a><pre>' + content + '</pre></main></html>', "text/html; charset=utf-8")
            names = {"/": "index.html", "/app.js": "app.js", "/resolve-ui.js": "resolve-ui.js", "/styles.css": "styles.css", "/favicon.svg": "favicon.svg"}
            if path not in names:
                return self.reply(404, {"error": "页面不存在。"})
            filename = STATIC / names[path]
            mime = mimetypes.guess_type(filename.name)[0] or "application/octet-stream"
            return self.reply(200, filename.read_bytes(), mime + ("; charset=utf-8" if mime.startswith("text/") or mime == "application/javascript" else ""))
        except KeyError:
            self.reply(404, {"error": "任务不存在。"})
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception:
            self.reply(500, {"error": "读取本机数据失败，请重新启动工具。"})

    def do_POST(self):
        if not self.local_request():
            return
        if not secrets.compare_digest(self.headers.get("X-Local-Token", ""), self.server.token):
            return self.reply(403, {"error": "页面已过期，请刷新后再试。"})
        try:
            self.connection.settimeout(10)
            path = urlsplit(self.path).path
            length = int(self.headers.get("Content-Length", "0"))
            limit = 2 * 1024 * 1024 if path == "/api/youtube/import" else 24000
            if not 0 < length <= limit or self.headers.get("Content-Type", "").split(";")[0] != "application/json":
                raise ValueError("提交内容格式不正确或过长。")
            payload = json.loads(self.rfile.read(length))
            if not isinstance(payload, dict):
                raise ValueError("提交内容格式不正确。")
            manager = self.server.manager
            if self.server.resolve_only and (path.startswith("/api/youtube/") or path == "/api/open"):
                return self.reply(403, {"error": "仅解析服务不执行本机文件操作，也不保存个人登录。"})
            if path == "/api/resolve":
                return self.reply(201, self.public_job(manager.create({"kind": "resolve", "url": payload.get("url", "")})))
            if path == "/api/youtube/open":
                webbrowser.open("https://www.youtube.com/")
                return self.reply(200, {"ok": True})
            if path in {"/api/youtube/import", "/api/youtube/forget"}:
                from site_handlers.youtube_session import SessionStore, import_cookie_text, SessionError
                try:
                    if path.endswith("/import"):
                        import_cookie_text(payload.get("cookies"))
                    else:
                        SessionStore().forget()
                except SessionError as error:
                    raise ValueError(str(error)) from None
                return self.reply(200, {"ok": True})
            if path == "/api/tasks":
                return self.reply(201, self.public_job(manager.create(payload)))
            if path.startswith("/api/tasks/"):
                parts = path.strip("/").split("/")
                if len(parts) == 4 and parts[3] in {"cancel", "retry"}:
                    job = manager.retry(parts[2], payload.get("options")) if parts[3] == "retry" else manager.cancel(parts[2])
                    return self.reply(200, self.public_job(job))
            if path == "/api/open":
                target = payload.get("target")
                if target == "downloads":
                    selected = manager.output_dir
                else:
                    job = manager.store.get(payload.get("id", ""))
                    if job["status"] != "completed" or not job["output"]:
                        raise ValueError("任务尚未生成可打开的文件。")
                    outputs = job.get("outputs") or [job["output"]]
                    index = payload.get("index", 0)
                    if type(index) is not int or not 0 <= index < len(outputs):
                        raise ValueError("文件编号无效。")
                    selected = Path(outputs[index]).resolve()
                    if not selected.is_relative_to(manager.output_dir) or not selected.is_file():
                        raise ValueError("下载文件已移动或删除，请到保存目录中查看。")
                    if target == "folder":
                        selected = selected.parent
                    elif target != "file":
                        raise ValueError("不支持的操作。")
                self.server.opener(selected)
                return self.reply(200, {"ok": True})
            if path == "/api/shutdown":
                self.reply(200, {"ok": True})
                threading.Thread(target=self.server.shutdown, daemon=True).start()
                return
            self.reply(404, {"error": "操作不存在。"})
        except KeyError:
            self.reply(404, {"error": "任务不存在。"})
        except (ValueError, TypeError, json.JSONDecodeError) as error:
            self.reply(400, {"error": str(error)})
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception:
            self.reply(500, {"error": "操作未完成，请查看本地服务日志或重新启动工具。"})


def local_get(url):
    with build_opener(ProxyHandler({})).open(url, timeout=1) as response:
        return json.load(response)


def launch(port, open_browser=True, resolve_only=False):
    url = f"http://127.0.0.1:{port}"
    try:
        health = local_get(url + "/api/health")
    except (OSError, ValueError, URLError):
        health = None
    if health:
        if health.get("app") != APP_ID or health.get("installation") != INSTALLATION:
            raise RuntimeError(f"端口 {port} 已被其他程序使用。可使用 --port 指定其他端口。")
        if bool(health.get("resolve_only")) != resolve_only:
            raise RuntimeError("此端口已运行另一种模式，请使用不同端口。")
    else:
        directory = ROOT / ".web"
        directory.mkdir(exist_ok=True)
        with (directory / "server.log").open("a", encoding="utf-8") as log:
            log.write("\n" + datetime.now().isoformat() + " 启动本地服务\n")
            log.flush()
            kwargs = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {"start_new_session": True}
            process = subprocess.Popen([sys.executable, "-X", "utf8", str(Path(__file__).resolve()), "--port", str(port)] + (["--resolve-only"] if resolve_only else []),
                                       cwd=ROOT, stdin=subprocess.DEVNULL, stdout=log, stderr=log, **kwargs)
        for _ in range(60):
            if process.poll() is not None:
                raise RuntimeError(f"本地服务未能启动，请查看 {directory / 'server.log'}。端口可能已被占用。")
            try:
                health = local_get(url + "/api/health")
                if health.get("app") == APP_ID and health.get("installation") == INSTALLATION:
                    break
            except (OSError, ValueError, URLError):
                pass
            time.sleep(0.2)
        else:
            process.terminate()
            raise RuntimeError("启动超时，请重新尝试。")
    if open_browser:
        webbrowser.open(url)
    print("本地下载工具已启动：" + url, flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--launch", action="store_true")
    parser.add_argument("--no-open", action="store_true")
    parser.add_argument("--resolve-only", action="store_true")
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("端口范围应为 1 到 65535。")
    if args.launch:
        launch(args.port, open_browser=not args.no_open, resolve_only=args.resolve_only)
        return
    server = LocalServer(args.port, resolve_only=args.resolve_only)
    print("本地下载工具：" + server.url, flush=True)
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        pass
    finally:
        server.manager.close()
        server.server_close()


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print("启动失败：" + str(error), file=sys.stderr)
        raise SystemExit(1)
