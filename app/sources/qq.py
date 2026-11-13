from __future__ import annotations

import logging
from typing import Any

from ..upstream import HttpClient, UpstreamError


logger = logging.getLogger(__name__)


def _as_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value)


def _https(url: str) -> str:
    if url.startswith("http://"):
        return "https://" + url[len("http://") :]
    return url


class QQSource:
    name = "qq"
    display_name = "QQ音乐"

    def __init__(self, http: HttpClient, search_url: str) -> None:
        self.http = http
        self.search_url = search_url

    def search(self, keyword: str, limit: int = 5) -> list[dict[str, Any]]:
        form = {"input": keyword, "filter": "name", "type": "qq", "page": "1"}
        headers = {
            "X-Requested-With": "XMLHttpRequest",
            "Origin": "https://music.txqq.pro",
            "Referer": "https://music.txqq.pro/",
        }
        try:
            data = self.http.post_form_json(self.search_url, form, headers=headers)
        except UpstreamError as exc:
            logger.warning("qq search failed: %s", exc)
            return []

        items = data.get("data") if isinstance(data, dict) else None
        if not isinstance(items, list):
            return []
        results = []
        for item in items[:limit]:
            if not isinstance(item, dict):
                continue
            song_id = item.get("songid")
            if not song_id:
                continue
            results.append(
                {
                    "source": self.name,
                    "source_id": str(song_id),
                    "title": _as_text(item.get("title")),
                    "artist": _as_text(item.get("author")),
                    "album": "",
                    "duration_ms": None,
                    "audio_url": _https(_as_text(item.get("url"))) or None,
                    "cover_url": _https(_as_text(item.get("pic"))) or None,
                    "lyrics_lrc": item.get("lrc") if isinstance(item.get("lrc"), str) else "",
                    "link": _as_text(item.get("link")) or None,
                    "raw": item,
                }
            )
        return results

    def refresh_audio(self, keyword: str, song_id: str) -> str | None:
        for item in self.search(keyword, limit=10):
            if str(item.get("source_id")) == str(song_id):
                return item.get("audio_url")
        return None
