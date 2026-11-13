from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.config import Config
from app.db import Database
from app.player import Player


class FakeClock:
    def __init__(self, start: float = 1000.0) -> None:
        self.value = start

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


class FakeAggregator:
    def __init__(self, db: Database) -> None:
        self.db = db
        self.refreshed = []

    def refresh_audio(self, track, force=False):
        self.refreshed.append(track["id"])
        self.db.update_track(
            track["id"],
            {
                "audio_primary": f"http://audio/{track['id']}",
                "audio_alternates": [],
            },
        )
        return self.db.get_track(track["id"])


def seed_track(db: Database, track_id: str, title: str, duration_ms: int, created_at: int) -> None:
    db.insert_track(
        {
            "id": track_id,
            "source": "netease",
            "source_id": track_id,
            "title": title,
            "artist": "Artist",
            "album": "Album",
            "duration_ms": duration_ms,
            "cover_url": f"http://cover/{track_id}",
            "audio_primary": f"http://original/{track_id}",
            "audio_alternates": [],
            "enabled": True,
            "created_at": created_at,
        }
    )


class PlayerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.tmp.name) / "test.db")
        self.clock = FakeClock()
        self.aggregator = FakeAggregator(self.db)
        self.config = Config(default_duration_ms=10_000)
        seed_track(self.db, "t1", "First", 10_000, 1)
        seed_track(self.db, "t2", "Second", 20_000, 2)
        self.player = Player(self.db, self.aggregator, self.config, clock=self.clock)

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def test_start_and_position(self):
        self.player.ensure_started()
        snapshot = self.player.snapshot()
        self.assertEqual(snapshot["track"]["id"], "t1")
        self.assertEqual(snapshot["position_ms"], 0)
        self.assertEqual(snapshot["audio"]["direct"], "http://audio/t1")

        self.clock.advance(5)
        snapshot = self.player.snapshot()
        self.assertAlmostEqual(snapshot["position_ms"], 5000, delta=20)

    def test_advance_at_end(self):
        self.player.ensure_started()
        first_seq = self.player.snapshot()["seq"]
        self.clock.advance(11)
        snapshot = self.player.snapshot()
        self.assertEqual(snapshot["seq"], first_seq + 1)
        self.assertEqual(snapshot["track"]["id"], "t2")
        self.assertEqual(snapshot["position_ms"], 0)

    def test_pause_resume(self):
        self.player.ensure_started()
        self.clock.advance(5)
        self.player.pause()
        self.clock.advance(100)
        snapshot = self.player.snapshot()
        self.assertTrue(snapshot["paused"])
        self.assertAlmostEqual(snapshot["position_ms"], 5000, delta=20)

        self.player.resume()
        self.clock.advance(2)
        snapshot = self.player.snapshot()
        self.assertFalse(snapshot["paused"])
        self.assertAlmostEqual(snapshot["position_ms"], 7000, delta=20)

    def test_queue_takes_priority(self):
        self.player.ensure_started()
        self.assertEqual(self.player.snapshot()["track"]["id"], "t1")
        self.db.enqueue("t2", "key", "nick", "message")
        self.player.skip("test")
        snapshot = self.player.snapshot()
        self.assertEqual(snapshot["track"]["id"], "t2")
        self.assertTrue(snapshot["queue"]["playing"])

        self.player.skip("test")
        snapshot = self.player.snapshot()
        self.assertIsNone(snapshot["track"])

    def test_playlist_does_not_loop(self):
        self.player.ensure_started()
        self.clock.advance(11)
        self.assertEqual(self.player.snapshot()["track"]["id"], "t2")
        self.clock.advance(21)
        idle = self.player.snapshot()
        self.assertIsNone(idle["track"])
        self.clock.advance(100)
        self.assertIsNone(self.player.snapshot()["track"])

        seed_track(self.db, "t3", "Third", 10_000, 3)
        restarted = self.player.snapshot()
        self.assertEqual(restarted["track"]["id"], "t3")

    def test_restart_recovers_state(self):
        self.player.ensure_started()
        self.clock.advance(5)
        before = self.player.snapshot()

        restarted = Player(self.db, self.aggregator, self.config, clock=self.clock)
        after = restarted.snapshot()
        self.assertEqual(after["seq"], before["seq"])
        self.assertEqual(after["track"]["id"], before["track"]["id"])
        self.assertAlmostEqual(after["position_ms"], before["position_ms"], delta=20)

    def test_client_ended_advances(self):
        self.player.ensure_started()
        seq = self.player.snapshot()["seq"]
        self.clock.advance(6)
        snapshot = self.player.report(seq, "ended")
        self.assertEqual(snapshot["seq"], seq + 1)
        self.assertEqual(snapshot["track"]["id"], "t2")

    def test_stale_state_jumps_to_fresh_track(self):
        self.player.ensure_started()
        self.clock.advance(100)
        snapshot = self.player.snapshot()
        self.assertIn(snapshot["track"]["id"], {"t1", "t2"})
        self.assertLess(snapshot["position_ms"], 1000)

    def test_idle_does_not_advance_every_tick(self):
        self.db.clear_pending()
        self.db.delete_track("t1")
        self.db.delete_track("t2")
        self.player = Player(self.db, self.aggregator, self.config, clock=self.clock)
        first = self.player.snapshot()
        self.assertIsNone(first["track"])
        self.clock.advance(10)
        second = self.player.snapshot()
        self.assertEqual(first["seq"], second["seq"])


if __name__ == "__main__":
    unittest.main()
