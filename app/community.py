from __future__ import annotations

import math
import threading
import time
from typing import Callable


class ListenerTracker:
    def __init__(
        self,
        ttl_sec: int = 30,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self.ttl_sec = max(5, int(ttl_sec))
        self.clock = clock or time.monotonic
        self._lock = threading.Lock()
        self._listeners: dict[str, float] = {}

    def _cleanup(self, now: float) -> None:
        expired = [
            key
            for key, seen in self._listeners.items()
            if now - seen > self.ttl_sec
        ]
        for key in expired:
            self._listeners.pop(key, None)

    def touch(self, key: str, now: float | None = None) -> int:
        if not key:
            return self.count(now)
        current = self.clock() if now is None else now
        with self._lock:
            self._listeners[key] = current
            self._cleanup(current)
            return len(self._listeners)

    def count(self, now: float | None = None) -> int:
        current = self.clock() if now is None else now
        with self._lock:
            self._cleanup(current)
            return len(self._listeners)


class VoteTracker:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._seq = -1
        self._votes: set[str] = set()
        self._switched_seq = -1

    def _ensure(self, seq: int) -> bool:
        if seq > self._seq:
            self._seq = seq
            self._votes.clear()
            self._switched_seq = -1
        return seq == self._seq

    @staticmethod
    def needed(listeners: int) -> int:
        return max(1, math.ceil(max(0, listeners) * 0.5))

    def stats(
        self, seq: int, listeners: int, voter: str | None = None
    ) -> dict[str, object]:
        with self._lock:
            if not self._ensure(seq):
                return {
                    "votes": 0,
                    "needed": self.needed(listeners),
                    "listeners": listeners,
                    "voted": False,
                }
            votes = len(self._votes)
            needed = self.needed(listeners)
            return {
                "votes": votes,
                "needed": needed,
                "listeners": listeners,
                "voted": bool(voter and voter in self._votes),
            }

    def vote(
        self, seq: int, voter: str, listeners: int
    ) -> dict[str, object]:
        with self._lock:
            if not self._ensure(seq):
                return {
                    "votes": len(self._votes),
                    "needed": self.needed(listeners),
                    "listeners": listeners,
                    "voted": False,
                    "added": False,
                    "passed": False,
                }
            added = voter not in self._votes
            if voter:
                self._votes.add(voter)
            votes = len(self._votes)
            needed = self.needed(listeners)
            return {
                "votes": votes,
                "needed": needed,
                "listeners": listeners,
                "voted": True,
                "added": added,
                "passed": votes >= needed,
            }

    def claim_switch(self, seq: int) -> bool:
        with self._lock:
            if seq != self._seq:
                return False
            if self._switched_seq == seq:
                return False
            self._switched_seq = seq
            return True

    def reset(self, seq: int) -> None:
        with self._lock:
            self._seq = seq
            self._votes.clear()
            self._switched_seq = -1
