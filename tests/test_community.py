from __future__ import annotations

import unittest

from app.community import LeafAdmission, ListenerTracker, VoteTracker


class FakeClock:
    def __init__(self, start: float = 1000.0) -> None:
        self.value = start

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


class ListenerTrackerTests(unittest.TestCase):
    def test_counts_and_expires(self):
        clock = FakeClock()
        tracker = ListenerTracker(ttl_sec=30, clock=clock)
        self.assertEqual(tracker.touch("a", clock()), 1)
        self.assertEqual(tracker.touch("b", clock()), 2)
        self.assertEqual(tracker.count(clock()), 2)

        clock.advance(31)
        self.assertEqual(tracker.count(clock()), 0)


class VoteTrackerTests(unittest.TestCase):
    def test_half_threshold_and_one_vote_per_person(self):
        votes = VoteTracker()
        first = votes.vote(1, "v:a", 3)
        self.assertEqual(first["votes"], 1)
        self.assertEqual(first["needed"], 2)
        self.assertFalse(first["passed"])

        duplicate = votes.vote(1, "v:a", 3)
        self.assertFalse(duplicate["added"])
        self.assertEqual(duplicate["votes"], 1)

        second = votes.vote(1, "v:b", 3)
        self.assertTrue(second["added"])
        self.assertTrue(second["passed"])
        self.assertEqual(second["votes"], 2)

    def test_switch_can_only_be_claimed_once(self):
        votes = VoteTracker()
        votes.vote(1, "v:a", 1)
        self.assertTrue(votes.claim_switch(1))
        self.assertFalse(votes.claim_switch(1))
        votes.vote(2, "v:a", 1)
        self.assertTrue(votes.claim_switch(2))
        self.assertFalse(votes.claim_switch(1))

    def test_old_sequence_cannot_claim_switch(self):
        votes = VoteTracker()
        votes.vote(2, "v:a", 1)
        self.assertFalse(votes.claim_switch(1))
        self.assertTrue(votes.claim_switch(2))

    def test_resets_on_new_sequence(self):
        votes = VoteTracker()
        votes.vote(1, "v:a", 1)
        stats = votes.stats(2, 1, "v:a")
        self.assertEqual(stats["votes"], 0)
        self.assertFalse(stats["voted"])


if __name__ == "__main__":
    unittest.main()


class LeafAdmissionTests(unittest.TestCase):
    def test_caps_distinct_clients_and_expires(self):
        clock = FakeClock()
        admission = LeafAdmission(max_active=2, ttl_sec=10, clock=clock)
        self.assertFalse(LeafAdmission(max_active=0, ttl_sec=10, clock=clock).allow("ip:a"))
        self.assertTrue(admission.allow("ip:a"))
        self.assertTrue(admission.allow("ip:b"))
        self.assertFalse(admission.allow("ip:c"))
        self.assertTrue(admission.allow("ip:a"))
        clock.advance(11)
        self.assertTrue(admission.allow("ip:c"))
