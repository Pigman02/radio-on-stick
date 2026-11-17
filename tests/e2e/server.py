"""Local-only browser E2E fixture for the mesh audio path.

Run from the project root:
    python3 tests/e2e/server.py --port 8795 &
    node tests/e2e/mesh.js

It serves a synthetic, valid MPEG-1 Layer III silent stream (no ffmpeg or
audio fixture is needed), fakes the player snapshot, and exposes the normal
/source, /ws and /mesh.json endpoints.
"""
from __future__ import annotations

import argparse
import os
import signal
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from app.app import build_app  # noqa: E402
from app.config import BASE_DIR, Config  # noqa: E402
from app.httpd import make_server  # noqa: E402

FRAME = bytes.fromhex("fffb90c0") + bytes(413)  # 128 kbps / 44.1 kHz
AUDIO = FRAME * 4590  # ~120 seconds


class Upstream(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_GET(self) -> None:
        start = 0
        header = self.headers.get("Range") or ""
        if header.startswith("bytes="):
            try:
                start = int(header[6:].split("-")[0])
            except ValueError:
                start = 0
        if start >= len(AUDIO):
            self.send_response(416)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        body = AUDIO[start:]
        self.send_response(206 if start else 200)
        self.send_header("Content-Type", "audio/mpeg")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Accept-Ranges", "bytes")
        if start:
            self.send_header("Content-Range", f"bytes {start}-{len(AUDIO) - 1}/{len(AUDIO)}")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def log_message(self, *args) -> None:
        pass


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8795")))
    parser.add_argument("--http-leaf-max", type=int, default=4)
    args = parser.parse_args()

    temp = Path(tempfile.mkdtemp(prefix="radio-mesh-e2e-"))
    upstream = ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
    threading.Thread(target=upstream.serve_forever, daemon=True).start()
    audio_url = f"http://127.0.0.1:{upstream.server_address[1]}/track.mp3"

    app = build_app(
        Config(
            data_dir=temp,
            db_path=temp / "radio.db",
            static_dir=BASE_DIR / "app" / "static",
            admin_token="e2e",
            http_leaf_max=args.http_leaf_max,
        )
    )
    app.db.insert_track(
        {
            "id": "e2e-track",
            "source": "local",
            "source_id": "e2e-track",
            "title": "Mesh E2E",
            "artist": "Tester",
            "album": "Test",
            "duration_ms": 120_000,
            "audio_primary": audio_url,
            "audio_alternates": [],
            "enabled": False,
            "created_at": int(time.time() * 1000),
        }
    )
    started_at = int(time.time() * 1000)

    def snapshot(ensure: bool = True) -> dict:
        now = int(time.time() * 1000)
        return {
            "server_time_ms": now,
            "station": "E2E",
            "seq": 1,
            "audio_rev": 1,
            "paused": False,
            "track": {
                "id": "e2e-track",
                "source": "local",
                "title": "Mesh E2E",
                "artist": "Tester",
                "album": "Test",
                "duration_ms": 120_000,
                "cover_url": "",
                "lyrics_url": "",
            },
            "started_at_ms": started_at,
            "position_ms": max(0, now - started_at),
            "duration_ms": 120_000,
            "audio": {
                "direct": "/native/e2e-track",
                "alternates": [],
                "upstream": audio_url,
            },
            "lyrics_url": "",
            "next": None,
            "queue": [],
        }

    app.player.snapshot = snapshot
    server = make_server(app, "127.0.0.1", args.port)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    print(f"http://127.0.0.1:{server.server_address[1]}", flush=True)

    def stop(*_args) -> None:
        os._exit(0)

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    while True:
        time.sleep(1)


if __name__ == "__main__":
    main()
