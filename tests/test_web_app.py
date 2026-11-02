"""Local HTTP boundaries, real worker processes, cancellation, and durable tasks."""
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from download_router import dispatch
from download_events import PREFIX
from site_handlers.common import Options
from web_app import LocalServer
from web_jobs import JobManager, JobStore
from site_handlers import youtube_session

WORKER = r'''
import json,sys,time
from pathlib import Path
job=json.loads(sys.argv[1])
def event(kind, **values):
    print('__VD_EVENT__'+json.dumps({'event':kind,**values}),flush=True)
event('progress',label='下载视频',fraction=.25,downloaded=1024,total_bytes=4096,speed=1024,eta=3)
if '/wait' in job['url']:
    time.sleep(60)
if '/fail' in job['url']:
    event('error',message='模拟媒体已过期')
    raise SystemExit(1)
time.sleep(.15)
path=Path(job['requested_output'])
path.write_bytes(b'test-media-fixture')
event('progress',label='保存文件',fraction=1,downloaded=4096,total_bytes=4096,speed=4096,eta=0)
event('completed',output=str(path))
'''


class LocalWebTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix=".web_test_", dir=ROOT.parent)
        self.work = Path(self.temp.name).resolve()
        self.assertEqual(self.work.parent, ROOT.parent.resolve())
        self.manager = JobManager(self.work / "state", self.work / "output",
                                  command_builder=lambda job: [sys.executable, "-u", "-c", WORKER, json.dumps(job)])
        self.opened = []
        self.server = LocalServer(0, self.manager, opener=self.opened.append)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = self.server.url
        self.token = self.request("/api/config")["token"]

    def tearDown(self):
        self.server.shutdown()
        self.manager.close()
        self.server.server_close()
        self.thread.join(timeout=3)
        self.temp.cleanup()

    def request(self, path, payload=None, headers=None):
        values = {"Content-Type": "application/json", "X-Local-Token": getattr(self, "token", "")}
        values.update(headers or {})
        req = Request(self.url + path, data=json.dumps(payload).encode() if payload is not None else None, headers=values)
        with urlopen(req, timeout=10) as response:
            return json.load(response)

    def wait_for(self, identifier, statuses):
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            job = self.request("/api/tasks/" + identifier)
            if job["status"] in statuses:
                return job
            time.sleep(.05)
        self.fail("Task timed out: " + json.dumps(job))

    def test_real_worker_progress_result_and_open_action(self):
        job = self.request("/api/tasks", {"url": "https://example.test/video"})
        result = self.wait_for(job["id"], {"completed", "failed"})
        self.assertEqual(result["status"], "completed", result)
        self.assertEqual(result["progress"]["fraction"], 1)
        self.assertTrue(Path(result["output"]).is_file())
        self.request("/api/open", {"target": "file", "id": job["id"]})
        self.assertEqual(self.opened, [Path(result["output"])])
        Path(result["output"]).unlink()
        with self.assertRaises(HTTPError) as error:
            self.request("/api/open", {"target": "file", "id": job["id"]})
        self.assertEqual(error.exception.code, 400)

    def test_queue_cancel_failure_retry_and_no_history_reordering(self):
        first = self.request("/api/tasks", {"url": "https://example.test/wait"})
        self.wait_for(first["id"], {"running"})
        second = self.request("/api/tasks", {"url": "https://example.test/video"})
        self.assertEqual(self.request("/api/tasks")["tasks"][0]["id"], second["id"])
        self.request(f"/api/tasks/{second['id']}/cancel", {})
        self.assertEqual(self.wait_for(second["id"], {"cancelled"})["status"], "cancelled")
        self.request(f"/api/tasks/{first['id']}/cancel", {})
        self.assertEqual(self.wait_for(first["id"], {"cancelled"})["status"], "cancelled")
        failed = self.request("/api/tasks", {"url": "https://example.test/fail"})
        result = self.wait_for(failed["id"], {"failed"})
        self.assertIn("已过期", result["error"])
        retried = self.request(f"/api/tasks/{failed['id']}/retry", {})
        self.assertNotEqual(retried["id"], failed["id"])
        self.wait_for(retried["id"], {"failed"})

    def test_cross_site_unknown_host_and_missing_token_cannot_create_jobs(self):
        for headers in ({"Origin": "https://evil.test"}, {"Host": "evil.test"},
                        {"X-Local-Token": ""}, {"Sec-Fetch-Site": "cross-site"}):
            with self.subTest(headers=headers), self.assertRaises(HTTPError) as error:
                self.request("/api/tasks", {"url": "https://example.test/video"}, headers)
            self.assertEqual(error.exception.code, 403)
        self.assertEqual(self.request("/api/tasks")["tasks"], [])

    def test_duplicate_server_does_not_open_or_recover_live_task_database(self):
        with patch("web_app.JobManager") as manager:
            with self.assertRaises(OSError):
                LocalServer(self.server.server_port)
        manager.assert_not_called()

    def test_recovery_marks_unfinished_tasks_and_preserves_completed_tasks(self):
        store = JobStore(self.work / "recovery")
        for identifier, status in (("a" * 32, "running"), ("b" * 32, "queued"), ("c" * 32, "completed")):
            store.put({"id": identifier, "status": status})
        store.recover()
        self.assertEqual(store.get("a" * 32)["status"], "interrupted")
        self.assertEqual(store.get("b" * 32)["status"], "interrupted")
        self.assertEqual(store.get("c" * 32)["status"], "completed")

    def test_router_emits_all_output_files_for_multi_video_posts(self):
        outputs = [self.work / "one.mp4", self.work / "two.mp4"]
        stream = io.StringIO()
        with patch.dict("os.environ", {"VIDEO_DOWNLOADER_EVENTS": "1"}), redirect_stdout(stream), \
                patch("site_handlers.x.run", return_value=outputs):
            dispatch("https://x.com/example/status/123", Options())
        events = [json.loads(line[len(PREFIX):]) for line in stream.getvalue().splitlines() if line.startswith(PREFIX)]
        self.assertEqual(events[-1]["outputs"], [str(path) for path in outputs])

    def test_youtube_import_forget_and_retry_permission_are_local_and_explicit(self):
        text = "# Netscape HTTP Cookie File\n.youtube.com\tTRUE\t/\tTRUE\t2147483647\tSAPISID\tsynthetic-api-secret\n"
        with patch.object(youtube_session, "DIRECTORY", self.work / "auth"):
            self.request("/api/youtube/import", {"cookies": text})
            self.assertEqual(youtube_session.SessionStore().load()["cookies"][0]["domain"], ".youtube.com")
            self.request("/api/youtube/forget", {})
            self.assertIsNone(youtube_session.SessionStore().load())
        job = self.request("/api/tasks", {"url": "https://example.test/fail", "options": {"check": True}})
        self.wait_for(job["id"], {"failed"})
        retry = self.request(f"/api/tasks/{job['id']}/retry", {"options": {"use_youtube_login": True}})
        self.assertTrue(retry["options"]["use_youtube_login"])
        self.assertTrue(retry["options"]["check"])
        self.wait_for(retry["id"], {"failed"})


if __name__ == "__main__":
    unittest.main(verbosity=2)
