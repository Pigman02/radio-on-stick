import json
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from app.app import build_app
from app.config import BASE_DIR, Config
from app.httpd import make_server
from app.stream import (
    ByteChunker,
    FRAME_HEADER_BYTES,
    FRAME_MAGIC,
    FORMAT_CODES,
    Mp3Chunker,
    StreamHub,
    StreamSession,
    StreamUnsupported,
    fnv1a,
    detect_audio_format,
    open_audio_source,
    open_mp3_source,
    parse_frame_header,
)

# MPEG-1 Layer III, 128 kbps, 44.1 kHz => 417 bytes per frame.
FRAME_HEADER = bytes.fromhex("fffb90c0")


def make_frame(size: int = 417, fill: int = 0) -> bytes:
    return FRAME_HEADER + bytes([fill]) * (size - 4)


def make_mp3(frames: int = 40) -> bytes:
    return b"ID3\x04\x00\x00\x00\x00\x00\x00" + b"".join(
        make_frame(fill=index & 0xFF) for index in range(frames)
    )


class RangeServer:
    def __init__(
        self, data: bytes, content_type: str = "audio/mpeg", honor_range: bool = True
    ) -> None:
        self.data = data
        self.content_type = content_type
        self.honor_range = honor_range
        self.request_count = 0
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), self._handler())
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.httpd.server_address[1]}/audio.mp3"

    def close(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(2)

    def _handler(self):
        payload = self.data
        content_type = self.content_type
        server = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_GET(self):
                server.request_count += 1
                start = 0
                header = self.headers.get("Range") or ""
                if server.honor_range and header.startswith("bytes="):
                    try:
                        start = int(header[6:].split("-")[0])
                    except ValueError:
                        start = 0
                if start >= len(payload):
                    self.send_response(416)
                    self.send_header("Content-Range", f"bytes */{len(payload)}")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                body = payload[start:]
                self.send_response(206 if start else 200)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Accept-Ranges", "bytes")
                if start:
                    self.send_header(
                        "Content-Range", f"bytes {start}-{len(payload) - 1}/{len(payload)}"
                    )
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        return Handler


class StreamParsingTests(unittest.TestCase):
    def test_parse_frame_header(self):
        info = parse_frame_header(FRAME_HEADER)
        self.assertIsNotNone(info)
        self.assertEqual(info.frame_len, 417)
        self.assertEqual(info.sample_rate, 44100)
        self.assertEqual(info.bitrate_kbps, 128)
        self.assertAlmostEqual(info.duration_ms, 26.122, places=2)

    def test_chunker_keeps_complete_frames(self):
        data = make_mp3(40)
        chunker = Mp3Chunker(1000)
        chunker.feed(data)
        chunks = []
        while True:
            chunk = chunker.pop_chunk()
            if chunk is None:
                break
            chunks.append(chunk)
        tail = chunker.pop_chunk(eof=True)
        if tail is not None:
            chunks.append(tail)
        self.assertGreaterEqual(len(chunks), 2)
        self.assertEqual(sum(len(chunk.payload) for chunk in chunks), 40 * 417)
        self.assertEqual(chunks[0].start_ms, 1000)
        for previous, current in zip(chunks, chunks[1:]):
            self.assertAlmostEqual(
                current.start_ms,
                previous.start_ms + previous.duration_ms,
                delta=2,
            )
        for chunk in chunks:
            self.assertEqual(len(chunk.payload) % 417, 0)
            self.assertEqual(chunk.payload[:4], FRAME_HEADER)

    def test_chunker_skips_id3_and_junk(self):
        data = b"junk" + make_mp3(8)
        chunker = Mp3Chunker(0)
        chunker.feed(data)
        chunk = chunker.pop_chunk(eof=True)
        self.assertIsNotNone(chunk)
        self.assertEqual(chunk.payload[:4], FRAME_HEADER)

    def test_frame_pack(self):
        chunker = Mp3Chunker(0)
        chunker.feed(make_mp3(2))
        chunk = chunker.pop_chunk(eof=True)
        self.assertIsNotNone(chunk)
        frame = chunk.frame(123)
        self.assertEqual(frame[:4], FRAME_MAGIC)
        self.assertEqual(len(frame), FRAME_HEADER_BYTES + len(chunk.payload))
        self.assertEqual(
            int.from_bytes(frame[32:36], "big"), fnv1a(chunk.payload)
        )


class UpstreamTests(unittest.TestCase):
    def setUp(self):
        self.server = RangeServer(make_mp3(40))
        self.temp = tempfile.TemporaryDirectory()
        self.config = Config(
            data_dir=Path(self.temp.name),
            db_path=Path(self.temp.name) / "radio.db",
            static_dir=BASE_DIR / "app" / "static",
            admin_token="test",
            transcode_mp3=False,
        )

    def tearDown(self):
        self.server.close()
        self.temp.cleanup()

    def test_open_mp3_source_at_offset(self):
        source = open_mp3_source(
            self.server.url,
            position_ms=1000,
            track={"source": "local"},
            user_agent="test",
            timeout=5,
        )
        try:
            data = source.read(4096)
            self.assertGreater(len(data), 0)
            self.assertGreaterEqual(data.find(FRAME_HEADER), 0)
        finally:
            source.close()

    def test_open_mp3_source_rejects_other_content(self):
        other = RangeServer(b"<html>not audio</html>", "text/html")
        try:
            with self.assertRaises(StreamUnsupported):
                open_mp3_source(
                    other.url,
                    position_ms=0,
                    track={},
                    user_agent="test",
                    timeout=5,
                )
        finally:
            other.close()

    def test_rejects_wav_like_pcm(self):
        wav = b"RIFF\x24\x00\x00\x00WAVEfmt " + bytes(range(256)) * 80
        other = RangeServer(wav, "audio/wav")
        try:
            with self.assertRaises(StreamUnsupported):
                open_mp3_source(
                    other.url,
                    position_ms=0,
                    track={},
                    user_agent="test",
                    timeout=5,
                )
        finally:
            other.close()

    def test_open_mp3_source_when_cdn_ignores_range(self):
        big = RangeServer(make_mp3(500), "audio/mpeg", honor_range=False)
        try:
            source = open_mp3_source(
                big.url,
                position_ms=9000,
                track={"source": "local"},
                user_agent="test",
                timeout=5,
            )
            try:
                data = source.read(8192)
                self.assertGreater(len(data), 0)
                self.assertGreaterEqual(data.find(FRAME_HEADER), 0)
            finally:
                source.close()
        finally:
            big.close()

    def test_session_shares_identical_chunks(self):
        started = int(time.time() * 1000)
        session = StreamSession(
            ("k",),
            self.server.url,
            {"source": "local"},
            position_ms=0,
            started_at_ms=started,
            duration_ms=40_000,
            user_agent="test",
            timeout=5,
        )
        session.start()
        try:
            manifest = session.manifest()
            self.assertEqual(manifest["format"], "mp3")
            self.assertEqual(manifest["header_bytes"], FRAME_HEADER_BYTES)
            first = session.get_chunk(0)
            same = session.get_chunk(0)
            self.assertEqual(first.payload, same.payload)
            self.assertEqual(first.server_ms, same.server_ms)
            self.assertEqual(len(first.frame(manifest["stream_id"])), FRAME_HEADER_BYTES + len(first.payload))
        finally:
            session.close()

    def test_hub_reuses_session_for_second_seed(self):
        now = {
            "server_time_ms": int(time.time() * 1000),
            "seq": 7,
            "audio_rev": 2,
            "paused": False,
            "position_ms": 0,
            "duration_ms": 40_000,
            "track": {"id": "t1"},
            "audio": {"direct": self.server.url},
        }
        hub = StreamHub(self.config)
        try:
            first = hub.manifest(now)
            second = hub.manifest(dict(now, server_time_ms=now["server_time_ms"] + 100))
            self.assertEqual(first["stream_id"], second["stream_id"])
            self.assertGreaterEqual(second["latest_seq"], first["first_seq"])
            self.assertEqual(self.server.request_count, 1)
            chunk = hub.get_chunk(first["stream_id"], first["first_seq"])
            self.assertTrue(chunk.payload)
        finally:
            hub.close()

    def test_hub_paused(self):
        hub = StreamHub(self.config)
        try:
            paused = hub.manifest(
                {
                    "server_time_ms": int(time.time() * 1000),
                    "seq": 1,
                    "audio_rev": 1,
                    "paused": True,
                    "position_ms": 100,
                    "duration_ms": 1000,
                    "track": {"id": "t1"},
                    "audio": {"direct": self.server.url},
                }
            )
            self.assertTrue(paused["paused"])
            self.assertIsNone(paused["stream_id"])
        finally:
            hub.close()

    def test_http_source_endpoints(self):
        app = build_app(self.config)
        now = {
            "server_time_ms": int(time.time() * 1000),
            "station": "test",
            "seq": 1,
            "audio_rev": 1,
            "paused": False,
            "position_ms": 0,
            "duration_ms": 40_000,
            "track": {
                "id": "t1",
                "source": "local",
                "title": "Test",
                "artist": "Tester",
                "duration_ms": 40_000,
            },
            "audio": {"direct": self.server.url, "alternates": []},
            "next": None,
            "queue": [],
        }
        app.player.snapshot = lambda ensure=True: dict(now)
        server = make_server(app, "127.0.0.1", 0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_address[1]}"
        try:
            with urllib.request.urlopen(base + "/source/manifest?seq=1", timeout=10) as response:
                manifest = json.loads(response.read())
            self.assertTrue(manifest["supported"])
            with urllib.request.urlopen(
                base
                + f"/source/chunk?stream_id={manifest['stream_id']}&seq={manifest['latest_seq']}",
                timeout=10,
            ) as response:
                body = response.read()
            self.assertEqual(body[:4], FRAME_MAGIC)
            self.assertEqual(len(body), FRAME_HEADER_BYTES + int(response.headers["X-Chunk-Bytes"]))
            try:
                urllib.request.urlopen(
                    base
                    + f"/source/chunk?stream_id={manifest['stream_id'] + 1}&seq={manifest['latest_seq']}",
                    timeout=10,
                )
                self.fail("unknown stream_id should not be 200")
            except urllib.error.HTTPError as exc:
                self.assertEqual(exc.code, 410)
            with urllib.request.urlopen(base + "/source/manifest?seq=999", timeout=10) as response:
                self.fail("stale manifest should not be 200")
        except urllib.error.HTTPError as exc:
            self.assertEqual(exc.code, 409)
        finally:
            server.shutdown()
            thread.join(2)
            server.server_close()
            app.db.close()


if __name__ == "__main__":
    unittest.main()


class FormatTests(unittest.TestCase):
    def test_detect_common_containers(self) -> None:
        self.assertEqual(detect_audio_format(b"RIFF\x00\x00\x00\x00WAVEfmt "), "wav")
        self.assertEqual(detect_audio_format(b"OggS\x00\x02" + b"\x00" * 20), "ogg")
        self.assertEqual(detect_audio_format(b"fLaC\x00\x00\x00\x22"), "flac")
        self.assertEqual(detect_audio_format(b"\x1aE\xdf\xa3\x00"), "webm")
        self.assertEqual(detect_audio_format(b"\x00\x00\x00\x18ftypM4A "), "m4a")
        adts = bytes.fromhex("fff150000c9ffc") + b"\x00" * 93
        self.assertEqual(detect_audio_format(adts * 3), "aac")
        self.assertEqual(detect_audio_format(make_mp3(3)), "mp3")
        self.assertIsNone(detect_audio_format(b"not audio at all"))

    def test_byte_chunker_timestamps_are_contiguous(self) -> None:
        chunker = ByteChunker(0, bytes_per_second=16_000)
        chunker.feed(b"x" * 16_000)
        first = chunker.pop_chunk()
        second_in_first = chunker.pop_chunk(eof=True)
        self.assertIsNotNone(first)
        self.assertIsNotNone(second_in_first)
        self.assertEqual(first.start_ms, 0)
        self.assertGreaterEqual(first.duration_ms, 490)
        self.assertGreaterEqual(
            second_in_first.start_ms, first.start_ms + first.duration_ms - 2
        )
        chunker.feed(b"y" * 16_000)
        second = chunker.pop_chunk(eof=True)
        self.assertIsNotNone(second)
        self.assertGreaterEqual(second.start_ms, second_in_first.start_ms + second_in_first.duration_ms - 2)

    def test_local_wav_source_and_midstream_rejection(self) -> None:
        pcm = b"\x00\x00" * 8000
        wav = (
            b"RIFF"
            + (36 + len(pcm)).to_bytes(4, "little")
            + b"WAVEfmt "
            + (16).to_bytes(4, "little")
            + (1).to_bytes(2, "little")
            + (1).to_bytes(2, "little")
            + (8000).to_bytes(4, "little")
            + (16000).to_bytes(4, "little")
            + (2).to_bytes(2, "little")
            + (16).to_bytes(2, "little")
            + b"data"
            + len(pcm).to_bytes(4, "little")
            + pcm
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "demo.wav"
            path.write_bytes(wav)
            opened = open_audio_source(
                "/media/uploads/demo.wav",
                position_ms=0,
                track={"source": "upload"},
                user_agent="test",
                timeout=5,
                local_path=path,
            )
            try:
                self.assertEqual(opened.format_name, "wav")
                self.assertEqual(opened.mime, "audio/wav")
                self.assertEqual(opened.read(4), b"RIFF")
            finally:
                opened.close()
            with self.assertRaises(StreamUnsupported):
                open_audio_source(
                    "/media/uploads/demo.wav",
                    position_ms=5000,
                    track={"source": "upload"},
                    user_agent="test",
                    timeout=5,
                    local_path=path,
                )

    def test_local_mp3_source_seek(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "demo.mp3"
            path.write_bytes(make_mp3(40))
            opened = open_audio_source(
                "/media/uploads/demo.mp3",
                position_ms=1000,
                track={"source": "upload"},
                user_agent="test",
                timeout=5,
                local_path=path,
            )
            try:
                self.assertEqual(opened.format_name, "mp3")
                self.assertEqual(opened.info.version, 3)
                opened.chunker.feed(opened.read(64 * 1024))
                chunk = opened.chunker.pop_chunk(eof=True)
                self.assertIsNotNone(chunk)
                self.assertEqual(chunk.payload[:4], FRAME_HEADER)
                self.assertGreaterEqual(chunk.start_ms, 1000)
            finally:
                opened.close()
