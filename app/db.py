from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Iterable


SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;
PRAGMA foreign_keys=ON;
PRAGMA busy_timeout=5000;

CREATE TABLE IF NOT EXISTS tracks (
    id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    source_id TEXT,
    netease_id TEXT,
    qq_id TEXT,
    title TEXT NOT NULL,
    artist TEXT,
    album TEXT,
    duration_ms INTEGER,
    duration_estimated INTEGER NOT NULL DEFAULT 0,
    cover_url TEXT,
    lyrics_lrc TEXT,
    lyrics_translation TEXT,
    lyrics_fetched_at INTEGER,
    audio_primary TEXT,
    audio_alternates TEXT NOT NULL DEFAULT '[]',
    audio_resolved_at INTEGER,
    meta_json TEXT,
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at INTEGER NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_track_source
    ON tracks(source, source_id) WHERE source_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_track_enabled ON tracks(enabled, created_at);

CREATE TABLE IF NOT EXISTS queue (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    track_id TEXT NOT NULL,
    requester_key TEXT,
    nickname TEXT,
    message TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    priority INTEGER NOT NULL DEFAULT 0,
    created_at INTEGER NOT NULL,
    started_at INTEGER,
    ended_at INTEGER,
    FOREIGN KEY(track_id) REFERENCES tracks(id)
);
CREATE INDEX IF NOT EXISTS idx_queue_status ON queue(status, priority, id);
CREATE INDEX IF NOT EXISTS idx_queue_requester ON queue(requester_key, status);

CREATE TABLE IF NOT EXISTS queue_pins (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    queue_id INTEGER NOT NULL,
    voter_key TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    UNIQUE(queue_id, voter_key),
    FOREIGN KEY(queue_id) REFERENCES queue(id)
);

CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ip_key TEXT,
    nickname TEXT,
    body TEXT,
    track_id TEXT,
    created_at INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'visible'
);
CREATE INDEX IF NOT EXISTS idx_messages_created ON messages(created_at);

CREATE TABLE IF NOT EXISTS request_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ip_key TEXT NOT NULL,
    track_id TEXT NOT NULL,
    nickname TEXT,
    created_at INTEGER NOT NULL,
    FOREIGN KEY(track_id) REFERENCES tracks(id)
);
CREATE INDEX IF NOT EXISTS idx_request_history_ip
    ON request_history(ip_key, created_at);
CREATE INDEX IF NOT EXISTS idx_request_history_created
    ON request_history(created_at);

CREATE TABLE IF NOT EXISTS plays (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    track_id TEXT NOT NULL,
    queue_id INTEGER,
    started_at INTEGER NOT NULL,
    ended_at INTEGER
);
CREATE INDEX IF NOT EXISTS idx_plays_started ON plays(started_at);

CREATE TABLE IF NOT EXISTS rate_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    key TEXT NOT NULL,
    action TEXT NOT NULL,
    ts INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_rate ON rate_events(key, action, ts);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT
);
"""


TRACK_FIELDS = (
    "id",
    "source",
    "source_id",
    "netease_id",
    "qq_id",
    "title",
    "artist",
    "album",
    "duration_ms",
    "duration_estimated",
    "cover_url",
    "lyrics_lrc",
    "lyrics_translation",
    "lyrics_fetched_at",
    "audio_primary",
    "audio_alternates",
    "audio_resolved_at",
    "meta_json",
    "enabled",
    "created_at",
)


def now_ms() -> int:
    return int(time.time() * 1000)


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _json_loads(value: Any, default: Any) -> Any:
    if value is None or value == "":
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


class Database:
    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.executescript(SCHEMA)
            # Keep only the newest row per (IP, track), then enforce it.
            self._conn.execute(
                "DELETE FROM request_history WHERE id NOT IN "
                "(SELECT MAX(id) FROM request_history GROUP BY ip_key, track_id)"
            )
            self._conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_request_history_unique "
                "ON request_history(ip_key, track_id)"
            )
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def health(self) -> bool:
        try:
            with self._lock:
                self._conn.execute("SELECT 1").fetchone()
            return True
        except sqlite3.Error:
            return False

    def _execute(self, sql: str, params: Iterable[Any] = ()) -> sqlite3.Cursor:
        with self._lock:
            try:
                cur = self._conn.execute(sql, tuple(params))
                self._conn.commit()
                return cur
            except Exception:
                self._conn.rollback()
                raise

    def _fetchall(self, sql: str, params: Iterable[Any] = ()) -> list[sqlite3.Row]:
        with self._lock:
            return list(self._conn.execute(sql, tuple(params)).fetchall())

    def _fetchone(self, sql: str, params: Iterable[Any] = ()) -> sqlite3.Row | None:
        with self._lock:
            return self._conn.execute(sql, tuple(params)).fetchone()

    def get_setting(self, key: str, default: str | None = None) -> str | None:
        row = self._fetchone("SELECT value FROM settings WHERE key = ?", (key,))
        return default if row is None else str(row["value"])

    def set_setting(self, key: str, value: Any) -> None:
        self._execute(
            "INSERT INTO settings(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, str(value)),
        )

    def set_settings(self, values: dict[str, Any]) -> None:
        with self._lock:
            try:
                for key, value in values.items():
                    self._conn.execute(
                        "INSERT INTO settings(key, value) VALUES(?, ?) "
                        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                        (key, str(value)),
                    )
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise

    @staticmethod
    def _track_to_row(track: dict[str, Any]) -> dict[str, Any]:
        row = {field: track.get(field) for field in TRACK_FIELDS}
        row["id"] = row["id"] or uuid.uuid4().hex
        row["created_at"] = int(row["created_at"] or now_ms())
        row["duration_estimated"] = int(bool(row.get("duration_estimated")))
        row["enabled"] = int(bool(row.get("enabled", True)))
        row["audio_alternates"] = _json_dumps(row.get("audio_alternates") or [])
        row["meta_json"] = _json_dumps(row.get("meta_json") or {})
        return row

    @staticmethod
    def _row_to_track(row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        track = dict(row)
        track["duration_estimated"] = bool(track.get("duration_estimated"))
        track["enabled"] = bool(track.get("enabled"))
        track["audio_alternates"] = _json_loads(track.get("audio_alternates"), [])
        track["meta_json"] = _json_loads(track.get("meta_json"), {})
        return track

    def insert_track(self, track: dict[str, Any]) -> str:
        row = self._track_to_row(track)
        columns = ", ".join(TRACK_FIELDS)
        placeholders = ", ".join("?" for _ in TRACK_FIELDS)
        self._execute(
            f"INSERT INTO tracks({columns}) VALUES({placeholders})",
            [row[field] for field in TRACK_FIELDS],
        )
        return str(row["id"])

    def update_track(self, track_id: str, values: dict[str, Any]) -> None:
        allowed = set(TRACK_FIELDS) - {"id"}
        updates: dict[str, Any] = {}
        for key, value in values.items():
            if key not in allowed:
                continue
            if key == "audio_alternates":
                value = _json_dumps(value or [])
            elif key == "meta_json":
                value = _json_dumps(value or {})
            elif key in {"duration_estimated", "enabled"}:
                value = int(bool(value))
            updates[key] = value
        if not updates:
            return
        assignments = ", ".join(f"{key} = ?" for key in updates)
        self._execute(
            f"UPDATE tracks SET {assignments} WHERE id = ?",
            [*updates.values(), track_id],
        )

    def get_track(self, track_id: str) -> dict[str, Any] | None:
        row = self._fetchone("SELECT * FROM tracks WHERE id = ?", (track_id,))
        return self._row_to_track(row)

    def find_track_by_source(self, source: str, source_id: str) -> dict[str, Any] | None:
        row = self._fetchone(
            "SELECT * FROM tracks WHERE source = ? AND source_id = ?",
            (source, source_id),
        )
        return self._row_to_track(row)

    def find_track_by_title_artist(
        self, title: str, artist: str
    ) -> dict[str, Any] | None:
        title = str(title or "").strip()
        artist = str(artist or "").strip()
        if not title:
            return None
        row = self._fetchone(
            "SELECT * FROM tracks WHERE lower(trim(title)) = lower(trim(?)) "
            "AND lower(trim(artist)) = lower(trim(?)) LIMIT 1",
            (title, artist),
        )
        return self._row_to_track(row)

    def find_track_by_ids(
        self, netease_id: str | None, qq_id: str | None
    ) -> dict[str, Any] | None:
        if not netease_id and not qq_id:
            return None
        row = self._fetchone(
            "SELECT * FROM tracks WHERE "
            "(netease_id IS NOT NULL AND netease_id = ?) OR "
            "(qq_id IS NOT NULL AND qq_id = ?) LIMIT 1",
            (netease_id or "", qq_id or ""),
        )
        return self._row_to_track(row)

    def list_playlist(self) -> list[dict[str, Any]]:
        rows = self._fetchall(
            "SELECT * FROM tracks WHERE enabled = 1 ORDER BY created_at, id"
        )
        return [track for row in rows if (track := self._row_to_track(row)) is not None]

    def list_playlist_summaries(self) -> list[dict[str, Any]]:
        rows = self._fetchall(
            "SELECT id, source, title, artist, album, duration_ms, "
            "duration_estimated, cover_url, audio_primary, audio_alternates, "
            "enabled, created_at "
            "FROM tracks WHERE enabled = 1 ORDER BY created_at, id"
        )
        return [track for row in rows if (track := self._row_to_track(row)) is not None]

    def delete_track(self, track_id: str) -> None:
        self._execute("DELETE FROM tracks WHERE id = ?", (track_id,))

    def list_all_tracks(self, limit: int = 500) -> list[dict[str, Any]]:
        rows = self._fetchall(
            "SELECT * FROM tracks ORDER BY created_at DESC, id LIMIT ?",
            (max(1, min(2000, int(limit))),),
        )
        return [track for row in rows if (track := self._row_to_track(row)) is not None]

    def delete_track_admin(self, track_id: str) -> dict[str, Any] | None:
        """Delete any track and its dependent queue/history rows atomically."""
        with self._lock:
            try:
                row = self._conn.execute(
                    "SELECT * FROM tracks WHERE id = ?", (track_id,)
                ).fetchone()
                if row is None:
                    return None
                track = self._row_to_track(row)
                queue_ids = [
                    int(item["id"])
                    for item in self._conn.execute(
                        "SELECT id FROM queue WHERE track_id = ?", (track_id,)
                    ).fetchall()
                ]
                if queue_ids:
                    placeholders = ",".join("?" for _ in queue_ids)
                    self._conn.execute(
                        f"DELETE FROM queue_pins WHERE queue_id IN ({placeholders})",
                        queue_ids,
                    )
                self._conn.execute(
                    "DELETE FROM queue WHERE track_id = ?", (track_id,)
                )
                self._conn.execute(
                    "DELETE FROM request_history WHERE track_id = ?", (track_id,)
                )
                self._conn.execute(
                    "DELETE FROM plays WHERE track_id = ?", (track_id,)
                )
                self._conn.execute("DELETE FROM tracks WHERE id = ?", (track_id,))
                self._conn.commit()
                return track
            except Exception:
                self._conn.rollback()
                raise

    def enqueue(
        self,
        track_id: str,
        requester_key: str | None,
        nickname: str | None,
        message: str | None,
        priority: int = 0,
        status: str = "pending",
        created_at: int | None = None,
    ) -> int:
        cur = self._execute(
            "INSERT INTO queue(track_id, requester_key, nickname, message, status, priority, created_at) "
            "VALUES(?, ?, ?, ?, ?, ?, ?)",
            (
                track_id,
                requester_key,
                nickname,
                message,
                status,
                int(priority),
                int(created_at or now_ms()),
            ),
        )
        return int(cur.lastrowid)

    def next_pending(self) -> dict[str, Any] | None:
        row = self._fetchone(
            "SELECT * FROM queue WHERE status = 'pending' "
            "ORDER BY priority DESC, id ASC LIMIT 1"
        )
        return None if row is None else dict(row)

    def set_queue_status(
        self,
        queue_id: int,
        status: str,
        started_at: int | None = None,
        ended_at: int | None = None,
    ) -> None:
        self._execute(
            "UPDATE queue SET status = ?, started_at = COALESCE(?, started_at), "
            "ended_at = COALESCE(?, ended_at) WHERE id = ?",
            (status, started_at, ended_at, queue_id),
        )

    def count_pending(self) -> int:
        row = self._fetchone("SELECT COUNT(*) AS n FROM queue WHERE status = 'pending'")
        return int(row["n"]) if row else 0

    def count_pending_for_key(self, key: str) -> int:
        row = self._fetchone(
            "SELECT COUNT(*) AS n FROM queue WHERE requester_key = ? "
            "AND status IN ('pending', 'playing')",
            (key,),
        )
        return int(row["n"]) if row else 0

    def recent_track_in_queue(self, track_id: str, since_ms: int) -> bool:
        row = self._fetchone(
            "SELECT 1 FROM queue WHERE track_id = ? AND status IN ('pending', 'playing') "
            "AND created_at >= ? LIMIT 1",
            (track_id, since_ms),
        )
        return row is not None

    def list_queue(self, limit: int = 20) -> list[dict[str, Any]]:
        rows = self._fetchall(
            "SELECT q.id, q.track_id, q.nickname, q.message, q.status, q.priority, "
            "q.created_at, q.started_at, q.ended_at, "
            "t.title, t.artist, t.album, t.duration_ms, t.cover_url, t.source "
            "FROM queue q JOIN tracks t ON t.id = q.track_id "
            "WHERE q.status IN ('pending', 'playing') "
            "ORDER BY q.priority DESC, q.id ASC LIMIT ?",
            (limit,),
        )
        return [dict(row) for row in rows]

    def list_queue_for(
        self, requester_key: str | None, limit: int = 20
    ) -> list[dict[str, Any]]:
        """Queue rows for the user-facing list, with an ownership flag.

        The hashed requester key is intentionally not returned to the browser;
        it is only used to compute can_cancel.
        """
        rows = self._fetchall(
            "SELECT q.id, q.track_id, q.requester_key, q.nickname, q.message, "
            "q.status, q.priority, q.created_at, q.started_at, q.ended_at, "
            "t.title, t.artist, t.album, t.duration_ms, t.cover_url, t.source "
            "FROM queue q JOIN tracks t ON t.id = q.track_id "
            "WHERE q.status IN ('pending', 'playing') "
            "ORDER BY q.priority DESC, q.id ASC LIMIT ?",
            (limit,),
        )
        result: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            owner = str(item.pop("requester_key", "") or "")
            item["can_cancel"] = bool(
                requester_key and owner == requester_key and item["status"] == "pending"
            )
            item["pinned_by_me"] = False
            if requester_key:
                pinned = self._fetchone(
                    "SELECT 1 FROM queue_pins WHERE queue_id = ? AND voter_key = ? LIMIT 1",
                    (int(item["id"]), requester_key),
                )
                item["pinned_by_me"] = pinned is not None
            result.append(item)
        return result

    def cancel_pending(self, queue_id: int, requester_key: str) -> bool:
        """Remove only the caller's own pending request."""
        with self._lock:
            try:
                row = self._conn.execute(
                    "SELECT 1 FROM queue WHERE id = ? AND requester_key = ? "
                    "AND status = 'pending' LIMIT 1",
                    (int(queue_id), requester_key),
                ).fetchone()
                if row is None:
                    return False
                self._conn.execute(
                    "DELETE FROM queue_pins WHERE queue_id = ?", (int(queue_id),)
                )
                cur = self._conn.execute(
                    "DELETE FROM queue WHERE id = ? AND requester_key = ? "
                    "AND status = 'pending'",
                    (int(queue_id), requester_key),
                )
                self._conn.commit()
                return bool(cur.rowcount)
            except Exception:
                self._conn.rollback()
                raise

    def clear_pending(self) -> int:
        with self._lock:
            try:
                rows = self._conn.execute(
                    "SELECT id FROM queue WHERE status = 'pending'"
                ).fetchall()
                for row in rows:
                    self._conn.execute(
                        "DELETE FROM queue_pins WHERE queue_id = ?", (int(row["id"]),)
                    )
                cur = self._conn.execute("DELETE FROM queue WHERE status = 'pending'")
                self._conn.commit()
                return int(cur.rowcount or 0)
            except Exception:
                self._conn.rollback()
                raise

    def cleanup_old_data(self, retention_days: int = 90, now: int | None = None) -> None:
        cutoff = int(now if now is not None else now_ms()) - retention_days * 86_400_000
        with self._lock:
            try:
                self._conn.execute(
                    "DELETE FROM plays WHERE started_at < ?", (cutoff,)
                )
                self._conn.execute(
                    "DELETE FROM messages WHERE created_at < ?", (cutoff,)
                )
                self._conn.execute(
                    "DELETE FROM request_history WHERE created_at < ?", (cutoff,)
                )
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise

    def delete_queue_item(self, queue_id: int) -> None:
        with self._lock:
            try:
                self._conn.execute(
                    "DELETE FROM queue_pins WHERE queue_id = ?", (int(queue_id),)
                )
                self._conn.execute(
                    "DELETE FROM queue WHERE id = ?", (int(queue_id),)
                )
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise

    def cleanup_history(self) -> int:
        with self._lock:
            try:
                self._conn.execute(
                    "DELETE FROM queue_pins WHERE queue_id IN "
                    "(SELECT id FROM queue WHERE status IN ('played', 'rejected'))"
                )
                cur = self._conn.execute(
                    "DELETE FROM queue WHERE status IN ('played', 'rejected')"
                )
                self._conn.commit()
                return int(cur.rowcount or 0)
            except Exception:
                self._conn.rollback()
                raise

    def pin_queue(
        self, queue_id: int, voter_key: str, created_at: int | None = None
    ) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT id, priority FROM queue WHERE id = ? AND status = 'pending'",
                (int(queue_id),),
            ).fetchone()
            if row is None:
                return None
            cur = self._conn.execute(
                "INSERT OR IGNORE INTO queue_pins(queue_id, voter_key, created_at) "
                "VALUES(?, ?, ?)",
                (int(queue_id), voter_key, int(created_at or now_ms())),
            )
            pinned = bool(cur.rowcount)
            if pinned:
                self._conn.execute(
                    "UPDATE queue SET priority = priority + 1 WHERE id = ?",
                    (int(queue_id),),
                )
            self._conn.commit()
            priority = self._conn.execute(
                "SELECT priority FROM queue WHERE id = ?", (int(queue_id),)
            ).fetchone()
            return {
                "pins": int(priority["priority"]) if priority else 0,
                "pinned": pinned,
            }

    def toggle_queue_pin(
        self, queue_id: int, voter_key: str, created_at: int | None = None
    ) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT id, priority FROM queue WHERE id = ? AND status = 'pending'",
                (int(queue_id),),
            ).fetchone()
            if row is None:
                return None
            existing = self._conn.execute(
                "SELECT 1 FROM queue_pins WHERE queue_id = ? AND voter_key = ? LIMIT 1",
                (int(queue_id), voter_key),
            ).fetchone()
            if existing is not None:
                self._conn.execute(
                    "DELETE FROM queue_pins WHERE queue_id = ? AND voter_key = ?",
                    (int(queue_id), voter_key),
                )
                self._conn.execute(
                    "UPDATE queue SET priority = MAX(0, priority - 1) WHERE id = ?",
                    (int(queue_id),),
                )
                pinned = False
            else:
                self._conn.execute(
                    "INSERT INTO queue_pins(queue_id, voter_key, created_at) VALUES(?, ?, ?)",
                    (int(queue_id), voter_key, int(created_at or now_ms())),
                )
                self._conn.execute(
                    "UPDATE queue SET priority = priority + 1 WHERE id = ?",
                    (int(queue_id),),
                )
                pinned = True
            self._conn.commit()
            priority = self._conn.execute(
                "SELECT priority FROM queue WHERE id = ?", (int(queue_id),)
            ).fetchone()
            return {
                "pins": int(priority["priority"]) if priority else 0,
                "pinned": pinned,
            }

    def insert_message(
        self,
        ip_key: str | None,
        nickname: str | None,
        body: str | None,
        track_id: str | None,
        created_at: int | None = None,
    ) -> int:
        cur = self._execute(
            "INSERT INTO messages(ip_key, nickname, body, track_id, created_at) "
            "VALUES(?, ?, ?, ?, ?)",
            (ip_key, nickname, body, track_id, int(created_at or now_ms())),
        )
        return int(cur.lastrowid)

    def list_messages(self, limit: int = 20) -> list[dict[str, Any]]:
        rows = self._fetchall(
            "SELECT m.id, m.nickname, m.body, m.track_id, m.created_at, "
            "t.title AS track_title, t.artist AS track_artist "
            "FROM messages m LEFT JOIN tracks t ON t.id = m.track_id "
            "WHERE m.status = 'visible' ORDER BY m.id DESC LIMIT ?",
            (limit,),
        )
        return [dict(row) for row in rows]

    def hide_message(self, message_id: int) -> None:
        self._execute("UPDATE messages SET status = 'hidden' WHERE id = ?", (message_id,))

    def add_request_history(
        self,
        ip_key: str,
        track_id: str,
        nickname: str | None,
        created_at: int | None = None,
    ) -> int:
        cur = self._execute(
            "INSERT INTO request_history(ip_key, track_id, nickname, created_at) "
            "VALUES(?, ?, ?, ?) "
            "ON CONFLICT(ip_key, track_id) DO UPDATE SET "
            "nickname = excluded.nickname, created_at = excluded.created_at",
            (ip_key, track_id, nickname, int(created_at or now_ms())),
        )
        return int(cur.lastrowid or 0)

    def list_request_history(self, ip_key: str, limit: int = 30) -> list[dict[str, Any]]:
        rows = self._fetchall(
            "SELECT h.id, h.track_id, h.nickname, h.created_at, "
            "t.title, t.artist, t.album, t.duration_ms, t.cover_url, t.source, "
            "t.duration_estimated "
            "FROM request_history h JOIN tracks t ON t.id = h.track_id "
            "WHERE h.ip_key = ? ORDER BY h.created_at DESC, h.id DESC LIMIT ?",
            (ip_key, max(1, int(limit))),
        )
        return [dict(row) for row in rows]

    def list_recent_request_history(self, limit: int = 30) -> list[dict[str, Any]]:
        """Shared request history: every listener sees the same recent rows."""
        rows = self._fetchall(
            "SELECT h.id, h.track_id, h.nickname, h.created_at, "
            "t.title, t.artist, t.album, t.duration_ms, t.cover_url, t.source, "
            "t.duration_estimated "
            "FROM request_history h JOIN tracks t ON t.id = h.track_id "
            "ORDER BY h.created_at DESC, h.id DESC LIMIT ?",
            (max(1, int(limit)),),
        )
        return [dict(row) for row in rows]

    def add_rate_event(self, key: str, action: str, ts: int) -> None:
        self._execute(
            "INSERT INTO rate_events(key, action, ts) VALUES(?, ?, ?)",
            (key, action, int(ts)),
        )

    def rate_events_since(self, key: str, action: str, since_ms: int) -> list[int]:
        rows = self._fetchall(
            "SELECT ts FROM rate_events WHERE key = ? AND action = ? AND ts >= ? "
            "ORDER BY ts ASC",
            (key, action, int(since_ms)),
        )
        return [int(row["ts"]) for row in rows]

    def cleanup_rate_events(self, before_ms: int) -> None:
        self._execute("DELETE FROM rate_events WHERE ts < ?", (int(before_ms),))

    # ------------------------------------------------------------------
    # Temporary uploaded audio lifecycle
    # ------------------------------------------------------------------

    def list_upload_tracks(self) -> list[dict[str, Any]]:
        rows = self._fetchall(
            "SELECT * FROM tracks WHERE source = 'upload' ORDER BY created_at, id"
        )
        return [track for row in rows if (track := self._row_to_track(row)) is not None]

    def upload_track_has_active_queue(self, track_id: str) -> bool:
        row = self._fetchone(
            "SELECT 1 FROM queue WHERE track_id = ? "
            "AND status IN ('pending', 'playing') LIMIT 1",
            (track_id,),
        )
        return row is not None

    def upload_track_is_playing(self, track_id: str) -> bool:
        row = self._fetchone(
            "SELECT 1 FROM queue WHERE track_id = ? AND status = 'playing' LIMIT 1",
            (track_id,),
        )
        return row is not None

    def upload_last_ended_at(self, track_id: str) -> int | None:
        row = self._fetchone(
            "SELECT MAX(ended_at) AS ended_at FROM plays WHERE track_id = ?",
            (track_id,),
        )
        if row is None or row["ended_at"] is None:
            return None
        return int(row["ended_at"])

    def delete_upload_track(self, track_id: str, *, allow_pending: bool = False) -> bool:
        """Delete an expired uploaded track and its user-facing history.

        A playing track is never removed.  A pending track is protected unless
        the hard retention deadline explicitly allows it (the caller then also
        removes its queue row).  This is intentionally a single transaction so
        a half-deleted row cannot be returned by the API.
        """
        with self._lock:
            try:
                playing = self._conn.execute(
                    "SELECT 1 FROM queue WHERE track_id = ? "
                    "AND status = 'playing' LIMIT 1",
                    (track_id,),
                ).fetchone()
                if playing is not None:
                    return False
                pending = self._conn.execute(
                    "SELECT 1 FROM queue WHERE track_id = ? "
                    "AND status = 'pending' LIMIT 1",
                    (track_id,),
                ).fetchone()
                if pending is not None and not allow_pending:
                    return False
                self._conn.execute(
                    "DELETE FROM queue_pins WHERE queue_id IN "
                    "(SELECT id FROM queue WHERE track_id = ?)",
                    (track_id,),
                )
                self._conn.execute(
                    "DELETE FROM queue WHERE track_id = ?", (track_id,)
                )
                self._conn.execute(
                    "DELETE FROM request_history WHERE track_id = ?", (track_id,)
                )
                self._conn.execute(
                    "DELETE FROM plays WHERE track_id = ?", (track_id,)
                )
                cur = self._conn.execute(
                    "DELETE FROM tracks WHERE id = ? AND source = 'upload'",
                    (track_id,),
                )
                self._conn.commit()
                return bool(cur.rowcount)
            except Exception:
                self._conn.rollback()
                raise

    def start_play(self, track_id: str, queue_id: int | None, started_at: int) -> int:
        cur = self._execute(
            "INSERT INTO plays(track_id, queue_id, started_at) VALUES(?, ?, ?)",
            (track_id, queue_id, int(started_at)),
        )
        return int(cur.lastrowid)

    def end_play(self, play_id: int | None, ended_at: int) -> None:
        if play_id is None:
            return
        self._execute("UPDATE plays SET ended_at = ? WHERE id = ?", (int(ended_at), int(play_id)))
