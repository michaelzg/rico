#!/usr/bin/env python3
import argparse
import asyncio


async def main():
    parser = argparse.ArgumentParser(description="Control client for raft coordinator")
    parser.add_argument("command", nargs="+", help="status | put <k> <v> | get <k>")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=7001)
    args = parser.parse_args()

    cmd = " ".join(args.command)
    reader, writer = await asyncio.open_connection(args.host, args.port)
    writer.write((cmd + "\n").encode())
    await writer.drain()
    resp = await reader.readline()
    print(resp.decode().strip())
    writer.close()
    await writer.wait_closed()


if __name__ == "__main__":
    asyncio.run(main())
