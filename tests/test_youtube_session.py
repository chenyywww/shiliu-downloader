"""Only synthetic YouTube sessions are used in these tests."""
import http.cookiejar
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "vendor"))
from site_handlers import browser, youtube, youtube_session
from site_handlers.common import Options

VIDEO = "https://www.youtube.com/watch?v=AbCdEfGhI12"


def cookies():
    return [{"name": "SAPISID", "value": "synthetic-youtube-secret", "domain": ".youtube.com",
             "path": "/", "expires": time.time() + 3600, "secure": True, "httpOnly": True},
            {"name": "SAPISID", "value": "unrelated-google-secret", "domain": ".google.com",
             "path": "/", "expires": time.time() + 3600}]


class YouTubeSessionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix=".youtube_test_", dir=ROOT.parent)
        self.work = Path(self.temp.name).resolve()
        self.assertEqual(self.work.parent, ROOT.parent.resolve())

    def tearDown(self):
        self.temp.cleanup()

    def test_encryption_domain_scope_expiry_and_forget(self):
        store = youtube_session.SessionStore(self.work)
        self.assertIsNone(store.load())
        fixture = cookies()
        store.save(fixture, "FixtureBrowser/1.0")
        data = store.path.read_bytes()
        self.assertNotIn(b"synthetic-youtube-secret", data)
        self.assertNotIn(b"unrelated-google-secret", data)
        record = store.load()
        self.assertEqual(len(record["cookies"]), 1)
        self.assertEqual(record["cookies"][0]["domain"], ".youtube.com")
        with patch.object(youtube_session.time, "time", return_value=fixture[0]["expires"] + 1):
            with self.assertRaises(youtube_session.SessionError):
                store.load()
        store.forget()
        self.assertIsNone(store.load())
        self.assertFalse(youtube_session.supported_host("youtube.com.evil.test"))

    def test_import_filters_google_and_temporary_export_is_deleted(self):
        store = youtube_session.SessionStore(self.work)
        expiry = str(int(time.time()) + 3600)
        text = ("# Netscape HTTP Cookie File\n"
                f"#HttpOnly_.youtube.com\tTRUE\t/\tTRUE\t{expiry}\tSAPISID\tsynthetic-youtube-secret\n"
                f".google.com\tTRUE\t/\tTRUE\t{expiry}\tSAPISID\tunrelated-google-secret\n")
        youtube_session.import_cookie_text(text, store)
        record = store.load()
        with youtube_session.cookie_file(record, self.work) as path:
            jar = http.cookiejar.MozillaCookieJar(str(path))
            jar.load(ignore_discard=True, ignore_expires=True)
            self.assertEqual([item.domain for item in jar], [".youtube.com"])
        self.assertFalse(path.exists())
        self.assertEqual([file.name for file in self.work.iterdir()], ["youtube-session.bin"])

    def test_invalid_import_does_not_echo_cookie_values_or_write_a_session(self):
        store = youtube_session.SessionStore(self.work)
        with self.assertRaises(youtube_session.SessionError) as error:
            youtube_session.import_cookie_text("# Netscape HTTP Cookie File\nSECRET-INVALID-ROW", store)
        self.assertNotIn("SECRET-INVALID-ROW", str(error.exception))
        self.assertFalse(store.path.exists())

    def test_cancel_does_not_save_or_read_an_existing_profile(self):
        manager = MagicMock()
        chrome = manager.__enter__.return_value.chromium.launch.return_value
        with (patch("playwright.sync_api.sync_playwright", return_value=manager),
              patch.object(browser, "wait_for_manual", side_effect=browser.BrowserInteractionCancelled("cancel")),
              patch.object(youtube_session.SessionStore, "save") as save):
            with self.assertRaises(browser.BrowserInteractionCancelled):
                youtube_session.login(VIDEO)
        chrome.new_context.assert_called_once_with()
        save.assert_not_called()
        chrome.close.assert_called_once()

    def test_anonymous_failure_does_not_read_login_without_opt_in(self):
        with (patch("site_handlers.yt_engine.run", side_effect=RuntimeError("Sign in to confirm your age")),
              patch.object(youtube_session.SessionStore, "load", side_effect=AssertionError("Account read"))):
            with self.assertRaisesRegex(RuntimeError, "允许 YouTube 登录后备"):
                youtube.run(VIDEO, Options())

    def test_public_success_does_not_read_login_even_when_allowed(self):
        with (patch("site_handlers.yt_engine.run", return_value="downloaded"),
              patch.object(youtube_session.SessionStore, "load", side_effect=AssertionError("Account read"))):
            self.assertEqual(youtube.run(VIDEO, Options(use_youtube_login=True)), "downloaded")

    def test_only_explicit_opt_in_passes_saved_session_to_ytdlp(self):
        record = {"cookies": cookies()[:1], "user_agent": "FixtureBrowser/1.0"}
        export = MagicMock()
        export.__enter__.return_value = self.work / "synthetic-cookies.txt"
        with (patch("site_handlers.yt_engine.run", side_effect=[RuntimeError("Sign in"), "downloaded"]) as native,
              patch.object(youtube_session.SessionStore, "load", return_value=record),
              patch.object(youtube_session, "cookie_file", return_value=export)):
            self.assertEqual(youtube.run(VIDEO, Options(use_youtube_login=True)), "downloaded")
        self.assertNotIn("cookiefile", native.call_args_list[0].kwargs)
        self.assertEqual(native.call_args_list[1].kwargs["cookiefile"], self.work / "synthetic-cookies.txt")


if __name__ == "__main__":
    unittest.main(verbosity=2)
