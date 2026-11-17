import unittest

from app.sources.bilibili import BilibiliSource, normalize_bilibili_url


class BilibiliUrlTests(unittest.TestCase):
    def test_public_video_urls(self):
        self.assertEqual(
            normalize_bilibili_url("https://www.bilibili.com/video/BV1GJ411x7h7?p=2"),
            "https://www.bilibili.com/video/BV1GJ411x7h7?p=2",
        )
        self.assertEqual(
            normalize_bilibili_url("http://bilibili.com/video/av80433022"),
            "https://bilibili.com/video/av80433022",
        )
        self.assertTrue(
            normalize_bilibili_url("https://b23.tv/abc123").startswith("https://b23.tv/")
        )
        self.assertEqual(
            normalize_bilibili_url("BV12w426rEcW"),
            "https://www.bilibili.com/video/BV12w426rEcW",
        )
        self.assertEqual(
            normalize_bilibili_url("bv12w426rEcW"),
            "https://www.bilibili.com/video/BV12w426rEcW",
        )
        self.assertEqual(
            normalize_bilibili_url("av80433022"),
            "https://www.bilibili.com/video/av80433022",
        )

    def test_rejects_non_bilibili_and_private_urls(self):
        for value in (
            "https://example.com/video/BV1GJ411x7h7",
            "ftp://www.bilibili.com/video/BV1GJ411x7h7",
            "https://user:pass@www.bilibili.com/video/BV1GJ411x7h7",
            "http://127.0.0.1/video/BV1GJ411x7h7",
            "https://www.bilibili.com.evil.example/video/BV1GJ411x7h7",
            "https://www.bilibili.com/space/123",
            "",
        ):
            self.assertEqual(normalize_bilibili_url(value), "", value)



if __name__ == "__main__":
    unittest.main()
