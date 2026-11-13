from __future__ import annotations

import logging
import urllib.parse
from typing import Any

from ..upstream import HttpClient, UpstreamError


logger = logging.getLogger(__name__)


def _as_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value)


class NeteaseSource:
    name = "netease"
    display_name = "网易云音乐"

    def __init__(self, http: HttpClient, search_url: str, lyric_url: str) -> None:
        self.http = http
        self.search_url = search_url
        self.lyric_url = lyric_url

    def search(self, keyword: str, limit: int = 5) -> list[dict[str, Any]]:
        form = {"s": keyword, "limit": str(limit), "type": "1", "offset": "0"}
        headers = {
            "Cookie": "appver=2.0.2",
            "Referer": "https://music.163.com/",
            "Origin": "https://music.163.com",
        }
        try:
            data = self.http.post_form_json(self.search_url, form, headers=headers)
        except UpstreamError as exc:
            logger.warning("netease search failed: %s", exc)
            return []

        songs = []
        result = data.get("result") if isinstance(data, dict) else None
        raw_songs = result.get("songs") if isinstance(result, dict) else None
        if not isinstance(raw_songs, list):
            return []
        for item in raw_songs[:limit]:
            if not isinstance(item, dict):
                continue
            song_id = item.get("id")
            if song_id is None:
                continue
            album = item.get("album") if isinstance(item.get("album"), dict) else {}
            artists = item.get("artists") if isinstance(item.get("artists"), list) else []
            artist = "、".join(
                _as_text(entry.get("name"))
                for entry in artists
                if isinstance(entry, dict) and entry.get("name")
            )
            pic_id = album.get("picId") if isinstance(album, dict) else None
            songs.append(
                {
                    "source": self.name,
                    "source_id": str(song_id),
                    "title": _as_text(item.get("name")),
                    "artist": artist,
                    "album": _as_text(album.get("name")),
                    "duration_ms": int(item["duration"]) if item.get("duration") else None,
                    "cover_pic_id": str(pic_id) if pic_id else None,
                    "raw": item,
                }
            )
        return songs

    def lyrics(self, song_id: str | int) -> tuple[str, str]:
        query = urllib.parse.urlencode(
            {"id": str(song_id), "lv": "1", "kv": "1", "tv": "-1"}
        )
        url = f"{self.lyric_url}?{query}"
        try:
            data = self.http.get_json(url, headers={"Cookie": "appver=2.0.2"})
        except UpstreamError as exc:
            logger.warning("netease lyric failed: %s", exc)
            return "", ""
        if not isinstance(data, dict):
            return "", ""
        lrc = data.get("lrc") if isinstance(data.get("lrc"), dict) else {}
        tlyric = data.get("tlyric") if isinstance(data.get("tlyric"), dict) else {}
        return _as_text(lrc.get("lyric")).strip(), _as_text(tlyric.get("lyric")).strip()
