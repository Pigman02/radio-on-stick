from __future__ import annotations

import logging
import secrets
import socket
import struct
import subprocess
import threading
import time
import urllib.error
import urllib.request
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .ffmpeg import resolve_ffmpeg

logger = logging.getLogger(__name__)

# DataChannel frame:
#   4s magic | I stream | I seq | I payload_len | I start_ms | I duration_ms
#   | d server_ms | I fnv1a | B format | 3x reserved
FRAME_MAGIC = b"RAD4"
FORMAT_CODES = {
    "mp3": 1,
    "aac": 2,
    "m4a": 3,
    "ogg": 4,
    "webm": 5,
    "wav": 6,
    "flac": 7,
}
FORMAT_MIMES = {
    "mp3": "audio/mpeg",
    "aac": "audio/aac",
    "m4a": 'audio/mp4; codecs="mp4a.40.2"',
    "ogg": "audio/ogg",
    "webm": "audio/webm; codecs=opus",
    "wav": "audio/wav",
    "flac": "audio/flac",
}
FRAME_HEADER = struct.Struct(">4sIIIIIdIB3x")
FRAME_HEADER_BYTES = FRAME_HEADER.size
CHUNK_MS = 500
MAX_CHUNK_BYTES = 48 * 1024
MIN_CHUNK_MS = 240
RING_CHUNKS = 20
PREFETCH_MS = 2000
IDLE_SEC = 35.0
# Container formats need their initialization header.  Starting a server-side
# stream a few hundred milliseconds after the track began is harmless (the
# client catches up); jumping into the middle would produce an undecodable
# fragment, so those listeners use native direct playback instead.
NON_MP3_START_GRACE_MS = 1500
START_TIMEOUT_SEC = 7.0
CHUNK_WAIT_SEC = 8.0


class StreamError(RuntimeError):
    pass


class StreamUnavailable(StreamError):
    pass


class StreamUnsupported(StreamError):
    pass


class StreamStale(StreamError):
    pass


class StreamGone(StreamError):
    pass


class StreamEnded(StreamError):
    pass


class StreamTimeout(StreamError):
    pass


def fnv1a(data: bytes) -> int:
    value = 0x811C9DC5
    for byte in data:
        value ^= byte
        value = (value * 0x01000193) & 0xFFFFFFFF
    return value


@dataclass
class Chunk:
    seq: int
    payload: bytes
    start_ms: int
    duration_ms: int
    server_ms: float
    format_name: str = "mp3"

    def frame(self, stream_id: int, format_name: str | None = None) -> bytes:
        format_name = format_name or self.format_name
        checksum = fnv1a(self.payload)
        header = FRAME_HEADER.pack(
            FRAME_MAGIC,
            stream_id & 0xFFFFFFFF,
            self.seq & 0xFFFFFFFF,
            len(self.payload),
            self.start_ms & 0xFFFFFFFF,
            self.duration_ms & 0xFFFFFFFF,
            float(self.server_ms),
            checksum,
            FORMAT_CODES.get(format_name, 1),
        )
        return header + self.payload


# ---------------------------------------------------------------------------
# MP3 frame parsing.  This only reads frame headers so chunks end on frame
# boundaries; audio bytes are never decoded or re-encoded.
# ---------------------------------------------------------------------------

_SAMPLE_RATES = {
    3: (44100, 48000, 32000),  # MPEG 1
    2: (22050, 24000, 16000),  # MPEG 2
    0: (11025, 12000, 8000),   # MPEG 2.5
}

_BITRATES = {
    (1, 1): (0, 32, 64, 96, 128, 160, 192, 224, 256, 288, 320, 352, 384, 416, 448),
    (1, 2): (0, 32, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320, 384),
    (1, 3): (0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320),
    (2, 1): (0, 32, 48, 56, 64, 80, 96, 112, 128, 144, 160, 176, 192, 224, 256),
    (2, 2): (0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160),
    (2, 3): (0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160),
}


@dataclass(frozen=True)
class FrameInfo:
    frame_len: int
    sample_rate: int
    duration_ms: float
    bitrate_kbps: int
    version: int
    layer: int


def parse_frame_header(data: bytes, offset: int = 0) -> FrameInfo | None:
    if len(data) - offset < 4:
        return None
    h = int.from_bytes(data[offset : offset + 4], "big")
    if (h & 0xFFE00000) != 0xFFE00000:
        return None
    version_bits = (h >> 19) & 0x3
    layer_bits = (h >> 17) & 0x3
    if version_bits == 1 or layer_bits == 0:
        return None
    bitrate_index = (h >> 12) & 0xF
    sample_index = (h >> 10) & 0x3
    padding = (h >> 9) & 0x1
    if bitrate_index == 0 or bitrate_index == 15 or sample_index == 3:
        return None
    rates = _SAMPLE_RATES.get(version_bits)
    if not rates:
        return None
    sample_rate = rates[sample_index]
    layer = 4 - layer_bits  # 3 -> Layer I, 2 -> Layer II, 1 -> Layer III
    table_key = (1 if version_bits == 3 else 2, layer)
    table = _BITRATES.get(table_key)
    if not table:
        return None
    bitrate = table[bitrate_index] * 1000
    if bitrate <= 0:
        return None
    if layer == 1:
        frame_len = (12 * bitrate // sample_rate + padding) * 4
        samples = 384
    elif layer == 2:
        frame_len = 144 * bitrate // sample_rate + padding
        samples = 1152
    else:
        factor = 144 if version_bits == 3 else 72
        frame_len = factor * bitrate // sample_rate + padding
        samples = 1152 if version_bits == 3 else 576
    if frame_len < 24 or frame_len > 4096:
        return None
    return FrameInfo(
        frame_len=frame_len,
        sample_rate=sample_rate,
        duration_ms=samples * 1000.0 / sample_rate,
        bitrate_kbps=bitrate // 1000,
        version=version_bits,
        layer=layer,
    )


def _find_frame(data: bytearray) -> int:
    limit = len(data) - 3
    for index in range(max(0, limit)):
        if data[index] != 0xFF:
            continue
        if parse_frame_header(data, index) is not None:
            return index
    return -1


def _looks_like_mp3(data: bytes | bytearray, min_frames: int = 3) -> tuple[int, FrameInfo] | None:
    """Require several consecutive frames so PCM/WAV noise is not mistaken for MP3."""
    limit = len(data) - 3
    for index in range(max(0, limit)):
        if data[index] != 0xFF:
            continue
        first = parse_frame_header(data, index)
        if first is None:
            continue
        signature = (first.version, first.layer, first.sample_rate)
        position = index
        count = 0
        while count < min_frames and position + 4 <= len(data):
            current = parse_frame_header(data, position)
            if (
                current is None
                or (current.version, current.layer, current.sample_rate) != signature
                or position + current.frame_len > len(data)
            ):
                break
            position += current.frame_len
            count += 1
        if count >= min_frames:
            return index, first
    return None


def _id3v2_size(data: bytearray) -> int:
    if len(data) < 10 or data[:3] != b"ID3":
        return 0
    size = (
        ((data[6] & 0x7F) << 21)
        | ((data[7] & 0x7F) << 14)
        | ((data[8] & 0x7F) << 7)
        | (data[9] & 0x7F)
    )
    return max(0, 10 + size)


class Mp3Chunker:
    """Turn an MP3 byte stream into chunks ending on frame boundaries."""

    def __init__(self, start_ms: int = 0, chunk_ms: int = CHUNK_MS) -> None:
        self.buffer = bytearray()
        self.start_ms = max(0, int(start_ms))
        self.chunk_ms = max(200, int(chunk_ms))
        self.elapsed_ms = 0
        self.chunk_start_ms = self.start_ms
        self.chunk_duration_ms = 0.0
        self.current = bytearray()
        self.started = False
        self._skip_id3 = True

    def feed(self, data: bytes) -> None:
        if data:
            self.buffer.extend(data)

    def pop_chunk(self, eof: bool = False) -> Chunk | None:
        while True:
            if not self.started and self._skip_id3:
                if len(self.buffer) < 10:
                    if eof:
                        if self.current:
                            break
                        return None
                    return None
                if self.buffer[:3] == b"ID3":
                    size = _id3v2_size(self.buffer)
                    if size <= 0:
                        self._skip_id3 = False
                    elif len(self.buffer) < size:
                        if eof:
                            self.buffer.clear()
                            self._skip_id3 = False
                        else:
                            return None
                    else:
                        del self.buffer[:size]
                        self._skip_id3 = False
                        continue
                else:
                    self._skip_id3 = False

            index = _find_frame(self.buffer)
            if index < 0:
                if eof:
                    if self.current:
                        return self._emit()
                    return None
                # Keep the last byte, since a sync word can span reads.
                if len(self.buffer) > 1:
                    del self.buffer[:-1]
                return None
            if index > 0:
                del self.buffer[:index]
            if len(self.buffer) < 4:
                return None
            info = parse_frame_header(self.buffer)
            if info is None:
                del self.buffer[0]
                continue
            if len(self.buffer) < info.frame_len:
                return None
            frame = bytes(self.buffer[: info.frame_len])
            del self.buffer[: info.frame_len]
            if not self.started:
                self.started = True
                self.chunk_start_ms = self.start_ms
            self.started = True
            self.current.extend(frame)
            self.chunk_duration_ms += info.duration_ms
            self.elapsed_ms += info.duration_ms
            if (
                self.chunk_duration_ms >= self.chunk_ms
                or len(self.current) >= MAX_CHUNK_BYTES
                or self.chunk_duration_ms >= MIN_CHUNK_MS
                and len(self.current) >= MAX_CHUNK_BYTES
            ):
                chunk = self._emit()
                if chunk is not None:
                    return chunk
                continue
            if eof and not self.buffer:
                return self._emit()

    def _emit(self) -> Chunk | None:
        if not self.current:
            return None
        payload = bytes(self.current)
        start_ms = int(round(self.chunk_start_ms))
        duration_ms = max(1, int(round(self.chunk_duration_ms)))
        chunk = Chunk(
            seq=0,
            payload=payload,
            start_ms=start_ms,
            duration_ms=duration_ms,
            server_ms=0.0,
        )
        self.current.clear()
        self.chunk_start_ms = self.start_ms + self.elapsed_ms
        self.chunk_duration_ms = 0.0
        return chunk


# ---------------------------------------------------------------------------
# Upstream reader.  It only reads bytes; it never writes audio to disk.
# ---------------------------------------------------------------------------


@dataclass
class OpenedSource:
    response: Any
    prefix: bytes
    info: FrameInfo | None
    content_type: str
    total_bytes: int | None
    format_name: str = "mp3"
    mime: str = "audio/mpeg"
    chunker: Any = None

    def read(self, size: int) -> bytes:
        if self.prefix:
            data = self.prefix[:size]
            self.prefix = self.prefix[size:]
            return data
        return self.response.read(size)

    def close(self) -> None:
        try:
            self.response.close()
        except Exception:
            pass


class ByteChunker:
    """Byte chunks for container formats without a cheap frame index.

    Bytes are never decoded.  Timestamps are estimated from the stream's
    bytes-per-second so the same timeline still works for every listener;
    formats with a frame header get a dedicated parser below.
    """

    def __init__(
        self,
        start_ms: int = 0,
        *,
        bytes_per_second: float,
        chunk_ms: int = CHUNK_MS,
        max_chunk_bytes: int = MAX_CHUNK_BYTES,
    ) -> None:
        self.buffer = bytearray()
        self.start_ms = max(0, int(start_ms))
        self.bytes_done = 0
        self.bytes_per_second = max(1000.0, float(bytes_per_second))
        self.target_bytes = max(
            4096,
            min(max_chunk_bytes, int(self.bytes_per_second * max(200, chunk_ms) / 1000)),
        )

    def feed(self, data: bytes) -> None:
        if data:
            self.buffer.extend(data)

    def pop_chunk(self, eof: bool = False) -> Chunk | None:
        if len(self.buffer) < self.target_bytes and not eof:
            return None
        if not self.buffer:
            return None
        size = min(len(self.buffer), self.target_bytes)
        payload = bytes(self.buffer[:size])
        del self.buffer[:size]
        start_ms = int(
            round(self.start_ms + self.bytes_done * 1000.0 / self.bytes_per_second)
        )
        duration_ms = max(
            1, int(round(len(payload) * 1000.0 / self.bytes_per_second))
        )
        self.bytes_done += len(payload)
        return Chunk(
            seq=0,
            payload=payload,
            start_ms=start_ms,
            duration_ms=duration_ms,
            server_ms=0.0,
        )


def _adts_info(data: bytes, offset: int = 0) -> tuple[int, int, int] | None:
    """Return (frame_len, sample_rate, duration_ms) for AAC ADTS."""
    if len(data) - offset < 7:
        return None
    header = data[offset : offset + 7]
    if header[0] != 0xFF or (header[1] & 0xF6) != 0xF0:
        return None
    sample_index = (header[2] >> 2) & 0x0F
    rates = (96000, 88200, 64000, 48000, 44100, 32000, 24000, 22050,
             16000, 12000, 11025, 8000, 7350)
    if sample_index >= len(rates):
        return None
    frame_len = ((header[3] & 0x03) << 11) | (header[4] << 3) | (header[5] >> 5)
    if frame_len < 7 or frame_len > 8192:
        return None
    sample_rate = rates[sample_index]
    return frame_len, sample_rate, int(round(1024 * 1000.0 / sample_rate))


def _looks_like_adts(data: bytes | bytearray, min_frames: int = 3) -> bool:
    position = 0
    count = 0
    while count < min_frames and position + 7 <= len(data):
        current = _adts_info(data, position)
        if current is None:
            return False
        frame_len, _sample_rate, _duration = current
        if position + frame_len > len(data):
            return False
        position += frame_len
        count += 1
    return count >= min_frames


def _wav_byte_rate(data: bytes | bytearray) -> float | None:
    if len(data) < 12 or data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        return None
    position = 12
    while position + 8 <= len(data):
        chunk_id = bytes(data[position : position + 4])
        size = int.from_bytes(data[position + 4 : position + 8], "little")
        body = position + 8
        if chunk_id == b"fmt " and body + 16 <= len(data):
            byte_rate = int.from_bytes(data[body + 8 : body + 12], "little")
            if byte_rate > 0:
                return float(byte_rate)
        position = body + max(0, size)
        if size % 2:
            position += 1
    return None


def detect_audio_format(
    data: bytes | bytearray,
    *,
    content_type: str = "",
    url: str = "",
) -> str | None:
    prefix = bytes(data[:128 * 1024])
    if prefix[:4] == b"RIFF" and prefix[8:12] == b"WAVE":
        return "wav"
    if prefix[:4] == b"OggS":
        return "ogg"
    if prefix[:4] == b"\x1aE\xdf\xa3":
        return "webm"
    if prefix[:4] == b"fLaC":
        return "flac"
    if len(prefix) >= 12 and prefix[4:8] == b"ftyp":
        return "m4a"
    if prefix[:3] == b"ID3" or _looks_like_mp3(prefix):
        return "mp3"
    if _looks_like_adts(prefix):
        return "aac"

    hint = (content_type or "").split(";")[0].strip().lower()
    hints = {
        "audio/mpeg": "mp3",
        "audio/mp3": "mp3",
        "audio/aac": "aac",
        "audio/aacp": "aac",
        "audio/mp4": "m4a",
        "audio/x-m4a": "m4a",
        "audio/m4a": "m4a",
        "audio/ogg": "ogg",
        "application/ogg": "ogg",
        "audio/opus": "ogg",
        "audio/webm": "webm",
        "video/webm": "webm",
        "audio/wav": "wav",
        "audio/x-wav": "wav",
        "audio/wave": "wav",
        "audio/flac": "flac",
        "audio/x-flac": "flac",
    }
    if hint in hints:
        return hints[hint]
    path = urllib.parse.urlsplit(str(url or "")).path.lower()
    extensions = {
        ".mp3": "mp3",
        ".aac": "aac",
        ".m4a": "m4a",
        ".mp4": "m4a",
        ".ogg": "ogg",
        ".oga": "ogg",
        ".opus": "ogg",
        ".webm": "webm",
        ".wav": "wav",
        ".flac": "flac",
    }
    for suffix, name in extensions.items():
        if path.endswith(suffix):
            return name
    return None


def _bytes_per_second(
    *,
    format_name: str,
    info: FrameInfo | None,
    total_bytes: int | None,
    duration_ms: int,
    prefix: bytes,
) -> float:
    if format_name == "mp3" and info is not None:
        return max(1000.0, info.bitrate_kbps * 1000.0 / 8.0)
    if format_name == "wav":
        rate = _wav_byte_rate(prefix)
        if rate:
            return rate
    if total_bytes and duration_ms > 0:
        return max(1000.0, total_bytes * 1000.0 / duration_ms)
    return 16_000.0


def _chunker_for(
    format_name: str,
    start_ms: int,
    *,
    bytes_per_second: float,
) -> Any:
    if format_name == "mp3":
        return Mp3Chunker(start_ms, CHUNK_MS)
    return ByteChunker(start_ms, bytes_per_second=bytes_per_second, chunk_ms=CHUNK_MS)


class TranscodedSource:
    """Stream ffmpeg MP3 output from an audio source.

    ffmpeg is the only external runtime dependency.  It is used for non-MP3
    input by default and can also be enabled for MP3 to cap server egress.
    """

    def __init__(self, process: subprocess.Popen[bytes], start_ms: int) -> None:
        self.process = process
        self.stdout = process.stdout
        self.format_name = "mp3"
        self.mime = FORMAT_MIMES["mp3"]
        self.content_type = FORMAT_MIMES["mp3"]
        self.total_bytes = None
        self.info = None
        self.prefix = b""
        self.chunker = Mp3Chunker(start_ms, CHUNK_MS)
        self._closed = False
        self._stderr_lines: list[str] = []
        self._stderr_thread = threading.Thread(
            target=self._drain_stderr, name="radio-ffmpeg-stderr", daemon=True
        )
        self._stderr_thread.start()

    def _drain_stderr(self) -> None:
        if self.process.stderr is None:
            return
        try:
            for raw in self.process.stderr:
                line = raw.decode("utf-8", "replace").strip()
                if line:
                    self._stderr_lines.append(line)
                    if len(self._stderr_lines) > 20:
                        self._stderr_lines.pop(0)
        except (OSError, ValueError):
            pass

    def read(self, size: int) -> bytes:
        if self.stdout is None:
            return b""
        try:
            return self.stdout.read(size)
        except (OSError, ValueError):
            return b""

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self.process.poll() is None:
            try:
                self.process.terminate()
                self.process.wait(timeout=2)
            except (OSError, subprocess.TimeoutExpired):
                try:
                    self.process.kill()
                    self.process.wait(timeout=2)
                except (OSError, subprocess.TimeoutExpired):
                    pass
        for stream in (self.stdout, self.process.stderr):
            try:
                if stream is not None:
                    stream.close()
            except OSError:
                pass


def _headers_for(track: dict[str, Any], user_agent: str) -> dict[str, str]:
    headers = {
        "User-Agent": user_agent,
        "Accept": "*/*",
        "Accept-Encoding": "identity",
        "Connection": "close",
    }
    source = str(track.get("source") or "")
    if source == "qq":
        headers["Referer"] = "https://music.qq.com/"
        headers["Origin"] = "https://music.qq.com"
    elif source == "netease":
        headers["Referer"] = "https://music.163.com/"
    elif source == "bilibili":
        headers["Referer"] = "https://www.bilibili.com/"
        headers["Origin"] = "https://www.bilibili.com"
    return headers


def open_transcoded_source(
    url: str,
    *,
    position_ms: int,
    track: dict[str, Any],
    user_agent: str,
    local_path: Path | None = None,
    ffmpeg_path: str = "ffmpeg",
    bitrate_kbps: int = 128,
) -> TranscodedSource:
    if not resolve_ffmpeg(ffmpeg_path):
        raise StreamUnsupported("服务器未安装 ffmpeg，无法转码音频")
    command = [
        ffmpeg_path,
        "-hide_banner",
        "-loglevel",
        "error",
        "-nostdin",
    ]
    if position_ms > 0:
        command.extend(["-ss", f"{position_ms / 1000.0:.3f}"])
    if local_path is not None:
        command.extend(["-i", str(local_path)])
    else:
        header_blob = "".join(
            f"{key}: {value}\r\n"
            for key, value in _headers_for(track, user_agent).items()
            if key not in {"Connection", "Accept-Encoding"}
        )
        if header_blob:
            command.extend(["-headers", header_blob])
        command.extend(["-i", url])
    command.extend(
        [
            "-vn",
            "-map",
            "0:a:0",
            "-c:a",
            "libmp3lame",
            "-b:a",
            f"{max(32, int(bitrate_kbps))}k",
            "-f",
            "mp3",
            "pipe:1",
        ]
    )
    try:
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
        )
    except OSError as exc:
        raise StreamUnavailable(f"ffmpeg 无法启动: {exc}") from exc
    return TranscodedSource(process, max(0, int(position_ms)))


def _open_url(url: str, headers: dict[str, str], timeout: float, range_start: int | None):
    request_headers = dict(headers)
    if range_start is not None and range_start > 0:
        request_headers["Range"] = f"bytes={range_start}-"
    request = urllib.request.Request(url, headers=request_headers, method="GET")
    return urllib.request.urlopen(request, timeout=timeout)


def open_audio_source(
    url: str,
    *,
    position_ms: int,
    track: dict[str, Any],
    user_agent: str,
    timeout: float,
    duration_ms: int = 0,
    local_path: Any = None,
) -> OpenedSource:
    """Open a remote or uploaded audio source and choose a chunker.

    This is the probe/passthrough layer.  StreamSession turns non-MP3 input
    (and optionally MP3 input) into a low-bitrate MP3 stream through ffmpeg.
    """
    if local_path is not None:
        path = Path(local_path)
        try:
            response = path.open("rb")
            prefix = response.read(128 * 1024)
        except OSError as exc:
            raise StreamUnavailable(f"本地上传音频无法打开: {exc}") from exc
        try:
            total_bytes = path.stat().st_size
            content_type = ""
            format_name = detect_audio_format(
                prefix, content_type=content_type, url=str(url)
            )
            if format_name is None:
                raise StreamUnsupported("无法识别的音频格式")
            if position_ms > NON_MP3_START_GRACE_MS and format_name != "mp3":
                raise StreamUnsupported("非 MP3 格式只支持从头播放")
            chunk_start_ms = position_ms if format_name == "mp3" else 0
            if format_name == "mp3":
                sniffed = _looks_like_mp3(prefix)
                if sniffed is None:
                    raise StreamUnsupported("不是可识别的 MP3 流")
                info = sniffed[1]
            else:
                info = None
            bps = _bytes_per_second(
                format_name=format_name,
                info=info,
                total_bytes=total_bytes,
                duration_ms=duration_ms,
                prefix=prefix,
            )
            if format_name == "mp3" and position_ms > 0:
                # Local files can be seeked precisely to a byte estimate; the
                # MP3 chunker then skips any partial frame at that offset.
                offset = int(position_ms * bps / 1000)
                response.seek(min(offset, total_bytes))
                prefix = b""
            return OpenedSource(
                response=response,
                prefix=prefix,
                info=info,
                content_type="",
                total_bytes=total_bytes,
                format_name=format_name,
                mime=FORMAT_MIMES[format_name],
                chunker=_chunker_for(format_name, chunk_start_ms, bytes_per_second=bps),
            )
        except Exception:
            response.close()
            raise

    headers = _headers_for(track, user_agent)
    try:
        first = _open_url(url, headers, timeout, None)
    except (urllib.error.URLError, socket.timeout, TimeoutError, OSError) as exc:
        raise StreamUnavailable(f"上游音频无法打开: {exc}") from exc
    try:
        prefix = first.read(128 * 1024)
        content_type = str(first.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        total = first.headers.get("Content-Length")
        total_bytes = int(total) if total and total.isdigit() else None
        format_name = detect_audio_format(
            prefix, content_type=content_type, url=url
        )
        if format_name is None:
            raise StreamUnsupported("无法识别的音频格式")
        if format_name == "mp3":
            sniffed = _looks_like_mp3(prefix)
            if sniffed is None:
                raise StreamUnsupported("不是可识别的 MP3 流")
            info = sniffed[1]
        else:
            info = None
        if format_name != "mp3" and position_ms > NON_MP3_START_GRACE_MS:
            raise StreamUnsupported("非 MP3 格式只支持从头播放")
        chunk_start_ms = position_ms if format_name == "mp3" else 0
        bps = _bytes_per_second(
            format_name=format_name,
            info=info,
            total_bytes=total_bytes,
            duration_ms=duration_ms,
            prefix=prefix,
        )
        start_offset = 0
        if position_ms > 0:
            if info is not None:
                start_offset = int(position_ms * info.bitrate_kbps * 1000 / 8 / 1000)
            else:
                start_offset = int(position_ms * bps / 1000)
        chunker = _chunker_for(format_name, chunk_start_ms, bytes_per_second=bps)

        if start_offset <= 0:
            return OpenedSource(
                response=first,
                prefix=prefix,
                info=info,
                content_type=content_type or FORMAT_MIMES[format_name],
                total_bytes=total_bytes,
                format_name=format_name,
                mime=FORMAT_MIMES[format_name],
                chunker=chunker,
            )
        if start_offset <= len(prefix):
            return OpenedSource(
                response=first,
                prefix=prefix[start_offset:],
                info=info,
                content_type=content_type or FORMAT_MIMES[format_name],
                total_bytes=total_bytes,
                format_name=format_name,
                mime=FORMAT_MIMES[format_name],
                chunker=chunker,
            )

        first.close()
        try:
            response = _open_url(url, headers, timeout, start_offset)
        except urllib.error.HTTPError as exc:
            if exc.code == 416:
                response = _open_url(url, headers, timeout, None)
                remaining = start_offset
                while remaining > 0:
                    block = response.read(min(64 * 1024, remaining))
                    if not block:
                        break
                    remaining -= len(block)
            elif exc.code in {400, 403, 405, 411}:
                response = _open_url(url, headers, timeout, None)
            else:
                raise
        status = int(getattr(response, "status", 200))
        if status == 200:
            remaining = start_offset
            while remaining > 0:
                block = response.read(min(64 * 1024, remaining))
                if not block:
                    break
                remaining -= len(block)
        return OpenedSource(
            response=response,
            prefix=b"",
            info=info,
            content_type=content_type or FORMAT_MIMES[format_name],
            total_bytes=total_bytes,
            format_name=format_name,
            mime=FORMAT_MIMES[format_name],
            chunker=chunker,
        )
    except Exception:
        try:
            first.close()
        except Exception:
            pass
        raise


def open_mp3_source(
    url: str,
    *,
    position_ms: int,
    track: dict[str, Any],
    user_agent: str,
    timeout: float,
    duration_ms: int = 0,
) -> OpenedSource:
    """Backward-compatible MP3-only wrapper used by the stream tests."""
    opened = open_audio_source(
        url,
        position_ms=position_ms,
        track=track,
        user_agent=user_agent,
        timeout=timeout,
        duration_ms=duration_ms,
    )
    if opened.format_name != "mp3":
        opened.close()
        raise StreamUnsupported("不是可识别的 MP3 流")
    return opened


# ---------------------------------------------------------------------------
# Shared stream session.  Two seeds read the same ring of chunks, so they
# produce exactly the same bytes and timestamps.
# ---------------------------------------------------------------------------


class StreamSession:
    def __init__(
        self,
        key: tuple[Any, ...],
        url: str,
        track: dict[str, Any],
        *,
        position_ms: int,
        started_at_ms: int,
        duration_ms: int,
        user_agent: str,
        timeout: float,
        local_path: Path | None = None,
        ffmpeg_path: str = "ffmpeg",
        transcode_bitrate_kbps: int = 128,
        transcode_mp3: bool = True,
        mp3_only_remote: bool = False,
    ) -> None:
        self.key = key
        self.local_path = local_path
        self.url = url
        self.track = track
        self.position_ms = max(0, int(position_ms))
        self.started_at_ms = int(started_at_ms)
        self.duration_ms = max(1000, int(duration_ms))
        self.user_agent = user_agent
        self.timeout = timeout
        self.ffmpeg_path = ffmpeg_path
        self.transcode_bitrate_kbps = max(32, int(transcode_bitrate_kbps))
        self.transcode_mp3 = bool(transcode_mp3)
        self.mp3_only_remote = bool(mp3_only_remote)
        self.stream_id = secrets.randbits(32) or 1
        self.chunk_ms = CHUNK_MS
        self.format_name = "mp3"
        self.mime = FORMAT_MIMES["mp3"]
        self.ring: deque[Chunk] = deque()
        self.next_seq = 0
        # `started_at_ms` is the snapshot time supplied by the player; the
        # upstream may take a moment to open.  Anchor media timestamps to
        # the moment the session actually starts producing so clients do not
        # inherit the upstream-open latency as a permanent sync offset.
        self.next_server_ms = float(int(time.time() * 1000))
        self.eof = False
        self.last_served_seq = -1
        self.error: str | None = None
        self.unsupported = False
        self.closed = False
        self.created_at = time.time()
        self.last_request = self.created_at
        self._cond = threading.Condition()
        self._thread: threading.Thread | None = None
        self._source: Any = None
        self._chunker: Any = None

    def start(self) -> None:
        with self._cond:
            if self._thread is not None:
                return
            self._thread = threading.Thread(
                target=self._run,
                name=f"radio-source-{self.stream_id:x}",
                daemon=True,
            )
            self._thread.start()

    def _append(self, chunk: Chunk) -> bool:
        with self._cond:
            # Emit roughly two seconds ahead of the media clock.  Without
            # this the reader races through a static file, the ring turns
            # over, and late clients receive non-contiguous audio.
            while not self.closed:
                if time.time() - self.last_request > IDLE_SEC:
                    self.closed = True
                    self._cond.notify_all()
                    return False
                due = self.next_server_ms - PREFETCH_MS
                remaining = (due - time.time() * 1000) / 1000.0
                if remaining <= 0:
                    break
                self._cond.wait(min(remaining, 0.25))
            while len(self.ring) >= RING_CHUNKS and not self.closed:
                # A client that just joined at the live edge has already
                # acknowledged the old edge; those chunks can never be asked
                # for again, so reclaim them instead of deadlocking the
                # producer behind a full ring.
                while self.ring and self.ring[0].seq < self.last_served_seq:
                    self.ring.popleft()
                if len(self.ring) < RING_CHUNKS:
                    break
                self._cond.wait(0.5)
            if self.closed:
                return False
            chunk.seq = self.next_seq
            chunk.server_ms = self.next_server_ms
            chunk.format_name = self.format_name
            self.next_seq += 1
            self.next_server_ms += max(1, chunk.duration_ms)
            self.ring.append(chunk)
            self._cond.notify_all()
            return True

    def _open_source(self) -> Any:
        # Bilibili DASH is a known non-MP3 container.  Probing it with a
        # 128 KiB read can block for many seconds on the CDN's first byte;
        # ffmpeg can open and seek the same URL directly, so skip the probe.
        if self.local_path is None and str(self.track.get("source") or "") == "bilibili":
            if self.mp3_only_remote:
                raise StreamUnsupported("管理员只允许 MP3 第三方音源")
            if not resolve_ffmpeg(self.ffmpeg_path):
                raise StreamUnsupported("服务器未安装 ffmpeg，无法转码哔哩哔哩音频")
            return open_transcoded_source(
                self.url,
                position_ms=self.position_ms,
                track=self.track,
                user_agent=self.user_agent,
                ffmpeg_path=self.ffmpeg_path,
                bitrate_kbps=self.transcode_bitrate_kbps,
            )
        try:
            source = open_audio_source(
                self.url,
                position_ms=self.position_ms,
                track=self.track,
                user_agent=self.user_agent,
                timeout=self.timeout,
                duration_ms=self.duration_ms,
                local_path=self.local_path,
            )
        except StreamUnsupported:
            # Non-MP3 containers cannot be entered in the middle.  Probe the
            # beginning, then let ffmpeg seek and emit a clean MP3 stream.
            if self.position_ms <= NON_MP3_START_GRACE_MS:
                raise
            probe = open_audio_source(
                self.url,
                position_ms=0,
                track=self.track,
                user_agent=self.user_agent,
                timeout=self.timeout,
                duration_ms=self.duration_ms,
                local_path=self.local_path,
            )
            try:
                if self.mp3_only_remote and self.local_path is None and probe.format_name != "mp3":
                    raise StreamUnsupported("管理员只允许 MP3 第三方音源")
                if probe.format_name == "mp3":
                    raise
            finally:
                probe.close()
            return open_transcoded_source(
                self.url,
                position_ms=self.position_ms,
                track=self.track,
                user_agent=self.user_agent,
                local_path=self.local_path,
                ffmpeg_path=self.ffmpeg_path,
                bitrate_kbps=self.transcode_bitrate_kbps,
            )
        if (
            self.mp3_only_remote
            and self.local_path is None
            and source.format_name != "mp3"
        ):
            source.close()
            raise StreamUnsupported("管理员只允许 MP3 第三方音源")
        transcode_available = bool(resolve_ffmpeg(self.ffmpeg_path))
        if source.format_name == "mp3" and (
            not self.transcode_mp3 or not transcode_available
        ):
            return source
        # MP3 and non-MP3 audio are normalized to a low-bitrate MP3 stream
        # when ffmpeg is available.  This keeps server egress below the
        # original bitrate (important for HTTP leaves and seeds) and keeps one
        # MSE-compatible timeline for every listener.
        source.close()
        return open_transcoded_source(
            self.url,
            position_ms=self.position_ms,
            track=self.track,
            user_agent=self.user_agent,
            local_path=self.local_path,
            ffmpeg_path=self.ffmpeg_path,
            bitrate_kbps=self.transcode_bitrate_kbps,
        )

    def _run(self) -> None:
        try:
            source = self._open_source()
            self._source = source
            self.format_name = source.format_name
            self.mime = source.mime
            self._chunker = source.chunker
            with self._cond:
                # Measure the media clock from the true upstream-open point.
                self.next_server_ms = float(int(time.time() * 1000))
            while not self.closed:
                chunk = self._chunker.pop_chunk()
                if chunk is None:
                    data = source.read(64 * 1024)
                    if not data:
                        while True:
                            chunk = self._chunker.pop_chunk(eof=True)
                            if chunk is None:
                                break
                            if not self._append(chunk):
                                break
                        with self._cond:
                            self.eof = True
                            self._cond.notify_all()
                        break
                    self._chunker.feed(data)
                    continue
                if not self._append(chunk):
                    break
        except StreamUnsupported as exc:
            with self._cond:
                self.unsupported = True
                self.error = str(exc)
                self.eof = True
                self._cond.notify_all()
        except Exception as exc:
            logger.warning("source stream failed: %s", exc)
            with self._cond:
                self.error = str(exc) or exc.__class__.__name__
                self.eof = True
                self._cond.notify_all()
        finally:
            if self._source is not None:
                self._source.close()
            with self._cond:
                self.eof = True
                self._cond.notify_all()

    def manifest(self) -> dict[str, Any]:
        deadline = time.monotonic() + START_TIMEOUT_SEC
        with self._cond:
            self.last_request = time.time()
            while not self.ring and not self.eof and self.error is None and not self.closed:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise StreamTimeout("等待音频首块超时")
                self._cond.wait(min(remaining, 0.5))
            if self.unsupported:
                raise StreamUnsupported(self.error or "不支持的音频格式")
            if not self.ring and self.error:
                raise StreamUnavailable(self.error)
            if not self.ring and self.eof:
                raise StreamEnded("音频流已结束")
            first = self.ring[0].seq if self.ring else self.next_seq
            latest = self.next_seq - 1
            return {
                "ok": True,
                "supported": True,
                "stream_id": self.stream_id,
                "format": self.format_name,
                "mime": self.mime,
                "header_bytes": FRAME_HEADER_BYTES,
                "chunk_ms": self.chunk_ms,
                "first_seq": first,
                "latest_seq": latest,
                "from_seq": max(first, latest - 3),
                "start_position_ms": self.position_ms,
                "started_at_ms": self.started_at_ms,
                "server_time_ms": int(time.time() * 1000),
                "duration_ms": self.duration_ms,
                "paused": False,
                "eof": self.eof,
            }

    def get_chunk(self, seq: int, timeout: float = CHUNK_WAIT_SEC) -> Chunk:
        deadline = time.monotonic() + timeout
        with self._cond:
            self.last_request = time.time()
            while True:
                if self.unsupported:
                    raise StreamUnsupported(self.error or "不支持的音频格式")
                if self.ring and seq < self.ring[0].seq:
                    raise StreamGone(f"块 {seq} 已被丢弃")
                for chunk in self.ring:
                    if chunk.seq == seq:
                        self.last_served_seq = max(self.last_served_seq, seq)
                        self._cond.notify_all()
                        return chunk
                if seq < self.next_seq:
                    raise StreamGone(f"块 {seq} 不存在")
                if self.eof:
                    raise StreamEnded("音频流已结束")
                if self.error:
                    raise StreamUnavailable(self.error)
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise StreamTimeout(f"等待块 {seq} 超时")
                self._cond.wait(min(remaining, 0.5))

    def close(self) -> None:
        with self._cond:
            self.closed = True
            self._cond.notify_all()
        if self._source is not None:
            self._source.close()


class StreamHub:
    def __init__(self, config: Any, policy_provider: Any = None) -> None:
        self.config = config
        self.policy_provider = policy_provider
        self._lock = threading.Lock()
        self._session: StreamSession | None = None
        self._key: tuple[Any, ...] | None = None

    @staticmethod
    def _track_key(now: dict[str, Any]) -> tuple[Any, ...] | None:
        track = now.get("track") or {}
        audio = now.get("audio") or {}
        # The public "direct" field always points back at this server; the
        # producer uses the internal upstream URL so third-party audio is
        # fetched server-side only.
        url = str(audio.get("upstream") or audio.get("direct") or "").strip()
        if not track or not url:
            return None
        if not (
            url.startswith(("http://", "https://"))
            or url.startswith("/media/uploads/")
        ):
            return None
        return (
            now.get("seq"),
            track.get("id"),
            now.get("audio_rev"),
            url,
        )

    def manifest(self, now: dict[str, Any], requested_seq: int | None = None) -> dict[str, Any]:
        if not now or not now.get("track"):
            self.close()
            raise StreamUnavailable("当前没有播放曲目")
        if requested_seq is not None and int(now.get("seq") or 0) != int(requested_seq):
            raise StreamStale("曲目已切换")
        key = self._track_key(now)
        if key is None:
            self.close()
            raise StreamUnavailable("当前没有可用音频")
        if now.get("paused"):
            self.close()
            return {
                "ok": True,
                "supported": True,
                "paused": True,
                "stream_id": None,
                "format": "mp3",
                "mime": FORMAT_MIMES["mp3"],
                "header_bytes": FRAME_HEADER_BYTES,
                "chunk_ms": CHUNK_MS,
                "first_seq": 0,
                "latest_seq": -1,
                "from_seq": 0,
                "start_position_ms": int(now.get("position_ms") or 0),
                "started_at_ms": int(now.get("server_time_ms") or 0),
                "server_time_ms": int(time.time() * 1000),
                "duration_ms": int(now.get("duration_ms") or 0),
                "eof": False,
            }
        policy = {}
        if self.policy_provider is not None:
            try:
                policy = self.policy_provider() or {}
            except Exception:
                policy = {}
        with self._lock:
            if self._session is None or self._key != key or self._session.closed:
                if self._session is not None:
                    self._session.close()
                track = now.get("track") or {}
                audio = now.get("audio") or {}
                url = str(audio.get("upstream") or audio.get("direct") or "")
                local_path = None
                if url.startswith("/media/uploads/"):
                    name = url[len("/media/uploads/") :]
                    if name and "/" not in name and "\\" not in name and name not in {".", ".."}:
                        uploads = getattr(self.config, "uploads_dir", None)
                        if uploads is None:
                            uploads = Path(self.config.data_dir) / "uploads"
                        local_path = Path(uploads) / name
                self._session = StreamSession(
                    key,
                    url,
                    dict(track),
                    position_ms=int(now.get("position_ms") or 0),
                    started_at_ms=int(now.get("server_time_ms") or time.time() * 1000),
                    duration_ms=int(now.get("duration_ms") or 0),
                    user_agent=self.config.user_agent,
                    timeout=max(3.0, float(self.config.upstream_timeout)),
                    local_path=local_path,
                    ffmpeg_path=getattr(self.config, "ffmpeg_path", "ffmpeg"),
                    transcode_bitrate_kbps=getattr(
                        self.config, "transcode_bitrate_kbps", 128
                    ),
                    transcode_mp3=getattr(self.config, "transcode_mp3", True),
                    mp3_only_remote=bool(policy.get("mp3_remote_only")),
                )
                self._key = key
                self._session.start()
            session = self._session
        return session.manifest()

    def get_chunk(self, stream_id: int, seq: int) -> Chunk:
        with self._lock:
            session = self._session
        if session is None or session.stream_id != int(stream_id):
            raise StreamGone("音频流已更换，请重新获取 manifest")
        return session.get_chunk(int(seq))

    def close(self) -> None:
        with self._lock:
            session = self._session
            self._session = None
            self._key = None
        if session is not None:
            session.close()
