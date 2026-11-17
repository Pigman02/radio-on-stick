from __future__ import annotations

import json
import tempfile
import threading
import unittest
import urllib.request
from pathlib import Path

from app.app import build_app
from app.community import ListenerTracker
from app.config import BASE_DIR, Config
from app.db import now_ms
from app.httpd import make_server
from app.uploads import cleanup_uploads
from tests.test_upload import FakeAggregator, make_wav


class UploadCleanupTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        data_dir = Path(self.tmp.name)
        self.config = Config(
            data_dir=data_dir,
            db_path=data_dir / "cleanup.db",
            static_dir=BASE_DIR / "app" / "static",
            admin_token="secret",
            request_per_min=100,
            message_per_min=100,
            queue_max=5,
            duplicate_window_sec=3600,
            search_per_min=100,
            min_form_ms=0,
            ip_hash_salt="cleanup-test",
            default_duration_ms=10_000,
            upload_retention_sec=3600,
            upload_offline_grace_sec=60,
            upload_cleanup_interval_sec=3600,
            http_leaf_max=1,
            http_leaf_ttl_sec=60,
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

    def upload(self) -> dict:
        request = urllib.request.Request(
            self.base + "/api/upload",
            data=make_wav(),
            method="POST",
            headers={
                "Content-Type": "audio/wav",
                "X-Filename": "cleanup.wav",
                "X-Title": "Cleanup Song",
                "X-Nickname": "Uploader",
                "X-Duration-Ms": "1000",
            },
        )
        with urllib.request.urlopen(request, timeout=15) as response:
            self.assertEqual(response.status, 201)
            return json.loads(response.read())

    def start_fake_listener(self, key: str):
        clock = [1000.0]
        tracker = ListenerTracker(ttl_sec=5, clock=lambda: clock[0])
        tracker.touch(key)
        self.app.listeners = tracker
        return clock

    def test_finished_upload_removed_after_play(self) -> None:
        body = self.upload()
        track_id = body["track"]["id"]
        track = self.app.db.get_track(track_id)
        self.assertIsNotNone(track)
        assert track is not None
        stored = Path(self.config.uploads_dir) / str(track["source_id"])
        self.assertTrue(stored.is_file())
        uploader_key = track["meta_json"]["uploader_key"]
        self.assertTrue(uploader_key.startswith("ip:"))
        self.start_fake_listener(uploader_key)

        self.app.player.advance("start")
        self.app.player.advance("skip")
        ended_at = self.app.db.upload_last_ended_at(track_id)
        self.assertIsNotNone(ended_at)

        # Temporary uploads do not outlive playback, even while the uploader
        # still has the radio page open.  The source is only ever read by the
        # transcoder/native fallback; it is never modified.
        result = cleanup_uploads(self.app, now=int(ended_at) + 1_000)
        self.assertEqual(result["files"], 1)
        self.assertFalse(stored.exists())
        self.assertIsNone(self.app.db.get_track(track_id))
        self.assertEqual(self.app.db.list_request_history("unused", 10), [])

    def test_foreign_file_in_uploads_dir_is_never_removed(self) -> None:
        foreign = Path(self.config.uploads_dir) / "owner-original.mp3"
        foreign.write_bytes(b"never-touch-this")
        # Make it look old enough for the orphan sweep.
        import os
        os.utime(foreign, (1, 1))
        cleanup_uploads(self.app, now=now_ms() + 90_000)
        self.assertTrue(foreign.is_file())
        self.assertEqual(foreign.read_bytes(), b"never-touch-this")

    def test_current_upload_is_never_removed(self) -> None:
        body = self.upload()
        track_id = body["track"]["id"]
        track = self.app.db.get_track(track_id)
        assert track is not None
        stored = Path(self.config.uploads_dir) / str(track["source_id"])
        self.app.player.advance("start")
        result = cleanup_uploads(self.app, now=now_ms() + 90_000)
        self.assertEqual(result["files"], 0)
        self.assertTrue(stored.is_file())

    def test_abandoned_upload_is_removed_by_retention(self) -> None:
        body = self.upload()
        track_id = body["track"]["id"]
        result = cleanup_uploads(self.app, now=now_ms() + 3_600_000 + 5_000)
        self.assertGreaterEqual(result["files"], 1)
        self.assertIsNone(self.app.db.get_track(track_id))

    def test_http_leaf_admission_cap(self) -> None:
        self.upload()
        self.app.player.advance("start")
        query = "/source/manifest?seq=1"
        first = urllib.request.Request(
            self.base + query,
            headers={"X-Radio-Source-Role": "http", "X-Real-IP": "10.0.0.1"},
        )
        with urllib.request.urlopen(first, timeout=10) as response:
            self.assertEqual(response.status, 200)
        second = urllib.request.Request(
            self.base + query,
            headers={"X-Radio-Source-Role": "http", "X-Real-IP": "10.0.0.2"},
        )
        with self.assertRaises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(second, timeout=10)
        self.assertEqual(caught.exception.code, 429)

    def test_re_requesting_upload_does_not_enable_rotation(self) -> None:
        body = self.upload()
        track_id = body["track"]["id"]
        self.app.player.advance("start")
        self.app.player.advance("skip")
        request = urllib.request.Request(
            self.base + "/api/request",
            data=json.dumps(
                {"track_id": track_id, "nickname": "Uploader"}
            ).encode(),
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=10) as response:
            self.assertEqual(response.status, 201)
        track = self.app.db.get_track(track_id)
        assert track is not None
        self.assertFalse(track["enabled"])


if __name__ == "__main__":
    unittest.main()
