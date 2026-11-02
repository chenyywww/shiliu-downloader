"""Drive the actual local page and downloader against generated local HLS media."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "vendor"))
from playwright.sync_api import sync_playwright, expect
import test_browser
from web_app import LocalServer
from web_jobs import JobManager
from site_handlers import youtube_session


class LocalWebBrowserTests(unittest.TestCase):
    def test_page_can_download_open_details_and_show_mobile_layout(self):
        fixture = test_browser.BrowserTests
        fixture.setUpClass()
        try:
            with tempfile.TemporaryDirectory(prefix=".web_browser_", dir=ROOT.parent) as name:
                work = Path(name).resolve()
                self.assertEqual(work.parent, ROOT.parent.resolve())
                manager = JobManager(work / "state", work / "output")
                opened = []
                server = LocalServer(0, manager, opener=opened.append)
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                try:
                    with sync_playwright() as playwright:
                        chrome = playwright.chromium.launch(channel="chrome", headless=True)
                        try:
                            page = chrome.new_page(viewport={"width": 1440, "height": 1050}, device_scale_factor=1)
                            errors = []
                            page.on("pageerror", lambda error: errors.append(str(error)))
                            page.goto(server.url, wait_until="networkidle")
                            expect(page.get_by_text("还没有下载任务")).to_be_visible()
                            page.locator("#video-url").fill(fixture.url + "/config-page")
                            page.get_by_role("button", name="开始下载", exact=True).click()
                            expect(page.locator(".task-card.completed")).to_have_count(1, timeout=45000)
                            job = manager.store.all()[0]
                            self.assertEqual(job["status"], "completed", job)
                            result = Path(job["output"])
                            info = json.loads(subprocess.check_output([
                                "ffprobe", "-v", "error", "-show_entries", "format=duration:stream=codec_type", "-of", "json", str(result)]))
                            self.assertLess(abs(float(info["format"]["duration"]) - 2), .2)
                            self.assertEqual({stream["codec_type"] for stream in info["streams"]}, {"video", "audio"})
                            page.get_by_role("button", name="详情", exact=True).click()
                            expect(page.locator("#detail-dialog")).to_be_visible()
                            expect(page.locator("#detail-body")).to_contain_text(result.name)
                            page.get_by_role("button", name="打开所在文件夹", exact=True).click()
                            expect(page.locator("#toast")).to_be_hidden(timeout=6000)
                            self.assertEqual(opened, [result.parent])
                            page.get_by_role("button", name="关闭任务详情").click()
                            page.get_by_role("button", name="站点与处理方式").click()
                            expect(page.locator("#sites-dialog")).to_be_visible()
                            page.get_by_role("button", name="关闭站点记录").click()
                            page.get_by_role("button", name="登录与辅助设置").click()
                            expect(page.locator("#settings-dialog")).to_be_visible()
                            expect(page.locator('#youtube-setup-button')).to_be_visible()
                            self.assertFalse(page.locator("#option-youtube").is_checked())
                            with patch("web_app.webbrowser.open", return_value=True) as launch:
                                page.locator("#youtube-setup-button").click()
                                expect(page.locator("#toast")).to_contain_text("已请求在默认浏览器打开 YouTube")
                                launch.assert_called_once_with("https://www.youtube.com/")
                            expect(page.locator('[data-login="login_youtube"]')).to_be_visible()
                            with patch.object(youtube_session, "DIRECTORY", work / "auth"):
                                page.locator("#youtube-cookie-file").set_input_files({
                                    "name": "synthetic-cookies.txt", "mimeType": "text/plain",
                                    "buffer": b"# Netscape HTTP Cookie File\n.youtube.com\tTRUE\t/\tTRUE\t2147483647\tSAPISID\tsynthetic-ui-secret\n"})
                                expect(page.locator("#toast")).to_contain_text("YouTube 会话已加密保存")
                                self.assertIsNotNone(youtube_session.SessionStore().load())
                                page.locator("#youtube-forget-button").click()
                                expect(page.locator("#toast")).to_contain_text("已清除本机 YouTube")
                                self.assertIsNone(youtube_session.SessionStore().load())
                            page.get_by_role("button", name="关闭设置").click()
                            qa = ROOT / ".web" / "qa"
                            qa.mkdir(parents=True, exist_ok=True)
                            page.screenshot(path=str(qa / "desktop.png"), full_page=True)
                            page.set_viewport_size({"width":390,"height":844})
                            page.screenshot(path=str(qa / "mobile.png"), full_page=True)
                            overflow = page.evaluate("Array.from(document.querySelectorAll('body *')).filter(e=>e.getBoundingClientRect().right>innerWidth+1).map(e=>e.tagName+'.'+e.className).slice(0,12)")
                            self.assertTrue(page.evaluate("document.documentElement.scrollWidth <= innerWidth"), overflow)
                            expect(page.locator("#submit-button")).to_be_visible()
                            self.assertEqual(errors, [])
                        finally:
                            chrome.close()
                finally:
                    server.shutdown()
                    manager.close()
                    server.server_close()
                    thread.join(timeout=3)
        finally:
            fixture.tearDownClass()


if __name__ == "__main__":
    unittest.main(verbosity=2)
