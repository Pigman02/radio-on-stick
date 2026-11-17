from __future__ import annotations

import json
import struct
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from app.app import build_app
from app.config import BASE_DIR, Config
from app.db import now_ms
from app.httpd import make_server
from app.stream import FRAME_HEADER_BYTES, FRAME_MAGIC, FORMAT_CODES


class FakeAggregator:
    def __init__(self, db) -> None:
        self.db = db

    def search(self, query, limit=None):
        return []

    def refresh_audio(self, track, force=False):
        return track

    def ensure_lyrics(self, track):
        return ("", "")


def make_wav(seconds: float = 1.0, rate: int = 8000) -> bytes:
    frames = int(seconds * rate)
    pcm = b"\x00\x00" * frames
    return (
        b"RIFF"
        + struct.pack("<I", 36 + len(pcm))
        + b"WAVEfmt "
        + struct.pack("<IHHIIHH", 16, 1, 1, rate, rate * 2, 2, 16)
        + b"data"
        + struct.pack("<I", len(pcm))
        + pcm
    )


class UploadTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        data_dir = Path(self.tmp.name)
        self.config = Config(
            data_dir=data_dir,
            db_path=data_dir / "upload.db",
            static_dir=BASE_DIR / "app" / "static",
            admin_token="secret",
            request_per_min=100,
            message_per_min=100,
            queue_max=5,
            duplicate_window_sec=3600,
            search_per_min=100,
            min_form_ms=0,
            ip_hash_salt="upload-test",
            default_duration_ms=10_000,
        )
        self.app = build_app(self.config)
        self.fake = FakeAggregator(self.app.db)
        self.app.aggregator = self.fake
        self.app.player.aggregator = self.fake
        self.server = make_server(self.app, "127.0.0.1", 0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.tmp.cleanup()

    def upload(self, data: bytes, filename: str, content_type: str = "audio/wav", **headers):
        request = urllib.request.Request(
            self.base + "/api/upload",
            data=data,
            method="POST",
            headers={
                "Content-Type": content_type,
                "X-Filename": filename,
                "X-Title": "Uploaded Song",
                "X-Nickname": "Uploader",
                "X-Duration-Ms": "1000",
                **headers,
            },
        )
        with urllib.request.urlopen(request, timeout=15) as response:
            return response.status, json.loads(response.read())

    def test_upload_wav_and_range_media(self) -> None:
        data = make_wav()
        status, body = self.upload(data, "demo.wav")
        self.assertEqual(status, 201)
        track = body["track"]
        self.assertEqual(track["source"], "upload")
        self.assertEqual(track["title"], "Uploaded Song")
        self.assertEqual(body["queue"]["pending"], 1)
        stored = next(self.config.uploads_dir.iterdir())
        self.assertEqual(stored.read_bytes(), data)

        request = urllib.request.Request(
            self.base + f"/media/uploads/{stored.name}",
            headers={"Range": "bytes=2-9"},
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            self.assertEqual(response.status, 206)
            self.assertEqual(response.headers["Content-Range"], f"bytes 2-9/{len(data)}")
            self.assertEqual(response.read(), data[2:10])
        with urllib.request.urlopen(
            self.base + f"/media/uploads/{stored.name}", timeout=5
        ) as response:
            self.assertEqual(response.status, 200)
            self.assertEqual(response.read(), data)

    def test_upload_rejects_unknown_extension_and_magic(self) -> None:
        with self.assertRaises(urllib.error.HTTPError) as unknown:
            self.upload(b"anything", "demo.txt", "text/plain")
        self.assertEqual(unknown.exception.code, 415)
        with self.assertRaises(urllib.error.HTTPError) as bad_magic:
            self.upload(b"<html>not audio</html>", "demo.wav", "audio/wav")
        self.assertEqual(bad_magic.exception.code, 415)
        self.assertEqual(list(self.config.uploads_dir.iterdir()), [])

    def test_uploaded_track_streams_with_format_header(self) -> None:
        _status, body = self.upload(make_wav(), "demo.wav")
        track = body["track"]
        self.app.player.advance("start")
        with urllib.request.urlopen(self.base + "/now.json", timeout=5) as response:
            now = json.loads(response.read())
        self.assertEqual(now["track"]["id"], track["id"])
        native = now["audio"]["direct"]
        self.assertTrue(native.startswith("/native/"))
        request = urllib.request.Request(
            self.base + native, headers={"Range": "bytes=0-31"}
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            self.assertEqual(response.status, 206)
            payload = response.read()
            self.assertEqual(len(payload), 32)
            self.assertEqual(payload[:4], b"RIFF")
        with urllib.request.urlopen(
            self.base + f"/source/manifest?seq={now['seq']}", timeout=10
        ) as response:
            manifest = json.loads(response.read())
        self.assertEqual(manifest["format"], "mp3")
        self.assertEqual(manifest["mime"], "audio/mpeg")
        self.assertEqual(manifest["header_bytes"], FRAME_HEADER_BYTES)
        with urllib.request.urlopen(
            self.base
            + f"/source/chunk?stream_id={manifest['stream_id']}&seq={manifest['first_seq']}",
            timeout=10,
        ) as response:
            frame = response.read()
        self.assertEqual(frame[:4], FRAME_MAGIC)
        self.assertEqual(frame[36], FORMAT_CODES["mp3"])
        self.assertEqual(frame[FRAME_HEADER_BYTES] & 0xFF, 0xFF)
        self.assertEqual(frame[FRAME_HEADER_BYTES + 1] & 0xE0, 0xE0)


if __name__ == "__main__":
    unittest.main()
