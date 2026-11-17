from __future__ import annotations

import json
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


class FakeAggregator:
    def __init__(self, db) -> None:
        self.db = db
        self.counter = 0
        self.last_query = ""

    def search(self, query, limit=None):
        self.last_query = query
        return [
            {
                "candidate_id": "a" * 32,
                "source": "netease",
                "source_label": "网易云音乐",
                "title": query,
                "artist": "Test Artist",
                "album": "Test Album",
                "duration_ms": 10_000,
                "duration_estimated": False,
                "cover_url": "https://example.invalid/cover.jpg",
            }
        ]

    def resolve_candidate(self, candidate_id):
        if candidate_id != "a" * 32:
            return None
        return self.resolve(self.last_query or "Candidate Song")

    def resolve(self, query):
        for track in self.db.list_playlist():
            if str(track.get("title") or "").casefold() == query.casefold():
                return track
        self.counter += 1
        track_id = f"new{self.counter}"
        self.db.insert_track(
            {
                "id": track_id,
                "source": "netease",
                "source_id": track_id,
                "title": query,
                "artist": "Test Artist",
                "album": "Test Album",
                "duration_ms": 10_000,
                "cover_url": "https://example.invalid/cover.jpg",
                "audio_primary": f"https://example.invalid/{track_id}.mp3",
                "audio_alternates": [],
                "enabled": True,
                "created_at": now_ms(),
            }
        )
        return self.db.get_track(track_id)

    def refresh_audio(self, track, force=False):
        self.db.update_track(
            track["id"],
            {
                "audio_primary": f"https://example.invalid/audio/{track['id']}.mp3",
                "audio_alternates": [],
            },
        )
        return self.db.get_track(track["id"])

    def ensure_lyrics(self, track):
        return ("[00:00.00]hello", "[00:00.00]你好")


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        data_dir = Path(self.tmp.name)
        self.config = Config(
            data_dir=data_dir,
            db_path=data_dir / "test.db",
            static_dir=BASE_DIR / "app" / "static",
            admin_token="secret",
            request_per_min=100,
            message_per_min=100,
            queue_max=2,
            duplicate_window_sec=3600,
            search_per_min=100,
            min_form_ms=0,
            ip_hash_salt="test-salt",
            default_duration_ms=10_000,
        )
        self.app = build_app(self.config)
        self.fake = FakeAggregator(self.app.db)
        self.app.aggregator = self.fake
        self.app.player.aggregator = self.fake
        self.app.db.insert_track(
            {
                "id": "t1",
                "source": "netease",
                "source_id": "t1",
                "title": "First Song",
                "artist": "First Artist",
                "album": "First Album",
                "duration_ms": 10_000,
                "cover_url": "https://example.invalid/first.jpg",
                "audio_primary": "https://example.invalid/first.mp3",
                "audio_alternates": [],
                "enabled": True,
                "created_at": 1,
            }
        )
        self.server = make_server(self.app, "127.0.0.1", 0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"

    def tearDown(self):
        self.server.shutdown()
        self.thread.join(timeout=2)
        self.server.server_close()
        self.app.db.close()
        self.tmp.cleanup()

    def request(self, method, path, payload=None, token=None, headers=None):
        data = None
        request_headers = dict(headers or {})
        if payload is not None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            request_headers["Content-Type"] = "application/json"
        if token:
            request_headers["Authorization"] = f"Bearer {token}"
        req = urllib.request.Request(
            f"{self.base}{path}", data=data, headers=request_headers, method=method
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as response:
                return response.status, json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            try:
                return exc.code, json.loads(exc.read().decode("utf-8"))
            finally:
                exc.close()

    def test_healthz_reports_ffmpeg(self):
        status, data = self.request("GET", "/healthz")
        self.assertEqual(status, 200)
        self.assertIn("ffmpeg", data)
        self.assertIn("available", data["ffmpeg"])

    def test_now_json(self):
        status, data = self.request(
            "GET", "/now.json", headers={"X-Real-IP": "10.0.0.1"}
        )
        self.assertEqual(status, 200)
        self.assertEqual(data["track"]["title"], "First Song")
        self.assertIn("audio", data)
        self.assertTrue(data["audio"]["direct"].startswith("/native/"))
        self.assertNotIn("upstream", data["audio"])
        self.assertIn("server_time_ms", data)
        self.assertEqual(data["listeners"], 1)
        self.assertEqual(data["vote"]["votes"], 0)
        self.assertEqual(data["vote"]["needed"], 1)

    def test_same_ip_counts_once_across_tabs(self):
        _, first = self.request(
            "GET", "/now.json", headers={"X-Real-IP": "10.0.0.9"}
        )
        _, second = self.request(
            "GET", "/now.json", headers={"X-Real-IP": "10.0.0.9"}
        )
        self.assertEqual(first["listeners"], 1)
        self.assertEqual(second["listeners"], 1)

    def test_now_next_contains_preload_audio(self):
        self.app.db.insert_track(
            {
                "id": "t2",
                "source": "netease",
                "source_id": "t2",
                "title": "Second Song",
                "artist": "Second Artist",
                "duration_ms": 10_000,
                "audio_primary": "https://example.invalid/second.mp3",
                "audio_alternates": [],
                "enabled": True,
                "created_at": 2,
            }
        )
        _, now = self.request("GET", "/now.json")
        self.assertEqual(now["next"]["id"], "t2")
        self.assertTrue(now["next"]["audio"]["direct"].startswith("/native/"))
        self.assertNotIn("upstream", now["next"]["audio"])
        self.assertNotIn("example.invalid/second.mp3", str(now["next"]["audio"]))

    def test_own_pending_request_can_be_cancelled(self):
        # Start the playlist fixture first so the new request stays pending.
        self.request("GET", "/now.json", headers={"X-Real-IP": "10.0.0.51"})
        status, body = self.request(
            "POST",
            "/api/request",
            {"query": "Cancel Song", "nickname": "Canceller"},
            headers={"X-Real-IP": "10.0.0.51"},
        )
        self.assertEqual(status, 201, body)
        queue_id = body["request_id"]
        _status, own_state = self.request(
            "GET", "/api/state", headers={"X-Real-IP": "10.0.0.51"}
        )
        own_item = next(
            item for item in own_state["upcoming"] if item.get("queue_id") == queue_id
        )
        self.assertTrue(own_item["can_cancel"])
        _status, other_state = self.request(
            "GET", "/api/state", headers={"X-Real-IP": "10.0.0.52"}
        )
        other_item = next(
            item for item in other_state["upcoming"] if item.get("queue_id") == queue_id
        )
        self.assertFalse(other_item["can_cancel"])

        status, _ = self.request(
            "POST",
            "/api/queue/cancel",
            {"queue_id": queue_id},
            headers={"X-Real-IP": "10.0.0.52"},
        )
        self.assertEqual(status, 404)

        status, _ = self.request(
            "POST",
            "/api/queue/cancel",
            {"queue_id": queue_id},
            headers={"X-Real-IP": "10.0.0.51"},
        )
        self.assertEqual(status, 200)
        _status, state = self.request(
            "GET", "/api/state", headers={"X-Real-IP": "10.0.0.51"}
        )
        self.assertFalse(
            any(item.get("queue_id") == queue_id for item in state["upcoming"])
        )

    def test_upcoming_merges_current_and_playlist(self):
        self.app.db.insert_track(
            {
                "id": "t2",
                "source": "netease",
                "source_id": "t2",
                "title": "Second Song",
                "artist": "Second Artist",
                "duration_ms": 10_000,
                "audio_primary": "https://example.invalid/second.mp3",
                "audio_alternates": [],
                "enabled": True,
                "created_at": 2,
            }
        )
        _, state = self.request("GET", "/api/state")
        upcoming = state["upcoming"]
        self.assertEqual(upcoming[0]["id"], "t1")
        self.assertEqual(upcoming[0]["status"], "playing")
        self.assertEqual(upcoming[1]["id"], "t2")
        self.assertEqual(upcoming[1]["status"], "upcoming")

    def test_lyrics_endpoint(self):
        _, now = self.request("GET", "/now.json")
        status, data = self.request("GET", f"/api/lyrics/{now['track']['id']}")
        self.assertEqual(status, 200)
        self.assertEqual(data["lrc"], "[00:00.00]hello")
        self.assertEqual(data["translation"], "[00:00.00]你好")

    def test_request_and_duplicate(self):
        payload = {
            "query": "New Song",
            "nickname": "Tester",
            "message": "hello",
            "ts": now_ms(),
        }
        status, data = self.request("POST", "/api/request", payload)
        self.assertEqual(status, 201, data)
        self.assertEqual(data["track"]["title"], "New Song")

        status, data = self.request("POST", "/api/request", payload)
        self.assertEqual(status, 409)
        self.assertEqual(data["error"], "duplicate")

    def test_honeypot(self):
        status, data = self.request(
            "POST",
            "/api/request",
            {"query": "New Song", "website": "bot", "ts": now_ms()},
        )
        self.assertEqual(status, 400)
        self.assertEqual(data["error"], "bad_request")

    def test_queue_full(self):
        for title in ("Song One", "Song Two"):
            status, data = self.request(
                "POST",
                "/api/request",
                {"query": title, "ts": now_ms()},
            )
            self.assertEqual(status, 201, data)
        status, data = self.request(
            "POST",
            "/api/request",
            {"query": "Song Three", "ts": now_ms()},
        )
        self.assertEqual(status, 429)
        self.assertEqual(data["error"], "queue_full")

    def test_admin_requires_token(self):
        status, data = self.request("POST", "/api/admin/skip")
        self.assertEqual(status, 401)
        self.assertEqual(data["error"], "unauthorized")

        status, data = self.request("POST", "/api/admin/skip", token="secret")
        self.assertEqual(status, 200, data)
        self.assertTrue(data["ok"])

    def test_report_rejects_bad_event(self):
        _, now = self.request("GET", "/now.json")
        status, data = self.request(
            "POST", "/api/report", {"seq": now["seq"], "event": "explode"}
        )
        self.assertEqual(status, 400)
        self.assertEqual(data["error"], "bad_event")

    def test_search_and_request_candidate(self):
        status, data = self.request("GET", "/api/search?q=Hello")
        self.assertEqual(status, 200)
        self.assertEqual(len(data["results"]), 1)
        candidate = data["results"][0]
        self.assertEqual(candidate["candidate_id"], "a" * 32)
        self.assertEqual(candidate["source_label"], "网易云音乐")

        status, data = self.request(
            "POST",
            "/api/request",
            {"candidate_id": candidate["candidate_id"], "nickname": "Tester", "ts": now_ms()},
        )
        self.assertEqual(status, 201, data)
        self.assertEqual(data["track"]["title"], "Hello")
        self.assertEqual(data["queue"]["pending"], 1)

    def test_expired_search_candidate(self):
        status, data = self.request(
            "POST",
            "/api/request",
            {"candidate_id": "b" * 32, "nickname": "Tester", "ts": now_ms()},
        )
        self.assertEqual(status, 410)
        self.assertEqual(data["error"], "search_expired")

    def test_request_history_and_requeue(self):
        status, first = self.request(
            "POST",
            "/api/request",
            {"query": "History Song", "nickname": "Tester", "ts": now_ms()},
        )
        self.assertEqual(status, 201, first)
        track_id = first["track"]["id"]

        _, state = self.request("GET", "/api/state")
        matching = [item for item in state["history"] if item["track_id"] == track_id]
        self.assertEqual(len(matching), 1)
        first_created = matching[0]["created_at"]

        self.app.db.clear_pending()
        self.app.player.skip("test")
        status, second = self.request(
            "POST",
            "/api/request",
            {"track_id": track_id, "nickname": "Tester", "ts": now_ms()},
        )
        self.assertEqual(status, 201, second)
        self.assertEqual(second["track"]["id"], track_id)

        _, state = self.request("GET", "/api/state")
        matching = [item for item in state["history"] if item["track_id"] == track_id]
        self.assertEqual(len(matching), 1)
        self.assertGreaterEqual(matching[0]["created_at"], first_created)

    def test_request_history_is_shared_between_listeners(self):
        status, first = self.request(
            "POST",
            "/api/request",
            {"query": "Shared History One", "nickname": "One", "ts": now_ms()},
            headers={"X-Real-IP": "10.0.2.1"},
        )
        self.assertEqual(status, 201, first)
        status, second = self.request(
            "POST",
            "/api/request",
            {"query": "Shared History Two", "nickname": "Two", "ts": now_ms()},
            headers={"X-Real-IP": "10.0.2.2"},
        )
        self.assertEqual(status, 201, second)

        _, state = self.request(
            "GET", "/api/state", headers={"X-Real-IP": "10.0.2.1"}
        )
        titles = {item["title"] for item in state["history"]}
        self.assertIn("Shared History One", titles)
        self.assertIn("Shared History Two", titles)

    def test_messages_are_separate_from_requests(self):
        status, data = self.request(
            "POST",
            "/api/messages",
            {"nickname": "Tester", "body": "hello there", "ts": now_ms()},
        )
        self.assertEqual(status, 201, data)
        self.assertEqual(data["message"]["body"], "hello there")

        _, state = self.request("GET", "/api/state")
        self.assertEqual(state["queue"], [])
        self.assertEqual(state["messages"][0]["body"], "hello there")

    def test_message_requires_body(self):
        status, data = self.request("POST", "/api/messages", {"nickname": "Tester"})
        self.assertEqual(status, 400)
        self.assertEqual(data["error"], "missing_body")

    def test_settings_update(self):
        status, data = self.request(
            "POST",
            "/api/admin/settings",
            {
                "station_name": "Test FM",
                "subtitle": "sub",
                "page_title": "Test FM",
                "notice": "notice",
                "background_interval_sec": 120,
            },
            token="secret",
        )
        self.assertEqual(status, 200, data)
        _, config = self.request("GET", "/api/config")
        self.assertEqual(config["station_name"], "Test FM")
        self.assertEqual(config["subtitle"], "sub")
        self.assertEqual(config["notice"], "notice")
        self.assertEqual(config["background_interval_sec"], 120)

    def test_admin_access_settings_are_admin_only(self):
        status, data = self.request(
            "POST",
            "/api/admin/settings",
            {
                "public_url": "https://radio.example.com/",
                "lan_url": "https://172.18.1.10:8443/",
                "tunnel_url": "https://demo.trycloudflare.com/",
                "access_note": "校园网测试",
            },
            token="secret",
        )
        self.assertEqual(status, 200, data)
        self.assertEqual(data["settings"]["public_url"], "https://radio.example.com/")
        status, state = self.request("GET", "/api/admin/state", token="secret")
        self.assertEqual(status, 200, state)
        self.assertEqual(state["settings"]["lan_url"], "https://172.18.1.10:8443/")
        _, config = self.request("GET", "/api/config")
        self.assertNotIn("public_url", config)
        self.assertNotIn("lan_url", config)

    def test_admin_access_settings_validate_urls(self):
        status, data = self.request(
            "POST",
            "/api/admin/settings",
            {"public_url": "not-a-url"},
            token="secret",
        )
        self.assertEqual(status, 400)
        self.assertEqual(data["error"], "bad_settings")

    def test_admin_state(self):
        status, data = self.request("GET", "/api/admin/state", token="secret")
        self.assertEqual(status, 200)
        self.assertIn("now", data)
        self.assertIn("queue", data)
        self.assertIn("messages", data)
        self.assertIn("settings", data)
        self.assertIn("backgrounds", data)

    def test_network_background_url(self):
        url = "https://example.invalid/background.jpg"
        status, data = self.request(
            "POST",
            "/api/admin/background/url",
            {"url": url},
            token="secret",
        )
        self.assertEqual(status, 200, data)
        _, config = self.request("GET", "/api/config")
        self.assertTrue(
            any(
                item["url"] == url and item["type"] == "network"
                for item in config["backgrounds"]
            )
        )

        status, data = self.request(
            "POST",
            "/api/admin/background/url/delete",
            {"url": url},
            token="secret",
        )
        self.assertEqual(status, 200, data)
        _, config = self.request("GET", "/api/config")
        self.assertFalse(
            any(item["url"] == url for item in config["backgrounds"])
        )

        status, data = self.request(
            "POST",
            "/api/admin/background/url",
            {"url": "javascript:alert(1)"},
            token="secret",
        )
        self.assertEqual(status, 400)
        self.assertEqual(data["error"], "bad_background_url")

    def test_background_upload_and_delete(self):
        png = b"\x89PNG\r\n\x1a\n" + b"0" * 128
        request = urllib.request.Request(
            f"{self.base}/api/admin/background",
            data=png,
            headers={
                "Authorization": "Bearer secret",
                "Content-Type": "image/png",
            },
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=10) as response:
            data = json.loads(response.read().decode("utf-8"))
            self.assertEqual(response.status, 201)
        background = data["background"]
        self.assertTrue(background["url"].startswith("/backgrounds/"))

        with urllib.request.urlopen(f"{self.base}{background['url']}", timeout=10) as response:
            self.assertEqual(response.status, 200)
            self.assertTrue(response.read().startswith(b"\x89PNG"))

        _, config = self.request("GET", "/api/config")
        self.assertTrue(any(item["name"] == background["name"] for item in config["backgrounds"]))

        status, data = self.request(
            "POST",
            "/api/admin/background/delete",
            {"name": background["name"]},
            token="secret",
        )
        self.assertEqual(status, 200, data)
        try:
            urllib.request.urlopen(f"{self.base}{background['url']}", timeout=10)
        except urllib.error.HTTPError as exc:
            self.assertEqual(exc.code, 404)
            exc.close()
        else:
            self.fail("background still accessible after delete")

    def test_static_index(self):
        with urllib.request.urlopen(f"{self.base}/", timeout=10) as response:
            body = response.read().decode("utf-8")
            self.assertEqual(response.status, 200)
            self.assertIn("app.js", body)
            self.assertIn("nicknameModal", body)
            self.assertNotIn("ON AIR", body)
            self.assertNotIn("adminBtn", body)

    def test_vote_skip_needs_half_of_listeners(self):
        self.app.db.insert_track(
            {
                "id": "t2",
                "source": "netease",
                "source_id": "t2",
                "title": "Second Song",
                "artist": "Second Artist",
                "duration_ms": 10_000,
                "audio_primary": "https://example.invalid/second.mp3",
                "audio_alternates": [],
                "enabled": True,
                "created_at": 2,
            }
        )
        _, first = self.request(
            "GET", "/now.json", headers={"X-Real-IP": "10.0.0.1"}
        )
        self.assertEqual(first["listeners"], 1)
        _, second = self.request(
            "GET", "/now.json", headers={"X-Real-IP": "10.0.0.2"}
        )
        self.assertEqual(second["listeners"], 2)
        self.assertEqual(second["vote"]["needed"], 1)

        _, third = self.request(
            "GET", "/now.json", headers={"X-Real-IP": "10.0.0.3"}
        )
        self.assertEqual(third["listeners"], 3)
        self.assertEqual(third["vote"]["needed"], 2)

        status, vote1 = self.request(
            "POST",
            "/api/vote-skip",
            {},
            headers={"X-Real-IP": "10.0.0.1"},
        )
        self.assertEqual(status, 200)
        self.assertFalse(vote1["switched"])
        self.assertEqual(vote1["vote"]["votes"], 1)

        status, vote2 = self.request(
            "POST",
            "/api/vote-skip",
            {},
            headers={"X-Real-IP": "10.0.0.2"},
        )
        self.assertEqual(status, 200)
        self.assertTrue(vote2["switched"])
        self.assertEqual(vote2["now"]["track"]["id"], "t2")
        self.assertEqual(vote2["now"]["vote"]["votes"], 0)

    def test_listener_list_includes_nicknames(self):
        status, first = self.request(
            "GET",
            "/api/listeners?nickname=Alice",
            headers={"X-Real-IP": "10.0.3.1"},
        )
        self.assertEqual(status, 200, first)
        status, second = self.request(
            "GET",
            "/api/listeners?nickname=Bob",
            headers={"X-Real-IP": "10.0.3.2"},
        )
        self.assertEqual(status, 200, second)
        names = {item["nickname"] for item in second["items"]}
        self.assertIn("Alice", names)
        self.assertIn("Bob", names)
        self.assertEqual(second["listeners"], len(second["items"]))

    def test_queue_pin_is_unique_per_person(self):
        self.app.db.insert_track(
            {
                "id": "t2",
                "source": "netease",
                "source_id": "t2",
                "title": "Second Song",
                "artist": "Second Artist",
                "duration_ms": 10_000,
                "audio_primary": "https://example.invalid/second.mp3",
                "audio_alternates": [],
                "enabled": True,
                "created_at": 2,
            }
        )
        q1 = self.app.db.enqueue("t1", "k", "one", None)
        q2 = self.app.db.enqueue("t2", "k", "two", None)

        status, data = self.request(
            "POST",
            "/api/queue/pin",
            {"queue_id": q2},
            headers={"X-Real-IP": "10.0.1.1"},
        )
        self.assertEqual(status, 200, data)
        self.assertTrue(data["pinned"])
        self.assertEqual(data["pins"], 1)

        status, data = self.request(
            "POST",
            "/api/queue/pin",
            {"queue_id": q2},
            headers={"X-Real-IP": "10.0.1.1"},
        )
        self.assertEqual(status, 200)
        self.assertFalse(data["pinned"])
        self.assertEqual(data["pins"], 0)

        status, data = self.request(
            "POST",
            "/api/queue/pin",
            {"queue_id": q2},
            headers={"X-Real-IP": "10.0.1.1"},
        )
        self.assertEqual(status, 200)
        self.assertTrue(data["pinned"])
        self.assertEqual(data["pins"], 1)

        status, data = self.request(
            "POST",
            "/api/queue/pin",
            {"queue_id": q2},
            headers={"X-Real-IP": "10.0.1.2"},
        )
        self.assertEqual(data["pins"], 2)

        _, state = self.request("GET", "/api/state")
        self.assertEqual(state["queue"][0]["track_id"], "t2")
        self.assertEqual(state["queue"][0]["priority"], 2)
        self.assertIn(q1, [item["id"] for item in state["queue"]])

    def test_admin_add_existing_queues_once(self):
        status, first = self.request(
            "POST",
            "/api/admin/track",
            {"query": "First Song"},
            token="secret",
        )
        self.assertEqual(status, 201, first)
        self.assertIsNotNone(first["queued"])

        status, second = self.request(
            "POST",
            "/api/admin/track",
            {"query": "First Song"},
            token="secret",
        )
        self.assertEqual(status, 201, second)
        self.assertIsNone(second["queued"])

    def test_admin_add_new_track_does_not_queue(self):
        status, data = self.request(
            "POST",
            "/api/admin/track",
            {"query": "Brand New Song"},
            token="secret",
        )
        self.assertEqual(status, 201, data)
        self.assertIsNone(data["queued"])

    def test_admin_page(self):
        with urllib.request.urlopen(f"{self.base}/admin", timeout=10) as response:
            body = response.read().decode("utf-8")
            self.assertEqual(response.status, 200)
            self.assertIn("app.js", body)
        with urllib.request.urlopen(f"{self.base}/admin.js", timeout=10) as response:
            script = response.read().decode("utf-8")
            self.assertEqual(response.status, 200)
            self.assertIn("adminConsole", script)
            self.assertIn("mp3_upload_only", script)


if __name__ == "__main__":
    unittest.main()
