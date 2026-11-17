import base64
import json
import os
import socket
import struct
import tempfile
import threading
import time
import unittest
from pathlib import Path

from app.app import build_app
from app.config import BASE_DIR, Config
from app.httpd import make_server


class WsClient:
    def __init__(self, port: int) -> None:
        self.sock = socket.create_connection(("127.0.0.1", port), timeout=5)
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        request = (
            f"GET /ws HTTP/1.1\r\n"
            f"Host: 127.0.0.1:{port}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n\r\n"
        )
        self.sock.sendall(request.encode("ascii"))
        response = b""
        while b"\r\n\r\n" not in response:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise RuntimeError("websocket handshake closed")
            response += chunk
        if b" 101 " not in response.split(b"\r\n", 1)[0]:
            raise RuntimeError(response.decode("utf-8", "replace"))
        self.buffer = bytearray()
        self.messages: list[dict] = []

    def send(self, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        mask = os.urandom(4)
        header = bytearray([0x81])
        if len(body) < 126:
            header.append(0x80 | len(body))
        elif len(body) < 65536:
            header.append(0x80 | 126)
            header.extend(struct.pack(">H", len(body)))
        else:
            header.append(0x80 | 127)
            header.extend(struct.pack(">Q", len(body)))
        header.extend(mask)
        header.extend(bytes(byte ^ mask[index % 4] for index, byte in enumerate(body)))
        self.sock.sendall(bytes(header))

    def _read_frame(self, timeout: float = 3.0) -> tuple[int, bytes]:
        self.sock.settimeout(timeout)
        while True:
            if len(self.buffer) >= 2:
                first, second = self.buffer[0], self.buffer[1]
                opcode = first & 0x0F
                masked = bool(second & 0x80)
                length = second & 0x7F
                offset = 2
                if length == 126:
                    if len(self.buffer) < 4:
                        pass
                    else:
                        length = struct.unpack_from(">H", self.buffer, 2)[0]
                        offset = 4
                elif length == 127:
                    if len(self.buffer) < 10:
                        pass
                    else:
                        length = struct.unpack_from(">Q", self.buffer, 2)[0]
                        offset = 10
                if len(self.buffer) >= offset + (4 if masked else 0) + length:
                    mask = b""
                    if masked:
                        mask = bytes(self.buffer[offset : offset + 4])
                        offset += 4
                    body = bytes(self.buffer[offset : offset + length])
                    del self.buffer[: offset + length]
                    if masked and mask:
                        body = bytes(
                            byte ^ mask[index % 4] for index, byte in enumerate(body)
                        )
                    return opcode, body
            chunk = self.sock.recv(8192)
            if not chunk:
                raise ConnectionError("closed")
            self.buffer.extend(chunk)

    def recv_json(self, timeout: float = 3.0) -> dict:
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("no websocket message")
            opcode, body = self._read_frame(remaining)
            if opcode == 0x9:
                # pong
                self.sock.sendall(bytes([0x8A, 0x80]) + os.urandom(4))
                continue
            if opcode == 0x8:
                raise ConnectionError("server closed")
            if opcode == 0x1:
                return json.loads(body.decode("utf-8"))

    def wait_for(self, kind: str, timeout: float = 3.0) -> dict:
        for index, message in enumerate(self.messages):
            if message.get("type") == kind:
                return self.messages.pop(index)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            message = self.recv_json(max(0.1, deadline - time.monotonic()))
            if message.get("type") == kind:
                return message
            self.messages.append(message)
        raise TimeoutError(f"no {kind}: {self.messages}")

    def expect_none(self, timeout: float = 0.6) -> None:
        for message in self.messages:
            if message.get("type") == "signal":
                raise AssertionError(f"unexpected signal: {message}")
        self.messages = [m for m in self.messages if m.get("type") != "signal"]
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                message = self.recv_json(max(0.05, deadline - time.monotonic()))
            except TimeoutError:
                return
            if message.get("type") == "signal":
                raise AssertionError(f"unexpected signal: {message}")
            self.messages.append(message)

    def close(self) -> None:
        try:
            self.sock.close()
        except OSError:
            pass


class MeshTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.app = build_app(
            Config(
                data_dir=root,
                db_path=root / "radio.db",
                static_dir=BASE_DIR / "app" / "static",
                admin_token="test",
            )
        )
        self.server = make_server(self.app, "127.0.0.1", 0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.port = self.server.server_address[1]
        self.clients: list[WsClient] = []

    def tearDown(self):
        for client in self.clients:
            client.close()
        self.server.shutdown()
        self.thread.join(2)
        self.server.server_close()
        self.app.db.close()
        self.temp.cleanup()

    def client(self) -> WsClient:
        client = WsClient(self.port)
        self.clients.append(client)
        return client

    def hello(self, client: WsClient, preferred: str = "child") -> dict:
        client.send({"type": "hello", "preferred_role": preferred})
        return client.wait_for("welcome")

    def wait_status(self, predicate, timeout: float = 3.0):
        deadline = time.monotonic() + timeout
        status = None
        while time.monotonic() < deadline:
            status = self.server.mesh.status()
            if predicate(status):
                return status
            time.sleep(0.03)
        raise AssertionError(f"status timeout: {status}")

    def test_one_node_is_seed(self):
        client = self.client()
        welcome = self.hello(client, "child")
        self.assertEqual(welcome["role"], "seed")
        status = self.server.mesh.status()
        self.assertEqual(status["seeds"], [welcome["id"]])
        self.assertEqual(status["nodes"][0]["parents"], [])

    def test_two_nodes_are_both_seeds(self):
        a = self.client()
        b = self.client()
        wa = self.hello(a)
        wb = self.hello(b)
        self.assertEqual(wa["role"], "seed")
        self.assertEqual(wb["role"], "seed")
        status = self.wait_status(lambda item: len(item["nodes"]) == 2 and len(item["seeds"]) == 2)
        self.assertEqual(len(status["seeds"]), 2)

    def test_third_node_gets_two_distinct_seeds(self):
        a = self.client()
        b = self.client()
        c = self.client()
        self.hello(a)
        self.hello(b)
        welcome = self.hello(c)
        self.assertEqual(welcome["role"], "child")
        first = c.wait_for("parent-added")
        second = c.wait_for("parent-added")
        self.assertNotEqual(first["parent_id"], second["parent_id"])
        status = self.wait_status(
            lambda item: len(item["nodes"]) == 3
            and len(item["seeds"]) == 2
            and next(n for n in item["nodes"] if n["id"] == welcome["id"])["parents"]
        )
        child = next(n for n in status["nodes"] if n["id"] == welcome["id"])
        self.assertEqual(sorted(child["parents"]), sorted([first["parent_id"], second["parent_id"]]))

    def test_directed_signal_and_non_neighbor_rejected(self):
        a = self.client()
        b = self.client()
        c = self.client()
        wa = self.hello(a)
        wb = self.hello(b)
        wc = self.hello(c)
        parent = c.wait_for("parent-added")["parent_id"]
        c.send({"type": "signal", "to": parent, "payload": {"kind": "offer", "sdp": "v=0"}})
        received = None
        for client, welcome in ((a, wa), (b, wb)):
            if welcome["id"] == parent:
                received = client.wait_for("signal")
        self.assertIsNotNone(received)
        self.assertEqual(received["from"], wc["id"])

        # Two seeds are not neighbors and must not receive each other's SDP.
        a.send({"type": "signal", "to": wb["id"], "payload": {"kind": "offer", "sdp": "x"}})
        error = a.wait_for("error")
        self.assertEqual(error["code"], "not_neighbor")
        b.expect_none()

    def test_upstream_lost_removes_link(self):
        a = self.client()
        b = self.client()
        c = self.client()
        wa = self.hello(a)
        wb = self.hello(b)
        wc = self.hello(c)
        first = c.wait_for("parent-added")["parent_id"]
        second = c.wait_for("parent-added")["parent_id"]
        c.send({"type": "upstream-lost", "parent_id": first})
        status = self.wait_status(
            lambda item: first
            not in next(n for n in item["nodes"] if n["id"] == wc["id"])["parents"]
        )
        child = next(n for n in status["nodes"] if n["id"] == wc["id"])
        self.assertEqual(child["parents"], [second])
        parent = next(n for n in status["nodes"] if n["id"] == first)
        self.assertNotIn(wc["id"], parent["children"])

    def test_seed_disconnect_promotes_child(self):
        a = self.client()
        b = self.client()
        c = self.client()
        wa = self.hello(a)
        self.hello(b)
        wc = self.hello(c)
        self.wait_status(
            lambda item: len(item["seeds"]) == 2 and len(item["nodes"]) == 3
        )
        first_seed = wa["id"]
        a.close()
        status = self.wait_status(
            lambda item: len(item["nodes"]) == 2 and len(item["seeds"]) == 2
        )
        self.assertNotIn(first_seed, status["seeds"])
        self.assertIn(wc["id"], status["seeds"])
        promoted = next(n for n in status["nodes"] if n["id"] == wc["id"])
        self.assertEqual(promoted["role"], "seed")
        self.assertEqual(promoted["parents"], [])


    def test_explicit_seed_request_replaces_one_of_two_seeds(self):
        a = self.client()
        b = self.client()
        c = self.client()
        wa = self.hello(a)
        wb = self.hello(b)
        wc = self.hello(c, "seed")
        self.assertEqual(wc["role"], "seed")
        status = self.wait_status(
            lambda item: len(item["nodes"]) == 3
            and wc["id"] in item["seeds"]
            and len(item["seeds"]) == 2
        )
        self.assertLessEqual(len(status["seeds"]), 2)
        self.assertIn(wc["id"], status["seeds"])
        old_seeds = {wa["id"], wb["id"]}
        demoted = old_seeds - set(status["seeds"])
        self.assertEqual(len(demoted), 1)
        old = next(n for n in status["nodes"] if n["id"] == next(iter(demoted)))
        self.assertEqual(old["role"], "child")
        self.assertEqual(len(old["parents"]), 2)
        newcomer = next(n for n in status["nodes"] if n["id"] == wc["id"])
        self.assertEqual(newcomer["role"], "seed")
        self.assertEqual(newcomer["parents"], [])


    def test_seed_replacement_preserves_old_seed_children(self):
        a = self.client()
        b = self.client()
        c = self.client()
        d = self.client()
        e = self.client()
        wa = self.hello(a)
        wb = self.hello(b)
        for client in (c, d, e):
            self.hello(client)
        before = self.wait_status(
            lambda item: len(item["nodes"]) == 5
            and len(item["seeds"]) == 2
            and all(
                len(n["parents"]) == 2
                for n in item["nodes"]
                if n["role"] == "child"
            )
        )
        old_children = {
            n["id"]: list(n["children"])
            for n in before["nodes"]
            if n["id"] in {wa["id"], wb["id"]}
        }
        replacement = self.client()
        welcome = self.hello(replacement, "seed")
        self.assertEqual(welcome["role"], "seed")
        after = self.wait_status(
            lambda item: len(item["nodes"]) == 6
            and welcome["id"] in item["seeds"]
            and all(
                len(n["parents"]) == 2
                for n in item["nodes"]
                if n["role"] == "child"
            )
        )
        self.assertEqual(len(after["seeds"]), 2)
        demoted = next(
            n for n in after["nodes"] if n["id"] in old_children and n["role"] == "child"
        )
        self.assertEqual(list(old_children[demoted["id"]]), demoted["children"])
        self.assertEqual(len(demoted["parents"]), 2)

    def test_non_relay_node_is_a_leaf(self):
        a = self.client()
        b = self.client()
        leaf = self.client()
        self.hello(a)
        self.hello(b)
        welcome = self.hello(leaf, "leaf")
        self.assertEqual(welcome["role"], "child")
        status = self.wait_status(
            lambda item: len(item["nodes"]) == 3
            and len(next(n for n in item["nodes"] if n["id"] == welcome["id"])["parents"]) == 2
        )
        node = next(n for n in status["nodes"] if n["id"] == welcome["id"])
        self.assertEqual(node["role"], "child")
        self.assertFalse(node["can_relay"])
        self.assertEqual(node["children"], [])
        self.assertEqual(len(node["parents"]), 2)

    def test_parent_loss_keeps_the_surviving_parent_while_adding(self):
        a = self.client()
        b = self.client()
        c = self.client()
        d = self.client()
        wa = self.hello(a)
        wb = self.hello(b)
        wc = self.hello(c)
        self.hello(d)
        status = self.wait_status(
            lambda item: len(item["nodes"]) == 4
            and len(next(n for n in item["nodes"] if n["id"] == wc["id"])["parents"]) == 2
        )
        before = next(n for n in status["nodes"] if n["id"] == wc["id"])
        lost, survivor = sorted(before["parents"])[:2]
        c.send({"type": "upstream-lost", "parent_id": lost})
        status = self.wait_status(
            lambda item: len(next(n for n in item["nodes"] if n["id"] == wc["id"])["parents"]) == 2
        )
        after = next(n for n in status["nodes"] if n["id"] == wc["id"])
        # The surviving parent must never be dropped while the replacement is
        # being established.  A fake upstream-lost on an otherwise healthy
        # parent may later reattach it, so do not require the lost id to stay
        # absent forever.
        self.assertIn(survivor, after["parents"])
        self.assertEqual(len(after["parents"]), 2)

    def test_child_disconnect_removes_downstream(self):
        a = self.client()
        b = self.client()
        c = self.client()
        wa = self.hello(a)
        wb = self.hello(b)
        wc = self.hello(c)
        self.wait_status(
            lambda item: len(item["nodes"]) == 3 and len(item["seeds"]) == 2
        )
        c.close()
        status = self.wait_status(
            lambda item: len(item["nodes"]) == 2
            and all(wc["id"] not in n["children"] for n in item["nodes"])
        )
        self.assertEqual(status["seeds"], sorted([wa["id"], wb["id"]]))

    def test_capacity_and_relay(self):
        clients = []
        welcomes = []
        for _ in range(7):
            client = self.client()
            clients.append(client)
            welcomes.append(self.hello(client))
        status = self.wait_status(lambda item: len(item["nodes"]) == 7, timeout=5)
        for node in status["nodes"]:
            self.assertLessEqual(len(node["children"]), 4)
            self.assertLessEqual(len(node["parents"]), 2)
            if node["role"] == "seed":
                self.assertEqual(node["parents"], [])
        # Two seeds have eight child slots and five children want two parents
        # each, so at least one child must be relayed through another child.
        by_id = {node["id"]: node for node in status["nodes"]}
        seed_ids = set(status["seeds"])
        for node in status["nodes"]:
            if node["role"] == "child":
                self.assertEqual(len(node["parents"]), 2, node)
                for parent_id in node["parents"]:
                    self.assertIn(node["id"], by_id[parent_id]["children"])
        self.assertTrue(
            any(
                node["role"] == "child"
                and set(node["parents"]) - seed_ids
                for node in status["nodes"]
            )
        )
        # Every node must be reachable from a seed without cycles.
        reached = set(seed_ids)
        queue = list(seed_ids)
        while queue:
            current = queue.pop()
            for child_id in by_id[current]["children"]:
                if child_id not in reached:
                    reached.add(child_id)
                    queue.append(child_id)
        self.assertEqual(reached, set(by_id))


if __name__ == "__main__":
    unittest.main()
