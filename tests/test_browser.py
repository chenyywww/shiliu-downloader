"""Local browser regression checks; no real website verification is automated."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "vendor"))

from playwright.sync_api import BrowserType, Frame, Page
from site_handlers import browser, generic
from site_handlers.common import Options, OUTPUT_DIR
import download_hls


class BrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory(prefix=".browser_test_", dir=OUTPUT_DIR)
        cls.work = Path(cls.directory.name).resolve()
        assert cls.work.parent == OUTPUT_DIR.resolve()
        cls.media = cls.work / "source.mp4"
        subprocess.run(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin",
             "-f", "lavfi", "-i", "color=c=blue:s=160x90:r=10:d=2",
             "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
             "-c:v", "libx264", "-g", "10", "-sc_threshold", "0",
             "-pix_fmt", "yuv420p", "-c:a", "aac",
             "-movflags", "+faststart", str(cls.media)],
            check=True, capture_output=True,
        )
        cls.requests = []
        cls.hls_requests = []
        cls.probe_requests = []
        subprocess.run(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin",
             "-i", str(cls.media), "-c", "copy", "-hls_time", "1",
             "-hls_list_size", "0", "-hls_playlist_type", "vod",
             "-hls_segment_filename", str(cls.work / "part%d.ts"), str(cls.work / "media.m3u8")],
            check=True, capture_output=True,
        )
        hls_payloads = {"/hls/master.m3u8": (
            b'#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=100000,RESOLUTION=160x90\n720p/video.m3u8\n',
            "application/vnd.apple.mpegurl",
        ), "/hls/720p/video.m3u8": (
            (cls.work / "media.m3u8").read_bytes().replace(b".ts", b".jpeg"),
            "application/vnd.apple.mpegurl",
        )}
        for segment in cls.work.glob("part*.ts"):
            hls_payloads["/hls/720p/" + segment.with_suffix(".jpeg").name] = (
                segment.read_bytes(), "image/jpeg"
            )
        media = cls.media.read_bytes()
        requests = cls.requests
        hls_requests, probe_requests = cls.hls_requests, cls.probe_requests

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_HEAD(self):
                self.do_GET()

            def do_GET(self):
                if self.path == "/loading.mp4":
                    body, mime, status = media, "video/mp4", 200
                elif self.path == "/asset.mp4":
                    requests.append(dict(self.headers))
                    if "fixture=allowed" not in self.headers.get("Cookie", ""):
                        self.send_error(403)
                        return
                    body, mime, status = media, "video/mp4", 200
                elif self.path.startswith("/hls/"):
                    hls_requests.append(dict(self.headers))
                    if (
                        "fixture=allowed" not in self.headers.get("Cookie", "")
                        or self.headers.get("Origin") != "https://fixture.test"
                        or self.headers.get("Referer") != "https://fixture.test/watch"
                        or self.headers.get("User-Agent") != "FixtureBrowser/1.0"
                    ):
                        self.send_error(403)
                        return
                    body, mime = hls_payloads[self.path]
                    status = 200
                elif self.path.startswith("/capture/"):
                    if "fixture=allowed" not in self.headers.get("Cookie", ""):
                        self.send_error(403)
                        return
                    body, mime = hls_payloads[self.path.replace("/capture/", "/hls/", 1)]
                    status = 200
                elif self.path == "/probe-no-session":
                    probe_requests.append(dict(self.headers))
                    body, mime, status = b"ok", "text/plain", 200
                elif self.path in ("/blocked", "/delayed"):
                    body = b"<!doctype html><title>Local verification fixture</title>"
                    if self.path == "/delayed":
                        body += b"<script>setTimeout(()=>location.href='/play',1200)</script>"
                    mime, status = "text/html", 403
                elif self.path == "/forbidden":
                    body, mime, status = b"Forbidden", "text/html", 403
                elif self.path == "/config-page":
                    body = (b"<!doctype html><title>Static player fixture</title>"
                            b"<video class='player'></video>"
                            b"<script>const source='/capture/master.m3u8';</script>")
                    mime, status = "text/html", 200
                elif self.path == "/delayed-player":
                    encoded = ",".join(str(ord(c)) for c in "/asset.mp4")
                    body = (
                        "<!doctype html><title>Loading animation fixture</title><video muted></video>"
                        "<script>fetch('/loading.mp4');setTimeout(()=>{document.querySelector('video').src="
                        f"String.fromCharCode({encoded});}},6500)</script>"
                    ).encode()
                    mime, status = "text/html", 200
                else:
                    encoded = ",".join(str(ord(c)) for c in "/asset.mp4")
                    body = (
                        "<!doctype html><title>Dynamic fixture</title><video muted></video>"
                        "<script>setTimeout(()=>{document.querySelector('video').src="
                        f"String.fromCharCode({encoded});}},100)</script>"
                    ).encode()
                    if self.path == "/capture-page":
                        body += (
                            b"<script>fetch('/capture/master.m3u8');"
                            b"fetch('/capture/720p/video.m3u8');</script>"
                        )
                    mime, status = "text/html", 200
                self.send_response(status)
                self.send_header("Content-Type", mime)
                self.send_header("Content-Length", str(len(body)))
                if self.path in ("/blocked", "/delayed"):
                    self.send_header("cf-mitigated", "challenge")
                if status == 200 and mime == "text/html":
                    self.send_header("Set-Cookie", "fixture=allowed; Path=/; SameSite=Lax")
                self.end_headers()
                if self.command != "HEAD":
                    self.wfile.write(body)

        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.url = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)
        assert cls.work.parent == OUTPUT_DIR.resolve()
        cls.directory.cleanup()

    def test_challenge_is_reported_before_media_detection(self):
        with self.assertRaisesRegex(browser.BrowserChallengeError, "Cloudflare.*403"):
            browser.capture(self.url + "/blocked", Options(check=True, browser_wait=5))
        with self.assertRaisesRegex(RuntimeError, "HTTP 403") as caught:
            browser.capture(self.url + "/forbidden", Options(check=True, browser_wait=5))
        self.assertNotIsInstance(caught.exception, browser.BrowserChallengeError)

    def test_advert_is_not_used_when_main_player_has_no_matching_request(self):
        candidates = {"https://ads.invalid/banner.mp4": {
            "url": "https://ads.invalid/banner.mp4", "kind": "file",
            "frame": "https://ads.invalid/frame", "order": 0,
        }}
        videos = {"https://example.test/watch": [{
            "src": "blob:https://example.test/video", "tag": "video", "area": 500000,
            "duration": 3600,
        }]}
        with self.assertRaisesRegex(RuntimeError, "可能只是广告"):
            browser.rank_candidates(candidates, videos)

    def test_main_player_manifest_ranks_above_advert_file(self):
        candidates = {
            "advert": {"url": "https://ads.invalid/banner.mp4", "kind": "file",
                       "frame": "https://ads.invalid/frame", "order": 0},
            "main": {"url": "https://cdn.invalid/master.m3u8", "kind": "hls",
                     "frame": "https://example.test/watch", "order": 1},
        }
        videos = {
            "https://example.test/watch": [{"src": "blob:https://example.test/video",
                "tag": "video", "area": 500000, "duration": 3600}],
            "https://ads.invalid/frame": [{"src": "https://ads.invalid/banner.mp4",
                "tag": "video", "area": 75000, "duration": 20}],
        }
        result = browser.rank_candidates(candidates, videos)
        self.assertEqual(result[0]["url"], "https://cdn.invalid/master.m3u8")

    def test_vod_duration_distinguishes_ads_in_the_same_frame(self):
        frame = "https://example.test/watch"
        master = "https://cdn.invalid/playlist.m3u8"
        child = "https://cdn.invalid/720p/video.m3u8"
        contents = {
            master: "#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=100\n720p/video.m3u8\n",
            child: "#EXTM3U\n#EXTINF:3600,\nsegment.jpeg\n#EXT-X-ENDLIST\n",
            "https://ads.invalid/live.m3u8": "#EXTM3U\n#EXTINF:4,\npart.mp4\n",
        }
        candidates = {
            url: {"url": url, "kind": "hls", "frame": frame, "order": index,
                  "manifest": browser.hls_metadata(text, url)}
            for index, (url, text) in enumerate(contents.items())
        }
        videos = {frame: [{"src": "blob:https://example.test/video", "tag": "video",
                           "area": 500000, "duration": 3600}]}
        result = browser.rank_candidates(candidates, videos)
        self.assertEqual(result[0]["url"], master)
        self.assertEqual(len(result), 2)
        self.assertTrue(result[0]["manifest"]["vod"])
        self.assertGreater(result[0]["score"] - result[1]["score"], 20)

    def test_hls_download_keeps_headers_and_session_cookies_in_workers(self):
        output = self.work / "hls-output.mp4"
        headers = {"User-Agent": "FixtureBrowser/1.0", "Origin": "https://fixture.test",
                   "Referer": "https://fixture.test/watch"}
        item = {"url": self.url + "/hls/master.m3u8", "kind": "hls",
                "headers": headers, "score": 1000, "manifest": {"vod": True}}
        cookies = [{"name": "fixture", "value": "allowed", "domain": "127.0.0.1",
                    "path": "/", "secure": False, "expires": -1}]
        with patch.object(browser, "capture", return_value=([item], cookies)):
            browser.run(self.url + "/play", Options(output=str(output), jobs=2))
        self.assertTrue(output.is_file())
        self.assertTrue(self.hls_requests)
        for request in self.hls_requests:
            for name, value in headers.items():
                self.assertEqual(request.get(name), value)
            self.assertIn("fixture=allowed", request.get("Cookie", ""))
        self.assertIsNone(download_hls.HTTP_SESSION.get())
        download_hls.fetch(self.url + "/probe-no-session", self.url)
        self.assertNotIn("Cookie", self.probe_requests[-1])
        self.assertNotIn("Origin", self.probe_requests[-1])
        subprocess.run(
            ["ffmpeg", "-v", "error", "-nostdin", "-xerror", "-i", str(output), "-f", "null", "-"],
            check=True, capture_output=True,
        )

    def test_browser_reads_manifest_bodies_and_combines_variants(self):
        items, _ = browser.capture(self.url + "/capture-page", Options(check=True, browser_wait=6))
        master = items[0]
        self.assertEqual(master["url"], self.url + "/capture/master.m3u8")
        self.assertTrue(master["manifest"]["master"])
        self.assertTrue(master["manifest"]["vod"])
        self.assertGreater(master["manifest"]["duration"], 1.9)
        self.assertFalse(any(item["url"].endswith("/720p/video.m3u8") for item in items))

    def test_configuration_is_extracted_without_playing_video_or_manual_panel(self):
        original_evaluate = Frame.evaluate
        playback_calls = []

        def evaluate(frame, expression, *args, **kwargs):
            if "video.play()" in expression:
                playback_calls.append(expression)
            return original_evaluate(frame, expression, *args, **kwargs)

        with (
            patch.object(Frame, "evaluate", evaluate),
            patch.object(browser, "wait_for_manual", side_effect=AssertionError("Unexpected manual panel")),
        ):
            items, _ = browser.capture(self.url + "/config-page", Options(check=True, browser_wait=8))
        self.assertTrue(items[0]["configured"])
        self.assertFalse(playback_calls)
        self.assertEqual(items[0]["url"], self.url + "/capture/master.m3u8")
        self.assertTrue(items[0]["manifest"]["vod"])

    def test_loading_animation_does_not_stop_capture_before_real_video_arrives(self):
        items, _ = browser.capture(self.url + "/delayed-player", Options(check=True, browser_wait=11))
        self.assertEqual(items[0]["url"], self.url + "/asset.mp4")

    def test_target_video_id_does_not_match_loading_or_recommended_video(self):
        self.assertTrue(browser.media_matches_video_id("https://cdn.test/play?__vid=123", "123"))
        self.assertFalse(browser.media_matches_video_id("https://cdn.test/loading.mp4", "123"))
        self.assertFalse(browser.media_matches_video_id("https://cdn.test/play?__vid=456", "123"))

    def test_verified_target_can_be_ranked_before_dom_player_is_ready(self):
        url = "https://cdn.test/play?__vid=123"
        candidates = {url: {"url": url, "kind": "file", "frame": "https://example.test/watch",
                            "order": 0, "configured": True}}
        self.assertTrue(browser.playable_media_seen(candidates, {}))
        self.assertEqual(browser.rank_candidates(candidates, {})[0]["url"], url)

    def test_http_configuration_can_download_without_launching_a_browser(self):
        from site_handlers.hls import run_webpage
        output = self.work / "configuration-output.mp4"
        with patch.object(BrowserType, "launch", side_effect=AssertionError("Unexpected browser")):
            run_webpage(self.url + "/config-page", Options(output=str(output)))
        self.assertTrue(output.is_file())
        subprocess.run(
            ["ffmpeg", "-v", "error", "-nostdin", "-xerror", "-i", str(output), "-f", "null", "-"],
            check=True, capture_output=True,
        )

    def test_challenge_can_clear_during_initial_wait(self):
        items, cookies = browser.capture(
            self.url + "/delayed", Options(check=True, browser_wait=6)
        )
        self.assertTrue(items[0]["url"].endswith("/asset.mp4"))
        self.assertTrue(any(cookie["name"] == "fixture" for cookie in cookies))

    def test_auto_manual_retry_rejects_early_ready_and_resets_timeout(self):
        original_launch, original_content = BrowserType.launch, Page.set_content
        launches, messages = [], []

        def launch(instance, *args, **kwargs):
            launches.append(kwargs.get("headless"))
            kwargs["headless"] = True  # Run our own control-page checks without visible windows.
            return original_launch(instance, *args, **kwargs)

        def content(panel, html, **kwargs):
            result = original_content(panel, html, **kwargs)
            if html == browser.MANUAL_PAGE:
                target = next(page for page in panel.context.pages if page != panel)
                target.evaluate("setTimeout(()=>location.href='/play',6000)")
                panel.expose_binding("recordTestStatus", lambda source, text: messages.append(text))
                panel.evaluate("""() => {
                    setTimeout(()=>document.querySelector('#ready').click(),50);
                    setTimeout(()=>window.recordTestStatus(
                        document.querySelector('#status').textContent),1000);
                    setTimeout(()=>document.querySelector('#ready').click(),6500);
                }""")
            return result

        start = time.monotonic()
        with (
            patch.object(BrowserType, "launch", launch),
            patch.object(Page, "set_content", content),
            patch.object(sys.stdin, "isatty", return_value=True),
        ):
            items, _ = browser.capture(
                self.url + "/blocked", Options(browser_wait=5)
            )
        self.assertEqual(launches, [True, False])
        self.assertGreater(time.monotonic() - start, 6)
        self.assertTrue(any("仍停留在验证页" in message for message in messages))
        self.assertTrue(items[0]["url"].endswith("/asset.mp4"))

    def test_manual_cancel_closes_browser(self):
        original_launch, original_content = BrowserType.launch, Page.set_content
        opened = []

        def launch(instance, *args, **kwargs):
            kwargs["headless"] = True
            opened.append(original_launch(instance, *args, **kwargs))
            return opened[-1]

        def content(panel, html, **kwargs):
            result = original_content(panel, html, **kwargs)
            if html == browser.MANUAL_PAGE:
                panel.evaluate("setTimeout(()=>document.querySelector('#cancel').click(),50)")
            return result

        with patch.object(BrowserType, "launch", launch), patch.object(Page, "set_content", content):
            with self.assertRaises(browser.BrowserInteractionCancelled):
                browser.capture(self.url + "/blocked", Options(show_browser=True))
        self.assertTrue(opened)
        self.assertTrue(all(not item.is_connected() for item in opened))

    def test_generic_download_keeps_captured_cookie_and_headers(self):
        output = self.work / "download.mp4"
        result = generic.run(self.url + "/play", Options(output=str(output), browser_wait=5))
        self.assertTrue(Path(result).is_file())
        metadata = json.loads(subprocess.check_output(
            ["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(result)]
        ))
        self.assertGreater(float(metadata["format"]["duration"]), 1.9)
        self.assertEqual({stream["codec_type"] for stream in metadata["streams"]}, {"video", "audio"})
        subprocess.run(
            ["ffmpeg", "-v", "error", "-nostdin", "-i", str(result), "-f", "null", "-"],
            check=True, capture_output=True,
        )
        self.assertTrue(all("fixture=allowed" in request.get("Cookie", "") for request in self.requests))
        self.assertTrue(any(request.get("Referer", "").endswith("/play") for request in self.requests))


if __name__ == "__main__":
    unittest.main(verbosity=2)
