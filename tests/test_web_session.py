"""Check per-origin scope, encryption, expiry, and cookie filtering."""
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from site_handlers import web_session


class ClearanceTests(unittest.TestCase):
    def test_encrypted_clearance_is_scoped_and_contains_no_account_cookies(self):
        with tempfile.TemporaryDirectory(prefix=".clearance_test_", dir=ROOT.parent) as name:
            work = Path(name).resolve()
            self.assertEqual(work.parent, ROOT.parent.resolve())
            store = web_session.ClearanceStore(work)
            url = "https://example.test/watch/one"
            expires = time.time() + 3600
            cookies = [
                {"name": "cf_clearance", "value": "test-clearance-secret", "domain": ".example.test",
                 "path": "/", "expires": expires, "httpOnly": True, "secure": True, "sameSite": "None"},
                {"name": "account_token", "value": "private-account-secret", "domain": ".example.test",
                 "path": "/", "expires": expires},
                {"name": "cf_clearance", "value": "unrelated-secret", "domain": ".other.test",
                 "path": "/", "expires": expires},
            ]
            self.assertTrue(store.save(url, cookies, "FixtureBrowser/1.0"))
            data = store.path(url).read_bytes()
            for value in (b"test-clearance-secret", b"private-account-secret", b"unrelated-secret"):
                self.assertNotIn(value, data)
            restored = store.load("https://example.test/watch/two")
            self.assertEqual([cookie["name"] for cookie in restored["cookies"]], ["cf_clearance"])
            self.assertEqual(restored["cookies"][0]["value"], "test-clearance-secret")
            self.assertIsNone(store.load("https://other.test/watch"))
            self.assertIsNone(store.load("http://example.test/watch"))
            with patch.object(web_session.time, "time", return_value=expires + 1):
                self.assertIsNone(store.load(url))
            store.path(url).write_bytes(b"broken")
            self.assertIsNone(store.load(url))

    def test_only_account_cookies_are_never_saved(self):
        with tempfile.TemporaryDirectory(prefix=".clearance_test_", dir=ROOT.parent) as name:
            store = web_session.ClearanceStore(name)
            url = "https://example.test/watch"
            self.assertFalse(store.save(url, [{"name": "account_token", "value": "secret",
                "domain": "example.test", "expires": time.time() + 3600}], "FixtureBrowser/1.0"))
            self.assertFalse(store.path(url).exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
