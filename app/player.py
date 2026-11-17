from __future__ import annotations

import json
import logging
import threading
import time
import urllib.parse
from typing import Any, Callable

from .config import Config
from .db import Database
from .sources.aggregate import TrackAggregator


logger = logging.getLogger(__name__)


def _safe_url(url: Any) -> str:
    value = str(url or "").strip()
    if not value:
        return ""
    # Uploaded files live under the app's own private media route.  Keep the
    # URL relative so the deployment host is never guessed by the player.
    if value.startswith("/media/uploads/"):
        name = value[len("/media/uploads/") :]
        if name and "/" not in name and "\\" not in name and name not in {".", ".."}:
            return value
        return ""
    if value.startswith("/native/"):
        name = value[len("/native/") :]
        if name and "/" not in name and "\\" not in name and name not in {".", ".."}:
            return value
        return ""
    try:
        parts = urllib.parse.urlsplit(value)
    except ValueError:
        return ""
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        return ""
    return value


K_TRACK = "player.current_track_id"
K_STARTED = "player.started_at_ms"
K_SEQ = "player.seq"
K_PAUSED = "player.paused"
K_PAUSED_POS = "player.paused_position_ms"
K_QUEUE = "player.current_queue_id"
K_PLAY = "player.current_play_id"
K_ROTATION = "player.rotation_index"
K_ROTATION_SKIP = "player.rotation_skip"
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
        self._advance_lock = threading.Lock()
        self._refresh_lock = threading.Lock()
        self._refresh_stats: dict[int, tuple[int, int]] = {}
        self._started_refresh: set[int] = set()
        self._resolver_lock = threading.Lock()
        self._resolvers: set[threading.Thread] = set()
        self._stopping = False
        self._background_slots = threading.BoundedSemaphore(4)
        self._refresh_inflight: set[int] = set()
        self._last_maintenance = 0.0
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
        try:
            raw_skip = json.loads(self.db.get_setting(K_ROTATION_SKIP) or "[]")
        except (TypeError, ValueError):
            raw_skip = []
        self._rotation_skip = {
            str(item) for item in raw_skip if isinstance(item, str)
        }
        self._rotation_exhausted = False
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
                K_ROTATION_SKIP: json.dumps(
                    sorted(self._rotation_skip), ensure_ascii=False
                ),
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
            self.db.delete_queue_item(self._queue_id)
        self._play_id = 0
        self._queue_id = 0

    def _pick_next(self) -> tuple[dict[str, Any] | None, int]:
        queued = self.db.next_pending()
        if queued is not None:
            track = self.db.get_track(queued["track_id"])
            if track is not None:
                return track, int(queued["id"])
        playlist = self.db.list_playlist_summaries()
        if not playlist:
            self._rotation_exhausted = True
            return None, 0
        index = self._rotation_index
        while index < len(playlist):
            candidate_id = str(playlist[index].get("id"))
            if candidate_id in self._rotation_skip:
                index += 1
                continue
            if len(playlist) > 1 and candidate_id == self._track_id:
                index += 1
                continue
            break
        if index >= len(playlist):
            self._rotation_index = index
            self._rotation_exhausted = True
            self._trim_rotation_skip(playlist)
            return None, 0
        self._rotation_index = index + 1
        self._rotation_exhausted = False
        self._trim_rotation_skip(playlist)
        chosen = playlist[index]
        track = self.db.get_track(str(chosen["id"]))
        return track, 0

    def _trim_rotation_skip(self, playlist: list[dict[str, Any]]) -> None:
        if not self._rotation_skip:
            return
        valid_ids = {str(item["id"]) for item in playlist}
        self._rotation_skip &= valid_ids
        for position, item in enumerate(playlist):
            if position < self._rotation_index:
                self._rotation_skip.discard(str(item["id"]))

    def _advance_state(self, reason: str) -> tuple[dict[str, Any] | None, int]:
        with self._lock:
            now = self._now_ms()
            self._finish_current(now)
            track, queue_id = self._pick_next()
            if track is None:
                had_skip = bool(self._rotation_skip)
                self._rotation_skip.clear()
                if not self._track_id:
                    if had_skip:
                        self._save_state()
                    return None, self._seq
                self._track_id = ""
                self._started_at = 0
                self._paused = False
                self._paused_position = 0
                self._audio_rev = 0
                self._seq += 1
                self._save_state()
                logger.info("player idle (%s)", reason)
                return None, self._seq

            if queue_id:
                self.db.set_queue_status(queue_id, "playing", started_at=now)
                playlist = self.db.list_playlist_summaries()
                playlist_ids = {str(item["id"]) for item in playlist}
                if str(track["id"]) in playlist_ids:
                    self._rotation_skip.add(str(track["id"]))
                self._trim_rotation_skip(playlist)

            self._track_id = str(track["id"])
            # Give every client a short, server-side pre-roll window before
            # the public timeline starts.  This is what prevents the first
            # seconds of a skipped track from being cut while clients poll
            # and open the new stream.
            preroll_reasons = {"vote", "admin", "skip", "admin-delete"}
            preroll = (
                max(0, int(self.config.track_preroll_ms))
                if reason in preroll_reasons
                else 0
            )
            self._started_at = now + preroll
            self._paused = False
            self._paused_position = 0
            self._queue_id = queue_id
            self._audio_rev = 0
            self._seq += 1
            self._started_refresh.clear()
            if len(self._refresh_stats) > 50:
                self._refresh_stats.clear()
            self._play_id = self.db.start_play(self._track_id, queue_id or None, now)
            self._save_state()
            logger.info("now playing: %s - %s (%s)", track.get("artist"), track.get("title"), reason)
            return track, self._seq

    def _resolve_track_audio(self, track: dict[str, Any], seq: int) -> None:
        if track.get("source") == "upload":
            return
        with self._refresh_lock:
            with self._lock:
                if seq != self._seq:
                    return
                if seq in self._started_refresh:
                    return
                self._started_refresh.add(seq)
                old_primary = track.get("audio_primary") or ""
                old_alternates = list(track.get("audio_alternates") or [])
            try:
                updated = self.aggregator.refresh_audio(track)
            except Exception as exc:
                logger.warning("audio refresh failed for %s: %s", track.get("title"), exc)
                return
            changed = (
                (updated.get("audio_primary") or "") != old_primary
                or list(updated.get("audio_alternates") or []) != old_alternates
            )
            if not changed:
                return
            try:
                with self._lock:
                    if seq != self._seq:
                        return
                    self._audio_rev += 1
                    self._save_state()
            except Exception as exc:
                logger.warning("could not persist refreshed audio: %s", exc)

    def advance(self, reason: str = "auto") -> dict[str, Any] | None:
        with self._advance_lock:
            track, seq = self._advance_state(reason)
            if track is not None:
                self._resolve_track_audio(track, seq)
            return track

    def _current_track(self) -> dict[str, Any] | None:
        if not self._track_id:
            return None
        return self.db.get_track(self._track_id)

    def _start_reason(self) -> str:
        with self._lock:
            if self._paused:
                return ""
            track = self._current_track()
            if track is None or self._started_at <= 0:
                if self.db.next_pending() is not None:
                    return "start"
                if self._rotation_exhausted:
                    return ""
                if not self.db.list_playlist_summaries():
                    self._rotation_exhausted = True
                    if self._rotation_skip:
                        self._rotation_skip.clear()
                        self._save_state()
                    return ""
                return "start"
            now = self._now_ms()
            duration = self._duration(track, self.config.default_duration_ms)
            elapsed = now - self._started_at
            if elapsed >= duration:
                if elapsed > duration * 2:
                    logger.info("player state stale, advancing to a fresh track")
                return "ended"
            return ""

    def ensure_started(self) -> None:
        self._ensure_state_only()

    def _ensure_state_only(self) -> None:
        """Advance state without blocking the caller on upstream network I/O."""
        if self._paused:
            return
        if not self._advance_lock.acquire(blocking=False):
            return
        try:
            reason = self._start_reason()
            if not reason:
                return
            track, seq = self._advance_state(reason)
            if track is not None:
                self._spawn_resolve(track, seq)
        finally:
            self._advance_lock.release()

    def tick(self) -> None:
        try:
            self._ensure_state_only()
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

    def notify_playlist_changed(self) -> None:
        with self._lock:
            self._rotation_exhausted = False

    def skip(self, reason: str = "skip") -> dict[str, Any] | None:
        return self.advance(reason)

    def skip_async(self, reason: str = "skip", expected_seq: int | None = None) -> bool:
        return self._advance_state_async(reason, expected_seq=expected_seq)

    def _peek_next(self) -> dict[str, Any] | None:
        queued = self.db.next_pending()
        if queued is not None:
            track = self.db.get_track(queued["track_id"])
            if track is not None:
                return track
        playlist = self.db.list_playlist_summaries()
        if not playlist:
            return None
        index = self._rotation_index
        while index < len(playlist):
            candidate_id = str(playlist[index].get("id"))
            if candidate_id in self._rotation_skip:
                index += 1
                continue
            if len(playlist) > 1 and candidate_id == self._track_id:
                index += 1
                continue
            break
        if index >= len(playlist):
            return None
        return playlist[index]

    @staticmethod
    def _native_url(track: dict[str, Any]) -> str:
        track_id = str(track.get("id") or "").strip()
        if not track_id:
            return ""
        return "/native/" + urllib.parse.quote(track_id, safe="")

    @staticmethod
    def _server_audio(track: dict[str, Any]) -> dict[str, Any]:
        # Never expose the third-party upstream URL to the browser.  Every
        # direct fallback goes back through this server endpoint.
        return {
            "direct": Player._native_url(track),
            "alternates": [],
            "upstream": _safe_url(track.get("audio_primary")),
        }

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
            "cover_url": _safe_url(track.get("cover_url")),
        }

    def _spawn_background(self, target: Callable[..., None], *args: Any) -> bool:
        if not self._background_slots.acquire(blocking=False):
            logger.warning("background resolve queue full; skipping task")
            return False

        def run() -> None:
            try:
                target(*args)
            finally:
                with self._resolver_lock:
                    self._resolvers.discard(threading.current_thread())
                self._background_slots.release()

        thread = threading.Thread(target=run, name="radio-resolve", daemon=True)
        with self._resolver_lock:
            if self._stopping:
                self._background_slots.release()
                return False
            self._resolvers.add(thread)
            try:
                thread.start()
            except Exception:
                self._resolvers.discard(thread)
                self._background_slots.release()
                logger.exception("failed to start background resolver")
                return False
        return True

    def _spawn_resolve(self, track: dict[str, Any], seq: int) -> bool:
        return self._spawn_background(self._resolve_track_audio, track, seq)

    def _advance_state_async(
        self, reason: str, expected_seq: int | None = None
    ) -> bool:
        if not self._advance_lock.acquire(blocking=False):
            return False
        try:
            with self._lock:
                if expected_seq is not None and expected_seq != self._seq:
                    return False
                before_seq = self._seq
            track, seq = self._advance_state(reason)
            if track is not None:
                self._spawn_resolve(track, seq)
            return seq != before_seq
        finally:
            self._advance_lock.release()

    def _refresh_audio_task(self, track: dict[str, Any], seq: int) -> None:
        try:
            self._refresh_audio(track, seq)
        finally:
            with self._lock:
                self._refresh_inflight.discard(seq)

    def _refresh_audio(self, track: dict[str, Any], seq: int) -> None:
        with self._refresh_lock:
            now = self._now_ms()
            with self._lock:
                if seq != self._seq:
                    return
                count, since = self._refresh_stats.get(seq, (0, now))
                if now - since > 60_000:
                    count, since = 0, now
                if count >= 3:
                    return
                self._refresh_stats[seq] = (count + 1, since)
                old_primary = track.get("audio_primary") or ""
                old_alternates = list(track.get("audio_alternates") or [])
            try:
                updated = self.aggregator.refresh_audio(track, force=True)
            except Exception as exc:
                logger.warning("re-resolve failed: %s", exc)
                return
            changed = (
                (updated.get("audio_primary") or "") != old_primary
                or list(updated.get("audio_alternates") or []) != old_alternates
            )
            if not changed:
                return
            try:
                with self._lock:
                    if seq != self._seq:
                        return
                    self._audio_rev += 1
                    self._save_state()
            except Exception as exc:
                logger.warning("could not persist refreshed audio: %s", exc)

    def report(self, seq: int, event: str) -> dict[str, Any]:
        with self._lock:
            if seq != self._seq:
                return self.snapshot(ensure=False)
            if event == "ended":
                track = self._current_track()
                should_advance = False
                if track is not None:
                    now = self._now_ms()
                    position = (
                        self._paused_position
                        if self._paused
                        else max(0, now - self._started_at)
                    )
                    duration = self._duration(track, self.config.default_duration_ms)
                    threshold = (
                        max(10_000, self.config.estimated_ended_min_sec * 1000)
                        if track.get("duration_estimated")
                        else min(30_000, int(duration * 0.5))
                    )
                    should_advance = (
                        position >= threshold or position >= duration - 1_000
                    )
            else:
                should_advance = False
                track = self._current_track() if event in {"error", "stalled"} else None

        if event == "ended" and should_advance:
            self._advance_state_async("client-ended", expected_seq=seq)
        elif event in {"error", "stalled"} and track is not None:
            with self._lock:
                if seq in self._refresh_inflight:
                    return self.snapshot(ensure=False)
                self._refresh_inflight.add(seq)
            if not self._spawn_background(self._refresh_audio_task, track, seq):
                with self._lock:
                    self._refresh_inflight.discard(seq)
        return self.snapshot(ensure=False)

    def upcoming(
        self, limit: int = 40, requester_key: str | None = None
    ) -> list[dict[str, Any]]:
        self._ensure_state_only()
        with self._lock:
            limit = max(1, int(limit))
            current = self._current_track()
            current_id = str(current["id"]) if current else ""
            queue_rows = self.db.list_queue_for(requester_key, 50)
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
                        "can_cancel": False,
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
                        "can_cancel": bool(row.get("can_cancel")),
                        "pinned_by_me": bool(row.get("pinned_by_me")),
                        "status": "pending",
                    }
                )
                seen.add(str(row["track_id"]))
                if len(items) >= limit:
                    return items

            playlist = self.db.list_playlist_summaries()
            index = self._rotation_index
            while index < len(playlist) and len(items) < limit:
                track = playlist[index]
                index += 1
                if str(track["id"]) in seen or str(track["id"]) in self._rotation_skip:
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
            return items

    def snapshot(self, ensure: bool = True) -> dict[str, Any]:
        if ensure:
            self._ensure_state_only()
        with self._lock:
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
            next_summary = None
            if next_track is not None:
                next_summary = self._track_summary(next_track)
                next_summary["lyrics_url"] = f"/api/lyrics/{next_track['id']}"
                next_summary["audio"] = Player._server_audio(next_track)
            base.update(
                {
                    "track": self._track_summary(track),
                    "started_at_ms": self._started_at,
                    "position_ms": position,
                    "duration_ms": duration,
                    "audio": Player._server_audio(track),
                    "lyrics_url": f"/api/lyrics/{track['id']}",
                    "next": next_summary,
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
            now = time.time()
            if now - self._last_maintenance > 3600:
                self._last_maintenance = now
                try:
                    self.db.cleanup_history()
                    self.db.cleanup_old_data()
                except Exception:
                    logger.exception("periodic database cleanup failed")
            self._stop.wait(self.config.poll_interval_sec)

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._stopping = False
        self._thread = threading.Thread(target=self._run, name="radio-player", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None
        with self._resolver_lock:
            self._stopping = True
            resolvers = list(self._resolvers)
        for thread in resolvers:
            if thread.ident is not None:
                thread.join(timeout=3.0)
