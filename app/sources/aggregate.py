from __future__ import annotations

import difflib
import logging
import re
import sqlite3
import threading
import time
import unicodedata
import urllib.parse
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from ..config import Config
from ..db import Database, now_ms
from .meting import MetingSource
from .netease import NeteaseSource
from .qq import QQSource


logger = logging.getLogger(__name__)

_PUNCT = re.compile(r"[\s\-_/·,，.。!！?？'\"“”‘’()（）\[\]【】《》]+")


def _norm(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return _PUNCT.sub("", text)


def _ratio(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    return difflib.SequenceMatcher(None, a, b).ratio()


def _url_key(url: str) -> str:
    try:
        parts = urllib.parse.urlsplit(str(url or "").strip())
    except ValueError:
        return ""
    return f"{parts.netloc}{parts.path}"


def _safe_url(url: Any) -> str:
    value = str(url or "").strip()
    if not value:
        return ""
    try:
        parts = urllib.parse.urlsplit(value)
    except ValueError:
        return ""
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        return ""
    return value


class TrackAggregator:
    def __init__(
        self,
        db: Database,
        config: Config,
        netease: NeteaseSource,
        qq: QQSource,
        meting: MetingSource,
    ) -> None:
        self.db = db
        self.config = config
        self.netease = netease
        self.qq = qq
        self.meting = meting
        self.search_sources = [netease, qq]
        self._candidate_lock = threading.Lock()
        self._candidates: dict[str, tuple[float, dict[str, Any]]] = {}
        self._executor = ThreadPoolExecutor(
            max_workers=max(2, len(self.search_sources) + 1),
            thread_name_prefix="radio-search",
        )
        self._search_slots = threading.BoundedSemaphore(
            max(4, len(self.search_sources) * 3)
        )

    def register_source(self, source: Any) -> None:
        if source not in self.search_sources:
            self.search_sources.append(source)

    def shutdown(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)

    def _source_name(self, source: Any) -> str:
        return str(getattr(source, "name", source.__class__.__name__))

    def _search_source(self, source: Any, query: str) -> list[dict[str, Any]]:
        name = self._source_name(source)
        try:
            items = source.search(query, self.config.search_limit)
        except Exception as exc:
            logger.warning("%s search error: %s", name, exc)
            return []
        normalized: list[dict[str, Any]] = []
        for item in items or []:
            if not isinstance(item, dict) or not item.get("source_id"):
                continue
            entry = dict(item)
            entry.setdefault("source", name)
            entry["source_label"] = getattr(source, "display_name", name)
            if not entry.get("cover_url") and entry.get("cover_pic_id"):
                entry["cover_url"] = self.meting.cover_url(entry["cover_pic_id"])
            normalized.append(entry)
        return normalized

    def _run_source(self, source: Any, query: str) -> list[dict[str, Any]]:
        return self._search_source(source, query)

    def _search_all(self, query: str) -> dict[str, list[dict[str, Any]]]:
        sources = list(self.search_sources)
        if not sources:
            return {}
        results: dict[str, list[dict[str, Any]]] = {}
        futures = {}
        for source in sources:
            name = self._source_name(source)
            if not self._search_slots.acquire(blocking=False):
                logger.warning("%s search skipped: queue full", name)
                results[name] = []
                continue
            try:
                future = self._executor.submit(self._run_source, source, query)
                future.add_done_callback(
                    lambda _future: self._search_slots.release()
                )
                futures[name] = future
            except Exception:
                self._search_slots.release()
                raise
        deadline = time.monotonic() + max(1.0, self.config.upstream_timeout + 1.0)
        for name, future in futures.items():
            remaining = max(0.05, deadline - time.monotonic())
            try:
                results[name] = future.result(timeout=remaining)
            except Exception as exc:
                logger.warning("%s search timeout: %s", name, exc)
                results[name] = []
        return results

    def _score_query(self, query: str, item: dict[str, Any]) -> float:
        q = _norm(query)
        title = _norm(item.get("title"))
        artist = _norm(item.get("artist"))
        if not q or not title:
            return 0.0
        if title == q:
            title_score = 100.0
        elif title in q or q in title:
            title_score = 78.0
        else:
            title_score = 65.0 * _ratio(title, q)
        if artist and (artist in q or q in artist):
            artist_score = 22.0
        else:
            artist_score = 10.0 * _ratio(artist, q)
        return title_score + artist_score

    def _score_pair(self, base: dict[str, Any], item: dict[str, Any]) -> float:
        title_score = _ratio(_norm(base.get("title")), _norm(item.get("title")))
        artist_score = _ratio(_norm(base.get("artist")), _norm(item.get("artist")))
        return title_score * 82.0 + artist_score * 18.0

    def _best(self, query: str, items: list[dict[str, Any]]) -> dict[str, Any] | None:
        if not items:
            return None
        scored = sorted(
            ((self._score_query(query, item), item) for item in items),
            key=lambda pair: pair[0],
            reverse=True,
        )
        score, best = scored[0]
        if score < 32.0:
            return None
        return best

    def _best_pair(
        self, base: dict[str, Any], items: list[dict[str, Any]]
    ) -> dict[str, Any] | None:
        if not items:
            return None
        scored = sorted(
            ((self._score_pair(base, item), item) for item in items),
            key=lambda pair: pair[0],
            reverse=True,
        )
        score, best = scored[0]
        if score < 52.0:
            return None
        return best

    def _cleanup_candidates(self, now: float) -> None:
        expired = [key for key, (expires, _) in self._candidates.items() if expires <= now]
        for key in expired:
            self._candidates.pop(key, None)

    def search(self, query: str, limit: int | None = None) -> list[dict[str, Any]]:
        query = " ".join(str(query or "").split())
        if not query:
            return []
        limit = max(1, min(30, limit or self.config.search_results_limit))
        by_source = self._search_all(query)
        scored: list[tuple[float, dict[str, Any]]] = []
        for source in self.search_sources:
            name = self._source_name(source)
            for item in by_source.get(name, []):
                score = self._score_query(query, item)
                if score >= 20.0:
                    scored.append((score, item))
        scored.sort(key=lambda pair: pair[0], reverse=True)
        unique: list[tuple[float, dict[str, Any]]] = []
        seen: set[tuple[str, str, str]] = set()
        for score, item in scored:
            key = (
                str(item.get("source") or ""),
                _norm(item.get("title")),
                _norm(item.get("artist")),
            )
            if key in seen:
                continue
            seen.add(key)
            unique.append((score, item))
        now = time.time()
        ttl = max(60, self.config.search_cache_ttl_sec)
        results: list[dict[str, Any]] = []
        with self._candidate_lock:
            self._cleanup_candidates(now)
            if len(self._candidates) > 800:
                self._candidates.clear()
            for _score, item in unique[:limit]:
                candidate_id = uuid.uuid4().hex
                self._candidates[candidate_id] = (now + ttl, dict(item))
                duration_ms = item.get("duration_ms")
                results.append(
                    {
                        "candidate_id": candidate_id,
                        "source": item.get("source"),
                        "source_label": item.get("source_label"),
                        "title": item.get("title") or "",
                        "artist": item.get("artist") or "",
                        "album": item.get("album") or "",
                        "duration_ms": int(duration_ms) if duration_ms else None,
                        "duration_estimated": not bool(duration_ms),
                        "cover_url": _safe_url(item.get("cover_url")),
                    }
                )
        return results

    def get_candidate(self, candidate_id: str) -> dict[str, Any] | None:
        now = time.time()
        with self._candidate_lock:
            self._cleanup_candidates(now)
            entry = self._candidates.get(candidate_id)
            if entry is None:
                return None
            expires, item = entry
            if expires <= now:
                self._candidates.pop(candidate_id, None)
                return None
            return dict(item)

    def resolve(self, query: str) -> dict[str, Any] | None:
        query = " ".join(str(query or "").split())
        if not query:
            return None
        by_source = self._search_all(query)
        netease_items = by_source.get(self._source_name(self.netease), [])
        qq_items = by_source.get(self._source_name(self.qq), [])
        netease_best = self._best(query, netease_items)
        qq_best = self._best(query, qq_items)

        if netease_best is None and qq_best is not None:
            extra = self._search_source(
                self.netease,
                f"{qq_best.get('title', '')} {qq_best.get('artist', '')}".strip(),
            )
            netease_best = self._best_pair(qq_best, extra)

        if qq_best is None and netease_best is not None:
            extra = self._search_source(
                self.qq,
                f"{netease_best.get('title', '')} {netease_best.get('artist', '')}".strip(),
            )
            qq_best = self._best_pair(netease_best, extra)

        return self._build_track(netease_best, qq_best, query)

    def resolve_candidate(self, candidate_id: str) -> dict[str, Any] | None:
        candidate = self.get_candidate(candidate_id)
        if candidate is None:
            return None
        query = f"{candidate.get('title', '')} {candidate.get('artist', '')}".strip()
        source = candidate.get("source")
        netease_best = None
        qq_best = None
        fallback = None

        if source == "netease":
            netease_best = candidate
            qq_best = self._best_pair(
                candidate, self._search_source(self.qq, query)
            )
        elif source == "qq":
            qq_best = candidate
            netease_best = self._best_pair(
                candidate, self._search_source(self.netease, query)
            )
        else:
            fallback = candidate
            netease_best = self._best_pair(
                candidate, self._search_source(self.netease, query)
            )
            qq_best = self._best_pair(
                candidate, self._search_source(self.qq, query)
            )

        return self._build_track(
            netease_best,
            qq_best,
            query,
            fallback=fallback,
            prefer_source=source,
        )

    def _build_track(
        self,
        netease_best: dict[str, Any] | None,
        qq_best: dict[str, Any] | None,
        query: str,
        *,
        fallback: dict[str, Any] | None = None,
        prefer_source: str | None = None,
    ) -> dict[str, Any] | None:
        metadata = netease_best or fallback or qq_best
        if metadata is None:
            return None

        netease_id = str(netease_best.get("source_id")) if netease_best else None
        qq_id = str(qq_best.get("source_id")) if qq_best else None
        preferred = prefer_source or ("netease" if netease_best else metadata.get("source"))
        if preferred == "netease" and netease_id:
            source = "netease"
            source_id = netease_id
        elif preferred == "qq" and qq_id:
            source = "qq"
            source_id = qq_id
        else:
            source = str(metadata.get("source") or "netease")
            source_id = str(metadata.get("source_id") or "")

        title = metadata.get("title") or (qq_best or {}).get("title") or ""
        artist = metadata.get("artist") or (qq_best or {}).get("artist") or ""
        album = metadata.get("album") or (netease_best or {}).get("album") or ""
        duration_ms = (
            (netease_best or {}).get("duration_ms")
            or (fallback or {}).get("duration_ms")
            or (qq_best or {}).get("duration_ms")
        )

        cover_url = ""
        if netease_best and netease_best.get("cover_pic_id"):
            cover_url = self.meting.cover_url(netease_best["cover_pic_id"])
        elif metadata.get("cover_url"):
            cover_url = metadata["cover_url"]
        elif qq_best and qq_best.get("cover_url"):
            cover_url = qq_best["cover_url"]

        lyrics_lrc = ""
        lyrics_translation = ""
        if netease_id:
            lyrics_lrc, lyrics_translation = self.netease.lyrics(netease_id)
            if not lyrics_lrc:
                lyrics_lrc = self.meting.lyrics(netease_id)
        if not lyrics_lrc:
            lyrics_lrc = str(
                metadata.get("lyrics_lrc")
                or (fallback or {}).get("lyrics_lrc")
                or (qq_best or {}).get("lyrics_lrc")
                or ""
            ).strip()
        lyrics_at = now_ms() if lyrics_lrc else None

        qq_url = (qq_best or {}).get("audio_url")
        fallback_audio = (fallback or {}).get("audio_url")
        meting_url = self.meting.audio_url(netease_id) if netease_id else None
        if preferred == "qq" and qq_url:
            primary = qq_url
        elif preferred not in (None, "netease") and fallback_audio:
            primary = fallback_audio
        else:
            primary = meting_url or fallback_audio or qq_url or ""

        alternates: list[str] = []
        seen_urls: set[str] = set()
        for candidate in (qq_url, meting_url):
            candidate = _safe_url(candidate)
            if not candidate or candidate == primary:
                continue
            key = _url_key(candidate)
            if not key or key in seen_urls:
                continue
            seen_urls.add(key)
            alternates.append(candidate)

        primary = _safe_url(primary)
        alternates = [url for url in alternates if _safe_url(url) and url != primary]
        cover_url = _safe_url(cover_url)

        if duration_ms is None:
            duration_ms = self.config.default_duration_ms
            duration_estimated = True
        else:
            duration_estimated = False

        track: dict[str, Any] = {
            "source": source,
            "source_id": source_id,
            "netease_id": netease_id,
            "qq_id": qq_id,
            "title": title,
            "artist": artist,
            "album": album,
            "duration_ms": int(duration_ms),
            "duration_estimated": duration_estimated,
            "cover_url": cover_url,
            "lyrics_lrc": lyrics_lrc,
            "lyrics_translation": lyrics_translation,
            "lyrics_fetched_at": lyrics_at,
            "audio_primary": primary,
            "audio_alternates": alternates,
            "audio_resolved_at": now_ms() if primary else None,
            "meta_json": {
                "query": query,
                "netease_id": netease_id,
                "qq_id": qq_id,
                "qq_audio_url": qq_url,
            },
            "enabled": True,
            "created_at": now_ms(),
        }

        existing = self.db.find_track_by_ids(netease_id, qq_id)
        if existing is None and source_id:
            existing = self.db.find_track_by_source(source, source_id)
        if existing is not None:
            updates: dict[str, Any] = {}
            for key in ("netease_id", "qq_id"):
                if track.get(key) and not existing.get(key):
                    updates[key] = track[key]
            for key in ("cover_url", "lyrics_lrc", "lyrics_translation", "audio_primary"):
                if track.get(key) and not existing.get(key):
                    updates[key] = track[key]
            if track["audio_alternates"]:
                merged = list(existing.get("audio_alternates") or [])
                for url in track["audio_alternates"]:
                    if url not in merged:
                        merged.append(url)
                updates["audio_alternates"] = merged
            if not existing.get("duration_ms") and track.get("duration_ms"):
                updates["duration_ms"] = track["duration_ms"]
                updates["duration_estimated"] = track["duration_estimated"]
            meta = dict(existing.get("meta_json") or {})
            meta.update({k: v for k, v in track["meta_json"].items() if v})
            updates["meta_json"] = meta
            if updates:
                self.db.update_track(existing["id"], updates)
            return self.db.get_track(existing["id"])

        try:
            track_id = self.db.insert_track(track)
        except sqlite3.IntegrityError:
            existing = self.db.find_track_by_ids(netease_id, qq_id)
            if existing is None and source_id:
                existing = self.db.find_track_by_source(source, source_id)
            return existing
        return self.db.get_track(track_id)

    def _qq_track_url(self, track: dict[str, Any]) -> str | None:
        qq_id = track.get("qq_id")
        if not qq_id:
            return None
        keyword = f"{track.get('title', '')} {track.get('artist', '')}".strip()
        url = self.qq.refresh_audio(keyword, str(qq_id))
        if not url and track.get("title"):
            url = self.qq.refresh_audio(str(track["title"]), str(qq_id))
        return url

    def ensure_lyrics(self, track: dict[str, Any]) -> tuple[str, str]:
        existing_lrc = str(track.get("lyrics_lrc") or "").strip()
        existing_translation = str(track.get("lyrics_translation") or "").strip()
        if existing_lrc:
            return existing_lrc, existing_translation

        fetched_at = int(track.get("lyrics_fetched_at") or 0)
        now = now_ms()
        if fetched_at and now - fetched_at < max(60, self.config.lyrics_retry_sec) * 1000:
            return "", existing_translation

        lrc = ""
        translation = ""
        netease_id = track.get("netease_id")
        if netease_id:
            lrc, translation = self.netease.lyrics(netease_id)
            if not lrc:
                lrc = self.meting.lyrics(netease_id)
        if not lrc and track.get("qq_id"):
            keyword = f"{track.get('title', '')} {track.get('artist', '')}".strip()
            for item in self.qq.search(keyword, limit=10):
                if str(item.get("source_id")) == str(track["qq_id"]):
                    lrc = str(item.get("lyrics_lrc") or "").strip()
                    break
        if lrc:
            self.db.update_track(
                track["id"],
                {
                    "lyrics_lrc": lrc,
                    "lyrics_translation": translation,
                    "lyrics_fetched_at": now,
                },
            )
            track["lyrics_lrc"] = lrc
            track["lyrics_translation"] = translation
            track["lyrics_fetched_at"] = now
        else:
            # Negative cache: avoid hammering upstream when no lyrics exist.
            self.db.update_track(track["id"], {"lyrics_fetched_at": now})
            track["lyrics_fetched_at"] = now
        return lrc, translation

    def refresh_audio(self, track: dict[str, Any], force: bool = False) -> dict[str, Any]:
        now = now_ms()
        resolved_at = int(track.get("audio_resolved_at") or 0)
        recent = resolved_at > 0 and now - resolved_at < 10 * 60 * 1000
        refreshed_qq = False
        qq_url = None if force or not recent else (
            str((track.get("meta_json") or {}).get("qq_audio_url") or "").strip() or None
        )
        if not qq_url and track.get("qq_id"):
            qq_url = self._qq_track_url(track)
            refreshed_qq = True
        if not qq_url:
            qq_url = str((track.get("meta_json") or {}).get("qq_audio_url") or "").strip() or None

        netease_id = track.get("netease_id")
        meting_url = self.meting.audio_url(netease_id) if netease_id else None
        primary = meting_url or qq_url or track.get("audio_primary") or ""

        alternates: list[str] = []
        seen_urls: set[str] = set()
        for candidate in (qq_url, meting_url, *(track.get("audio_alternates") or [])):
            candidate = _safe_url(candidate)
            if not candidate or candidate == primary:
                continue
            key = _url_key(candidate)
            if not key or key in seen_urls:
                continue
            seen_urls.add(key)
            alternates.append(candidate)

        primary = _safe_url(primary)
        alternates = [url for url in alternates if _safe_url(url) and url != primary]
        updates: dict[str, Any] = {
            "audio_primary": primary,
            "audio_alternates": alternates,
            "audio_resolved_at": now if refreshed_qq else (resolved_at or now),
        }
        if qq_url:
            meta = dict(track.get("meta_json") or {})
            meta["qq_audio_url"] = qq_url
            updates["meta_json"] = meta

        self.db.update_track(track["id"], updates)
        updated = self.db.get_track(track["id"]) or track
        self.ensure_lyrics(updated)
        return self.db.get_track(track["id"]) or updated
