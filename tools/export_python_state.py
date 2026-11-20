#!/usr/bin/env python3
"""Export the Python station's SQLite state into Go station JSON.

Usage:
  python3 tools/export_python_state.py --db data/radio.db --out data/station-go-state.json

It keeps the Go station's current track, queue, requests, history and messages
so the Go station can be tested as a drop-in replacement for the Python one.
Local upload paths use the Python uploads directory; remote tracks keep their
server-side audio URL.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

SETTING_UI = {
    "ui.station_name": "station_name",
    "ui.subtitle": "subtitle",
    "ui.page_title": "page_title",
    "ui.notice": "notice",
    "ui.background_interval_sec": "background_interval_sec",
}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default="data/radio.db")
    parser.add_argument("--out", default="data/station-go-state.json")
    parser.add_argument("--uploads", default="data/uploads")
    args = parser.parse_args()
    db_path = Path(args.db)
    if not db_path.is_file():
        raise SystemExit(f"database not found: {db_path}")
    db = sqlite3.connect(db_path)
    db.row_factory = sqlite3.Row
    settings = {row["key"]: row["value"] for row in db.execute("SELECT key, value FROM settings")}
    tracks = {row["id"]: dict(row) for row in db.execute("SELECT * FROM tracks WHERE enabled = 1")}

    def track(row: dict) -> dict:
        source = str(row.get("source") or "")
        audio = str(row.get("audio_primary") or "")
        path = ""
        if source == "upload" and audio.startswith("/media/uploads/"):
            name = audio[len("/media/uploads/"):]
            if name and "/" not in name and "\\" not in name:
                path = str(Path(args.uploads) / name)
        return {
            "id": row["id"], "title": row.get("title") or "", "artist": row.get("artist") or "",
            "album": row.get("album") or "", "duration_ms": int(row.get("duration_ms") or 0),
            "format": "mp3", "url": "" if path else audio, "path": path,
            "source": source, "source_id": row.get("source_id") or "",
            "cover_url": row.get("cover_url") or "",
        }

    current_id = settings.get("player.current_track_id") or ""
    current_row = tracks.get(current_id)
    queue_rows = [row for row in tracks.values()]
    queue_rows.sort(key=lambda row: int(row.get("created_at") or 0))
    queue = [track(row) for row in queue_rows if row["id"] != current_id]

    requests = []
    for row in db.execute("SELECT id, track_id, nickname, status, priority, created_at FROM queue WHERE status = 'pending' ORDER BY priority DESC, id ASC"):
        source = tracks.get(row["track_id"])
        if not source:
            continue
        item = track(source)
        requests.append({
            "queue_id": int(row["id"]), "track_id": source["id"], "title": source.get("title") or "",
            "artist": source.get("artist") or "", "cover_url": source.get("cover_url") or "",
            "nickname": row["nickname"] or "", "status": "pending",
            "created_at": int(row["created_at"] or 0), "pin_count": int(row["priority"] or 0),
            "duration_ms": int(source.get("duration_ms") or 0),
            "duration_estimated": not bool(source.get("duration_ms")),
        })

    history = []
    for row in db.execute("SELECT track_id, nickname, created_at FROM request_history ORDER BY created_at DESC LIMIT 200"):
        source = tracks.get(row["track_id"])
        if not source:
            continue
        history.append({
            "id": f"h{len(history) + 1}", "track_id": source["id"],
            "title": source.get("title") or "", "artist": source.get("artist") or "",
            "cover_url": source.get("cover_url") or "", "nickname": row["nickname"] or "",
            "created_at": int(row["created_at"] or 0),
            "duration_ms": int(source.get("duration_ms") or 0),
            "duration_estimated": not bool(source.get("duration_ms")),
            "source": source.get("source") or "", "source_id": source.get("source_id") or "",
            "url": source.get("audio_primary") or "",
        })

    messages = []
    for row in db.execute("SELECT id, nickname, body, created_at FROM messages WHERE status = 'visible' ORDER BY created_at DESC LIMIT 200"):
        messages.append({
            "id": f"m{row['id']}", "nickname": row["nickname"] or "匿名",
            "body": row["body"] or "", "created_at": int(row["created_at"] or 0),
        })

    values = {key: settings.get(source, "") for source, key in SETTING_UI.items()}
    try:
        values["background_interval_sec"] = int(values.get("background_interval_sec") or 300)
    except ValueError:
        values["background_interval_sec"] = 300
    payload = {
        "settings": values,
        "policy": {},
        "history": history,
        "messages": messages,
        "requests": requests,
        "current": track(current_row) if current_row else None,
        "queue": queue,
        "started_at_ms": int(settings.get("player.started_at_ms") or 0),
        "paused": settings.get("player.paused") == "1",
        "paused_position_ms": int(settings.get("player.paused_position_ms") or 0),
        "seq": int(settings.get("player.seq") or 0),
        "audio_rev": int(settings.get("player.audio_rev") or 0),
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"out": str(out), "tracks": len(tracks), "queue": len(queue), "history": len(history), "messages": len(messages)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
