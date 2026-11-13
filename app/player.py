from __future__ import annotations

import logging
import threading
import time
from typing import Any, Callable

from .config import Config
from .db import Database
from .sources.aggregate import TrackAggregator


logger = logging.getLogger(__name__)

K_TRACK = "player.current_track_id"
K_STARTED = "player.started_at_ms"
K_SEQ = "player.seq"
K_PAUSED = "player.paused"
K_PAUSED_POS = "player.paused_position_ms"
K_QUEUE = "player.current_queue_id"
K_PLAY = "player.current_play_id"
K_ROTATION = "player.rotation_index"
K_AUDIO_REV = "player.audio_rev"


class Player:
    def __init__(
        self,
        db: Database,
        aggregator: TrackAggregator,
        config: Config,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self.db = db
        self.aggregator = aggregator
        self.config = config
        self.clock = clock or time.time
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._load_state()

    def _now_ms(self) -> int:
        return int(self.clock() * 1000)

    @staticmethod
    def _int_setting(value: str | None, default: int = 0) -> int:
        try:
            return int(value) if value not in (None, "") else default
        except (TypeError, ValueError):
            return default

    def _load_state(self) -> None:
        self._track_id = self.db.get_setting(K_TRACK) or ""
        self._started_at = self._int_setting(self.db.get_setting(K_STARTED))
        self._seq = self._int_setting(self.db.get_setting(K_SEQ))
        self._paused = (self.db.get_setting(K_PAUSED) or "0") == "1"
        self._paused_position = self._int_setting(self.db.get_setting(K_PAUSED_POS))
        self._queue_id = self._int_setting(self.db.get_setting(K_QUEUE))
        self._play_id = self._int_setting(self.db.get_setting(K_PLAY))
        self._rotation_index = self._int_setting(self.db.get_setting(K_ROTATION))
        self._audio_rev = self._int_setting(self.db.get_setting(K_AUDIO_REV))

    def _save_state(self) -> None:
        self.db.set_settings(
            {
                K_TRACK: self._track_id,
                K_STARTED: self._started_at,
                K_SEQ: self._seq,
                K_PAUSED: "1" if self._paused else "0",
                K_PAUSED_POS: self._paused_position,
                K_QUEUE: self._queue_id,
                K_PLAY: self._play_id,
                K_ROTATION: self._rotation_index,
                K_AUDIO_REV: self._audio_rev,
            }
        )

    @staticmethod
    def _duration(track: dict[str, Any], default_ms: int) -> int:
        try:
            value = int(track.get("duration_ms") or 0)
        except (TypeError, ValueError):
            value = 0
        return max(1000, value or default_ms)

    def _finish_current(self, now: int) -> None:
        if self._play_id:
            self.db.end_play(self._play_id, now)
        if self._queue_id:
            self.db.set_queue_status(self._queue_id, "played", ended_at=now)
        self._play_id = 0
        self._queue_id = 0

    def _pick_next(self) -> tuple[dict[str, Any] | None, int]:
        queued = self.db.next_pending()
        if queued is not None:
            track = self.db.get_track(queued["track_id"])
            if track is not None:
                return track, int(queued["id"])
        playlist = self.db.list_playlist()
        if not playlist:
            return None, 0
        index = self._rotation_index
        if index >= len(playlist):
            return None, 0
        if len(playlist) > 1 and str(playlist[index].get("id")) == self._track_id:
            index += 1
        if index >= len(playlist):
            self._rotation_index = index
            return None, 0
        self._rotation_index = index + 1
        return playlist[index], 0

    def advance(self, reason: str = "auto") -> dict[str, Any] | None:
        with self._lock:
            now = self._now_ms()
            self._finish_current(now)
            track, queue_id = self._pick_next()
            if track is None:
                if not self._track_id:
                    return None
                self._track_id = ""
                self._started_at = 0
                self._paused = False
                self._paused_position = 0
                self._audio_rev = 0
                self._seq += 1
                self._save_state()
                logger.info("player idle (%s)", reason)
                return None

            if queue_id:
                self.db.set_queue_status(queue_id, "playing", started_at=now)
            try:
                track = self.aggregator.refresh_audio(track)
            except Exception as exc:
                logger.warning("audio refresh failed for %s: %s", track.get("title"), exc)

            self._track_id = str(track["id"])
            self._started_at = now
            self._paused = False
            self._paused_position = 0
            self._queue_id = queue_id
            self._audio_rev = 0
            self._seq += 1
            self._play_id = self.db.start_play(self._track_id, queue_id or None, now)
            self._save_state()
            logger.info("now playing: %s - %s (%s)", track.get("artist"), track.get("title"), reason)
            return track

    def _current_track(self) -> dict[str, Any] | None:
        if not self._track_id:
            return None
        return self.db.get_track(self._track_id)

    def ensure_started(self) -> None:
        with self._lock:
            if self._paused:
                return
            track = self._current_track()
            if track is None or self._started_at <= 0:
                if not self.db.list_playlist() and self.db.next_pending() is None:
                    return
                self.advance("start")
                return
            now = self._now_ms()
            duration = self._duration(track, self.config.default_duration_ms)
            elapsed = now - self._started_at
            if elapsed >= duration:
                if elapsed > duration * 2:
                    logger.info("player state stale, advancing to a fresh track")
                self.advance("ended")

    def tick(self) -> None:
        try:
            self.ensure_started()
        except Exception:
            logger.exception("player tick failed")

    def pause(self) -> None:
        with self._lock:
            if self._paused:
                return
            track = self._current_track()
            if track is None or self._started_at <= 0:
                return
            now = self._now_ms()
            duration = self._duration(track, self.config.default_duration_ms)
            self._paused_position = max(0, min(duration, now - self._started_at))
            self._paused = True
            self._save_state()
            logger.info("player paused at %dms", self._paused_position)

    def resume(self) -> None:
        with self._lock:
            if not self._paused:
                return
            now = self._now_ms()
            self._started_at = now - max(0, self._paused_position)
            self._paused = False
            self._save_state()
            logger.info("player resumed")

    def skip(self, reason: str = "skip") -> dict[str, Any] | None:
        return self.advance(reason)

    def _peek_next(self) -> dict[str, Any] | None:
        queued = self.db.next_pending()
        if queued is not None:
            track = self.db.get_track(queued["track_id"])
            if track is not None:
                return track
        playlist = self.db.list_playlist()
        if not playlist:
            return None
        index = self._rotation_index
        if index >= len(playlist):
            return None
        if len(playlist) > 1 and str(playlist[index].get("id")) == self._track_id:
            index += 1
        if index >= len(playlist):
            return None
        return playlist[index]

    @staticmethod
    def _track_summary(track: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": track.get("id"),
            "source": track.get("source"),
            "title": track.get("title"),
            "artist": track.get("artist"),
            "album": track.get("album"),
            "duration_ms": track.get("duration_ms"),
            "duration_estimated": bool(track.get("duration_estimated")),
            "cover_url": track.get("cover_url") or "",
        }

    def report(self, seq: int, event: str) -> dict[str, Any]:
        with self._lock:
            if seq != self._seq:
                return self.snapshot(ensure=False)
            if event == "ended":
                track = self._current_track()
                if track is not None:
                    now = self._now_ms()
                    position = (
                        self._paused_position
                        if self._paused
                        else max(0, now - self._started_at)
                    )
                    duration = self._duration(track, self.config.default_duration_ms)
                    if position >= min(30_000, int(duration * 0.5)):
                        self.advance("client-ended")
            elif event in {"error", "stalled"}:
                track = self._current_track()
                if track is not None:
                    try:
                        self.aggregator.refresh_audio(track, force=True)
                    except Exception as exc:
                        logger.warning("re-resolve failed: %s", exc)
                    self._audio_rev += 1
                    self._save_state()
            return self.snapshot(ensure=False)

    def upcoming(self, limit: int = 40) -> list[dict[str, Any]]:
        with self._lock:
            self.ensure_started()
            limit = max(1, int(limit))
            current = self._current_track()
            current_id = str(current["id"]) if current else ""
            queue_rows = self.db.list_queue(50)
            items: list[dict[str, Any]] = []
            seen: set[str] = set()

            playing = next((row for row in queue_rows if row["status"] == "playing"), None)
            if playing is not None:
                items.append(
                    {
                        "id": playing["track_id"],
                        "queue_id": playing["id"],
                        "source": playing.get("source"),
                        "title": playing.get("title"),
                        "artist": playing.get("artist"),
                        "album": playing.get("album"),
                        "duration_ms": playing.get("duration_ms"),
                        "cover_url": playing.get("cover_url") or "",
                        "nickname": playing.get("nickname"),
                        "priority": int(playing.get("priority") or 0),
                        "status": "playing",
                    }
                )
                seen.add(str(playing["track_id"]))
            elif current is not None:
                items.append(
                    {
                        **self._track_summary(current),
                        "queue_id": None,
                        "nickname": None,
                        "priority": 0,
                        "status": "playing",
                    }
                )
                seen.add(current_id)

            for row in queue_rows:
                if row["status"] != "pending" or str(row["track_id"]) in seen:
                    continue
                items.append(
                    {
                        "id": row["track_id"],
                        "queue_id": row["id"],
                        "source": row.get("source"),
                        "title": row.get("title"),
                        "artist": row.get("artist"),
                        "album": row.get("album"),
                        "duration_ms": row.get("duration_ms"),
                        "cover_url": row.get("cover_url") or "",
                        "nickname": row.get("nickname"),
                        "priority": int(row.get("priority") or 0),
                        "status": "pending",
                    }
                )
                seen.add(str(row["track_id"]))
                if len(items) >= limit:
                    return items

            playlist = self.db.list_playlist()
            index = self._rotation_index
            while index < len(playlist) and len(items) < limit:
                track = playlist[index]
                index += 1
                if str(track["id"]) in seen:
                    continue
                items.append(
                    {
                        **self._track_summary(track),
                        "queue_id": None,
                        "nickname": None,
                        "priority": 0,
                        "status": "upcoming",
                    }
                )
                seen.add(str(track["id"]))

            for track in playlist:
                if len(items) >= limit:
                    break
                if str(track["id"]) in seen:
                    continue
                items.append(
                    {
                        **self._track_summary(track),
                        "queue_id": None,
                        "nickname": None,
                        "priority": 0,
                        "status": "played",
                    }
                )
                seen.add(str(track["id"]))
            return items

    def snapshot(self, ensure: bool = True) -> dict[str, Any]:
        with self._lock:
            if ensure:
                self.ensure_started()
            now = self._now_ms()
            base = {
                "server_time_ms": now,
                "station": {"name": self.config.station_name},
                "seq": self._seq,
                "audio_rev": self._audio_rev,
                "paused": self._paused,
            }
            track = self._current_track()
            if track is None:
                base.update(
                    {
                        "track": None,
                        "started_at_ms": 0,
                        "position_ms": 0,
                        "duration_ms": 0,
                        "audio": {"direct": "", "alternates": []},
                        "lyrics_url": "",
                        "next": None,
                        "queue": {"pending": self.db.count_pending(), "playing": False},
                    }
                )
                return base

            duration = self._duration(track, self.config.default_duration_ms)
            if self._paused:
                position = max(0, min(duration, self._paused_position))
            else:
                position = max(0, min(duration, now - self._started_at))
            next_track = self._peek_next()
            base.update(
                {
                    "track": self._track_summary(track),
                    "started_at_ms": self._started_at,
                    "position_ms": position,
                    "duration_ms": duration,
                    "audio": {
                        "direct": track.get("audio_primary") or "",
                        "alternates": list(track.get("audio_alternates") or []),
                    },
                    "lyrics_url": f"/api/lyrics/{track['id']}",
                    "next": self._track_summary(next_track) if next_track else None,
                    "queue": {
                        "pending": self.db.count_pending(),
                        "playing": bool(self._queue_id),
                    },
                }
            )
            return base

    def _run(self) -> None:
        while not self._stop.is_set():
            self.tick()
            self._stop.wait(self.config.poll_interval_sec)

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="radio-player", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None
