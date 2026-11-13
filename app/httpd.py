from __future__ import annotations

import hmac
import ipaddress
import json
import logging
import mimetypes
import re
import urllib.parse
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from .app import RadioApp
from .db import now_ms
from .ratelimit import make_ip_key
from .settings import (
    SettingsError,
    load_ui_settings,
    public_settings,
    save_ui_settings,
)


logger = logging.getLogger(__name__)

_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_BACKGROUND_NAME = re.compile(r"^[a-f0-9]{32}\.(jpg|png|webp)$")
_ALLOWED_REPORT_EVENTS = {"ended", "error", "stalled"}
_IMAGE_TYPES = {"image/jpeg", "image/jpg", "image/png", "image/webp"}


class ApiError(Exception):
    def __init__(
        self,
        status: int,
        code: str,
        message: str,
        retry_after: int | None = None,
        extra: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.status = int(status)
        self.code = code
        self.message = message
        self.retry_after = retry_after
        self.extra = extra or {}


def _clean_text(value: Any, max_len: int, *, single_line: bool = True) -> str:
    text = str(value or "")
    if single_line:
        text = text.replace("\r", " ").replace("\n", " ")
    text = _CONTROL.sub("", text)
    text = " ".join(text.split()) if single_line else text.strip()
    if len(text) > max_len:
        raise ApiError(400, "too_long", f"文本超过 {max_len} 字")
    return text


def _summary(track: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": track.get("id"),
        "source": track.get("source"),
        "title": track.get("title"),
        "artist": track.get("artist"),
        "album": track.get("album"),
        "duration_ms": track.get("duration_ms"),
        "duration_estimated": bool(track.get("duration_estimated")),
        "cover_url": track.get("cover_url") or "",
    }


def _image_extension(data: bytes) -> str | None:
    if data.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    return None


class RadioServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, server_address: tuple[str, int], app: RadioApp) -> None:
        self.app = app
        super().__init__(server_address, RadioHandler)


class RadioHandler(BaseHTTPRequestHandler):
    server_version = "Radio/0.2"
    protocol_version = "HTTP/1.1"

    @property
    def app(self) -> RadioApp:
        return self.server.app  # type: ignore[attr-defined]

    def log_message(self, fmt: str, *args: Any) -> None:
        logger.debug("%s - %s", self.address_string(), fmt % args)

    def _send_json(
        self,
        status: int,
        payload: dict[str, Any],
        *,
        headers: dict[str, str] | None = None,
        cache: str = "no-store",
    ) -> None:
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_response(int(status))
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", cache)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Connection", "close")
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def _send_error_json(
        self,
        status: int,
        code: str,
        message: str,
        retry_after: int | None = None,
        extra: dict[str, Any] | None = None,
    ) -> None:
        payload = {"ok": False, "error": code, "message": message}
        if extra:
            payload.update(extra)
        headers = {"Retry-After": str(retry_after)} if retry_after else None
        self._send_json(status, payload, headers=headers)

    def _send_file(self, path: Path, *, cache_sec: int = 0) -> None:
        try:
            body = path.read_bytes()
        except OSError:
            self._send_error_json(404, "not_found", "文件不存在")
            return
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        if path.suffix == ".js":
            content_type = "application/javascript; charset=utf-8"
        elif path.suffix in {".css", ".svg", ".html"}:
            content_type = f"{content_type}; charset=utf-8"
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header(
            "Cache-Control",
            f"public, max-age={cache_sec}" if cache_sec else "no-cache",
        )
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "SAMEORIGIN")
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)

    def _static_path(self, url_path: str) -> Path | None:
        relative = url_path.lstrip("/") or "index.html"
        candidate = (self.app.config.static_dir / relative).resolve()
        try:
            candidate.relative_to(self.app.config.static_dir.resolve())
        except ValueError:
            return None
        return candidate

    def _client_ip(self) -> str:
        peer = self.client_address[0]
        try:
            is_loopback = ipaddress.ip_address(peer).is_loopback
        except ValueError:
            is_loopback = False
        if is_loopback:
            forwarded = self.headers.get("X-Real-IP") or ""
            if not forwarded:
                chain = self.headers.get("X-Forwarded-For") or ""
                forwarded = chain.split(",")[0].strip()
            if forwarded:
                try:
                    return str(ipaddress.ip_address(forwarded))
                except ValueError:
                    pass
        return peer

    def _ip_key(self) -> str:
        return make_ip_key(self._client_ip(), self.app.config.ip_hash_salt)

    def _voter_key(self, value: Any = None) -> str:
        # One listener/voter per network address. This avoids counting
        # multiple tabs on the same device as different listeners.
        return f"ip:{self._ip_key()}"

    def _query_listener(self, query_string: str) -> str:
        key = f"ip:{self._ip_key()}"
        self.app.listeners.touch(key)
        return key

    def _decorate_snapshot(
        self, now: dict[str, Any], voter: str | None = None
    ) -> dict[str, Any]:
        listeners = self.app.listeners.count()
        seq = int(now.get("seq") or 0)
        now["listeners"] = listeners
        now["vote"] = self.app.votes.stats(seq, listeners, voter)
        return now

    def _read_json(self) -> dict[str, Any]:
        length_header = self.headers.get("Content-Length")
        if not length_header:
            return {}
        try:
            length = int(length_header)
        except ValueError as exc:
            raise ApiError(400, "bad_request", "无效的 Content-Length") from exc
        if length <= 0:
            return {}
        if length > self.app.config.max_body_bytes:
            raise ApiError(413, "body_too_large", "请求体过大")
        body = self.rfile.read(length)
        content_type = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        if content_type and content_type != "application/json":
            raise ApiError(415, "unsupported_media_type", "只接受 application/json")
        try:
            data = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            raise ApiError(400, "bad_json", "JSON 格式错误") from exc
        if not isinstance(data, dict):
            raise ApiError(400, "bad_json", "JSON 必须是对象")
        return data

    def _read_raw(self, max_bytes: int) -> bytes:
        length_header = self.headers.get("Content-Length")
        if not length_header:
            raise ApiError(411, "length_required", "缺少 Content-Length")
        try:
            length = int(length_header)
        except ValueError as exc:
            raise ApiError(400, "bad_request", "无效的 Content-Length") from exc
        if length <= 0:
            raise ApiError(400, "bad_request", "空文件")
        if length > max_bytes:
            raise ApiError(413, "body_too_large", "文件过大")
        return self.rfile.read(length)

    def _require_admin(self) -> None:
        token = self.app.config.admin_token
        if not token:
            raise ApiError(503, "admin_disabled", "未配置管理 token")
        header = self.headers.get("Authorization") or ""
        prefix = "Bearer "
        supplied = header[len(prefix) :] if header.startswith(prefix) else ""
        if not supplied or not hmac.compare_digest(supplied, token):
            raise ApiError(401, "unauthorized", "管理 token 无效")

    def _backgrounds(self) -> list[dict[str, Any]]:
        root = self.app.config.backgrounds_dir
        if not root.is_dir():
            return []
        items: list[dict[str, Any]] = []
        for path in root.iterdir():
            if not path.is_file() or not _BACKGROUND_NAME.match(path.name):
                continue
            try:
                modified = int(path.stat().st_mtime)
            except OSError:
                continue
            items.append(
                {
                    "name": path.name,
                    "url": f"/backgrounds/{path.name}",
                    "mtime": modified,
                }
            )
        items.sort(key=lambda item: item["mtime"], reverse=True)
        return items

    def _public_config(self) -> dict[str, Any]:
        settings = public_settings(load_ui_settings(self.app.db, self.app.config))
        settings["backgrounds"] = self._backgrounds()
        return settings

    def _form_guard(self, data: dict[str, Any]) -> None:
        if str(data.get("website") or "").strip():
            raise ApiError(400, "bad_request", "请求无效")
        timestamp = data.get("ts")
        if timestamp is None:
            return
        try:
            timestamp = int(timestamp)
        except (TypeError, ValueError) as exc:
            raise ApiError(400, "bad_request", "无效的时间戳") from exc
        current = now_ms()
        if current - timestamp < self.app.config.min_form_ms:
            raise ApiError(429, "too_fast", "提交太快，请稍后再试", retry_after=2)
        if timestamp > current + 300_000:
            raise ApiError(400, "bad_request", "时间戳异常")

    def _check_banned(self, *texts: str) -> None:
        folded = "\n".join(texts).casefold()
        for word in self.app.config.banned_words:
            if word and word.casefold() in folded:
                raise ApiError(400, "blocked_word", "内容包含不允许的词语")

    def do_GET(self) -> None:
        try:
            self._dispatch_get()
        except ApiError as exc:
            self._send_error_json(exc.status, exc.code, exc.message, exc.retry_after, exc.extra)
        except (BrokenPipeError, ConnectionResetError):
            return
        except Exception:
            logger.exception("GET %s failed", self.path)
            self._send_error_json(500, "internal_error", "服务内部错误")

    def do_POST(self) -> None:
        try:
            self._dispatch_post()
        except ApiError as exc:
            self._send_error_json(exc.status, exc.code, exc.message, exc.retry_after, exc.extra)
        except (BrokenPipeError, ConnectionResetError):
            return
        except Exception:
            logger.exception("POST %s failed", self.path)
            self._send_error_json(500, "internal_error", "服务内部错误")

    def _dispatch_get(self) -> None:
        parsed = urllib.parse.urlsplit(self.path)
        path = parsed.path

        if path == "/now.json":
            voter = self._query_listener(parsed.query)
            now = self.app.player.snapshot()
            self._send_json(200, self._decorate_snapshot(now, voter))
            return
        if path == "/api/config":
            self._send_json(200, self._public_config())
            return
        if path == "/api/state":
            voter = self._query_listener(parsed.query)
            now = self._decorate_snapshot(self.app.player.snapshot(), voter)
            self._send_json(
                200,
                {
                    "now": now,
                    "queue": self.app.db.list_queue(20),
                    "messages": self.app.db.list_messages(20),
                    "listeners": now["listeners"],
                },
            )
            return
        if path == "/api/messages":
            self._send_json(200, {"messages": self.app.db.list_messages(20)})
            return
        if path == "/api/search":
            self._handle_search(parsed.query)
            return
        if path.startswith("/api/lyrics/"):
            track_id = urllib.parse.unquote(path[len("/api/lyrics/") :])
            track = self.app.db.get_track(track_id)
            if track is None:
                raise ApiError(404, "not_found", "曲目不存在")
            lrc, translation = self.app.aggregator.ensure_lyrics(track)
            self._send_json(
                200,
                {"track_id": track_id, "lrc": lrc, "translation": translation},
                cache="public, max-age=3600" if lrc else "no-store",
            )
            return
        if path == "/healthz":
            ok = self.app.db.health()
            self._send_json(
                200 if ok else 503,
                {
                    "ok": ok,
                    "db": ok,
                    "seq": self.app.player.snapshot(ensure=False).get("seq"),
                    "time_ms": now_ms(),
                    "sources": [getattr(s, "name", "") for s in self.app.aggregator.search_sources],
                },
            )
            return
        if path == "/api/admin/state":
            self._require_admin()
            self._send_json(
                200,
                {
                    "now": self.app.player.snapshot(),
                    "queue": self.app.db.list_queue(50),
                    "messages": self.app.db.list_messages(50),
                    "settings": public_settings(
                        load_ui_settings(self.app.db, self.app.config)
                    ),
                    "backgrounds": self._backgrounds(),
                },
            )
            return
        if path == "/api/admin/queue":
            self._require_admin()
            self._send_json(
                200,
                {"queue": self.app.db.list_queue(50), "pending": self.app.db.count_pending()},
            )
            return
        if path.startswith("/backgrounds/"):
            name = urllib.parse.unquote(path[len("/backgrounds/") :])
            if not _BACKGROUND_NAME.match(name):
                raise ApiError(404, "not_found", "背景不存在")
            file_path = self.app.config.backgrounds_dir / name
            if not file_path.is_file():
                raise ApiError(404, "not_found", "背景不存在")
            self._send_file(file_path, cache_sec=86_400)
            return
        if path in {"/admin", "/admin/"}:
            admin_page = self.app.config.static_dir / "admin.html"
            if not admin_page.is_file():
                raise ApiError(404, "not_found", "管理页面不存在")
            self._send_file(admin_page, cache_sec=0)
            return

        static = self._static_path(urllib.parse.unquote(path))
        if static is not None and static.is_file():
            cache_sec = 0 if static.name == "index.html" else self.app.config.static_cache_sec
            self._send_file(static, cache_sec=cache_sec)
            return
        raise ApiError(404, "not_found", "路径不存在")

    def _dispatch_post(self) -> None:
        path = urllib.parse.urlsplit(self.path).path
        if path == "/api/messages":
            self._handle_message()
            return
        if path == "/api/request":
            self._handle_request()
            return
        if path == "/api/report":
            self._handle_report()
            return
        if path == "/api/vote-skip":
            self._handle_vote_skip()
            return
        if path == "/api/queue/pin":
            self._handle_queue_pin()
            return
        if path.startswith("/api/admin/"):
            self._require_admin()
            self._handle_admin(path)
            return
        raise ApiError(404, "not_found", "路径不存在")

    def _handle_search(self, query_string: str) -> None:
        params = urllib.parse.parse_qs(query_string)
        query = _clean_text((params.get("q") or [""])[0], 80)
        if not query:
            raise ApiError(400, "missing_query", "请输入歌名")
        try:
            limit = int((params.get("limit") or [""])[0] or self.app.config.search_results_limit)
        except ValueError:
            limit = self.app.config.search_results_limit
        ip_key = self._ip_key()
        decision = self.app.limiter.consume_search(ip_key)
        if not decision.allowed:
            raise ApiError(
                429,
                decision.reason,
                "搜索过于频繁，请稍后再试",
                retry_after=decision.retry_after,
            )
        results = self.app.aggregator.search(query, limit=limit)
        self._send_json(200, {"ok": True, "query": query, "results": results})

    def _handle_message(self) -> None:
        data = self._read_json()
        self._form_guard(data)
        nickname = _clean_text(data.get("nickname"), self.app.config.nickname_max_len) or "匿名"
        body = _clean_text(
            data.get("body"), self.app.config.message_max_len, single_line=False
        )
        if not body:
            raise ApiError(400, "missing_body", "请输入留言")
        self._check_banned(nickname, body)
        ip_key = self._ip_key()
        decision = self.app.limiter.consume_message(ip_key)
        if not decision.allowed:
            reason = f"1 分钟内最多留言 {self.app.config.message_per_min} 条，请稍后再试"
            raise ApiError(429, decision.reason, reason, retry_after=decision.retry_after)
        created_at = now_ms()
        message_id = self.app.db.insert_message(ip_key, nickname, body, None, created_at)
        logger.info("message %s by %s", message_id, nickname)
        self._send_json(
            201,
            {
                "ok": True,
                "message": {
                    "id": message_id,
                    "nickname": nickname,
                    "body": body,
                    "created_at": created_at,
                },
            },
        )

    def _handle_request(self) -> None:
        data = self._read_json()
        self._form_guard(data)
        nickname = _clean_text(data.get("nickname"), self.app.config.nickname_max_len) or "匿名"
        candidate_id = str(data.get("candidate_id") or "").strip()
        query = _clean_text(data.get("query"), 80)
        if not candidate_id and not query:
            raise ApiError(400, "missing_query", "请选择一首歌")
        if candidate_id and not re.fullmatch(r"[a-f0-9]{32}", candidate_id):
            raise ApiError(400, "bad_candidate", "无效的搜索结果")
        self._check_banned(nickname)

        ip_key = self._ip_key()
        precheck = self.app.limiter.precheck_request(ip_key)
        if not precheck.allowed:
            raise ApiError(
                429,
                precheck.reason,
                f"1 分钟内最多点 {self.app.config.request_per_min} 首，请稍后再试",
                retry_after=precheck.retry_after,
            )
        if self.app.db.count_pending() >= self.app.config.queue_max:
            raise ApiError(429, "queue_full", "点歌队列已满，请稍后再试", retry_after=30)

        if candidate_id:
            track = self.app.aggregator.resolve_candidate(candidate_id)
            if track is None:
                raise ApiError(410, "search_expired", "搜索结果已过期，请重新搜索")
        else:
            track = self.app.aggregator.resolve(query)
            if track is None:
                raise ApiError(404, "not_found", "没有找到这首歌，换个关键词试试")

        track_summary = _summary(track)
        duplicate_since = now_ms() - self.app.config.duplicate_window_sec * 1000
        if self.app.db.recent_track_in_queue(track["id"], duplicate_since):
            raise ApiError(409, "duplicate", "这首歌已经在队列里了")
        current = self.app.player.snapshot(ensure=False).get("track")
        if current and current.get("id") == track["id"]:
            raise ApiError(409, "duplicate", "这首歌正在播放")

        decision = self.app.limiter.consume_request(ip_key)
        if not decision.allowed:
            raise ApiError(
                429,
                decision.reason,
                f"1 分钟内最多点 {self.app.config.request_per_min} 首，请稍后再试",
                retry_after=decision.retry_after,
            )

        queue_id = self.app.db.enqueue(track["id"], ip_key, nickname, None)
        logger.info("queued request %s by %s: %s", queue_id, nickname, track.get("title"))
        self._send_json(
            201,
            {
                "ok": True,
                "request_id": queue_id,
                "track": track_summary,
                "queue": {"pending": self.app.db.count_pending()},
            },
        )

    def _handle_vote_skip(self) -> None:
        data = self._read_json()
        voter = self._voter_key(data.get("lid") or data.get("voter_id"))
        self.app.listeners.touch(voter)
        now = self.app.player.snapshot(ensure=False)
        if not now.get("track"):
            raise ApiError(409, "no_track", "当前没有播放")
        seq = int(now.get("seq") or 0)
        listeners = self.app.listeners.count()
        result = self.app.votes.vote(seq, voter, listeners)
        switched = False
        if result["added"] and result["passed"]:
            self.app.player.skip("vote")
            now = self.app.player.snapshot()
            self.app.votes.reset(int(now.get("seq") or 0))
            switched = True
        now = self._decorate_snapshot(now, voter)
        self._send_json(
            200,
            {
                "ok": True,
                "switched": switched,
                "vote": now["vote"],
                "now": now,
            },
        )

    def _handle_queue_pin(self) -> None:
        data = self._read_json()
        try:
            queue_id = int(data.get("queue_id"))
        except (TypeError, ValueError) as exc:
            raise ApiError(400, "bad_queue_id", "无效的队列项") from exc
        voter = self._voter_key(data.get("lid") or data.get("voter_id"))
        result = self.app.db.pin_queue(queue_id, voter)
        if result is None:
            raise ApiError(404, "queue_not_found", "这首歌已经不在队列里")
        self._send_json(
            200,
            {
                "ok": True,
                "queue_id": queue_id,
                "pins": result["pins"],
                "pinned": result["pinned"],
                "queue": self.app.db.list_queue(50),
            },
        )

    def _handle_report(self) -> None:
        data = self._read_json()
        event = str(data.get("event") or "").strip().lower()
        if event not in _ALLOWED_REPORT_EVENTS:
            raise ApiError(400, "bad_event", "无效的上报事件")
        try:
            seq = int(data.get("seq"))
        except (TypeError, ValueError) as exc:
            raise ApiError(400, "bad_seq", "无效的 seq") from exc
        ip_key = self._ip_key()
        decision = self.app.limiter.consume_report(ip_key)
        if not decision.allowed:
            raise ApiError(
                429,
                decision.reason,
                "上报过于频繁",
                retry_after=decision.retry_after,
            )
        self._send_json(200, self.app.player.report(seq, event))

    def _handle_admin(self, path: str) -> None:
        if path == "/api/admin/skip":
            self.app.player.skip("admin")
            self._send_json(200, {"ok": True, "now": self.app.player.snapshot()})
            return
        if path == "/api/admin/pause":
            self.app.player.pause()
            self._send_json(200, {"ok": True, "now": self.app.player.snapshot()})
            return
        if path == "/api/admin/resume":
            self.app.player.resume()
            self._send_json(200, {"ok": True, "now": self.app.player.snapshot()})
            return
        if path == "/api/admin/settings":
            data = self._read_json()
            try:
                values = save_ui_settings(self.app.db, self.app.config, data)
            except SettingsError as exc:
                raise ApiError(400, "bad_settings", str(exc)) from exc
            self._send_json(200, {"ok": True, "settings": public_settings(values)})
            return
        if path == "/api/admin/track":
            data = self._read_json()
            candidate_id = str(data.get("candidate_id") or "").strip()
            query = _clean_text(data.get("query"), 80)
            if candidate_id:
                track = self.app.aggregator.resolve_candidate(candidate_id)
            elif query:
                track = self.app.aggregator.resolve(query)
            else:
                raise ApiError(400, "missing_query", "请输入歌名或选择搜索结果")
            if track is None:
                raise ApiError(404, "not_found", "没有找到这首歌")
            self.app.db.update_track(track["id"], {"enabled": True})
            self._send_json(201, {"ok": True, "track": _summary(track)})
            return
        if path == "/api/admin/clear-queue":
            cleared = self.app.db.clear_pending()
            self._send_json(200, {"ok": True, "cleared": cleared})
            return
        if path == "/api/admin/hide-message":
            data = self._read_json()
            try:
                message_id = int(data.get("id"))
            except (TypeError, ValueError) as exc:
                raise ApiError(400, "bad_id", "无效的留言 ID") from exc
            self.app.db.hide_message(message_id)
            self._send_json(200, {"ok": True})
            return
        if path == "/api/admin/background":
            self._handle_background_upload()
            return
        if path == "/api/admin/background/delete":
            data = self._read_json()
            name = str(data.get("name") or "").strip()
            if not _BACKGROUND_NAME.match(name):
                raise ApiError(400, "bad_name", "无效的背景文件")
            target = self.app.config.backgrounds_dir / name
            if not target.is_file():
                raise ApiError(404, "not_found", "背景不存在")
            try:
                target.unlink()
            except OSError as exc:
                raise ApiError(500, "delete_failed", "删除失败") from exc
            self._send_json(200, {"ok": True})
            return
        raise ApiError(404, "not_found", "管理路径不存在")

    def _handle_background_upload(self) -> None:
        content_type = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        if content_type and content_type not in _IMAGE_TYPES:
            raise ApiError(415, "unsupported_media_type", "只支持 JPG/PNG/WEBP")
        data = self._read_raw(self.app.config.background_max_bytes)
        extension = _image_extension(data)
        if extension is None:
            raise ApiError(400, "bad_image", "不是有效的图片文件")
        name = f"{uuid.uuid4().hex}.{extension}"
        target = self.app.config.backgrounds_dir / name
        try:
            target.write_bytes(data)
        except OSError as exc:
            raise ApiError(500, "write_failed", "保存失败") from exc
        self._send_json(
            201,
            {
                "ok": True,
                "background": {
                    "name": name,
                    "url": f"/backgrounds/{name}",
                },
            },
        )


def make_server(app: RadioApp, host: str | None = None, port: int | None = None) -> RadioServer:
    bind_host = host if host is not None else app.config.host
    bind_port = port if port is not None else app.config.port
    return RadioServer((bind_host, int(bind_port)), app)
