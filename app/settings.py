from __future__ import annotations

from typing import Any

from .config import Config
from .db import Database


KEY_STATION_NAME = "ui.station_name"
KEY_SUBTITLE = "ui.subtitle"
KEY_PAGE_TITLE = "ui.page_title"
KEY_NOTICE = "ui.notice"
KEY_BG_INTERVAL = "ui.background_interval_sec"

MAX_LENGTHS = {
    KEY_STATION_NAME: 40,
    KEY_SUBTITLE: 80,
    KEY_PAGE_TITLE: 60,
    KEY_NOTICE: 300,
}

INPUT_KEYS = {
    "station_name": KEY_STATION_NAME,
    "subtitle": KEY_SUBTITLE,
    "page_title": KEY_PAGE_TITLE,
    "notice": KEY_NOTICE,
    "background_interval_sec": KEY_BG_INTERVAL,
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
        "station_name": values.get(KEY_STATION_NAME) or "我的电台",
        "subtitle": values.get(KEY_SUBTITLE) or "",
        "page_title": values.get(KEY_PAGE_TITLE) or values.get(KEY_STATION_NAME) or "我的电台",
        "notice": values.get(KEY_NOTICE) or "",
        "background_interval_sec": int(values.get(KEY_BG_INTERVAL) or 300),
    }
