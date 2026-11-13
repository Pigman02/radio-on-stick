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

CREATE TABLE IF NOT EXISTS plays (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    track_id TEXT NOT NULL,
    queue_id INTEGER,
    started_at INTEGER NOT NULL,
    ended_at INTEGER
);

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
            cur = self._conn.execute(sql, tuple(params))
            self._conn.commit()
            return cur

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
            for key, value in values.items():
                self._conn.execute(
                    "INSERT INTO settings(key, value) VALUES(?, ?) "
                    "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                    (key, str(value)),
                )
            self._conn.commit()

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

    def delete_track(self, track_id: str) -> None:
        self._execute("DELETE FROM tracks WHERE id = ?", (track_id,))

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

    def clear_pending(self) -> int:
        cur = self._execute("UPDATE queue SET status = 'rejected' WHERE status = 'pending'")
        return int(cur.rowcount or 0)

    def cleanup_history(self) -> int:
        with self._lock:
            self._conn.execute(
                "DELETE FROM queue_pins WHERE queue_id IN "
                "(SELECT id FROM queue WHERE status IN ('played', 'rejected'))"
            )
            cur = self._conn.execute(
                "DELETE FROM queue WHERE status IN ('played', 'rejected')"
            )
            self._conn.commit()
            return int(cur.rowcount or 0)

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
