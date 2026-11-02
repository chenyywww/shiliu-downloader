"""Distribution must exclude credentials, personal history, and unrelated files."""
from pathlib import Path
import sys
import tempfile
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from build_release import create_release


class ReleaseTests(unittest.TestCase):
    def test_distribution_contains_source_but_no_local_secrets_or_history(self):
        with tempfile.TemporaryDirectory(prefix=".release_test_", dir=ROOT.parent) as name:
            work = Path(name).resolve()
            self.assertEqual(work.parent, ROOT.parent.resolve())
            source = work / "source"
            files = {"download_router.py": "print('source')", "site_handlers/youtube_session.py": "# source",
                     "README.en.md": "# English guide", "docs/usage.en.md": "# Usage",
                     "docs/assets/workspace.png": "synthetic-preview", "docs/private.txt": "private-account-secret",
                     "web_ui/app.js": "// source", "vendor/dependency/__init__.py": "# vendor",
                     ".auth/youtube-session.bin": "private-account-secret", ".web/tasks.sqlite3": "private-history",
                     "download-error.log": "private-history", "my_password.txt": "private-account-secret",
                     "vendor/__pycache__/module.pyc": "cache", "web_ui/preview.log": "private-history"}
            for relative, data in files.items():
                path = source / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(data, encoding="utf-8")
            archive = create_release(source, work / "release.zip")
            with zipfile.ZipFile(archive) as bundle:
                names = bundle.namelist()
                self.assertIn("local-video-downloader/site_handlers/youtube_session.py", names)
                self.assertIn("local-video-downloader/vendor/dependency/__init__.py", names)
                self.assertIn("local-video-downloader/README.en.md", names)
                self.assertIn("local-video-downloader/docs/usage.en.md", names)
                self.assertIn("local-video-downloader/docs/assets/workspace.png", names)
                self.assertNotIn("local-video-downloader/docs/private.txt", names)
                for name in names:
                    value = bundle.read(name)
                    self.assertNotIn(b"private-account-secret", value)
                    self.assertNotIn(b"private-history", value)
                    self.assertNotIn(".auth/", name)
                    self.assertNotIn(".web/", name)


if __name__ == "__main__":
    unittest.main(verbosity=2)
