"""Use synthetic login data only; never read a personal browser or saved session."""
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "vendor"))
from site_handlers import browser, douyin, douyin_session
from site_handlers.common import Options

VIDEO = "https://www.douyin.com/video/1234567890123456789"


def fixture_state():
    return {
        "cookies": [
            {"name": "sessionid", "value": "synthetic-douyin-secret", "domain": ".douyin.com",
             "path": "/", "expires": time.time() + 3600, "httpOnly": True,
             "secure": True, "sameSite": "Lax"},
            {"name": "sessionid", "value": "unrelated-secret", "domain": ".other.test",
             "path": "/", "expires": time.time() + 3600},
        ],
        "origins": [
            {"origin": "https://www.douyin.com", "localStorage": [{"name": "test", "value": "fixture"}]},
            {"origin": "https://other.test", "localStorage": []},
        ],
    }


class DouyinSessionTests(unittest.TestCase):
    def test_encryption_scope_expiry_and_corruption(self):
        with tempfile.TemporaryDirectory(prefix=".douyin_test_", dir=ROOT.parent) as name:
            self.assertEqual(Path(name).resolve().parent, ROOT.parent.resolve())
            store = douyin_session.SessionStore(name)
            self.assertIsNone(store.load())
            state = fixture_state()
            store.save(state, "FixtureBrowser/1.0")
            blob = store.path.read_bytes()
            self.assertNotIn(b"synthetic-douyin-secret", blob)
            self.assertNotIn(b"unrelated-secret", blob)
            record = store.load()
            self.assertEqual(len(record["state"]["cookies"]), 1)
            self.assertEqual(record["state"]["cookies"][0]["value"], "synthetic-douyin-secret")
            self.assertEqual(len(record["state"]["origins"]), 1)
            self.assertEqual(record["user_agent"], "FixtureBrowser/1.0")
            with patch.object(douyin_session.time, "time", return_value=state["cookies"][0]["expires"] + 1):
                with self.assertRaises(douyin_session.SessionError):
                    store.load()
            store.path.write_bytes(b"broken")
            with self.assertRaises(douyin_session.SessionError):
                store.load()

    def test_missing_login_and_invalid_agent_are_not_saved(self):
        with tempfile.TemporaryDirectory(prefix=".douyin_test_", dir=ROOT.parent) as name:
            store = douyin_session.SessionStore(name)
            with self.assertRaises(douyin_session.SessionError):
                store.save({"cookies": [], "origins": []}, "FixtureBrowser/1.0")
            with self.assertRaises(douyin_session.SessionError):
                store.save(fixture_state(), "Fixture\r\nInjected: header")
            self.assertFalse(store.path.exists())
        self.assertFalse(douyin_session.supported_host("douyin.com.evil.test"))

    def test_cancel_does_not_read_or_save_login_state(self):
        manager = MagicMock()
        chrome = manager.__enter__.return_value.chromium.launch.return_value
        context = chrome.new_context.return_value
        with (
            patch("playwright.sync_api.sync_playwright", return_value=manager),
            patch.object(browser, "wait_for_manual", side_effect=browser.BrowserInteractionCancelled("cancel")),
            patch.object(douyin_session.SessionStore, "save") as save,
        ):
            with self.assertRaises(browser.BrowserInteractionCancelled):
                douyin_session.login(VIDEO)
        save.assert_not_called()
        context.storage_state.assert_not_called()
        chrome.close.assert_called_once()

    def test_saved_session_reaches_both_extractors_without_changing_target(self):
        record = {"state": douyin_session.filter_state(fixture_state()), "user_agent": "FixtureBrowser/1.0"}
        cookies = MagicMock()
        cookies.__enter__.return_value = Path("synthetic-cookies.txt")
        with (
            patch.object(douyin_session.SessionStore, "load", return_value=record),
            patch.object(douyin_session, "cookie_file", return_value=cookies),
            patch("site_handlers.yt_engine.run", side_effect=RuntimeError("native unavailable")) as native,
            patch.object(browser, "run", return_value="downloaded") as fallback,
        ):
            self.assertEqual(douyin.run(VIDEO, Options(check=True)), "downloaded")
        self.assertEqual(native.call_args.kwargs["cookiefile"], Path("synthetic-cookies.txt"))
        self.assertEqual(native.call_args.kwargs["headers"], {"User-Agent": "FixtureBrowser/1.0"})
        self.assertEqual(fallback.call_args.args[0], VIDEO)
        options = fallback.call_args.args[1]
        self.assertEqual(options.expected_video_id, "1234567890123456789")
        self.assertEqual(options.browser_state, record["state"])
        self.assertEqual(options.browser_user_agent, record["user_agent"])
        self.assertTrue(options.passive_browser)


if __name__ == "__main__":
    unittest.main(verbosity=2)
