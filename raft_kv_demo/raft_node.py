#!/usr/bin/env python3
import argparse
import asyncio
import json
import random
import time
from typing import Dict, List, Optional


class RaftNode:
    def __init__(self, node_id: str, peers: List[str], host: str, port: int):
        self.node_id = node_id
        self.peers = [p for p in peers if p and p != node_id]
        self.host = host
        self.port = port

        self.reader: Optional[asyncio.StreamReader] = None
        self.writer: Optional[asyncio.StreamWriter] = None

        self.role = "follower"
        self.current_term = 0
        self.voted_for: Optional[str] = None
        self.leader_id: Optional[str] = None

        self.log: List[dict] = []
        self.commit_index = -1
        self.last_applied = -1
        self.kv: Dict[str, str] = {}

        self.next_index: Dict[str, int] = {}
        self.match_index: Dict[str, int] = {}
        self.votes_received = set()

        self.pending_put: Dict[str, int] = {}

        self.election_deadline = 0.0
        self.heartbeat_interval = 0.25
        self.last_heartbeat_sent = 0.0

    def reset_election_timeout(self):
        self.election_deadline = time.monotonic() + random.uniform(0.9, 1.8)

    async def send(self, msg: dict):
        if not self.writer:
            return
        self.writer.write((json.dumps(msg) + "\n").encode())
        await self.writer.drain()

    async def send_to(self, target: str, msg: dict):
        out = dict(msg)
        out["to"] = target
        out["from"] = self.node_id
        await self.send(out)

    async def broadcast(self, msg: dict):
        for peer in self.peers:
            await self.send_to(peer, msg)

    def last_log_index(self) -> int:
        return len(self.log) - 1

    def last_log_term(self) -> int:
        if not self.log:
            return 0
        return int(self.log[-1]["term"])

    def is_up_to_date(self, other_last_idx: int, other_last_term: int) -> bool:
        my_term = self.last_log_term()
        if other_last_term != my_term:
            return other_last_term > my_term
        return other_last_idx >= self.last_log_index()

    async def become_follower(self, term: int, leader_id: Optional[str] = None):
        if term > self.current_term:
            self.current_term = term
            self.voted_for = None
        self.role = "follower"
        self.leader_id = leader_id
        self.votes_received.clear()
        self.reset_election_timeout()

    async def become_leader(self):
        self.role = "leader"
        self.leader_id = self.node_id
        self.next_index = {p: len(self.log) for p in self.peers}
        self.match_index = {p: -1 for p in self.peers}
        await self.send({"type": "leader_announce", "leader_id": self.node_id, "term": self.current_term})
        await self.replicate_to_all()

    async def start_election(self):
        self.role = "candidate"
        self.current_term += 1
        self.voted_for = self.node_id
        self.votes_received = {self.node_id}
        self.leader_id = None
        self.reset_election_timeout()

        msg = {
            "type": "request_vote",
            "term": self.current_term,
            "candidate_id": self.node_id,
            "last_log_index": self.last_log_index(),
            "last_log_term": self.last_log_term(),
        }
        await self.broadcast(msg)
        print(f"[{self.node_id}] election started term={self.current_term}")

    async def replicate_to_peer(self, peer: str):
        next_idx = self.next_index.get(peer, len(self.log))
        prev_idx = next_idx - 1
        prev_term = int(self.log[prev_idx]["term"]) if prev_idx >= 0 else 0
        entries = self.log[next_idx:]
        await self.send_to(
            peer,
            {
                "type": "append_entries",
                "term": self.current_term,
                "leader_id": self.node_id,
                "prev_log_index": prev_idx,
                "prev_log_term": prev_term,
                "entries": entries,
                "leader_commit": self.commit_index,
            },
        )

    async def replicate_to_all(self):
        for peer in self.peers:
            await self.replicate_to_peer(peer)
        self.last_heartbeat_sent = time.monotonic()

    async def apply_commits(self):
        while self.last_applied < self.commit_index:
            self.last_applied += 1
            entry = self.log[self.last_applied]
            self.kv[entry["key"]] = entry["value"]
            print(f"[{self.node_id}] applied idx={self.last_applied} {entry['key']}={entry['value']}")

    async def maybe_advance_commit(self):
        if self.role != "leader":
            return
        if not self.log:
            return
        candidates = sorted(
            [self.last_log_index()] + [idx for idx in self.match_index.values()], reverse=True
        )
        quorum = (len(self.peers) + 1) // 2 + 1

        for idx in candidates:
            count = 1
            for p in self.peers:
                if self.match_index.get(p, -1) >= idx:
                    count += 1
            if count >= quorum and idx > self.commit_index and self.log[idx]["term"] == self.current_term:
                self.commit_index = idx
                await self.apply_commits()
                await self.flush_pending_puts()
                break

    async def flush_pending_puts(self):
        done = [req_id for req_id, idx in self.pending_put.items() if idx <= self.commit_index]
        for req_id in done:
            idx = self.pending_put.pop(req_id)
            e = self.log[idx]
            await self.send(
                {
                    "type": "client_put_response",
                    "req_id": req_id,
                    "status": "committed",
                    "leader_id": self.node_id,
                    "term": self.current_term,
                    "index": idx,
                    "key": e["key"],
                    "value": e["value"],
                }
            )

    async def on_request_vote(self, msg: dict):
        term = int(msg.get("term", 0))
        candidate_id = msg.get("candidate_id")
        grant = False

        if term < self.current_term:
            grant = False
        else:
            if term > self.current_term:
                await self.become_follower(term)

            c_last_idx = int(msg.get("last_log_index", -1))
            c_last_term = int(msg.get("last_log_term", 0))
            can_vote = self.voted_for in (None, candidate_id)
            up_to_date = self.is_up_to_date(c_last_idx, c_last_term)
            if can_vote and up_to_date:
                self.voted_for = candidate_id
                grant = True
                self.reset_election_timeout()

        await self.send_to(
            candidate_id,
            {
                "type": "vote_response",
                "term": self.current_term,
                "vote_granted": grant,
                "voter_id": self.node_id,
            },
        )

    async def on_vote_response(self, msg: dict):
        if self.role != "candidate":
            return
        term = int(msg.get("term", 0))
        if term > self.current_term:
            await self.become_follower(term)
            return
        if term < self.current_term:
            return
        if msg.get("vote_granted"):
            self.votes_received.add(msg.get("voter_id"))
            quorum = (len(self.peers) + 1) // 2 + 1
            if len(self.votes_received) >= quorum:
                print(f"[{self.node_id}] became leader term={self.current_term}")
                await self.become_leader()

    async def on_append_entries(self, msg: dict):
        term = int(msg.get("term", 0))
        leader_id = msg.get("leader_id")

        if term < self.current_term:
            await self.send_to(
                leader_id,
                {
                    "type": "append_response",
                    "term": self.current_term,
                    "success": False,
                    "match_index": self.last_log_index(),
                },
            )
            return

        if term > self.current_term or self.role != "follower":
            await self.become_follower(term, leader_id=leader_id)
        else:
            self.leader_id = leader_id
            self.reset_election_timeout()

        prev_idx = int(msg.get("prev_log_index", -1))
        prev_term = int(msg.get("prev_log_term", 0))

        if prev_idx >= 0:
            if prev_idx >= len(self.log) or int(self.log[prev_idx]["term"]) != prev_term:
                await self.send_to(
                    leader_id,
                    {
                        "type": "append_response",
                        "term": self.current_term,
                        "success": False,
                        "match_index": self.last_log_index(),
                    },
                )
                return

        entries = msg.get("entries", [])
        insert_at = prev_idx + 1
        for i, entry in enumerate(entries):
            idx = insert_at + i
            if idx < len(self.log):
                if int(self.log[idx]["term"]) != int(entry["term"]):
                    self.log = self.log[:idx]
                    self.log.append(entry)
            else:
                self.log.append(entry)

        leader_commit = int(msg.get("leader_commit", -1))
        if leader_commit > self.commit_index:
            self.commit_index = min(leader_commit, self.last_log_index())
            await self.apply_commits()

        await self.send_to(
            leader_id,
            {
                "type": "append_response",
                "term": self.current_term,
                "success": True,
                "match_index": self.last_log_index(),
            },
        )

    async def on_append_response(self, msg: dict, src: str):
        if self.role != "leader":
            return
        term = int(msg.get("term", 0))
        if term > self.current_term:
            await self.become_follower(term)
            return

        if not msg.get("success"):
            self.next_index[src] = max(0, self.next_index.get(src, len(self.log)) - 1)
            await self.replicate_to_peer(src)
            return

        match_idx = int(msg.get("match_index", -1))
        self.match_index[src] = match_idx
        self.next_index[src] = match_idx + 1
        await self.maybe_advance_commit()

    async def on_client_put(self, msg: dict):
        req_id = msg.get("req_id")
        if self.role != "leader":
            await self.send(
                {
                    "type": "client_redirect",
                    "req_id": req_id,
                    "leader_id": self.leader_id,
                    "term": self.current_term,
                }
            )
            return

        entry = {
            "term": self.current_term,
            "key": str(msg.get("key")),
            "value": str(msg.get("value")),
        }
        self.log.append(entry)
        idx = self.last_log_index()
        self.pending_put[req_id] = idx
        await self.replicate_to_all()

    async def on_client_get(self, msg: dict):
        req_id = msg.get("req_id")
        key = str(msg.get("key"))
        if self.role != "leader":
            await self.send(
                {
                    "type": "client_redirect",
                    "req_id": req_id,
                    "leader_id": self.leader_id,
                    "term": self.current_term,
                }
            )
            return

        await self.send(
            {
                "type": "client_get_response",
                "req_id": req_id,
                "leader_id": self.node_id,
                "term": self.current_term,
                "key": key,
                "value": self.kv.get(key),
                "commit_index": self.commit_index,
            }
        )

    async def handle_message(self, msg: dict):
        t = msg.get("type")
        src = msg.get("from")
        if t == "request_vote":
            await self.on_request_vote(msg)
        elif t == "vote_response":
            await self.on_vote_response(msg)
        elif t == "append_entries":
            await self.on_append_entries(msg)
        elif t == "append_response":
            if src:
                await self.on_append_response(msg, src)
        elif t == "client_put":
            await self.on_client_put(msg)
        elif t == "client_get":
            await self.on_client_get(msg)

    async def reader_loop(self):
        assert self.reader
        while True:
            raw = await self.reader.readline()
            if not raw:
                raise ConnectionError("coordinator closed connection")
            msg = json.loads(raw.decode())
            await self.handle_message(msg)

    async def ticker_loop(self):
        self.reset_election_timeout()
        while True:
            now = time.monotonic()
            if self.role == "leader":
                if now - self.last_heartbeat_sent >= self.heartbeat_interval:
                    await self.replicate_to_all()
            else:
                if now >= self.election_deadline:
                    await self.start_election()
            await asyncio.sleep(0.05)

    async def run(self):
        random.seed(f"{self.node_id}-{time.time()}")
        self.reader, self.writer = await asyncio.open_connection(self.host, self.port)
        await self.send({"type": "hello", "node_id": self.node_id})
        print(f"[{self.node_id}] connected to coordinator {self.host}:{self.port} peers={self.peers}")
        await asyncio.gather(self.reader_loop(), self.ticker_loop())


async def main():
    parser = argparse.ArgumentParser(description="Raft demo node")
    parser.add_argument("--id", required=True)
    parser.add_argument("--peers", required=True, help="comma-separated node IDs")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=7000)
    args = parser.parse_args()

    node = RaftNode(node_id=args.id, peers=args.peers.split(","), host=args.host, port=args.port)
    await node.run()


if __name__ == "__main__":
    asyncio.run(main())
