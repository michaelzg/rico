#!/usr/bin/env python3
import argparse
import socket
import threading
import time


def handle(conn, addr):
    try:
        data = conn.recv(1024).decode(errors="ignore").strip()
        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        print(f"[{ts}] {addr[0]}:{addr[1]} {data}")
    finally:
        conn.close()


def main():
    parser = argparse.ArgumentParser(description="Simple heartbeat sink for Pico connectivity test")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=9000)
    args = parser.parse_args()

    s = socket.socket()
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind((args.host, args.port))
    s.listen(20)
    print(f"listening on {args.host}:{args.port}")

    while True:
        conn, addr = s.accept()
        threading.Thread(target=handle, args=(conn, addr), daemon=True).start()


if __name__ == "__main__":
    main()
