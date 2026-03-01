# MicroPython Raft demo node for Pico 2W.
# Upload as main.py (or import from boot script) on each board.

import network
import ujson as json
import urandom
import usocket as socket
import uselect
import utime


class PicoRaftNode:
    def __init__(self, node_id, peers, coordinator_host, coordinator_port):
        self.node_id = node_id
        self.peers = [p for p in peers if p != node_id]
        self.host = coordinator_host
        self.port = coordinator_port

        self.role = "follower"
        self.current_term = 0
        self.voted_for = None
        self.leader_id = None

        self.log = []
        self.commit_index = -1
        self.last_applied = -1
        self.kv = {}

        self.next_index = {}
        self.match_index = {}
        self.votes = {}
        self.pending_put = {}

        self.last_heartbeat_ms = utime.ticks_ms()
        self.heartbeat_interval_ms = 250
        self.election_deadline_ms = 0

        self.sock = None
        self.poller = uselect.poll()
        self.read_buf = b""

    def now(self):
        return utime.ticks_ms()

    def rand_election_timeout_ms(self):
        return 900 + (urandom.getrandbits(10) % 900)

    def reset_election_timeout(self):
        self.election_deadline_ms = utime.ticks_add(self.now(), self.rand_election_timeout_ms())

    def connect(self):
        ai = socket.getaddrinfo(self.host, self.port)[0][-1]
        self.sock = socket.socket()
        self.sock.connect(ai)
        self.sock.setblocking(False)
        self.poller.register(self.sock, uselect.POLLIN)
        self.send({"type": "hello", "node_id": self.node_id})
        self.reset_election_timeout()
        print("connected", self.node_id, "to", self.host, self.port)

    def send(self, msg):
        data = (json.dumps(msg) + "\n").encode()
        self.sock.send(data)

    def send_to(self, target, msg):
        out = dict(msg)
        out["from"] = self.node_id
        out["to"] = target
        self.send(out)

    def broadcast(self, msg):
        for p in self.peers:
            self.send_to(p, msg)

    def last_log_index(self):
        return len(self.log) - 1

    def last_log_term(self):
        if not self.log:
            return 0
        return int(self.log[-1]["term"])

    def is_up_to_date(self, other_idx, other_term):
        my_term = self.last_log_term()
        if other_term != my_term:
            return other_term > my_term
        return other_idx >= self.last_log_index()

    def become_follower(self, term, leader_id=None):
        if term > self.current_term:
            self.current_term = term
            self.voted_for = None
        self.role = "follower"
        self.leader_id = leader_id
        self.votes = {}
        self.reset_election_timeout()

    def become_leader(self):
        self.role = "leader"
        self.leader_id = self.node_id
        for p in self.peers:
            self.next_index[p] = len(self.log)
            self.match_index[p] = -1
        self.send({"type": "leader_announce", "leader_id": self.node_id, "term": self.current_term})
        self.replicate_to_all()

    def start_election(self):
        self.role = "candidate"
        self.current_term += 1
        self.voted_for = self.node_id
        self.votes = {self.node_id: True}
        self.leader_id = None
        self.reset_election_timeout()
        self.broadcast(
            {
                "type": "request_vote",
                "term": self.current_term,
                "candidate_id": self.node_id,
                "last_log_index": self.last_log_index(),
                "last_log_term": self.last_log_term(),
            }
        )
        print(self.node_id, "started election term", self.current_term)

    def apply_commits(self):
        while self.last_applied < self.commit_index:
            self.last_applied += 1
            e = self.log[self.last_applied]
            self.kv[e["key"]] = e["value"]

    def quorum(self):
        return (len(self.peers) + 1) // 2 + 1

    def maybe_commit(self):
        if self.role != "leader" or not self.log:
            return
        max_idx = self.last_log_index()
        for idx in range(max_idx, self.commit_index, -1):
            count = 1
            for p in self.peers:
                if self.match_index.get(p, -1) >= idx:
                    count += 1
            if count >= self.quorum() and self.log[idx]["term"] == self.current_term:
                self.commit_index = idx
                self.apply_commits()
                self.flush_put_responses()
                return

    def flush_put_responses(self):
        done = []
        for req_id, idx in self.pending_put.items():
            if idx <= self.commit_index:
                e = self.log[idx]
                self.send(
                    {
                        "type": "client_put_response",
                        "req_id": req_id,
                        "status": "committed",
                        "leader_id": self.node_id,
                        "key": e["key"],
                        "value": e["value"],
                    }
                )
                done.append(req_id)
        for r in done:
            self.pending_put.pop(r, None)

    def replicate_to_peer(self, peer):
        next_idx = self.next_index.get(peer, len(self.log))
        prev_idx = next_idx - 1
        prev_term = self.log[prev_idx]["term"] if prev_idx >= 0 else 0
        entries = self.log[next_idx:]
        self.send_to(
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

    def replicate_to_all(self):
        for p in self.peers:
            self.replicate_to_peer(p)
        self.last_heartbeat_ms = self.now()

    def on_request_vote(self, m):
        term = int(m.get("term", 0))
        candidate = m.get("candidate_id")
        grant = False

        if term < self.current_term:
            grant = False
        else:
            if term > self.current_term:
                self.become_follower(term)
            can_vote = (self.voted_for is None) or (self.voted_for == candidate)
            up_to_date = self.is_up_to_date(int(m.get("last_log_index", -1)), int(m.get("last_log_term", 0)))
            if can_vote and up_to_date:
                self.voted_for = candidate
                self.reset_election_timeout()
                grant = True

        self.send_to(
            candidate,
            {
                "type": "vote_response",
                "term": self.current_term,
                "vote_granted": grant,
                "voter_id": self.node_id,
            },
        )

    def on_vote_response(self, m):
        if self.role != "candidate":
            return
        term = int(m.get("term", 0))
        if term > self.current_term:
            self.become_follower(term)
            return
        if term < self.current_term:
            return
        if m.get("vote_granted"):
            self.votes[m.get("voter_id")] = True
            if len(self.votes) >= self.quorum():
                print(self.node_id, "became leader term", self.current_term)
                self.become_leader()

    def on_append_entries(self, m):
        term = int(m.get("term", 0))
        leader = m.get("leader_id")
        if term < self.current_term:
            self.send_to(leader, {"type": "append_response", "term": self.current_term, "success": False, "match_index": self.last_log_index()})
            return

        if term > self.current_term or self.role != "follower":
            self.become_follower(term, leader)
        else:
            self.leader_id = leader
            self.reset_election_timeout()

        prev_idx = int(m.get("prev_log_index", -1))
        prev_term = int(m.get("prev_log_term", 0))
        if prev_idx >= 0:
            if prev_idx >= len(self.log) or int(self.log[prev_idx]["term"]) != prev_term:
                self.send_to(leader, {"type": "append_response", "term": self.current_term, "success": False, "match_index": self.last_log_index()})
                return

        entries = m.get("entries", [])
        insert_at = prev_idx + 1
        i = 0
        while i < len(entries):
            idx = insert_at + i
            e = entries[i]
            if idx < len(self.log):
                if int(self.log[idx]["term"]) != int(e["term"]):
                    self.log = self.log[:idx]
                    self.log.append(e)
            else:
                self.log.append(e)
            i += 1

        leader_commit = int(m.get("leader_commit", -1))
        if leader_commit > self.commit_index:
            self.commit_index = leader_commit if leader_commit < self.last_log_index() else self.last_log_index()
            self.apply_commits()

        self.send_to(leader, {"type": "append_response", "term": self.current_term, "success": True, "match_index": self.last_log_index()})

    def on_append_response(self, m, src):
        if self.role != "leader":
            return
        term = int(m.get("term", 0))
        if term > self.current_term:
            self.become_follower(term)
            return

        if not m.get("success"):
            self.next_index[src] = max(0, self.next_index.get(src, len(self.log)) - 1)
            self.replicate_to_peer(src)
            return

        match_idx = int(m.get("match_index", -1))
        self.match_index[src] = match_idx
        self.next_index[src] = match_idx + 1
        self.maybe_commit()

    def on_client_put(self, m):
        req_id = m.get("req_id")
        if self.role != "leader":
            self.send({"type": "client_redirect", "req_id": req_id, "leader_id": self.leader_id, "term": self.current_term})
            return
        e = {"term": self.current_term, "key": str(m.get("key")), "value": str(m.get("value"))}
        self.log.append(e)
        self.pending_put[req_id] = self.last_log_index()
        self.replicate_to_all()

    def on_client_get(self, m):
        req_id = m.get("req_id")
        key = str(m.get("key"))
        if self.role != "leader":
            self.send({"type": "client_redirect", "req_id": req_id, "leader_id": self.leader_id, "term": self.current_term})
            return
        self.send(
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

    def handle_message(self, m):
        t = m.get("type")
        src = m.get("from")
        if t == "request_vote":
            self.on_request_vote(m)
        elif t == "vote_response":
            self.on_vote_response(m)
        elif t == "append_entries":
            self.on_append_entries(m)
        elif t == "append_response" and src:
            self.on_append_response(m, src)
        elif t == "client_put":
            self.on_client_put(m)
        elif t == "client_get":
            self.on_client_get(m)

    def pump_socket(self):
        events = self.poller.poll(10)
        for sock, evt in events:
            if evt & uselect.POLLIN:
                try:
                    data = sock.recv(1024)
                except OSError:
                    data = b""
                if not data:
                    raise OSError("coordinator disconnected")
                self.read_buf += data
                while b"\n" in self.read_buf:
                    line, self.read_buf = self.read_buf.split(b"\n", 1)
                    if line:
                        self.handle_message(json.loads(line.decode()))

    def tick(self):
        now = self.now()
        if self.role == "leader":
            if utime.ticks_diff(now, self.last_heartbeat_ms) >= self.heartbeat_interval_ms:
                self.replicate_to_all()
        else:
            if utime.ticks_diff(now, self.election_deadline_ms) >= 0:
                self.start_election()

    def run(self):
        self.connect()
        while True:
            self.pump_socket()
            self.tick()
            utime.sleep_ms(20)


def connect_wifi(ssid, password, timeout_s=15):
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    if not wlan.isconnected():
        wlan.connect(ssid, password)
        start = utime.time()
        while not wlan.isconnected():
            if utime.time() - start > timeout_s:
                raise RuntimeError("Wi-Fi connect timeout")
            utime.sleep_ms(200)
    print("wifi up", wlan.ifconfig())


# Example configuration; update per device.
WIFI_SSID = "YOUR_WIFI_SSID"
WIFI_PASSWORD = "YOUR_WIFI_PASSWORD"
NODE_ID = "pico1"
PEERS = ["pico1", "pico2", "pico3"]
COORDINATOR_HOST = "192.168.107.33"
COORDINATOR_PORT = 7000


def main():
    connect_wifi(WIFI_SSID, WIFI_PASSWORD)
    node = PicoRaftNode(NODE_ID, PEERS, COORDINATOR_HOST, COORDINATOR_PORT)
    node.run()


if __name__ == "__main__":
    main()
