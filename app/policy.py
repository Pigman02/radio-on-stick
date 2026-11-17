from __future__ import annotations

import ipaddress
import json
from typing import Any

from .db import Database


KEY_MP3_UPLOAD_ONLY = "policy.mp3_upload_only"
KEY_MP3_REMOTE_ONLY = "policy.mp3_remote_only"
KEY_ACCESS_MODE = "policy.access_mode"
KEY_ALLOW_CIDRS = "policy.allow_cidrs"
KEY_MAX_LISTENERS = "policy.max_listeners"
KEY_REQUESTS_ENABLED = "policy.requests_enabled"
KEY_UPLOADS_ENABLED = "policy.uploads_enabled"
KEY_MESSAGES_ENABLED = "policy.messages_enabled"

ACCESS_MODES = {"open", "allowlist"}
POLICY_KEYS = {
    KEY_MP3_UPLOAD_ONLY,
    KEY_MP3_REMOTE_ONLY,
    KEY_ACCESS_MODE,
    KEY_ALLOW_CIDRS,
    KEY_MAX_LISTENERS,
    KEY_REQUESTS_ENABLED,
    KEY_UPLOADS_ENABLED,
    KEY_MESSAGES_ENABLED,
}


class PolicyError(ValueError):
    pass


def _bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value or "").strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    if text == "":
        return default
    return default


def parse_cidrs(raw: Any) -> list[str]:
    if isinstance(raw, list):
        values = [str(item).strip() for item in raw]
    else:
        values = [
            item.strip()
            for item in str(raw or "").replace("\n", ",").split(",")
            if item.strip()
        ]
    result: list[str] = []
    for value in values:
        try:
            network = ipaddress.ip_network(value, strict=False)
        except ValueError as exc:
            raise PolicyError(f"无效的 CIDR：{value}") from exc
        text = str(network)
        if text not in result:
            result.append(text)
    if len(result) > 100:
        raise PolicyError("CIDR 最多 100 条")
    return result


def load_policy(db: Database) -> dict[str, Any]:
    raw_mode = str(db.get_setting(KEY_ACCESS_MODE) or "open").strip().lower()
    if raw_mode not in ACCESS_MODES:
        raw_mode = "open"
    try:
        raw_cidrs = json.loads(db.get_setting(KEY_ALLOW_CIDRS) or "[]")
    except (TypeError, ValueError):
        raw_cidrs = []
    try:
        cidrs = parse_cidrs(raw_cidrs)
    except PolicyError:
        cidrs = []
    try:
        max_listeners = max(0, min(10_000, int(db.get_setting(KEY_MAX_LISTENERS) or 0)))
    except (TypeError, ValueError):
        max_listeners = 0
    return {
        "mp3_upload_only": _bool(db.get_setting(KEY_MP3_UPLOAD_ONLY), False),
        "mp3_remote_only": _bool(db.get_setting(KEY_MP3_REMOTE_ONLY), False),
        "access_mode": raw_mode,
        "allow_cidrs": cidrs,
        "max_listeners": max_listeners,
        "requests_enabled": _bool(db.get_setting(KEY_REQUESTS_ENABLED), True),
        "uploads_enabled": _bool(db.get_setting(KEY_UPLOADS_ENABLED), True),
        "messages_enabled": _bool(db.get_setting(KEY_MESSAGES_ENABLED), True),
    }


def save_policy(db: Database, values: dict[str, Any]) -> dict[str, Any]:
    current = load_policy(db)
    updates: dict[str, str] = {}

    for input_key in (
        "mp3_upload_only",
        "mp3_remote_only",
        "requests_enabled",
        "uploads_enabled",
        "messages_enabled",
    ):
        if input_key in values:
            updates[input_key] = "1" if _bool(values[input_key]) else "0"

    if "access_mode" in values:
        mode = str(values.get("access_mode") or "").strip().lower()
        if mode not in ACCESS_MODES:
            raise PolicyError("准入模式只能是 open 或 allowlist")
        updates[KEY_ACCESS_MODE] = mode

    if "allow_cidrs" in values:
        cidrs = parse_cidrs(values.get("allow_cidrs"))
        updates[KEY_ALLOW_CIDRS] = json.dumps(cidrs, ensure_ascii=False)

    if "max_listeners" in values:
        try:
            max_listeners = int(values.get("max_listeners"))
        except (TypeError, ValueError) as exc:
            raise PolicyError("最大听众人数必须是整数") from exc
        updates[KEY_MAX_LISTENERS] = str(max(0, min(10_000, max_listeners)))

    key_map = {
        "mp3_upload_only": KEY_MP3_UPLOAD_ONLY,
        "mp3_remote_only": KEY_MP3_REMOTE_ONLY,
        "requests_enabled": KEY_REQUESTS_ENABLED,
        "uploads_enabled": KEY_UPLOADS_ENABLED,
        "messages_enabled": KEY_MESSAGES_ENABLED,
    }
    for input_key, stored in list(updates.items()):
        if input_key in key_map:
            updates[key_map[input_key]] = updates.pop(input_key)
    if updates:
        db.set_settings(updates)
        current = load_policy(db)
    return current


def ip_allowed(ip_text: str, cidrs: list[str]) -> bool:
    if not cidrs:
        return False
    try:
        address = ipaddress.ip_address(str(ip_text))
    except ValueError:
        return False
    for item in cidrs:
        try:
            if address in ipaddress.ip_network(item, strict=False):
                return True
        except ValueError:
            continue
    return False
