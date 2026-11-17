from __future__ import annotations

import json
import urllib.parse
from typing import Any

from .config import Config
from .db import Database


KEY_STATION_NAME = "ui.station_name"
KEY_SUBTITLE = "ui.subtitle"
KEY_PAGE_TITLE = "ui.page_title"
KEY_NOTICE = "ui.notice"
KEY_BG_INTERVAL = "ui.background_interval_sec"
KEY_BACKGROUND_URLS = "ui.background_urls"
KEY_PUBLIC_URL = "ui.public_url"
KEY_LAN_URL = "ui.lan_url"
KEY_TUNNEL_URL = "ui.tunnel_url"
KEY_ACCESS_NOTE = "ui.access_note"
MAX_BACKGROUND_URLS = 20

MAX_LENGTHS = {
    KEY_STATION_NAME: 40,
    KEY_SUBTITLE: 80,
    KEY_PAGE_TITLE: 60,
    KEY_NOTICE: 300,
    KEY_PUBLIC_URL: 300,
    KEY_LAN_URL: 300,
    KEY_TUNNEL_URL: 300,
    KEY_ACCESS_NOTE: 500,
}

URL_KEYS = {KEY_PUBLIC_URL, KEY_LAN_URL, KEY_TUNNEL_URL}

INPUT_KEYS = {
    "station_name": KEY_STATION_NAME,
    "subtitle": KEY_SUBTITLE,
    "page_title": KEY_PAGE_TITLE,
    "notice": KEY_NOTICE,
    "background_interval_sec": KEY_BG_INTERVAL,
    "public_url": KEY_PUBLIC_URL,
    "lan_url": KEY_LAN_URL,
    "tunnel_url": KEY_TUNNEL_URL,
    "access_note": KEY_ACCESS_NOTE,
}


class SettingsError(ValueError):
    pass


def _defaults(config: Config) -> dict[str, Any]:
    return {
        KEY_STATION_NAME: config.station_name,
        KEY_SUBTITLE: config.station_subtitle,
        KEY_PAGE_TITLE: config.page_title or config.station_name,
        KEY_NOTICE: config.notice,
        KEY_BG_INTERVAL: config.background_interval_sec,
        KEY_PUBLIC_URL: "",
        KEY_LAN_URL: "",
        KEY_TUNNEL_URL: "",
        KEY_ACCESS_NOTE: "",
    }


def load_ui_settings(db: Database, config: Config) -> dict[str, Any]:
    values = _defaults(config)
    for key in list(values):
        stored = db.get_setting(key)
        if stored is None:
            continue
        if key == KEY_BG_INTERVAL:
            try:
                values[key] = max(30, min(86_400, int(stored)))
            except (TypeError, ValueError):
                pass
        else:
            values[key] = stored
    return values


def _clean_text(value: Any, key: str, *, required: bool = False) -> str:
    text = " ".join(str(value or "").replace("\r", " ").replace("\n", " ").split())
    limit = MAX_LENGTHS[key]
    if len(text) > limit:
        raise SettingsError(f"文本超过 {limit} 字")
    if required and not text:
        raise SettingsError("内容不能为空")
    return text


def save_ui_settings(
    db: Database, config: Config, values: dict[str, Any]
) -> dict[str, Any]:
    current = load_ui_settings(db, config)
    updates: dict[str, Any] = {}

    for input_key, key in INPUT_KEYS.items():
        if input_key in values:
            value = values[input_key]
        elif key in values:
            value = values[key]
        else:
            continue
        if key == KEY_BG_INTERVAL:
            try:
                interval = int(value)
            except (TypeError, ValueError) as exc:
                raise SettingsError("背景轮换间隔必须是整数") from exc
            updates[key] = max(30, min(86_400, interval))
        elif key in URL_KEYS:
            text = _clean_text(value, key)
            if text:
                try:
                    parts = urllib.parse.urlsplit(text)
                except ValueError as exc:
                    raise SettingsError("访问地址必须是 http/https URL") from exc
                if parts.scheme not in {"http", "https"} or not parts.netloc:
                    raise SettingsError("访问地址必须是 http/https URL")
            updates[key] = text
        else:
            updates[key] = _clean_text(
                value, key, required=key == KEY_STATION_NAME
            )

    if updates:
        db.set_settings(updates)
        current.update(updates)
    return current


def public_settings(values: dict[str, Any]) -> dict[str, Any]:
    return {
        "station_name": values.get(KEY_STATION_NAME) or "猫娘电台",
        "subtitle": values.get(KEY_SUBTITLE) or "",
        "page_title": values.get(KEY_PAGE_TITLE) or values.get(KEY_STATION_NAME) or "猫娘电台",
        "notice": values.get(KEY_NOTICE) or "",
        "background_interval_sec": int(values.get(KEY_BG_INTERVAL) or 300),
    }


def admin_settings(values: dict[str, Any]) -> dict[str, Any]:
    """Admin-only settings, including deployment URLs/notes."""
    result = public_settings(values)
    result.update(
        {
            "public_url": values.get(KEY_PUBLIC_URL) or "",
            "lan_url": values.get(KEY_LAN_URL) or "",
            "tunnel_url": values.get(KEY_TUNNEL_URL) or "",
            "access_note": values.get(KEY_ACCESS_NOTE) or "",
        }
    )
    return result


def _valid_background_url(url: Any) -> bool:
    text = str(url or "").strip()
    if not text or len(text) > 500:
        return False
    try:
        parts = urllib.parse.urlsplit(text)
    except ValueError:
        return False
    return parts.scheme in {"http", "https"} and bool(parts.netloc)


def load_background_urls(db: Database) -> list[str]:
    raw = db.get_setting(KEY_BACKGROUND_URLS) or "[]"
    try:
        values = json.loads(raw)
    except (TypeError, ValueError):
        return []
    if not isinstance(values, list):
        return []
    result: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if _valid_background_url(text) and text not in result:
            result.append(text)
    return result


def save_background_urls(db: Database, urls: list[str]) -> list[str]:
    values: list[str] = []
    for url in urls:
        text = str(url or "").strip()
        if _valid_background_url(text) and text not in values:
            values.append(text)
    db.set_setting(KEY_BACKGROUND_URLS, json.dumps(values, ensure_ascii=False))
    return values


def add_background_url(db: Database, url: Any) -> list[str]:
    text = str(url or "").strip()
    if not _valid_background_url(text):
        raise SettingsError("背景地址必须是 http/https URL")
    urls = load_background_urls(db)
    if text in urls:
        return urls
    if len(urls) >= MAX_BACKGROUND_URLS:
        raise SettingsError(f"网络背景最多 {MAX_BACKGROUND_URLS} 个")
    urls.append(text)
    return save_background_urls(db, urls)


def remove_background_url(db: Database, url: Any) -> list[str]:
    text = str(url or "").strip()
    urls = [item for item in load_background_urls(db) if item != text]
    return save_background_urls(db, urls)
