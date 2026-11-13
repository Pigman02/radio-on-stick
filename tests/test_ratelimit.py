from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.config import Config
from app.db import Database
from app.ratelimit import RateLimiter, make_ip_key


class FakeClock:
    def __init__(self, start: float = 1_000_000.0) -> None:
        self.value = start

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


class RateLimiterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.tmp.name) / "test.db")
        self.clock = FakeClock()

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def test_cooldown(self):
        limiter = RateLimiter(
            self.db,
            Config(request_cooldown_sec=60, request_daily_limit=10, request_pending_limit=5),
            clock=self.clock,
        )
        self.assertTrue(limiter.consume_request("k").allowed)
        denied = limiter.consume_request("k")
        self.assertFalse(denied.allowed)
        self.assertEqual(denied.reason, "cooldown")
        self.assertGreater(denied.retry_after, 0)

        self.clock.advance(61)
        self.assertTrue(limiter.consume_request("k").allowed)

    def test_daily_limit(self):
        limiter = RateLimiter(
            self.db,
            Config(request_cooldown_sec=0, request_daily_limit=2, request_pending_limit=5),
            clock=self.clock,
        )
        self.assertTrue(limiter.consume_request("k").allowed)
        self.clock.advance(1)
        self.assertTrue(limiter.consume_request("k").allowed)
        self.clock.advance(1)
        denied = limiter.consume_request("k")
        self.assertFalse(denied.allowed)
        self.assertEqual(denied.reason, "daily_limit")

    def test_pending_limit(self):
        self.db.insert_track(
            {
                "id": "t1",
                "source": "netease",
                "source_id": "t1",
                "title": "A",
                "duration_ms": 1000,
                "enabled": True,
                "created_at": 1,
            }
        )
        self.db.enqueue("t1", "k", "n", "m")
        self.db.enqueue("t1", "k", "n", "m")
        limiter = RateLimiter(
            self.db,
            Config(request_cooldown_sec=0, request_daily_limit=10, request_pending_limit=2),
            clock=self.clock,
        )
        decision = limiter.precheck_request("k")
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason, "too_many_pending")

    def test_report_limit(self):
        limiter = RateLimiter(
            self.db,
            Config(report_limit_per_min=2),
            clock=self.clock,
        )
        self.assertTrue(limiter.consume_report("r").allowed)
        self.assertTrue(limiter.consume_report("r").allowed)
        denied = limiter.consume_report("r")
        self.assertFalse(denied.allowed)
        self.assertEqual(denied.reason, "report_limit")

    def test_search_limit(self):
        limiter = RateLimiter(
            self.db,
            Config(search_per_min=2),
            clock=self.clock,
        )
        self.assertTrue(limiter.consume_search("s").allowed)
        self.assertTrue(limiter.consume_search("s").allowed)
        denied = limiter.consume_search("s")
        self.assertFalse(denied.allowed)
        self.assertEqual(denied.reason, "search_limit")

    def test_message_cooldown_and_daily_limit(self):
        limiter = RateLimiter(
            self.db,
            Config(message_cooldown_sec=30, message_daily_limit=10),
            clock=self.clock,
        )
        self.assertTrue(limiter.consume_message("m").allowed)
        denied = limiter.consume_message("m")
        self.assertFalse(denied.allowed)
        self.assertEqual(denied.reason, "message_cooldown")

        self.clock.advance(31)
        self.assertTrue(limiter.consume_message("m").allowed)

        daily = RateLimiter(
            self.db,
            Config(message_cooldown_sec=0, message_daily_limit=1),
            clock=self.clock,
        )
        self.clock.advance(1)
        self.assertTrue(daily.consume_message("d").allowed)
        denied = daily.consume_message("d")
        self.assertFalse(denied.allowed)
        self.assertEqual(denied.reason, "message_daily_limit")

    def test_ip_key_is_deterministic(self):
        self.assertEqual(make_ip_key("1.2.3.4", "salt"), make_ip_key("1.2.3.4", "salt"))
        self.assertNotEqual(make_ip_key("1.2.3.4", "salt"), make_ip_key("1.2.3.5", "salt"))


if __name__ == "__main__":
    unittest.main()
