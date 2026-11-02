"""Actual web export followed by a separate local downloader process."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "vendor"))
from playwright.sync_api import sync_playwright, expect
import test_resolution
from web_jobs import JobManager
from web_app import LocalServer


class ResolveUITests(unittest.TestCase):
    def test_export_then_separate_client_process_downloads_video(self):
        fixture = test_resolution.ResolutionTests
        fixture.setUpClass()
        try:
            with tempfile.TemporaryDirectory(prefix=".resolve_ui_", dir=ROOT.parent) as name:
                work = Path(name).resolve()
                self.assertEqual(work.parent, ROOT.parent.resolve())
                manager = JobManager(work / "state", work / "server-output", resolve_only=True, allow_private=True)
                server = LocalServer(0, manager)
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                try:
                    with sync_playwright() as p:
                        browser = p.chromium.launch(channel="chrome", headless=True)
                        try:
                            page = browser.new_page(viewport={"width":1440,"height":1050}, accept_downloads=True)
                            errors = []
                            page.on("pageerror", lambda error: errors.append(str(error)))
                            page.goto(server.url, wait_until="networkidle")
                            expect(page.locator("h1")).to_have_text("视频链接解析")
                            expect(page.locator("#settings-button")).to_be_hidden()
                            fixture.requests.clear()
                            page.locator("#video-url").fill(fixture.url + "/article")
                            page.get_by_role("button", name="开始解析", exact=True).click()
                            expect(page.locator(".resolved-source")).to_have_count(1, timeout=30000)
                            self.assertFalse(any(path.endswith(".jpeg") for path in fixture.requests))
                            self.assertEqual(list(manager.output_dir.iterdir()), [])
                            with page.expect_download() as download:
                                page.get_by_role("link", name="导出本机任务 JSON ↓").click()
                            task_file = work / "download-plan.json"
                            download.value.save_as(task_file)
                            value = json.loads(task_file.read_text(encoding="utf-8"))
                            self.assertEqual(value["sources"][0]["kind"], "hls")
                            self.assertNotIn("Cookie", value["sources"][0]["headers"])
                            output = work / "client-result.mp4"
                            subprocess.run([sys.executable, "-X", "utf8", str(ROOT / "download_plan.py"), str(task_file),
                                "-o", str(output), "--allow-private-test"], check=True, capture_output=True, timeout=45)
                            self.assertTrue(output.is_file())
                            self.assertTrue(any(path.endswith(".jpeg") for path in fixture.requests))
                            self.assertEqual(list(manager.output_dir.iterdir()), [])
                            qa = ROOT / ".web" / "qa"
                            qa.mkdir(parents=True, exist_ok=True)
                            page.screenshot(path=str(qa / "resolve-desktop.png"), full_page=True)
                            page.set_viewport_size({"width":390,"height":844})
                            self.assertTrue(page.evaluate("document.documentElement.scrollWidth <= innerWidth"))
                            page.screenshot(path=str(qa / "resolve-mobile.png"), full_page=True)
                            self.assertEqual(errors, [])
                        finally:
                            browser.close()
                finally:
                    server.shutdown()
                    manager.close()
                    server.server_close()
                    thread.join(timeout=3)
        finally:
            fixture.tearDownClass()


if __name__ == "__main__":
    unittest.main(verbosity=2)
