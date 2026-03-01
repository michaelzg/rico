#!/usr/bin/env python3
import asyncio
import json
import signal
import subprocess
import sys
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent
PY = sys.executable
NODE_PORT = 17000
CTL_PORT = 17001


async def ctl(cmd: str) -> dict:
    reader, writer = await asyncio.open_connection("127.0.0.1", CTL_PORT)
    writer.write((cmd + "\n").encode())
    await writer.drain()
    raw = await reader.readline()
    writer.close()
    await writer.wait_closed()
    return json.loads(raw.decode())


async def wait_for_leader(timeout_s=8.0):
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        status = await ctl("status")
        if status.get("ok") and status.get("leader_hint"):
            return status["leader_hint"]
        await asyncio.sleep(0.25)
    raise TimeoutError("no leader elected")


async def main():
    procs = []
    try:
        coord = subprocess.Popen(
            [
                PY,
                str(BASE / "coordinator.py"),
                "--node-host",
                "127.0.0.1",
                "--node-port",
                str(NODE_PORT),
                "--ctl-host",
                "127.0.0.1",
                "--ctl-port",
                str(CTL_PORT),
            ],
            cwd=str(BASE),
        )
        procs.append(coord)
        await asyncio.sleep(0.4)

        peers = "pico1,pico2,pico3"
        for node in ["pico1", "pico2", "pico3"]:
            p = subprocess.Popen(
                [
                    PY,
                    str(BASE / "raft_node.py"),
                    "--id",
                    node,
                    "--peers",
                    peers,
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(NODE_PORT),
                ],
                cwd=str(BASE),
            )
            procs.append(p)

        leader = await wait_for_leader()
        print("leader:", leader)

        r1 = await ctl("put color blue")
        print("put:", r1)
        r2 = await ctl("get color")
        print("get:", r2)

        if not r1.get("ok"):
            raise RuntimeError("put failed")
        if not r2.get("ok"):
            raise RuntimeError("get failed")

        reply = r2.get("reply", {})
        if reply.get("value") != "blue":
            raise RuntimeError(f"unexpected value: {reply}")

        print("smoke test passed")
    finally:
        for p in reversed(procs):
            try:
                p.send_signal(signal.SIGTERM)
            except Exception:
                pass
        await asyncio.sleep(0.5)
        for p in reversed(procs):
            if p.poll() is None:
                p.kill()


if __name__ == "__main__":
    asyncio.run(main())
