from __future__ import annotations

import http.cookiejar
import json
import logging
import re
import threading
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from ..config import Config


logger = logging.getLogger(__name__)

_ALLOWED_HOSTS = {
    "bilibili.com",
    "www.bilibili.com",
    "m.bilibili.com",
    "b23.tv",
    "www.b23.tv",
}
_FETCH_HOSTS = _ALLOWED_HOSTS | {"api.bilibili.com"}
_VIDEO_PATH = re.compile(r"^/(?:video|s/video)/(BV[0-9A-Za-z]+|av\d+)", re.IGNORECASE)
_URL_LIKE = re.compile(r"^https?://", re.IGNORECASE)
_BARE_BV = re.compile(r"^[Bb][Vv][0-9A-Za-z]{10}$")
_BARE_AV = re.compile(r"^av\d+$", re.IGNORECASE)
_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


class BilibiliError(RuntimeError):
    pass


def normalize_bilibili_url(value: str) -> str:
    raw = str(value or "").strip()
    if _BARE_BV.fullmatch(raw):
        return f"https://www.bilibili.com/video/BV{raw[2:]}"
    if _BARE_AV.fullmatch(raw):
        return f"https://www.bilibili.com/video/{raw.lower()}"
    if not raw or not _URL_LIKE.match(raw):
        return ""
    try:
        parts = urllib.parse.urlsplit(raw)
    except ValueError:
        return ""
    if parts.scheme.lower() not in {"http", "https"}:
        return ""
    if parts.username or parts.password:
        return ""
    host = (parts.hostname or "").lower().rstrip(".")
    if host not in _ALLOWED_HOSTS:
        return ""
    try:
        port = parts.port
    except ValueError:
        return ""
    if port not in (None, 80, 443):
        return ""
    path = parts.path or "/"
    if host not in {"b23.tv", "www.b23.tv"} and not _VIDEO_PATH.match(path):
        return ""
    return urllib.parse.urlunsplit(("https", host, path, parts.query, ""))


def is_bilibili_url(value: str) -> bool:
    return bool(normalize_bilibili_url(value))


class BilibiliSource:
    """Resolve a public Bilibili video page to its DASH audio URL.

    This intentionally uses Bilibili's public web API only.  It does not use
    account cookies, DRM bypasses, paid/member-only streams or a browser
    automation dependency.
    """

    name = "bilibili"
    display_name = "哔哩哔哩"

    def __init__(self, config: Config) -> None:
        self.config = config
        self._lock = threading.Lock()
        self._cookies = http.cookiejar.CookieJar()
        self._opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self._cookies)
        )
        self._bootstrapped = False

    def available(self) -> bool:
        return True

    @staticmethod
    def _headers(referer: str = "https://www.bilibili.com/") -> dict[str, str]:
        return {
            "User-Agent": _UA,
            "Referer": referer,
            "Origin": "https://www.bilibili.com",
            "Accept": "application/json, text/plain, */*",
        }

    def _bootstrap(self) -> None:
        with self._lock:
            if self._bootstrapped:
                return
            self._bootstrapped = True
        try:
            request = urllib.request.Request(
                "https://www.bilibili.com/",
                headers={"User-Agent": _UA, "Accept": "text/html"},
            )
            with self._opener.open(request, timeout=10) as response:
                response.read(4096)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            logger.debug("bilibili cookie bootstrap failed: %s", exc)

    def _open(self, url: str, *, referer: str, timeout: float = 15.0) -> Any:
        request = urllib.request.Request(url, headers=self._headers(referer))
        response = self._opener.open(request, timeout=timeout)
        final = urllib.parse.urlsplit(response.geturl())
        host = (final.hostname or "").lower().rstrip(".")
        if host not in _FETCH_HOSTS:
            response.close()
            raise BilibiliError("哔哩哔哩跳转到了不允许的地址")
        return response

    def _follow_short_url(self, url: str) -> str:
        try:
            with self._open(url, referer="https://www.bilibili.com/") as response:
                final = response.geturl()
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise BilibiliError("短链接解析失败") from exc
        normalized = normalize_bilibili_url(final)
        if not normalized:
            raise BilibiliError("短链接没有指向哔哩哔哩视频")
        return normalized

    def _get_json(self, url: str, referer: str) -> dict[str, Any]:
        try:
            with self._open(url, referer=referer) as response:
                payload = json.loads(response.read().decode("utf-8", "replace"))
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
            raise BilibiliError("哔哩哔哩 API 请求失败") from exc
        if not isinstance(payload, dict):
            raise BilibiliError("哔哩哔哩返回了无法识别的数据")
        if int(payload.get("code") or 0) != 0:
            message = str(payload.get("message") or payload.get("message") or "视频不可用")
            raise BilibiliError(message[:160])
        data = payload.get("data")
        return data if isinstance(data, dict) else {}

    def _video_id(self, page_url: str) -> tuple[str, str, int]:
        parts = urllib.parse.urlsplit(page_url)
        match = _VIDEO_PATH.match(parts.path or "")
        if not match:
            raise BilibiliError("不支持的哔哩哔哩链接")
        value = match.group(1)
        query = urllib.parse.parse_qs(parts.query)
        try:
            page = max(1, int((query.get("p") or ["1"])[0]))
        except (TypeError, ValueError):
            page = 1
        if value.lower().startswith("bv"):
            return "bvid", value, page
        return "aid", value[2:], page

    def resolve_url(self, value: str) -> dict[str, Any]:
        page_url = normalize_bilibili_url(value)
        if not page_url:
            raise BilibiliError("只支持哔哩哔哩公开视频链接")
        if urllib.parse.urlsplit(page_url).hostname in {"b23.tv", "www.b23.tv"}:
            page_url = self._follow_short_url(page_url)
        self._bootstrap()
        key, video_id, page = self._video_id(page_url)
        view_url = "https://api.bilibili.com/x/web-interface/view?" + urllib.parse.urlencode({key: video_id})
        referer = f"https://www.bilibili.com/video/{video_id}"
        view = self._get_json(view_url, referer)
        pages = view.get("pages") if isinstance(view.get("pages"), list) else []
        if not pages:
            raise BilibiliError("视频没有可播放的分 P")
        selected = pages[min(max(page - 1, 0), len(pages) - 1)]
        cid = selected.get("cid")
        if not cid:
            raise BilibiliError("视频缺少 cid")
        play_url = "https://api.bilibili.com/x/player/playurl?" + urllib.parse.urlencode(
            {
                "bvid": str(view.get("bvid") or ""),
                "cid": str(cid),
                "fnval": "16",
                "fnver": "0",
                "fourk": "1",
            }
        )
        play = self._get_json(play_url, referer)
        dash = play.get("dash") if isinstance(play.get("dash"), dict) else {}
        audio_items = dash.get("audio") if isinstance(dash.get("audio"), list) else []
        audio_url = ""
        best_bandwidth = -1
        for item in audio_items:
            if not isinstance(item, dict):
                continue
            candidate = str(item.get("baseUrl") or item.get("base_url") or "")
            if not candidate.startswith(("http://", "https://")):
                continue
            try:
                bandwidth = int(item.get("bandwidth") or 0)
            except (TypeError, ValueError):
                bandwidth = 0
            if bandwidth >= best_bandwidth:
                best_bandwidth = bandwidth
                audio_url = candidate
        if not audio_url:
            raise BilibiliError("这个公开视频没有可用的纯音频流")
        duration = dash.get("duration") or selected.get("duration") or view.get("duration")
        try:
            duration_ms = int(float(duration) * 1000)
        except (TypeError, ValueError):
            duration_ms = 0
        base_title = str(view.get("title") or "哔哩哔哩视频").strip()
        part_title = str(selected.get("part") or "").strip()
        title = (
            f"{base_title} - {part_title}"
            if len(pages) > 1 and part_title and part_title != base_title
            else base_title
        )
        owner = view.get("owner") if isinstance(view.get("owner"), dict) else {}
        artist = str(owner.get("name") or "哔哩哔哩").strip()
        thumbnail = str(view.get("pic") or selected.get("first_frame") or "").strip()
        if thumbnail.startswith("http://"):
            thumbnail = "https://" + thumbnail[7:]
        bvid = str(view.get("bvid") or "")
        aid = view.get("aid")
        source_id = bvid or (str(aid) if aid else video_id)
        canonical = (
            f"https://www.bilibili.com/video/{bvid}/"
            if bvid
            else f"https://www.bilibili.com/video/av{aid}/"
        )
        return {
            "source": self.name,
            "source_id": source_id,
            "title": title[:160],
            "artist": artist[:80],
            "album": "哔哩哔哩",
            "duration_ms": duration_ms or self.config.default_duration_ms,
            "duration_estimated": not bool(duration_ms),
            "cover_url": thumbnail,
            "audio_url": audio_url,
            "lyrics_lrc": "",
            "webpage_url": canonical,
        }
