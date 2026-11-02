"""Prove the boundary: resolve metadata first, fetch video only in the local client."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "vendor"))
import download_hls
import download_plan
from resolve_router import resolve, resolve_native
from resolve_net import MetadataClient, MediaFound, validate_url
from resolution import plan, source, validate_plan
from site_handlers.common import Options
from web_app import LocalServer
from web_jobs import JobManager


class ResolutionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix=".resolve_test_", dir=ROOT.parent)
        cls.work = Path(cls.temp.name).resolve()
        assert cls.work.parent == ROOT.parent.resolve()
        subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=c=green:s=160x90:r=10:d=2",
            "-f", "lavfi", "-i", "sine=frequency=500:duration=2", "-c:v", "libx264", "-g", "10", "-sc_threshold", "0",
            "-pix_fmt", "yuv420p", "-c:a", "aac", "-hls_time", "1", "-hls_list_size", "0", "-hls_playlist_type", "vod",
            "-hls_segment_filename", str(cls.work / "part%d.ts"), str(cls.work / "media.m3u8")], check=True, capture_output=True)
        for kind, output in (("v", "video.mp4"), ("a", "audio.m4a")):
            subprocess.run(["ffmpeg", "-v", "error", "-i", str(cls.work / "media.m3u8"),
                "-map", "0:" + kind + ":0", "-c", "copy", str(cls.work / output)], check=True, capture_output=True)
        cls.requests = []
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def do_GET(self):
                cls.requests.append(self.path)
                mime = "text/html"
                if self.path == "/article":
                    body = b'<iframe src="/embed" width="800" height="500" allowfullscreen></iframe>'
                elif self.path == "/embed":
                    body = b'<video></video><script>const source="/master.m3u8";</script>'
                elif self.path == "/dynamic":
                    body = b'<video></video><script>fetch("/master.m3u8").then(r=>r.text()).then(()=>fetch("/part0.jpeg"));</script>'
                elif self.path == "/outer":
                    body = f'<iframe src="http://localhost:{cls.server.server_port}/dynamic" width="800" height="500" allowfullscreen></iframe>'.encode()
                elif self.path == "/master.m3u8":
                    mime = "application/vnd.apple.mpegurl"
                    body = b'#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=100000,RESOLUTION=160x90\nmedia.m3u8\n'
                elif self.path == "/media.m3u8":
                    mime = "application/vnd.apple.mpegurl"
                    body = (cls.work / "media.m3u8").read_bytes().replace(b".ts", b".jpeg")
                elif self.path in {"/video.mp4", "/audio.m4a"}:
                    mime = "video/mp4" if self.path.endswith(".mp4") else "audio/mp4"
                    body = (cls.work / self.path.lstrip("/")).read_bytes()
                elif self.path.endswith(".jpeg"):
                    mime = "image/jpeg"
                    path = cls.work / (Path(self.path).stem + ".ts")
                    body = b'PREFIX-IMAGE-' + path.read_bytes()
                else:
                    self.send_error(404)
                    return
                self.send_response(200)
                self.send_header("Content-Type", mime)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.url = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=3)
        cls.temp.cleanup()

    def test_article_resolves_without_segments_then_local_client_downloads_and_merges(self):
        self.requests.clear()
        with patch.object(download_hls, "download", side_effect=AssertionError("Server download")), \
             patch.object(download_hls, "run_ffmpeg", side_effect=AssertionError("Server FFmpeg")):
            result = resolve(self.url + "/article", allow_private=True, browser=False)
        self.assertEqual(result["status"], "ready", result)
        self.assertEqual(result["sources"][0]["url"], self.url + "/master.m3u8")
        self.assertFalse(any(path.endswith(".jpeg") for path in self.requests))
        output = self.work / "local-result.mp4"
        download_plan.download(result, Options(output=str(output)), allow_private=True)
        self.assertTrue(any(path.endswith(".jpeg") for path in self.requests))
        info = json.loads(subprocess.check_output(["ffprobe", "-v", "error", "-show_entries", "format=duration:stream=codec_type", "-of", "json", str(output)]))
        self.assertLess(abs(float(info["format"]["duration"]) - 2), .2)
        self.assertEqual({item["codec_type"] for item in info["streams"]}, {"video", "audio"})

    def test_browser_discovers_manifest_but_aborts_segment_requests(self):
        from resolve_browser import discover
        self.requests.clear()
        value = discover(self.url + "/dynamic", "fixture", allow_private=True, seconds=3)
        self.assertEqual(value["status"], "ready", value)
        self.assertEqual(value["sources"][0]["kind"], "hls")
        self.assertFalse(any(path.endswith(".jpeg") for path in self.requests), self.requests)

    def test_separate_video_and_audio_are_merged_only_by_the_local_client(self):
        value = plan(self.url + "/article", "fixture", [
            source(self.url + "/video.mp4", role="video"),
            source(self.url + "/audio.m4a", role="audio")])
        result = download_plan.download(value, Options(output=str(self.work / "tracks.mp4")), allow_private=True)
        self.assertEqual(result.suffix, ".mkv")
        info = json.loads(subprocess.check_output(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type:format=duration", "-of", "json", str(result)]))
        self.assertEqual({item["codec_type"] for item in info["streams"]}, {"video", "audio"})
        self.assertLess(abs(float(info["format"]["duration"]) - 2), .2)

    def test_media_response_is_closed_without_body_read(self):
        client = MetadataClient(True)
        response = MagicMock()
        response.headers = {"Content-Type": "video/mp4"}
        response.geturl.return_value = self.url + "/opaque"
        client.opener = MagicMock()
        client.opener.open.return_value = response
        with self.assertRaises(MediaFound):
            client.fetch(self.url + "/opaque")
        response.read.assert_not_called()
        response.close.assert_called_once()

    def test_cross_origin_player_is_reopened_under_metadata_interception(self):
        from resolve_browser import discover
        self.requests.clear()
        value = discover(self.url + "/outer", "fixture", allow_private=True, seconds=2)
        self.assertEqual(value["input_url"], self.url + "/outer")
        self.assertEqual(value["status"], "ready")
        self.assertFalse(any(path.endswith(".jpeg") for path in self.requests), self.requests)

    def test_private_targets_and_credential_headers_are_rejected(self):
        for url in ("http://127.0.0.1/x", "http://169.254.169.254/latest/meta-data", "http://[::1]/", "file:///C:/secret", "https://name:pass@example.com/"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                validate_url(url)
        value = plan(self.url + "/article", "fixture", [source(self.url + "/master.m3u8", "hls")])
        value["sources"][0]["headers"]["Cookie"] = "private-token"
        with self.assertRaisesRegex(ValueError, "账号凭证"):
            validate_plan(value, allow_private=True)

    def test_native_extraction_never_downloads_or_exports_cookies(self):
        fake = MagicMock()
        ydl = fake.__enter__.return_value
        ydl.extract_info.return_value = {"title": "fixture", "url": self.url + "/clip.mp4", "vcodec": "h264", "acodec": "aac", "http_headers": {"Cookie": "not-for-export", "Referer": "https://example.test/"}}
        transport = MagicMock()
        transport.__enter__.return_value.received = 100
        with patch("yt_dlp.YoutubeDL", return_value=fake), patch("resolve_router.native_transport", return_value=transport):
            value = resolve_native("https://www.youtube.com/watch?v=AbCdEfGhI12", "YouTube", allow_private=True)
        self.assertFalse(ydl.extract_info.call_args.kwargs["download"])
        self.assertNotIn("Cookie", value["sources"][0]["headers"])

    def test_resolve_api_exports_only_json_and_rejects_download_and_login(self):
        manager = JobManager(self.work / "api", self.work / "api-output", resolve_only=True, allow_private=True)
        server = LocalServer(0, manager)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = server.url
        def get(path):
            with urlopen(base + path, timeout=10) as response:
                return json.load(response)
        token = get("/api/config")["token"]
        def post(path, value):
            request = Request(base + path, data=json.dumps(value).encode(), headers={"Content-Type": "application/json", "X-Local-Token": token})
            with urlopen(request, timeout=10) as response:
                return json.load(response)
        try:
            self.requests.clear()
            job = post("/api/resolve", {"url": self.url + "/article"})
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                result = get("/api/tasks/" + job["id"])
                if result["status"] not in {"queued", "running"}:
                    break
                time.sleep(.1)
            self.assertEqual(result["status"], "completed", result)
            task = get("/api/tasks/" + job["id"] + "/plan")
            self.assertEqual(task["sources"][0]["kind"], "hls")
            self.assertEqual(list(manager.output_dir.iterdir()), [])
            self.assertFalse(any(path.endswith(".jpeg") for path in self.requests))
            for payload in ({"kind": "download", "url": self.url + "/article"}, {"kind": "login_youtube"}):
                with self.assertRaises(HTTPError) as error:
                    post("/api/tasks", payload)
                self.assertEqual(error.exception.code, 400)
            with self.assertRaises(HTTPError) as error:
                post("/api/youtube/import", {"cookies": "private"})
            self.assertEqual(error.exception.code, 403)
        finally:
            server.shutdown()
            manager.close()
            server.server_close()
            thread.join(timeout=3)


if __name__ == "__main__":
    unittest.main(verbosity=2)
