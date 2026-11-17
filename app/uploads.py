from __future__ import annotations

import logging
import re
import time
from pathlib import Path
from typing import Any

from .db import now_ms

logger = logging.getLogger(__name__)

# A file may be created by /api/upload a few milliseconds before its DB row.
# Never treat such a fresh orphan as leftover garbage.
ORPHAN_GRACE_SEC = 3600
# Only files created by /api/upload use this name shape.  Anything else in the
# temporary directory is treated as foreign and is never removed.
SAFE_UPLOAD_NAME = re.compile(r"^[0-9a-f]{32}\.[a-z0-9]{1,8}$")


def is_safe_upload_name(name: str) -> bool:
    return bool(SAFE_UPLOAD_NAME.fullmatch(str(name or "")))


def _cover_filename(track: dict[str, Any]) -> str:
    meta = track.get("meta_json") or {}
    if not isinstance(meta, dict):
        return ""
    name = str(meta.get("cover_name") or "")
    return name if is_safe_upload_name(name) else ""


def _upload_filename(track: dict[str, Any]) -> str:
    name = str(track.get("source_id") or "").strip()
    if name:
        if is_safe_upload_name(name):
            return name
        return ""
    url = str(track.get("audio_primary") or "")
    prefix = "/media/uploads/"
    if url.startswith(prefix):
        candidate = url[len(prefix) :]
        if candidate and "/" not in candidate and "\\" not in candidate:
            return candidate
    return ""


def cleanup_uploads(
    app: Any,
    *,
    now: int | None = None,
) -> dict[str, int]:
    """Remove temporary uploads after playback and uploader departure.

    Policy:
    - never touch the current track or a track with a pending/playing queue row;
    - remove a one-shot upload after it ended and its uploader has been
      offline for the configured grace period;
    - remove any upload older than RADIO_UPLOAD_RETENTION_SEC, including
      abandoned uploads that never played;
    - remove unreferenced files only after a long orphan grace period.

    Returns counters for logging/tests; the operation is idempotent.
    """
    current_ms = int(now if now is not None else now_ms())
    retention_ms = max(60, int(app.config.upload_retention_sec)) * 1000
    grace_ms = max(0, int(app.config.upload_offline_grace_sec)) * 1000
    delete_after_play = bool(
        getattr(app.config, "upload_delete_after_play", True)
    )
    current_track = str(app.db.get_setting("player.current_track_id") or "")
    uploads_dir: Path = app.config.uploads_dir
    tracks = app.db.list_upload_tracks()
    referenced: set[str] = set()
    removed_tracks = 0
    removed_files = 0
    errors = 0

    for track in tracks:
        track_id = str(track.get("id") or "")
        filename = _upload_filename(track)
        if filename:
            referenced.add(filename)
        cover_name = _cover_filename(track)
        if cover_name:
            referenced.add(cover_name)
        if not track_id:
            continue
        try:
            if track_id == current_track:
                continue
            active_queue = app.db.upload_track_has_active_queue(track_id)
            meta = track.get("meta_json") or {}
            if not isinstance(meta, dict):
                meta = {}
            uploader_key = str(meta.get("uploader_key") or "")
            created_at = int(track.get("created_at") or 0)
            age = max(0, current_ms - created_at)
            last_ended = app.db.upload_last_ended_at(track_id)
            ended = last_ended is not None and last_ended > 0
            uploader_online = bool(
                uploader_key and app.listeners.is_active(uploader_key)
            )
            expired_by_age = age >= retention_ms
            finished_and_offline = bool(
                ended
                and not uploader_online
                and current_ms - int(last_ended) >= grace_ms
            )
            # A re-added upload can be part of the rotation.  Let it survive
            # until the hard retention deadline rather than breaking a future
            # playlist slot, but do not keep one-shot uploads forever.
            if track.get("enabled") and not expired_by_age:
                continue
            if active_queue:
                # Queued audio is not garbage until the hard deadline.  At the
                # deadline it is allowed to remove a still-pending item, but a
                # playing item remains protected by delete_upload_track().
                if not expired_by_age:
                    continue
                if app.db.upload_track_is_playing(track_id):
                    continue
            immediate_after_play = bool(
                delete_after_play and ended and not active_queue
            )
            if not (expired_by_age or finished_and_offline or immediate_after_play):
                continue

            if not app.db.delete_upload_track(
                track_id, allow_pending=expired_by_age
            ):
                continue
            removed_tracks += 1
            if filename:
                try:
                    (uploads_dir / filename).unlink(missing_ok=True)
                    removed_files += 1
                except OSError as exc:
                    logger.warning("could not remove upload %s: %s", filename, exc)
            if cover_name:
                try:
                    (uploads_dir / cover_name).unlink(missing_ok=True)
                    removed_files += 1
                except OSError as exc:
                    logger.warning("could not remove cover %s: %s", cover_name, exc)
            logger.info(
                "removed uploaded track %s (%s, age=%dms, ended=%s)",
                track_id,
                filename or "-",
                age,
                ended,
            )
        except Exception:
            errors += 1
            logger.exception("upload cleanup failed for track %s", track_id)

    # Clean files left behind by a crash or a failed DB transaction.  The
    # one-hour grace avoids racing a request that is still between writing a
    # file and inserting its track row.
    if uploads_dir.is_dir():
        now_sec = time.time()
        try:
            candidates = list(uploads_dir.iterdir())
        except OSError:
            candidates = []
        for path in candidates:
            if not path.is_file() or path.name in referenced:
                continue
            if not is_safe_upload_name(path.name):
                logger.warning("leaving foreign file in uploads dir untouched: %s", path.name)
                continue
            try:
                if now_sec - path.stat().st_mtime < ORPHAN_GRACE_SEC:
                    continue
                path.unlink()
                removed_files += 1
                logger.info("removed orphan upload %s", path.name)
            except OSError as exc:
                errors += 1
                logger.warning("could not remove orphan upload %s: %s", path.name, exc)

    return {
        "tracks": removed_tracks,
        "files": removed_files,
        "errors": errors,
    }
