from __future__ import annotations

import json
import os
import shutil
import subprocess
import uuid
from pathlib import Path
from typing import Any

from .ffmpeg import resolve_ffmpeg


_TAG_KEYS = {
    "title": ("title", "TIT2", "TITLE"),
    "artist": ("artist", "TPE1", "ARTIST"),
    "album": ("album", "TALB", "ALBUM"),
    "lyrics": (
        "lyrics",
        "unsyncedlyrics",
        "unsynced_lyrics",
        "syncedlyrics",
        "USLT",
        "LYRICS",
    ),
}


def resolve_ffprobe(ffmpeg_path: str = "ffmpeg") -> str:
    """Find ffprobe next to the configured ffmpeg, then on PATH."""
    ffmpeg = resolve_ffmpeg(ffmpeg_path)
    candidates: list[Path] = []
    if ffmpeg:
        base = Path(ffmpeg)
        sibling = base.with_name("ffprobe")
        candidates.extend([sibling, sibling.with_suffix(base.suffix)])
    found = shutil.which("ffprobe")
    if found:
        candidates.append(Path(found))
    for candidate in candidates:
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return ""


def _run_json(command: list[str], timeout: float = 12.0) -> dict[str, Any]:
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            check=False,
            timeout=timeout,
            text=True,
        )
    except (OSError, subprocess.TimeoutExpired):
        return {}
    if result.returncode != 0 or not result.stdout:
        return {}
    try:
        data = json.loads(result.stdout)
    except (TypeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _decode_id3_text(payload: bytes, encoding: int) -> str:
    if encoding == 0:
        return payload.decode("latin1", "replace").strip("\x00").strip()
    if encoding == 3:
        return payload.decode("utf-8", "replace").strip("\x00").strip()
    if encoding == 1:
        return payload.decode("utf-16", "replace").strip("\x00").strip()
    return payload.decode("utf-16-be", "replace").strip("\x00").strip()


def _syncsafe(value: bytes) -> int:
    return (
        ((value[0] & 0x7F) << 21)
        | ((value[1] & 0x7F) << 14)
        | ((value[2] & 0x7F) << 7)
        | (value[3] & 0x7F)
    )


def _parse_id3(path: Path) -> dict[str, Any]:
    """Small ID3v2 fallback for tags, USLT lyrics and APIC art."""
    result: dict[str, Any] = {}
    try:
        with path.open("rb") as handle:
            header = handle.read(10)
            if len(header) < 10 or header[:3] != b"ID3":
                return result
            version = header[3]
            size = _syncsafe(header[6:10])
            data = handle.read(size)
    except OSError:
        return result
    position = 0
    frame_size_bytes = 4
    while position + 10 <= len(data):
        frame_id = data[position : position + 4]
        if not frame_id.strip(b"\x00"):
            break
        if version == 4:
            frame_size = _syncsafe(data[position + 4 : position + 8])
        else:
            frame_size = int.from_bytes(data[position + 4 : position + 8], "big")
        if frame_size <= 0 or position + 10 + frame_size > len(data):
            break
        body = data[position + 10 : position + 10 + frame_size]
        try:
            if frame_id == b"TIT2":
                result["title"] = _decode_id3_text(body[1:], body[0])
            elif frame_id == b"TPE1":
                result["artist"] = _decode_id3_text(body[1:], body[0])
            elif frame_id == b"TALB":
                result["album"] = _decode_id3_text(body[1:], body[0])
            elif frame_id == b"USLT":
                result["lyrics"] = _decode_id3_text(body[4:], body[0])
            elif frame_id == b"APIC" and "cover_bytes" not in result:
                encoding = body[0]
                rest = body[1:]
                mime_end = rest.find(b"\x00")
                if mime_end > 0:
                    mime = rest[:mime_end].decode("latin1", "replace").lower()
                    rest = rest[mime_end + 1 :]
                    if rest:
                        rest = rest[1:]  # picture type
                        if encoding in (1, 2):
                            # UTF-16 description: find a double NUL on an even boundary.
                            end = 0
                            while end + 1 < len(rest):
                                if rest[end : end + 2] == b"\x00\x00":
                                    end += 2
                                    break
                                end += 2
                            rest = rest[end:]
                        else:
                            end = rest.find(b"\x00")
                            if end >= 0:
                                rest = rest[end + 1 :]
                        if rest:
                            result["cover_bytes"] = rest
                            result["cover_mime"] = mime
        except (IndexError, UnicodeDecodeError):
            pass
        position += 10 + frame_size
        # ID3v2.2 uses 6-byte headers; not needed for modern files.
    return result


def _find_tag(tags: dict[str, Any], names: tuple[str, ...]) -> str:
    lowered = {str(key).lower(): str(value or "").strip() for key, value in tags.items()}
    for name in names:
        value = lowered.get(name.lower(), "")
        if value:
            return value
    return ""


def _extract_cover(
    ffmpeg_path: str,
    source: Path,
    stream_index: int,
    uploads_dir: Path,
) -> tuple[str, str]:
    executable = resolve_ffmpeg(ffmpeg_path)
    if not executable:
        return "", ""
    name = f"{uuid.uuid4().hex}.jpg"
    target = uploads_dir / name
    try:
        result = subprocess.run(
            [
                executable,
                "-hide_banner",
                "-loglevel",
                "error",
                "-nostdin",
                "-y",
                "-i",
                str(source),
                "-map",
                f"0:{stream_index}",
                "-frames:v",
                "1",
                "-c:v",
                "mjpeg",
                "-f",
                "image2",
                str(target),
            ],
            capture_output=True,
            timeout=15,
            check=False,
        )
        if result.returncode == 0 and target.is_file() and target.stat().st_size > 0:
            return name, f"/media/uploads/{name}"
    except (OSError, subprocess.TimeoutExpired):
        pass
    try:
        target.unlink(missing_ok=True)
    except OSError:
        pass
    return "", ""


def _write_embedded_cover(
    cover_bytes: bytes,
    mime: str,
    uploads_dir: Path,
) -> tuple[str, str]:
    if not cover_bytes:
        return "", ""
    suffix = ".png" if "png" in mime else ".jpg"
    name = f"{uuid.uuid4().hex}{suffix}"
    target = uploads_dir / name
    try:
        target.write_bytes(cover_bytes)
    except OSError:
        return "", ""
    return name, f"/media/uploads/{name}"


def probe_uploaded_audio(
    path: Path,
    *,
    ffmpeg_path: str = "ffmpeg",
    uploads_dir: Path,
) -> dict[str, Any]:
    """Read-only metadata probe; cover extraction writes only a new sidecar."""
    result: dict[str, Any] = {
        "title": "",
        "artist": "",
        "album": "",
        "duration_ms": 0,
        "lyrics": "",
        "cover_name": "",
        "cover_url": "",
    }
    ffprobe = resolve_ffprobe(ffmpeg_path)
    if ffprobe:
        data = _run_json(
            [
                ffprobe,
                "-v",
                "error",
                "-show_format",
                "-show_streams",
                "-of",
                "json",
                str(path),
            ]
        )
        fmt = data.get("format") if isinstance(data.get("format"), dict) else {}
        tags = fmt.get("tags") if isinstance(fmt.get("tags"), dict) else {}
        for stream in data.get("streams") or []:
            if isinstance(stream, dict) and isinstance(stream.get("tags"), dict):
                tags = {**stream["tags"], **tags}
        result["title"] = _find_tag(tags, _TAG_KEYS["title"])
        result["artist"] = _find_tag(tags, _TAG_KEYS["artist"])
        result["album"] = _find_tag(tags, _TAG_KEYS["album"])
        result["lyrics"] = _find_tag(tags, _TAG_KEYS["lyrics"])
        try:
            result["duration_ms"] = int(float(fmt.get("duration") or 0) * 1000)
        except (TypeError, ValueError):
            pass
        for stream in data.get("streams") or []:
            if not isinstance(stream, dict):
                continue
            disposition = stream.get("disposition") or {}
            if stream.get("codec_type") == "video" and disposition.get("attached_pic"):
                cover_name, cover_url = _extract_cover(
                    ffmpeg_path,
                    path,
                    int(stream.get("index", 0)),
                    uploads_dir,
                )
                if cover_name:
                    result["cover_name"] = cover_name
                    result["cover_url"] = cover_url
                break
    if not result["cover_name"] or not result["lyrics"]:
        fallback = _parse_id3(path)
        for key in ("title", "artist", "album", "lyrics"):
            if fallback.get(key) and not result.get(key):
                result[key] = fallback[key]
        if fallback.get("cover_bytes") and not result.get("cover_name"):
            name, url = _write_embedded_cover(
                fallback["cover_bytes"],
                str(fallback.get("cover_mime") or "image/jpeg"),
                uploads_dir,
            )
            if name:
                result["cover_name"] = name
                result["cover_url"] = url
    # Only timestamped LRC is useful to the current lyrics parser.
    lyrics = str(result.get("lyrics") or "").strip().replace("\\n", "\n")
    if lyrics and "[" not in lyrics:
        result["lyrics"] = ""
    else:
        result["lyrics"] = lyrics
    return result
