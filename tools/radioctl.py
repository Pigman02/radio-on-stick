#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request


def call(base: str, path: str, *, method: str = "GET", payload: dict | None = None, token: str = "") -> dict:
    data = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(f"{base.rstrip('/')}{path}", data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")
        try:
            parsed = json.loads(body)
        except ValueError:
            parsed = {"ok": False, "error": f"HTTP {exc.code}", "message": body}
        raise SystemExit(json.dumps(parsed, ensure_ascii=False, indent=2)) from exc
    except urllib.error.URLError as exc:
        raise SystemExit(f"cannot reach {base}: {exc}") from exc


def administrative(args: argparse.Namespace, path: str, *, payload: dict | None = None) -> dict:
    token = args.token or os.environ.get("RADIO_ADMIN_TOKEN", "")
    if not token:
        raise SystemExit("missing --token or RADIO_ADMIN_TOKEN")
    return call(args.base, path, method="POST", payload=payload, token=token)


def main() -> int:
    parser = argparse.ArgumentParser(description="Radio on Stick control tool")
    parser.add_argument("--base", default="http://127.0.0.1:8080", help="service base URL")
    parser.add_argument("--token", default="", help="admin token (or RADIO_ADMIN_TOKEN)")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("now", help="show /now.json")
    sub.add_parser("state", help="show now/queue/messages")
    sub.add_parser("messages", help="show recent messages")
    sub.add_parser("queue", help="show queue (admin)")
    sub.add_parser("skip", help="skip current track (admin)")
    sub.add_parser("pause", help="pause the station (admin)")
    sub.add_parser("resume", help="resume the station (admin)")
    sub.add_parser("clear-queue", help="clear pending requests (admin)")

    add_parser = sub.add_parser("add", help="add a track to the playlist (admin)")
    add_parser.add_argument("query", help="song title / artist")

    hide_parser = sub.add_parser("hide-message", help="hide a message (admin)")
    hide_parser.add_argument("id", type=int)

    args = parser.parse_args()

    if args.command == "now":
        result = call(args.base, "/now.json")
    elif args.command == "state":
        result = call(args.base, "/api/state")
    elif args.command == "messages":
        result = call(args.base, "/api/messages")
    elif args.command == "queue":
        token = args.token or os.environ.get("RADIO_ADMIN_TOKEN", "")
        result = call(args.base, "/api/admin/queue", token=token)
    elif args.command == "skip":
        result = administrative(args, "/api/admin/skip")
    elif args.command == "pause":
        result = administrative(args, "/api/admin/pause")
    elif args.command == "resume":
        result = administrative(args, "/api/admin/resume")
    elif args.command == "clear-queue":
        result = administrative(args, "/api/admin/clear-queue")
    elif args.command == "add":
        result = administrative(args, "/api/admin/track", payload={"query": args.query})
    elif args.command == "hide-message":
        result = administrative(args, "/api/admin/hide-message", payload={"id": args.id})
    else:
        parser.error("unknown command")

    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
