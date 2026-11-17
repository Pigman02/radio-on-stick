from __future__ import annotations

import base64
import hashlib
import json
import logging
import socket
import struct
import threading
import time
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
MAX_CHILDREN = 4
MAX_PARENTS = 2
MIN_PARENTS = 2
MAX_SEEDS = 2
MAX_MESSAGE = 256 * 1024
HEARTBEAT_SEC = 10.0
IDLE_SEC = 30.0
BLOCK_PARENT_SEC = 8.0


class WsClosed(RuntimeError):
    pass


class WsConnection:
    """Minimal RFC6455 server side: text JSON, ping/pong and close."""

    def __init__(self, sock: socket.socket, initial: bytes = b"") -> None:
        self.sock = sock
        self.buffer = bytearray(initial)
        self.send_lock = threading.Lock()
        self.closed = False
        self.last_recv = time.monotonic()
        self.last_ping = self.last_recv
        self.fragments: list[bytes] = []
        self.fragment_opcode = 0

    def _send_frame(self, opcode: int, payload: bytes) -> None:
        if self.closed:
            raise WsClosed("connection closed")
        header = bytearray([0x80 | (opcode & 0x0F)])
        length = len(payload)
        if length < 126:
            header.append(length)
        elif length < 65536:
            header.append(126)
            header.extend(struct.pack(">H", length))
        else:
            header.append(127)
            header.extend(struct.pack(">Q", length))
        with self.send_lock:
            try:
                self.sock.sendall(bytes(header) + payload)
            except OSError as exc:
                self.closed = True
                raise WsClosed(str(exc)) from exc

    def send_text(self, text: str) -> None:
        self._send_frame(0x1, text.encode("utf-8"))

    def send_json(self, payload: dict[str, Any]) -> None:
        self.send_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))

    def send_pong(self, payload: bytes) -> None:
        self._send_frame(0xA, payload)

    def send_close(self) -> None:
        if self.closed:
            return
        try:
            self._send_frame(0x8, b"")
        except WsClosed:
            pass
        self.closed = True

    def close(self) -> None:
        self.closed = True
        try:
            self.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            self.sock.close()
        except OSError:
            pass

    def feed(self, data: bytes) -> list[tuple[int, bytes]]:
        if data:
            self.buffer.extend(data)
        messages: list[tuple[int, bytes]] = []
        while True:
            if len(self.buffer) < 2:
                break
            first = self.buffer[0]
            second = self.buffer[1]
            fin = bool(first & 0x80)
            opcode = first & 0x0F
            masked = bool(second & 0x80)
            length = second & 0x7F
            offset = 2
            if length == 126:
                if len(self.buffer) < 4:
                    break
                length = struct.unpack_from(">H", self.buffer, 2)[0]
                offset = 4
            elif length == 127:
                if len(self.buffer) < 10:
                    break
                length = struct.unpack_from(">Q", self.buffer, 2)[0]
                offset = 10
            if length > MAX_MESSAGE:
                raise WsClosed("websocket message too large")
            mask = b""
            if masked:
                if len(self.buffer) < offset + 4:
                    break
                mask = bytes(self.buffer[offset : offset + 4])
                offset += 4
            if len(self.buffer) < offset + length:
                break
            payload = bytes(self.buffer[offset : offset + length])
            del self.buffer[: offset + length]
            if masked and mask:
                payload = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))

            if opcode == 0x8:
                messages.append((opcode, payload))
                self.closed = True
                break
            if opcode == 0x9:
                self.send_pong(payload)
                continue
            if opcode == 0xA:
                continue
            if opcode in (0x1, 0x2, 0x0):
                if opcode == 0x0:
                    if not self.fragments:
                        raise WsClosed("unexpected continuation")
                    self.fragments.append(payload)
                    if fin:
                        complete = b"".join(self.fragments)
                        messages.append((self.fragment_opcode, complete))
                        self.fragments = []
                        self.fragment_opcode = 0
                elif fin:
                    messages.append((opcode, payload))
                else:
                    self.fragment_opcode = opcode
                    self.fragments = [payload]
                continue
        return messages


@dataclass
class Node:
    id: str
    ws: WsConnection
    preferred_role: str = "child"
    can_relay: bool = True
    role: str = "child"
    parents: set[str] = field(default_factory=set)
    children: set[str] = field(default_factory=set)
    joined_at: float = field(default_factory=time.monotonic)
    hello: bool = False
    waiting: bool = False
    blocked_parents: dict[str, float] = field(default_factory=dict)


class MeshHub:
    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.nodes: dict[str, Node] = {}
        self.seeds: set[str] = set()
        self.next_number = 0
        self.closed = False

    # -- low level ---------------------------------------------------------
    def _log(self, message: str) -> None:
        logger.info("mesh %s", message)

    def _send(self, node: Node, payload: dict[str, Any]) -> bool:
        try:
            node.ws.send_json(payload)
            return True
        except (WsClosed, OSError):
            node.ws.closed = True
            return False

    def _error(self, node: Node, code: str, message: str) -> None:
        self._send(node, {"type": "error", "code": code, "message": message})

    def new_node(self, ws: WsConnection) -> Node:
        with self.lock:
            self.next_number += 1
            return Node(id=f"n{self.next_number}", ws=ws)

    def serve(self, node: Node) -> None:
        """Read frames until close; call cleanup in all cases."""
        try:
            node.ws.sock.settimeout(1.0)
            while not self.closed and not node.ws.closed:
                now = time.monotonic()
                if node.hello and now - node.ws.last_recv > IDLE_SEC:
                    break
                if not node.hello and now - node.joined_at > 15.0:
                    break
                if now - node.ws.last_ping > HEARTBEAT_SEC:
                    try:
                        node.ws._send_frame(0x9, b"r")
                    except WsClosed:
                        break
                    node.ws.last_ping = now
                try:
                    data = node.ws.sock.recv(8192)
                except socket.timeout:
                    continue
                except OSError:
                    break
                if not data:
                    break
                node.ws.last_recv = time.monotonic()
                try:
                    messages = node.ws.feed(data)
                except WsClosed:
                    break
                for opcode, payload in messages:
                    if opcode == 0x8:
                        try:
                            node.ws.send_close()
                        except WsClosed:
                            pass
                        break
                    if opcode != 0x1:
                        continue
                    try:
                        message = json.loads(payload.decode("utf-8"))
                    except (UnicodeDecodeError, ValueError):
                        self._error(node, "bad_json", "消息不是合法 JSON")
                        continue
                    if isinstance(message, dict):
                        self.handle(node, message)
                if node.ws.closed:
                    break
        finally:
            self.cleanup(node)

    # -- topology ----------------------------------------------------------
    def target_seed_count(self) -> int:
        if len(self.nodes) >= 2:
            return MAX_SEEDS
        if len(self.nodes) >= 1:
            return 1
        return 0

    def _choose_seed(self) -> Node | None:
        candidates = [
            n
            for n in self.nodes.values()
            if n.role == "child" and n.hello and n.can_relay
        ]
        candidates.sort(
            key=lambda n: (
                0 if n.preferred_role == "seed" else 1,
                -len(n.children),
                n.joined_at,
            )
        )
        return candidates[0] if candidates else None

    def _promote(self, node: Node) -> None:
        if node.role == "seed" or not node.can_relay:
            return
        for parent_id in list(node.parents):
            self._detach(parent_id, node.id, "promoted", notify_parent=True, notify_child=False)
        node.parents.clear()
        node.waiting = False
        node.blocked_parents.clear()
        node.role = "seed"
        self.seeds.add(node.id)
        self._send(
            node,
            {
                "type": "role",
                "role": "seed",
                "source": {
                    "manifest_url": "/source/manifest",
                    "chunk_url": "/source/chunk",
                },
            },
        )
        self._log(f"promote {node.id} -> seed")

    def _replacement_candidate(self, newcomer: Node) -> Node | None:
        """Pick an existing server seed to replace for an explicit seed request.

        Prefer a seed that did not ask for the seed role, then the least-loaded
        seed, then the oldest one.  Existing child links are preserved: the old
        seed becomes a normal relay child and keeps its downstreams.
        """
        if not newcomer.can_relay or newcomer.preferred_role != "seed":
            return None
        if len(self.seeds) < MAX_SEEDS:
            return None
        candidates = [self.nodes.get(node_id) for node_id in self.seeds]
        candidates = [node for node in candidates if node is not None and node.role == "seed"]
        if not candidates:
            return None
        candidates.sort(
            key=lambda node: (
                0 if node.preferred_role != "seed" else 1,
                len(node.children),
                node.joined_at,
            )
        )
        return candidates[0]

    def _replace_seed(self, newcomer: Node) -> bool:
        old = self._replacement_candidate(newcomer)
        if old is None or old.id == newcomer.id:
            return False
        self.seeds.discard(old.id)
        old.role = "child"
        old.waiting = False
        old.blocked_parents.clear()
        # A seed has no upstream, but keep this safe if state was odd.
        for parent_id in list(old.parents):
            self._detach(
                parent_id,
                old.id,
                "seed-replaced",
                notify_parent=True,
                notify_child=False,
            )
        old.parents.clear()
        self._send(old, {"type": "role", "role": "child"})
        newcomer.role = "seed"
        self.seeds.add(newcomer.id)
        self._log(
            f"replace seed {old.id} -> {newcomer.id} "
            f"(preferred={old.preferred_role})"
        )
        self._ensure_parents(old, "seed-replaced")
        self.top_up_all("seed-replaced")
        return True

    def maintain_seeds(self) -> None:
        target = self.target_seed_count()
        guard = 0
        while len(self.seeds) < target and guard < 10:
            guard += 1
            candidate = self._choose_seed()
            if candidate is None:
                break
            self._promote(candidate)

    def _is_linked(self, a: Node, b: Node) -> bool:
        return a.id in b.parents or a.id in b.children

    def _is_ancestor(self, ancestor_id: str, node_id: str) -> bool:
        seen: set[str] = set()
        stack = [node_id]
        while stack:
            current_id = stack.pop()
            if current_id in seen:
                continue
            seen.add(current_id)
            current = self.nodes.get(current_id)
            if current is None:
                continue
            for parent_id in current.parents:
                if parent_id == ancestor_id:
                    return True
                stack.append(parent_id)
        return False

    def _candidates_for(self, child: Node) -> list[Node]:
        result: list[Node] = []
        seen: set[str] = set()
        queue: list[str] = sorted(self.seeds)
        while queue:
            node_id = queue.pop(0)
            if node_id in seen:
                continue
            seen.add(node_id)
            candidate = self.nodes.get(node_id)
            if candidate is None or candidate.id == child.id:
                continue
            # Keep walking the tree even when this node is full or already a
            # parent: its children may still have downstream capacity and are
            # exactly the relays needed once the two seeds fill their slots.
            queue.extend(sorted(candidate.children))
            if not candidate.can_relay:
                continue
            if candidate.role == "child" and len(candidate.parents) < MIN_PARENTS:
                continue
            if candidate.id in child.parents:
                continue
            if len(candidate.children) >= MAX_CHILDREN:
                continue
            if (child.blocked_parents.get(candidate.id) or 0) > time.time():
                continue
            if self._is_ancestor(child.id, candidate.id):
                continue
            result.append(candidate)
        return result

    def _add_parent(self, child: Node, parent: Node, reason: str) -> bool:
        if not child.hello or not parent.hello:
            return False
        if child.role == "seed" or child.id == parent.id:
            return False
        if len(child.parents) >= MAX_PARENTS or len(parent.children) >= MAX_CHILDREN:
            return False
        if not parent.can_relay:
            return False
        if parent.id in child.parents:
            return False
        if self._is_ancestor(child.id, parent.id):
            return False
        if (child.blocked_parents.get(parent.id) or 0) > time.time():
            return False
        child.parents.add(parent.id)
        parent.children.add(child.id)
        child.waiting = False
        self._send(child, {"type": "parent-added", "parent_id": parent.id, "reason": reason})
        self._send(parent, {"type": "child-joined", "child_id": child.id, "reason": reason})
        self._log(f"link {parent.id} -> {child.id} ({reason})")
        return True

    def _detach(
        self,
        parent_id: str,
        child_id: str,
        reason: str,
        *,
        notify_parent: bool = True,
        notify_child: bool = True,
    ) -> bool:
        parent = self.nodes.get(parent_id)
        child = self.nodes.get(child_id)
        if parent is None or child is None:
            return False
        if child_id not in parent.children:
            return False
        parent.children.discard(child_id)
        child.parents.discard(parent_id)
        if notify_parent:
            self._send(parent, {"type": "child-left", "child_id": child_id, "reason": reason})
        if notify_child:
            self._send(child, {"type": "parent-removed", "parent_id": parent_id, "reason": reason})
        self._log(f"unlink {parent_id} -> {child_id} ({reason})")
        return True

    def _ensure_parents(self, child: Node, reason: str) -> None:
        if not child.hello or child.role == "seed":
            return
        if len(child.parents) >= MAX_PARENTS:
            return
        for candidate in self._candidates_for(child):
            if len(child.parents) >= MAX_PARENTS:
                break
            self._add_parent(child, candidate, reason)
        if len(child.parents) < MIN_PARENTS:
            if not child.waiting:
                child.waiting = True
                reason_code = "no_capacity" if not child.parents else "need_second_parent"
                self._send(child, {"type": "waiting", "reason": reason_code})
                self._log(
                    f"wait {child.id}: {reason_code} parents={len(child.parents)}"
                )
        else:
            child.waiting = False

    def top_up_all(self, reason: str) -> None:
        for child in sorted(
            (n for n in self.nodes.values() if n.role == "child" and n.hello),
            key=lambda n: n.joined_at,
        ):
            if len(child.parents) < MAX_PARENTS:
                self._ensure_parents(child, reason)

    def _block_parent(self, child: Node, parent_id: str) -> None:
        child.blocked_parents[parent_id] = time.time() + BLOCK_PARENT_SEC

    # -- messages ----------------------------------------------------------
    def handle(self, node: Node, message: dict[str, Any]) -> None:
        kind = str(message.get("type") or "")
        with self.lock:
            if kind == "hello":
                self._hello(node, message)
                return
            if not node.hello:
                self._error(node, "hello_required", "请先发送 hello")
                return
            if kind == "signal":
                self._signal(node, message)
            elif kind == "upstream-lost":
                self._upstream_lost(node, message)
            elif kind == "downstream-lost":
                self._downstream_lost(node, message)
            elif kind == "bye":
                self.cleanup(node)
            elif kind == "ping":
                self._send(node, {"type": "pong", "ts": int(time.time() * 1000)})

    def _hello(self, node: Node, message: dict[str, Any]) -> None:
        if node.hello:
            self._error(node, "already_hello", "重复 hello")
            return
        raw_preferred = str(message.get("preferred_role") or "child").lower()
        explicit_leaf = raw_preferred == "leaf"
        can_relay = (
            not explicit_leaf
            and message.get("can_relay") is not False
        )
        preferred = "seed" if raw_preferred == "seed" and can_relay else "child"
        node.preferred_role = preferred
        node.can_relay = can_relay
        node.hello = True
        node.joined_at = time.monotonic()
        self.nodes[node.id] = node
        target = self.target_seed_count()
        # At two nodes both devices are seeds, regardless of the browser's
        # preference.  Above that the preferred role only breaks ties when a
        # seed has to be promoted after a failure.
        if self._replace_seed(node):
            role = "seed"
        elif not node.can_relay:
            role = "child"
        elif not self.seeds or len(self.seeds) < target:
            role = "seed"
        else:
            role = "child"
        node.role = role
        if role == "seed":
            self.seeds.add(node.id)
        self._send(
            node,
            {
                "type": "welcome",
                "id": node.id,
                "role": role,
                "preferred_role": preferred,
                "capacity": MAX_CHILDREN,
                "max_parents": MAX_PARENTS,
                "min_parents": MIN_PARENTS,
                "can_relay": node.can_relay,
                "source": {
                    "manifest_url": "/source/manifest",
                    "chunk_url": "/source/chunk",
                },
            },
        )
        self._log(f"hello {node.id} preferred={preferred} role={role} nodes={len(self.nodes)}")
        self.maintain_seeds()
        self.top_up_all("join")

    def _signal(self, node: Node, message: dict[str, Any]) -> None:
        target_id = str(message.get("to") or "")
        payload = message.get("payload")
        if not target_id or not isinstance(payload, dict):
            self._error(node, "bad_signal", "信令缺少 to 或 payload")
            return
        target = self.nodes.get(target_id)
        if target is None or not target.hello:
            self._error(node, "not_found", "目标节点不存在")
            return
        if not self._is_linked(node, target):
            self._error(node, "not_neighbor", "只能和直接父/子节点交换信令")
            return
        kind = payload.get("kind")
        if kind not in {"offer", "answer", "ice"}:
            self._error(node, "bad_signal", "未知信令类型")
            return
        if kind in {"offer", "answer"}:
            if not isinstance(payload.get("sdp"), str) or len(payload["sdp"]) > 200_000:
                self._error(node, "bad_signal", "SDP 无效")
                return
        if kind == "ice" and not isinstance(payload.get("candidate"), dict):
            self._error(node, "bad_signal", "ICE 候选无效")
            return
        self._send(target, {"type": "signal", "from": node.id, "payload": payload})

    def _upstream_lost(self, node: Node, message: dict[str, Any]) -> None:
        parent_id = str(message.get("parent_id") or "")
        if not parent_id or parent_id not in node.parents:
            return
        self._detach(parent_id, node.id, "upstream-lost", notify_parent=True, notify_child=False)
        self._block_parent(node, parent_id)
        node.waiting = False
        self._ensure_parents(node, "upstream-lost")
        self.top_up_all("upstream-lost")

    def _downstream_lost(self, node: Node, message: dict[str, Any]) -> None:
        child_id = str(message.get("child_id") or "")
        if not child_id or child_id not in node.children:
            return
        child = self.nodes.get(child_id)
        if child is None:
            self._detach(node.id, child_id, "downstream-lost")
            return
        self._detach(node.id, child_id, "downstream-lost", notify_parent=False, notify_child=True)
        self._block_parent(child, node.id)
        child.waiting = False
        self._ensure_parents(child, "downstream-lost")
        self.top_up_all("downstream-lost")

    def cleanup(self, node: Node) -> None:
        with self.lock:
            if node.id not in self.nodes:
                node.ws.close()
                return
            self._log(f"leave {node.id} role={node.role}")
            for child_id in list(node.children):
                self._detach(node.id, child_id, "parent-left", notify_parent=False, notify_child=True)
            for parent_id in list(node.parents):
                self._detach(parent_id, node.id, "child-left", notify_parent=True, notify_child=False)
            self.nodes.pop(node.id, None)
            self.seeds.discard(node.id)
            try:
                node.ws.send_close()
            except (WsClosed, OSError):
                pass
            node.ws.close()
            self.maintain_seeds()
            self.top_up_all("leave")

    def status(self) -> dict[str, Any]:
        with self.lock:
            return {
                "seeds": sorted(self.seeds),
                "nodes": [
                    {
                        "id": n.id,
                        "role": n.role,
                        "parents": sorted(n.parents),
                        "children": sorted(n.children),
                        "preferred": n.preferred_role,
                        "can_relay": n.can_relay,
                        "waiting": n.waiting,
                    }
                    for n in sorted(self.nodes.values(), key=lambda item: item.id)
                ],
            }

    def shutdown(self) -> None:
        with self.lock:
            self.closed = True
            nodes = list(self.nodes.values())
            self.nodes.clear()
            self.seeds.clear()
        for node in nodes:
            try:
                node.ws.send_close()
            except Exception:
                pass
            node.ws.close()


def accept_websocket(handler: Any) -> WsConnection:
    """Complete the HTTP upgrade and return a frame connection."""
    key = handler.headers.get("Sec-WebSocket-Key")
    upgrade = (handler.headers.get("Upgrade") or "").lower()
    if not key or upgrade != "websocket":
        raise ValueError("not a websocket request")
    accept = base64.b64encode(
        hashlib.sha1((key + GUID).encode("ascii")).digest()
    ).decode("ascii")
    handler.send_response(101, "Switching Protocols")
    handler.send_header("Upgrade", "websocket")
    handler.send_header("Connection", "Upgrade")
    handler.send_header("Sec-WebSocket-Accept", accept)
    handler.end_headers()
    handler.close_connection = True
    try:
        handler.wfile.flush()
    except OSError:
        pass
    return WsConnection(handler.connection)
