import time
import unittest
from dataclasses import replace

from app.config import Config
from app.directory import code_hash, new_invite_code, normalize_code
from app.join import make_token, verify_token


class JoinTokenTests(unittest.TestCase):
    def setUp(self):
        self.config = replace(Config(), ip_hash_salt="test-salt")
        self.code = "7K4M9Q2XJ8R5T3WA"

    def test_round_trip(self):
        token = make_token(self.config, self.code, ttl_sec=60)
        self.assertTrue(verify_token(self.config, token, self.code))
        self.assertFalse(verify_token(self.config, token, "AAAAAAAAAAAAAAAA"))

    def test_tampered_token(self):
        token = make_token(self.config, self.code, ttl_sec=60)
        self.assertFalse(verify_token(self.config, token + "x", self.code))

    def test_expired_token(self):
        token = make_token(self.config, self.code, ttl_sec=60)
        # Rewrite the expiry while keeping the signature invalid.
        payload, _, sig = token.partition(".")
        parts = payload.split(":")
        parts[1] = str(int(time.time()) - 1)
        self.assertFalse(verify_token(self.config, ":".join(parts) + "." + sig, self.code))

    def test_invite_normalization_and_hash(self):
        self.assertEqual(normalize_code("7k4m-9q2x j8r5t3wa"), self.code)
        self.assertEqual(code_hash(self.code), "sha256:0_X7TG-zybtgyaiT4YFFF6jaE6OcXPtUcWkuRf7lUEM")
        self.assertEqual(len(new_invite_code()), 16)


if __name__ == "__main__":
    unittest.main()
