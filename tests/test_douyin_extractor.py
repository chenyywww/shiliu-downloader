"""Validate target identity and required download headers in the local backport."""
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "vendor"))
from yt_dlp.extractor.tiktok import DouyinIE
from yt_dlp.utils import ExtractorError

VIDEO_ID = "1234567890123456789"
URL = "https://www.douyin.com/video/" + VIDEO_ID


class DouyinExtractorTests(unittest.TestCase):
    def test_wrong_work_from_server_is_rejected_before_media_parsing(self):
        extractor = DouyinIE()
        with (
            patch.object(extractor, "_ensure_required_cookies"),
            patch.object(extractor, "_sign_query", return_value={}),
            patch.object(extractor, "_download_json", return_value={"aweme_detail": {"aweme_id": "111"}}),
            patch.object(extractor, "_parse_aweme_video_app") as parse,
        ):
            with self.assertRaisesRegex(ExtractorError, "different video"):
                extractor._real_extract(URL)
        parse.assert_not_called()

    def test_matching_work_preserves_media_and_sets_cdn_referer(self):
        extractor = DouyinIE()
        media = {"id": VIDEO_ID, "formats": [{"url": "https://media.test/video.mp4"}]}
        with (
            patch.object(extractor, "_ensure_required_cookies"),
            patch.object(extractor, "_sign_query", return_value={"signed": "fixture"}) as sign,
            patch.object(extractor, "_download_json", return_value={"aweme_detail": {"aweme_id": VIDEO_ID}}),
            patch.object(extractor, "_parse_aweme_video_app", return_value=media),
        ):
            result = extractor._real_extract(URL)
        self.assertEqual(result["formats"], media["formats"])
        self.assertEqual(result["http_headers"], {"Referer": "https://www.douyin.com/"})
        self.assertEqual(sign.call_args.args[0], {"aweme_id": VIDEO_ID, "aid": 6383})


if __name__ == "__main__":
    unittest.main(verbosity=2)
