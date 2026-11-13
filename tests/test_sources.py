from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path

from app.config import Config
from app.db import Database
from app.sources.aggregate import TrackAggregator
from app.sources.meting import MetingSource
from app.sources.netease import NeteaseSource
from app.sources.qq import QQSource


FIXTURES = Path(__file__).parent / "fixtures"


def fixture(name: str):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class FakeHttp:
    def __init__(self, netease=None, qq=None, lyric=None, text=""):
        self.netease = netease
        self.qq = qq
        self.lyric = lyric
        self.text = text
        self.calls = []

    def post_form_json(self, url, form, headers=None, timeout=None):
        self.calls.append(("POST", url, dict(form), headers))
        if "music.163.com" in url:
            return copy.deepcopy(self.netease)
        return copy.deepcopy(self.qq)

    def get_json(self, url, headers=None, timeout=None):
        self.calls.append(("GET", url, None, headers))
        return copy.deepcopy(self.lyric)

    def get_text(self, url, headers=None, timeout=None):
        self.calls.append(("TEXT", url, None, headers))
        return self.text


class SourceParsingTests(unittest.TestCase):
    def test_netease_search_and_lyrics(self):
        http = FakeHttp(
            netease=fixture("netease_search.json"),
            lyric=fixture("netease_lyric.json"),
        )
        source = NeteaseSource(http, "http://music.163.com/api/search/get/web", "http://music.163.com/api/song/lyric")
        songs = source.search("iron lotus", limit=5)
        self.assertEqual(len(songs), 2)
        first = songs[0]
        self.assertEqual(first["source_id"], "1887467088")
        self.assertEqual(first["title"], "Iron Lotus")
        self.assertEqual(first["artist"], "Mili")
        self.assertEqual(first["album"], "To Kill a Living Book -for Library Of Ruina-")
        self.assertEqual(first["duration_ms"], 240092)
        self.assertEqual(first["cover_pic_id"], "109951166524388678")

        lrc, translation = source.lyrics("1887467088")
        self.assertIn("Frozen stairs", lrc)
        self.assertIn("冻结的阶梯", translation)

    def test_qq_search_and_refresh(self):
        http = FakeHttp(qq=fixture("qq_search.json"))
        source = QQSource(http, "https://music.txqq.pro/")
        songs = source.search("iron lotus", limit=5)
        self.assertEqual(len(songs), 1)
        first = songs[0]
        self.assertEqual(first["source_id"], "003Zsdd82WfgMY")
        self.assertEqual(first["title"], "Iron Lotus")
        self.assertEqual(first["artist"], "Mili")
        self.assertIn("aqqmusic.tc.qq.com", first["audio_url"])
        self.assertIn("gtimg.cn", first["cover_url"])
        self.assertIn("Frozen stairs", first["lyrics_lrc"])

        refreshed = source.refresh_audio("iron lotus", "003Zsdd82WfgMY")
        self.assertIn("vkey=test", refreshed)

    def test_meting_urls_and_lyrics(self):
        http = FakeHttp(text="[00:00.00]hello")
        source = MetingSource(http, "https://api.qijieya.cn/meting/")
        self.assertIn("type=url", source.audio_url(123))
        self.assertIn("id=123", source.audio_url(123))
        self.assertIn("type=pic", source.cover_url(456))
        self.assertEqual(source.lyrics(123), "[00:00.00]hello")


class FakeNetease:
    def __init__(self):
        self.items = fixture("netease_search.json")["result"]["songs"]
        self.qq_match = {
            "id": 999,
            "name": "QQ Song",
            "duration": 123000,
            "artists": [{"name": "Artist"}],
            "album": {"name": "QQ Album", "picId": 555},
        }
        self.lyric = fixture("netease_lyric.json")

    def search(self, keyword, limit=5):
        lowered = keyword.casefold()
        if "iron" in lowered:
            return [
                {
                    "source": "netease",
                    "source_id": str(item["id"]),
                    "title": item["name"],
                    "artist": "、".join(a["name"] for a in item["artists"]),
                    "album": item["album"]["name"],
                    "duration_ms": item["duration"],
                    "cover_pic_id": str(item["album"]["picId"]),
                    "raw": item,
                }
                for item in self.items[:limit]
            ]
        if "qq song" in lowered and "artist" in lowered:
            item = self.qq_match
            return [
                {
                    "source": "netease",
                    "source_id": str(item["id"]),
                    "title": item["name"],
                    "artist": "、".join(a["name"] for a in item["artists"]),
                    "album": item["album"]["name"],
                    "duration_ms": item["duration"],
                    "cover_pic_id": str(item["album"]["picId"]),
                    "raw": item,
                }
            ]
        return []

    def lyrics(self, song_id):
        if str(song_id) == "999":
            return "", ""
        return (
            self.lyric["lrc"]["lyric"],
            self.lyric["tlyric"]["lyric"],
        )


class FakeQQ:
    def __init__(self):
        self.url = "https://aqqmusic.tc.qq.com/C400003Zsdd82WfgMY.m4a?vkey=old"

    def search(self, keyword, limit=5):
        lowered = keyword.casefold()
        if "iron" in lowered or "uniqueqq" in lowered or "qq song" in lowered:
            return [
                {
                    "source": "qq",
                    "source_id": "003Zsdd82WfgMY",
                    "title": "Iron Lotus" if "iron" in lowered else "QQ Song",
                    "artist": "Mili" if "iron" in lowered else "Artist",
                    "album": "",
                    "duration_ms": None,
                    "audio_url": self.url,
                    "cover_url": "https://y.gtimg.cn/cover.jpg",
                    "lyrics_lrc": "[00:00.00]qq lyric",
                    "raw": {},
                }
            ]
        return []

    def refresh_audio(self, keyword, song_id):
        return f"{self.url}&fresh=1"


class FakeMeting:
    def audio_url(self, song_id):
        return f"meting-audio:{song_id}"

    def cover_url(self, pic_id):
        return f"meting-cover:{pic_id}"

    def lyrics(self, song_id):
        return "[00:00.00]meting fallback"


class FakeExtraSource:
    name = "extra"
    display_name = "额外源"

    def search(self, keyword, limit=5):
        if "iron" in keyword.casefold():
            return [
                {
                    "source": "extra",
                    "source_id": "extra-1",
                    "title": "Iron Lotus (Extra)",
                    "artist": "Mili",
                    "album": "Extra Album",
                    "duration_ms": 200_000,
                    "cover_url": "https://extra.invalid/cover.jpg",
                    "audio_url": "https://extra.invalid/audio.mp3",
                    "lyrics_lrc": "[00:00.00]extra lyric",
                }
            ]
        return []


class AggregatorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.tmp.name) / "test.db")
        self.config = Config()
        self.aggregator = TrackAggregator(
            self.db,
            self.config,
            FakeNetease(),
            FakeQQ(),
            FakeMeting(),
        )

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def test_merges_all_fields(self):
        track = self.aggregator.resolve("Iron Lotus Mili")
        self.assertIsNotNone(track)
        assert track is not None
        self.assertEqual(track["title"], "Iron Lotus")
        self.assertEqual(track["artist"], "Mili")
        self.assertEqual(track["album"], "To Kill a Living Book -for Library Of Ruina-")
        self.assertEqual(track["duration_ms"], 240092)
        self.assertFalse(track["duration_estimated"])
        self.assertEqual(track["cover_url"], "meting-cover:109951166524388678")
        self.assertEqual(track["audio_primary"], "meting-audio:1887467088")
        self.assertTrue(any("aqqmusic" in url for url in track["audio_alternates"]))
        self.assertIn("Frozen stairs", track["lyrics_lrc"])
        self.assertIn("冻结的阶梯", track["lyrics_translation"])

    def test_resolve_is_idempotent(self):
        first = self.aggregator.resolve("Iron Lotus Mili")
        second = self.aggregator.resolve("Iron Lotus Mili")
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(len(self.db.list_playlist()), 1)

    def test_qq_only_track_gets_netease_duration(self):
        track = self.aggregator.resolve("QQ Song")
        self.assertIsNotNone(track)
        assert track is not None
        self.assertEqual(track["title"], "QQ Song")
        self.assertEqual(track["duration_ms"], 123000)
        self.assertFalse(track["duration_estimated"])
        self.assertEqual(track["netease_id"], "999")
        self.assertEqual(track["qq_id"], "003Zsdd82WfgMY")

    def test_refresh_audio_updates_alternate(self):
        track = self.aggregator.resolve("Iron Lotus Mili")
        refreshed = self.aggregator.refresh_audio(track, force=True)
        self.assertEqual(refreshed["audio_primary"], "meting-audio:1887467088")
        self.assertTrue(any("fresh=1" in url for url in refreshed["audio_alternates"]))

    def test_no_match(self):
        self.assertIsNone(self.aggregator.resolve("completely unknown song xyz"))

    def test_search_candidates_and_candidate_resolution(self):
        results = self.aggregator.search("Iron Lotus")
        self.assertTrue(results)
        self.assertTrue(all(len(item["candidate_id"]) == 32 for item in results))
        self.assertTrue(any(item["source"] == "netease" for item in results))

        candidate = results[0]
        self.assertIsNotNone(self.aggregator.get_candidate(candidate["candidate_id"]))
        track = self.aggregator.resolve_candidate(candidate["candidate_id"])
        self.assertIsNotNone(track)
        assert track is not None
        self.assertEqual(track["title"], candidate["title"])

    def test_search_source_registry_is_extensible(self):
        self.aggregator.register_source(FakeExtraSource())
        results = self.aggregator.search("Iron Lotus")
        self.assertTrue(any(item["source"] == "extra" for item in results))
        extra = next(item for item in results if item["source"] == "extra")
        track = self.aggregator.resolve_candidate(extra["candidate_id"])
        self.assertIsNotNone(track)
        assert track is not None
        self.assertEqual(track["audio_primary"], "https://extra.invalid/audio.mp3")


if __name__ == "__main__":
    unittest.main()
