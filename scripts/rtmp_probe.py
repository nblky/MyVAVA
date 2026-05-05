#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import socket
import struct
import time
from pathlib import Path


HANDSHAKE_SIZE = 1536


def recv_exact(conn: socket.socket, size: int) -> bytes:
    chunks = []
    remaining = size
    while remaining > 0:
        chunk = conn.recv(remaining)
        if not chunk:
            raise ConnectionError(f"peer closed with {remaining} bytes remaining")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def printable_runs(data: bytes, min_len: int = 4) -> list[str]:
    out: list[str] = []
    buf: list[int] = []
    for byte in data:
        if 32 <= byte <= 126:
            buf.append(byte)
            continue
        if len(buf) >= min_len:
            out.append(bytes(buf).decode("ascii", errors="ignore"))
        buf = []
    if len(buf) >= min_len:
        out.append(bytes(buf).decode("ascii", errors="ignore"))
    deduped: list[str] = []
    seen = set()
    for item in out:
        if item not in seen:
            deduped.append(item)
            seen.add(item)
    return deduped


def save_blob(path: Path, name: str, data: bytes) -> None:
    path.mkdir(parents=True, exist_ok=True)
    (path / name).write_bytes(data)


def handle_client(conn: socket.socket, addr: tuple[str, int], out_dir: Path) -> None:
    remote_ip, remote_port = addr
    stamp = time.strftime("%Y%m%d-%H%M%S")
    prefix = f"{stamp}_{remote_ip.replace('.', '_')}_{remote_port}"

    conn.settimeout(10)
    c0c1 = recv_exact(conn, 1 + HANDSHAKE_SIZE)
    version = c0c1[0]
    c1 = c0c1[1:]

    server_time = int(time.time()) & 0xFFFFFFFF
    s1 = struct.pack(">I", server_time) + b"\x00\x00\x00\x00" + os.urandom(HANDSHAKE_SIZE - 8)
    s0s1s2 = bytes([version]) + s1 + c1
    conn.sendall(s0s1s2)

    c2 = recv_exact(conn, HANDSHAKE_SIZE)

    post = bytearray()
    deadline = time.time() + 3
    while time.time() < deadline:
        try:
            chunk = conn.recv(4096)
        except socket.timeout:
            break
        if not chunk:
            break
        post.extend(chunk)
        if len(post) >= 65536:
            break

    save_blob(out_dir, f"{prefix}_c0c1.bin", c0c1)
    save_blob(out_dir, f"{prefix}_c2.bin", c2)
    if post:
        save_blob(out_dir, f"{prefix}_post.bin", bytes(post))

    print(f"accepted {remote_ip}:{remote_port} at {stamp}", flush=True)
    print(f"rtmp_version={version}", flush=True)
    print(f"c1_head={c1[:32].hex()}", flush=True)
    print(f"post_len={len(post)}", flush=True)
    if post:
        print(f"post_head={bytes(post[:128]).hex()}", flush=True)
        strings = printable_runs(bytes(post))
        if strings:
            print("ascii_runs=", flush=True)
            for item in strings[:40]:
                print(f"  {item}", flush=True)
        else:
            print("ascii_runs= none", flush=True)
    print(f"saved_prefix={out_dir / prefix}", flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="Minimal RTMP probe server")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=1935)
    parser.add_argument(
        "--out-dir",
        default=str(Path(__file__).resolve().parents[1] / "data" / "rtmp_probe"),
        help="directory to store captured handshake and payload blobs",
    )
    args = parser.parse_args()

    out_dir = Path(args.out_dir)
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind((args.host, args.port))
    server.listen(20)
    print(f"listening on {args.host}:{args.port}", flush=True)

    try:
        while True:
            conn, addr = server.accept()
            try:
                handle_client(conn, addr, out_dir)
            except Exception as exc:
                print(f"client_error {addr}: {exc}", flush=True)
            finally:
                try:
                    conn.close()
                except Exception:
                    pass
    finally:
        server.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
