from __future__ import annotations

import urllib.parse

from ..upstream import HttpClient, UpstreamError


class MetingSource:
    def __init__(self, http: HttpClient, base_url: str) -> None:
        self.http = http
        self.base_url = base_url

    def _url(self, kind: str, item_id: str) -> str:
        query = urllib.parse.urlencode(
            {"server": "netease", "type": kind, "id": str(item_id)}
        )
        return f"{self.base_url}?{query}"

    def audio_url(self, song_id: str | int) -> str:
        return self._url("url", str(song_id))

    def cover_url(self, pic_id: str | int) -> str:
        return self._url("pic", str(pic_id))

    def lyrics_url(self, song_id: str | int) -> str:
        return self._url("lrc", str(song_id))

    def lyrics(self, song_id: str | int) -> str:
        try:
            return self.http.get_text(self.lyrics_url(song_id)).strip()
        except UpstreamError:
            return ""
