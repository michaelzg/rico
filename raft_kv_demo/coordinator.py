#!/usr/bin/env python3
import argparse
import asyncio
import json
import time
import uuid
from dataclasses import dataclass
from typing import Dict, Optional


@dataclass
class NodeConn:
    node_id: str
    writer: asyncio.StreamWriter
    connected_at: float


class Coordinator:
    def __init__(self):
        self.nodes: Dict[str, NodeConn] = {}
        self.leader_hint: Optional[str] = None
        self.leader_term: int = 0
        self.pending: Dict[str, asyncio.Future] = {}

    async def send(self, target: str, msg: dict) -> bool:
        conn = self.nodes.get(target)
        if not conn:
            return False
        try:
            conn.writer.write((json.dumps(msg) + "\n").encode())
            await conn.writer.drain()
            return True
        except Exception:
            return False

    async def handle_node(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        peer = writer.get_extra_info("peername")
        node_id = None
        try:
            hello_raw = await reader.readline()
            if not hello_raw:
                writer.close()
                await writer.wait_closed()
                return
            hello = json.loads(hello_raw.decode())
            if hello.get("type") != "hello" or not hello.get("node_id"):
                writer.close()
                await writer.wait_closed()
                return
            node_id = str(hello["node_id"])
            self.nodes[node_id] = NodeConn(node_id=node_id, writer=writer, connected_at=time.time())
            print(f"[coord] node connected: {node_id} from {peer}")

            while True:
                raw = await reader.readline()
                if not raw:
                    break
                msg = json.loads(raw.decode())
                await self.handle_node_message(node_id, msg)
        except Exception as exc:
            print(f"[coord] node error ({node_id}): {exc}")
        finally:
            if node_id and self.nodes.get(node_id) and self.nodes[node_id].writer is writer:
                self.nodes.pop(node_id, None)
                if self.leader_hint == node_id:
                    self.leader_hint = None
                print(f"[coord] node disconnected: {node_id}")
            writer.close()
            await writer.wait_closed()

    async def handle_node_message(self, src: str, msg: dict):
        msg_type = msg.get("type")

        if msg_type == "leader_announce":
            term = int(msg.get("term", 0))
            leader = msg.get("leader_id", src)
            if term >= self.leader_term:
                self.leader_term = term
                self.leader_hint = leader
            return

        req_id = msg.get("req_id")
        if req_id and req_id in self.pending and msg_type in {
            "client_put_response",
            "client_get_response",
            "client_redirect",
            "client_error",
        }:
            fut = self.pending[req_id]
            if not fut.done():
                fut.set_result(msg)
            return

        target = msg.get("to")
        if not target:
            return

        if target == "*":
            for node_id in list(self.nodes.keys()):
                if node_id == src:
                    continue
                out = dict(msg)
                out["from"] = src
                await self.send(node_id, out)
            return

        out = dict(msg)
        out["from"] = src
        await self.send(str(target), out)

    async def request(self, payload: dict, timeout_sec: float = 8.0) -> dict:
        req_id = payload.setdefault("req_id", uuid.uuid4().hex)
        fut = asyncio.get_running_loop().create_future()
        self.pending[req_id] = fut

        def choose_target() -> Optional[str]:
            if self.leader_hint and self.leader_hint in self.nodes:
                return self.leader_hint
            if self.nodes:
                return sorted(self.nodes.keys())[0]
            return None

        target = choose_target()
        if not target:
            self.pending.pop(req_id, None)
            return {"ok": False, "error": "no nodes connected"}

        msg = dict(payload)
        msg["to"] = target
        sent = await self.send(target, msg)
        if not sent:
            self.pending.pop(req_id, None)
            return {"ok": False, "error": f"failed to reach {target}"}

        try:
            reply = await asyncio.wait_for(fut, timeout=timeout_sec)
            if reply.get("type") == "client_redirect":
                hinted = reply.get("leader_id")
                if hinted and hinted in self.nodes and hinted != target:
                    self.pending[req_id] = asyncio.get_running_loop().create_future()
                    msg["to"] = hinted
                    await self.send(hinted, msg)
                    reply = await asyncio.wait_for(self.pending[req_id], timeout=timeout_sec)
            return {"ok": True, "reply": reply}
        except asyncio.TimeoutError:
            return {"ok": False, "error": "timeout waiting for node response"}
        finally:
            self.pending.pop(req_id, None)

    async def handle_control(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        peer = writer.get_extra_info("peername")
        try:
            raw = await reader.readline()
            if not raw:
                writer.close()
                await writer.wait_closed()
                return
            cmd = raw.decode().strip()
            parts = cmd.split()
            result = None

            if not parts:
                result = {"ok": False, "error": "empty command"}
            elif parts[0] == "status":
                result = {
                    "ok": True,
                    "nodes": sorted(self.nodes.keys()),
                    "leader_hint": self.leader_hint,
                    "leader_term": self.leader_term,
                }
            elif parts[0] == "put" and len(parts) >= 3:
                key = parts[1]
                value = " ".join(parts[2:])
                result = await self.request({"type": "client_put", "key": key, "value": value})
            elif parts[0] == "get" and len(parts) == 2:
                key = parts[1]
                result = await self.request({"type": "client_get", "key": key})
            else:
                result = {
                    "ok": False,
                    "error": "unknown command; use: status | put <key> <value> | get <key>",
                }

            writer.write((json.dumps(result) + "\n").encode())
            await writer.drain()
        except Exception as exc:
            err = {"ok": False, "error": str(exc), "peer": str(peer)}
            writer.write((json.dumps(err) + "\n").encode())
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()


async def main():
    parser = argparse.ArgumentParser(description="Raft demo coordinator/relay")
    parser.add_argument("--node-host", default="0.0.0.0")
    parser.add_argument("--node-port", type=int, default=7000)
    parser.add_argument("--ctl-host", default="127.0.0.1")
    parser.add_argument("--ctl-port", type=int, default=7001)
    args = parser.parse_args()

    coord = Coordinator()
    node_server = await asyncio.start_server(coord.handle_node, args.node_host, args.node_port)
    ctl_server = await asyncio.start_server(coord.handle_control, args.ctl_host, args.ctl_port)

    print(f"[coord] node relay listening on {args.node_host}:{args.node_port}")
    print(f"[coord] control listening on {args.ctl_host}:{args.ctl_port}")

    async with node_server, ctl_server:
        await asyncio.gather(node_server.serve_forever(), ctl_server.serve_forever())


if __name__ == "__main__":
    asyncio.run(main())
