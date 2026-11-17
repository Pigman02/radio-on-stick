import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from app.app import build_app
from app.config import BASE_DIR, Config
from app.httpd import make_server
from app.policy import load_policy, save_policy
from tests.test_upload import FakeAggregator, make_wav


class PolicyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        data_dir = Path(self.tmp.name)
        self.config = Config(
            data_dir=data_dir,
            db_path=data_dir / "policy.db",
            static_dir=BASE_DIR / "app" / "static",
            admin_token="secret",
            request_per_min=100,
            message_per_min=100,
            queue_max=5,
            search_per_min=100,
            min_form_ms=0,
            ip_hash_salt="policy-test",
            upload_retention_sec=3600,
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

    def admin(self, path: str, payload: dict | None = None) -> dict:
        data = None if payload is None else json.dumps(payload).encode()
        request = urllib.request.Request(
            self.base + path,
            data=data,
            method="POST" if data is not None else "GET",
            headers={
                "Authorization": "Bearer secret",
                **({"Content-Type": "application/json"} if data else {}),
            },
        )
        with urllib.request.urlopen(request, timeout=10) as response:
            return json.loads(response.read())

    def test_policy_round_trip_and_false_flags(self) -> None:
        result = self.admin(
            "/api/admin/policy",
            {
                "mp3_upload_only": True,
                "mp3_remote_only": True,
                "requests_enabled": False,
                "uploads_enabled": False,
                "messages_enabled": False,
                "access_mode": "allowlist",
                "allow_cidrs": "127.0.0.0/8",
                "max_listeners": 2,
            },
        )
        self.assertTrue(result["policy"]["mp3_upload_only"])
        self.assertFalse(result["policy"]["requests_enabled"])
        self.assertFalse(result["policy"]["uploads_enabled"])
        self.assertFalse(result["policy"]["messages_enabled"])
        self.assertEqual(result["policy"]["access_mode"], "allowlist")
        self.assertEqual(result["policy"]["max_listeners"], 2)
        self.assertIn("127.0.0.0/8", result["policy"]["allow_cidrs"])

    def test_max_listeners_blocks_new_ip(self) -> None:
        save_policy(self.app.db, {"max_listeners": 1})
        first = urllib.request.Request(
            self.base + "/now.json", headers={"X-Real-IP": "10.0.0.1"}
        )
        with urllib.request.urlopen(first, timeout=10) as response:
            self.assertEqual(response.status, 200)
        second = urllib.request.Request(
            self.base + "/now.json", headers={"X-Real-IP": "10.0.0.2"}
        )
        with self.assertRaises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(second, timeout=10)
        self.assertEqual(caught.exception.code, 503)

    def test_upload_mp3_only_rejects_wav_without_leaving_file(self) -> None:
        save_policy(self.app.db, {"mp3_upload_only": True})
        request = urllib.request.Request(
            self.base + "/api/upload",
            data=make_wav(),
            method="POST",
            headers={
                "Content-Type": "audio/wav",
                "X-Filename": "nope.wav",
                "X-Title": "Nope",
                "X-Nickname": "Tester",
                "X-Duration-Ms": "1000",
            },
        )
        with self.assertRaises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(request, timeout=10)
        self.assertEqual(caught.exception.code, 415)
        self.assertEqual(list(Path(self.config.uploads_dir).iterdir()), [])

    def test_delete_any_track(self) -> None:
        self.app.db.insert_track(
            {
                "id": "victim",
                "source": "netease",
                "source_id": "victim",
                "title": "Victim",
                "artist": "A",
                "duration_ms": 1000,
                "audio_primary": "https://example.invalid/victim.mp3",
                "audio_alternates": [],
                "enabled": True,
                "created_at": 1,
            }
        )
        result = self.admin("/api/admin/track/delete", {"track_id": "victim"})
        self.assertTrue(result["ok"])
        self.assertIsNone(self.app.db.get_track("victim"))


if __name__ == "__main__":
    unittest.main()
