from __future__ import annotations

import hashlib
import threading
import time
from dataclasses import dataclass
from typing import Callable

from .config import Config
from .db import Database


ACT_REQUEST = "request"
ACT_REPORT = "report"
ACT_SEARCH = "search"
ACT_MESSAGE = "message"
DAY_MS = 86_400_000


def make_ip_key(ip: str, salt: str) -> str:
    digest = hashlib.sha256(f"{salt}|{ip}".encode("utf-8")).hexdigest()
    return digest[:20]


@dataclass(frozen=True)
class RateDecision:
    allowed: bool
    reason: str = ""
    retry_after: int = 0


class RateLimiter:
    def __init__(
        self,
        db: Database,
        config: Config,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self.db = db
        self.config = config
        self.clock = clock or time.time
        self._lock = threading.Lock()
        self._last_cleanup = 0

    def _now_ms(self) -> int:
        return int(self.clock() * 1000)

    def _cleanup(self, now: int) -> None:
        if now - self._last_cleanup < 3_600_000:
            return
        self._last_cleanup = now
        self.db.cleanup_rate_events(now - 2 * DAY_MS)

    def _check(self, key: str, now: int) -> RateDecision:
        cooldown_ms = max(0, self.config.request_cooldown_sec) * 1000
        if cooldown_ms:
            events = self.db.rate_events_since(key, ACT_REQUEST, now - cooldown_ms)
            if events:
                remaining = cooldown_ms - (now - events[-1])
                return RateDecision(False, "cooldown", max(1, remaining // 1000))

        daily = max(1, self.config.request_daily_limit)
        events = self.db.rate_events_since(key, ACT_REQUEST, now - DAY_MS)
        if len(events) >= daily:
            remaining = DAY_MS - (now - events[0])
            return RateDecision(False, "daily_limit", max(1, remaining // 1000))

        pending = self.db.count_pending_for_key(key)
        if pending >= max(1, self.config.request_pending_limit):
            return RateDecision(False, "too_many_pending", 30)
        return RateDecision(True)

    def precheck_request(self, key: str, now: int | None = None) -> RateDecision:
        now = self._now_ms() if now is None else int(now)
        return self._check(key, now)

    def consume_request(self, key: str, now: int | None = None) -> RateDecision:
        now = self._now_ms() if now is None else int(now)
        with self._lock:
            decision = self._check(key, now)
            if decision.allowed:
                self.db.add_rate_event(key, ACT_REQUEST, now)
                self._cleanup(now)
            return decision

    def consume_report(self, key: str, now: int | None = None) -> RateDecision:
        now = self._now_ms() if now is None else int(now)
        window = 60_000
        with self._lock:
            events = self.db.rate_events_since(key, ACT_REPORT, now - window)
            if len(events) >= max(1, self.config.report_limit_per_min):
                remaining = window - (now - events[0])
                return RateDecision(False, "report_limit", max(1, remaining // 1000))
            self.db.add_rate_event(key, ACT_REPORT, now)
            self._cleanup(now)
            return RateDecision(True)

    def consume_search(self, key: str, now: int | None = None) -> RateDecision:
        now = self._now_ms() if now is None else int(now)
        window = 60_000
        with self._lock:
            events = self.db.rate_events_since(key, ACT_SEARCH, now - window)
            if len(events) >= max(1, self.config.search_per_min):
                remaining = window - (now - events[0])
                return RateDecision(False, "search_limit", max(1, remaining // 1000))
            self.db.add_rate_event(key, ACT_SEARCH, now)
            self._cleanup(now)
            return RateDecision(True)

    def consume_message(self, key: str, now: int | None = None) -> RateDecision:
        now = self._now_ms() if now is None else int(now)
        with self._lock:
            cooldown_ms = max(0, self.config.message_cooldown_sec) * 1000
            if cooldown_ms:
                events = self.db.rate_events_since(key, ACT_MESSAGE, now - cooldown_ms)
                if events:
                    remaining = cooldown_ms - (now - events[-1])
                    return RateDecision(False, "message_cooldown", max(1, remaining // 1000))

            events = self.db.rate_events_since(key, ACT_MESSAGE, now - DAY_MS)
            if len(events) >= max(1, self.config.message_daily_limit):
                remaining = DAY_MS - (now - events[0])
                return RateDecision(False, "message_daily_limit", max(1, remaining // 1000))

            self.db.add_rate_event(key, ACT_MESSAGE, now)
            self._cleanup(now)
            return RateDecision(True)
