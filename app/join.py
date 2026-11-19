from __future__ import annotations

import base64
import hashlib
import hmac
import time
from typing import Any

from .directory import code_hash, normalize_code


COOKIE_NAME = "radio_join"


def _secret(config: Any) -> bytes:
    value = str(getattr(config, "join_secret", "") or getattr(config, "ip_hash_salt", ""))
    return hashlib.sha256(("radio-join-secret:" + value).encode()).digest()


def make_token(config: Any, code: str, ttl_sec: int | None = None) -> str:
    ttl = max(60, int(ttl_sec or getattr(config, "join_ttl_sec", 604800)))
    expires = int(time.time()) + ttl
    marker = code_hash(code)[7:23]
    payload = f"v1:{expires}:{marker}"
    signature = hmac.new(_secret(config), payload.encode(), hashlib.sha256).digest()
    return payload + "." + base64.urlsafe_b64encode(signature).rstrip(b"=").decode()


def verify_token(config: Any, token: str, code: str) -> bool:
    try:
        payload, encoded = str(token or "").split(".", 1)
        version, expires_raw, marker = payload.split(":", 2)
        expires = int(expires_raw)
    except (TypeError, ValueError):
        return False
    if version != "v1" or expires < int(time.time()):
        return False
    if marker != code_hash(code)[7:23]:
        return False
    expected = hmac.new(_secret(config), payload.encode(), hashlib.sha256).digest()
    supplied = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
    return hmac.compare_digest(expected, supplied)


def valid_code(code: str) -> bool:
    return len(normalize_code(code)) == 16
