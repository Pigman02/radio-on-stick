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

    def test_request_limit_allows_five_per_minute(self):
        limiter = RateLimiter(
            self.db,
            Config(request_per_min=5, message_per_min=10),
            clock=self.clock,
        )
        for _ in range(5):
            self.assertTrue(limiter.consume_request("k").allowed)
            self.clock.advance(1)
        denied = limiter.consume_request("k")
        self.assertFalse(denied.allowed)
        self.assertEqual(denied.reason, "request_limit")
        self.assertGreater(denied.retry_after, 0)

        self.clock.advance(61)
        self.assertTrue(limiter.consume_request("k").allowed)

    def test_message_limit_allows_ten_per_minute(self):
        limiter = RateLimiter(
            self.db,
            Config(request_per_min=5, message_per_min=10),
            clock=self.clock,
        )
        for _ in range(10):
            self.assertTrue(limiter.consume_message("m").allowed)
            self.clock.advance(2)
        denied = limiter.consume_message("m")
        self.assertFalse(denied.allowed)
        self.assertEqual(denied.reason, "message_limit")
        self.assertGreater(denied.retry_after, 0)

        self.clock.advance(61)
        self.assertTrue(limiter.consume_message("m").allowed)

    def test_requests_are_not_blocked_after_one(self):
        limiter = RateLimiter(
            self.db,
            Config(request_per_min=5),
            clock=self.clock,
        )
        self.assertTrue(limiter.consume_request("k").allowed)
        self.assertTrue(limiter.consume_request("k").allowed)
        self.assertTrue(limiter.consume_request("k").allowed)

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

    def test_ip_key_is_deterministic(self):
        self.assertEqual(make_ip_key("1.2.3.4", "salt"), make_ip_key("1.2.3.4", "salt"))
        self.assertNotEqual(make_ip_key("1.2.3.4", "salt"), make_ip_key("1.2.3.5", "salt"))


if __name__ == "__main__":
    unittest.main()
