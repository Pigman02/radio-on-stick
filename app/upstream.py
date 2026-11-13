from __future__ import annotations

import json
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Mapping

import logging


logger = logging.getLogger(__name__)


class UpstreamError(RuntimeError):
    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


@dataclass
class Response:
    status: int
    headers: dict[str, str]
    body: bytes
    url: str

    def text(self) -> str:
        content_type = self.headers.get("content-type", "")
        charset = "utf-8"
        for part in content_type.split(";")[1:]:
            key, _, value = part.strip().partition("=")
            if key.lower() == "charset" and value:
                charset = value.strip("\"' ")
                break
        for encoding in (charset, "utf-8", "gb18030"):
            try:
                return self.body.decode(encoding)
            except (LookupError, UnicodeDecodeError):
                continue
        return self.body.decode("utf-8", "replace")

    def json(self) -> Any:
        return json.loads(self.text())


class HttpClient:
    def __init__(
        self,
        timeout: float = 8.0,
        retries: int = 2,
        user_agent: str = "radio-on-stick/0.1",
        max_bytes: int = 2_000_000,
    ) -> None:
        self.timeout = timeout
        self.retries = max(0, int(retries))
        self.user_agent = user_agent
        self.max_bytes = int(max_bytes)

    def request(
        self,
        method: str,
        url: str,
        *,
        data: Mapping[str, Any] | bytes | str | None = None,
        headers: Mapping[str, str] | None = None,
        timeout: float | None = None,
        max_bytes: int | None = None,
    ) -> Response:
        payload: bytes | None
        if isinstance(data, Mapping):
            payload = urllib.parse.urlencode(data).encode("utf-8")
        elif isinstance(data, str):
            payload = data.encode("utf-8")
        else:
            payload = data

        merged_headers = {
            "User-Agent": self.user_agent,
            "Accept": "application/json,text/plain,*/*",
            "Accept-Encoding": "identity",
        }
        if headers:
            merged_headers.update(headers)

        limit = self.max_bytes if max_bytes is None else int(max_bytes)
        last_error: Exception | None = None
        attempts = self.retries + 1
        for attempt in range(attempts):
            req = urllib.request.Request(url, data=payload, headers=merged_headers, method=method)
            try:
                with urllib.request.urlopen(req, timeout=timeout or self.timeout) as resp:
                    body = resp.read(limit + 1)
                    if len(body) > limit:
                        raise UpstreamError("upstream response too large", resp.status)
                    return Response(
                        status=int(resp.status),
                        headers={k.lower(): v for k, v in resp.headers.items()},
                        body=body,
                        url=resp.geturl(),
                    )
            except urllib.error.HTTPError as exc:
                body = exc.read(limit + 1)
                if len(body) > limit:
                    raise UpstreamError("upstream response too large", exc.code) from exc
                response = Response(
                    status=int(exc.code),
                    headers={k.lower(): v for k, v in exc.headers.items()} if exc.headers else {},
                    body=body,
                    url=exc.geturl(),
                )
                if 500 <= response.status < 600 and attempt + 1 < attempts:
                    last_error = UpstreamError(
                        f"upstream HTTP {response.status}", response.status
                    )
                    time.sleep(0.2 * (attempt + 1))
                    continue
                return response
            except (urllib.error.URLError, socket.timeout, TimeoutError, ConnectionError) as exc:
                last_error = exc
                if attempt + 1 < attempts:
                    time.sleep(0.2 * (attempt + 1))
                    continue
                raise UpstreamError(f"upstream request failed: {exc}") from exc
        raise UpstreamError(f"upstream request failed: {last_error}")

    def get_json(
        self,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        timeout: float | None = None,
    ) -> Any:
        response = self.request("GET", url, headers=headers, timeout=timeout)
        if not 200 <= response.status < 300:
            raise UpstreamError(f"upstream HTTP {response.status}", response.status)
        try:
            return response.json()
        except ValueError as exc:
            raise UpstreamError("upstream returned invalid JSON") from exc

    def post_form_json(
        self,
        url: str,
        form: Mapping[str, Any],
        *,
        headers: Mapping[str, str] | None = None,
        timeout: float | None = None,
    ) -> Any:
        merged = {"Content-Type": "application/x-www-form-urlencoded; charset=UTF-8"}
        if headers:
            merged.update(headers)
        response = self.request("POST", url, data=form, headers=merged, timeout=timeout)
        if not 200 <= response.status < 300:
            raise UpstreamError(f"upstream HTTP {response.status}", response.status)
        try:
            return response.json()
        except ValueError as exc:
            raise UpstreamError("upstream returned invalid JSON") from exc

    def get_text(
        self,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        timeout: float | None = None,
    ) -> str:
        response = self.request("GET", url, headers=headers, timeout=timeout)
        if not 200 <= response.status < 300:
            raise UpstreamError(f"upstream HTTP {response.status}", response.status)
        return response.text()
