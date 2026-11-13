from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping


BASE_DIR = Path(__file__).resolve().parent.parent


def _text(env: Mapping[str, str], key: str, default: str) -> str:
    value = env.get(key)
    return default if value is None else value


def _int(env: Mapping[str, str], key: str, default: int) -> int:
    value = env.get(key)
    if value is None or value == "":
        return default
    return int(value)


def _float(env: Mapping[str, str], key: str, default: float) -> float:
    value = env.get(key)
    if value is None or value == "":
        return default
    return float(value)


def _csv(env: Mapping[str, str], key: str, default: tuple[str, ...]) -> tuple[str, ...]:
    value = env.get(key)
    if value is None or value.strip() == "":
        return default
    return tuple(item.strip() for item in value.split(",") if item.strip())


@dataclass(frozen=True)
class Config:
    host: str = "127.0.0.1"
    port: int = 8080
    data_dir: Path = BASE_DIR / "data"
    db_path: Path = BASE_DIR / "data" / "radio.db"
    static_dir: Path = BASE_DIR / "app" / "static"
    station_name: str = "我的电台"
    station_subtitle: str = "分享一首歌，留一句话"
    page_title: str = ""
    notice: str = ""
    admin_token: str = ""
    ip_hash_salt: str = "dev-salt-change-me"
    request_per_min: int = 5
    message_per_min: int = 10
    queue_max: int = 20
    duplicate_window_sec: int = 1800
    message_max_len: int = 200
    nickname_max_len: int = 16
    min_form_ms: int = 1200
    report_limit_per_min: int = 10
    search_per_min: int = 20
    lyrics_per_min: int = 30
    lyrics_retry_sec: int = 600
    pin_per_min: int = 10
    search_limit: int = 5
    search_results_limit: int = 12
    search_cache_ttl_sec: int = 900
    background_interval_sec: int = 300
    background_max_bytes: int = 8 * 1024 * 1024
    listener_ttl_sec: int = 30
    banned_words: tuple[str, ...] = field(default_factory=tuple)
    default_duration_ms: int = 240_000
    poll_interval_sec: float = 0.5
    upstream_timeout: float = 8.0
    upstream_retries: int = 2
    user_agent: str = (
        "Mozilla/5.0 (X11; Linux aarch64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    )
    netease_search_url: str = "https://music.163.com/api/search/get/web"
    netease_lyric_url: str = "https://music.163.com/api/song/lyric"
    qq_search_url: str = "https://music.txqq.pro/"
    meting_base_url: str = "https://api.qijieya.cn/meting/"
    max_body_bytes: int = 16 * 1024
    static_cache_sec: int = 3600
    log_level: str = "INFO"

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "Config":
        source = os.environ if env is None else env
        data_dir = Path(_text(source, "RADIO_DATA_DIR", str(BASE_DIR / "data")))
        db_path = Path(_text(source, "RADIO_DB_PATH", str(data_dir / "radio.db")))
        return cls(
            host=_text(source, "RADIO_HOST", "127.0.0.1"),
            port=_int(source, "RADIO_PORT", 8080),
            data_dir=data_dir,
            db_path=db_path,
            static_dir=Path(_text(source, "RADIO_STATIC_DIR", str(BASE_DIR / "app" / "static"))),
            station_name=_text(source, "RADIO_STATION_NAME", "我的电台"),
            station_subtitle=_text(
                source, "RADIO_STATION_SUBTITLE", "分享一首歌，留一句话"
            ),
            page_title=_text(source, "RADIO_PAGE_TITLE", ""),
            notice=_text(source, "RADIO_NOTICE", ""),
            admin_token=_text(source, "RADIO_ADMIN_TOKEN", ""),
            ip_hash_salt=_text(source, "RADIO_IP_SALT", "dev-salt-change-me"),
            request_per_min=_int(source, "RADIO_REQUEST_PER_MIN", 5),
            message_per_min=_int(source, "RADIO_MESSAGE_PER_MIN", 10),
            queue_max=_int(source, "RADIO_QUEUE_MAX", 20),
            duplicate_window_sec=_int(source, "RADIO_DUPLICATE_WINDOW", 1800),
            message_max_len=_int(source, "RADIO_MESSAGE_MAX", 200),
            nickname_max_len=_int(source, "RADIO_NICK_MAX", 16),
            min_form_ms=_int(source, "RADIO_MIN_FORM_MS", 1200),
            report_limit_per_min=_int(source, "RADIO_REPORT_PER_MIN", 10),
            search_per_min=_int(source, "RADIO_SEARCH_PER_MIN", 20),
            lyrics_per_min=_int(source, "RADIO_LYRICS_PER_MIN", 30),
            lyrics_retry_sec=_int(source, "RADIO_LYRICS_RETRY", 600),
            pin_per_min=_int(source, "RADIO_PIN_PER_MIN", 10),
            banned_words=_csv(source, "RADIO_BANNED_WORDS", ()),
            default_duration_ms=_int(source, "RADIO_DEFAULT_DURATION_MS", 240_000),
            poll_interval_sec=_float(source, "RADIO_POLL_INTERVAL", 0.5),
            search_limit=_int(source, "RADIO_SEARCH_LIMIT", 5),
            search_results_limit=_int(source, "RADIO_SEARCH_RESULTS", 12),
            search_cache_ttl_sec=_int(source, "RADIO_SEARCH_CACHE_TTL", 900),
            background_interval_sec=_int(source, "RADIO_BG_INTERVAL", 300),
            background_max_bytes=_int(
                source, "RADIO_BG_MAX_BYTES", 8 * 1024 * 1024
            ),
            listener_ttl_sec=_int(source, "RADIO_LISTENER_TTL", 30),
            upstream_timeout=_float(source, "RADIO_UPSTREAM_TIMEOUT", 8.0),
            upstream_retries=_int(source, "RADIO_UPSTREAM_RETRIES", 2),
            user_agent=_text(source, "RADIO_USER_AGENT", cls.user_agent),
            netease_search_url=_text(
                source, "RADIO_NETEASE_SEARCH_URL", "https://music.163.com/api/search/get/web"
            ),
            netease_lyric_url=_text(
                source, "RADIO_NETEASE_LYRIC_URL", "https://music.163.com/api/song/lyric"
            ),
            qq_search_url=_text(source, "RADIO_QQ_SEARCH_URL", "https://music.txqq.pro/"),
            meting_base_url=_text(
                source, "RADIO_METING_BASE_URL", "https://api.qijieya.cn/meting/"
            ),
            max_body_bytes=_int(source, "RADIO_MAX_BODY_BYTES", 16 * 1024),
            static_cache_sec=_int(source, "RADIO_STATIC_CACHE", 3600),
            log_level=_text(source, "RADIO_LOG_LEVEL", "INFO"),
        )

    @property
    def backgrounds_dir(self) -> Path:
        return self.data_dir / "backgrounds"

    def ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.backgrounds_dir.mkdir(parents=True, exist_ok=True)
