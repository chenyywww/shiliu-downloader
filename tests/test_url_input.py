from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from url_input import extract_url
from download_router import select_route
from site_handlers import douyin
from site_handlers.common import Options

VIDEO = "https://www.douyin.com/video/1234567890123456789"
SHORT = "https://v.douyin.com/ExampleShare/"


class ShareInputTests(unittest.TestCase):
    def test_share_text_and_markdown_are_accepted(self):
        self.assertEqual(extract_url("示例账号 示例分享内容 #示例标签 " + SHORT + " 复制此链接，打开抖音！"), SHORT)
        self.assertEqual(extract_url("[视频](" + VIDEO + ")"), VIDEO)
        self.assertEqual(extract_url('  "' + VIDEO + '"  '), VIDEO)

    def test_homepage_plus_one_specific_share_link_selects_the_work(self):
        text = "[https://www.douyin.com/?recommend=1](https://www.douyin.com/?recommend=1)\n分享：" + SHORT
        self.assertEqual(extract_url(text), SHORT)

    def test_multiple_works_are_not_silently_chosen(self):
        with self.assertRaisesRegex(ValueError, "多个"):
            extract_url(VIDEO + " " + SHORT)
        with self.assertRaises(ValueError):
            extract_url("https://example.test/watch https://other.test/watch")

    def test_plain_urls_with_parentheses_are_preserved(self):
        url = "https://example.test/video(name).mp4?x=1&y=2"
        self.assertEqual(extract_url(url), url)

    def test_plain_non_url_input_has_actionable_error(self):
        with self.assertRaisesRegex(ValueError, "分享文案"):
            extract_url("示例分享内容")


class DouyinRouteTests(unittest.TestCase):
    def test_work_urls_are_normalized(self):
        self.assertEqual(douyin.video_url(VIDEO + "?previous_page=web_code_link"), VIDEO)
        self.assertEqual(douyin.video_url("https://www.douyin.com/?recommend=1&modal_id=1234567890123456789"), VIDEO)
        self.assertEqual(douyin.video_url("https://www.iesdouyin.com/share/video/1234567890123456789/"), VIDEO)

    def test_short_url_resolves_to_one_work(self):
        with patch("download_hls.fetch", return_value=(b"", VIDEO + "?previous_page=web_code_link")):
            self.assertEqual(douyin.video_url(SHORT), VIDEO)

    def test_homepage_is_rejected_before_network_requests(self):
        with patch("download_hls.fetch", side_effect=AssertionError("Unexpected network")):
            with self.assertRaisesRegex(ValueError, "首页"):
                douyin.video_url("https://www.douyin.com/?recommend=1")

    def test_domain_routing_stays_scoped(self):
        self.assertEqual(select_route(SHORT)["handler"], "douyin")
        self.assertEqual(select_route(VIDEO)["handler"], "douyin")
        self.assertEqual(select_route("https://www.douyin.com.evil.test/video/1")["handler"], "generic")
        self.assertEqual(select_route("https://www.youtube.com/watch?v=AbCdEfGhI12")["handler"], "youtube")

    def test_anonymous_browser_fallback_receives_canonical_work(self):
        with (
            patch("site_handlers.douyin_session.SessionStore.load", return_value=None),
            patch("site_handlers.yt_engine.run", side_effect=RuntimeError("Fresh cookies are needed")),
            patch("site_handlers.browser.run", return_value="downloaded") as fallback,
        ):
            result = douyin.run(VIDEO, Options(check=True))
        self.assertEqual(result, "downloaded")
        self.assertEqual(fallback.call_args.args[0], VIDEO)
        self.assertTrue(fallback.call_args.args[1].output.endswith("Douyin_1234567890123456789.mp4"))

    def test_anonymous_success_does_not_read_saved_login(self):
        with (
            patch("site_handlers.yt_engine.run", return_value="downloaded") as native,
            patch("site_handlers.douyin_session.SessionStore.load", side_effect=AssertionError("Login read")),
            patch("site_handlers.browser.run", side_effect=AssertionError("Browser launched")),
        ):
            self.assertEqual(douyin.run(VIDEO, Options(check=True)), "downloaded")
        self.assertNotIn("cookiefile", native.call_args.kwargs)


if __name__ == "__main__":
    unittest.main(verbosity=2)
