#!/usr/bin/env python3
"""Minimal TCP/TLS probe for the VAVA base station.

This script is designed for one job first: confirm whether the station
actively connects to a fake cloud endpoint and show enough metadata to keep
reversing without needing root packet capture.

Typical uses:
- MQTT over TLS: port 9903
- Storage / RTMP first-byte probe: port 1935
"""

from __future__ import annotations

import argparse
import binascii
import socket
import ssl
import sys
import time
from typing import Optional


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Capture inbound TCP/TLS probes")
    parser.add_argument("--host", default="0.0.0.0", help="listen host")
    parser.add_argument("--port", type=int, default=9903, help="listen port")
    parser.add_argument("--timeout", type=float, default=20.0, help="idle wait timeout in seconds")
    parser.add_argument("--accept-count", type=int, default=1, help="number of connections to capture")
    parser.add_argument("--read-size", type=int, default=512, help="bytes to read from each connection")
    parser.add_argument("--tls", action="store_true", help="wrap accepted sockets with TLS")
    parser.add_argument("--certfile", help="PEM certificate used with --tls")
    parser.add_argument("--keyfile", help="PEM private key used with --tls")
    return parser.parse_args()


def looks_like_tls_client_hello(data: bytes) -> bool:
    return len(data) >= 6 and data[0] == 0x16 and data[1] == 0x03 and data[5] == 0x01


def extract_tls_sni(data: bytes) -> Optional[str]:
    if not looks_like_tls_client_hello(data):
        return None
    if len(data) < 9:
        return None

    record_len = int.from_bytes(data[3:5], "big")
    record = data[5 : 5 + record_len]
    if len(record) < 4 or record[0] != 0x01:
        return None

    hello_len = int.from_bytes(record[1:4], "big")
    hello = record[4 : 4 + hello_len]
    if len(hello) < 34:
        return None

    idx = 34
    if idx >= len(hello):
        return None

    session_id_len = hello[idx]
    idx += 1 + session_id_len
    if idx + 2 > len(hello):
        return None

    cipher_len = int.from_bytes(hello[idx : idx + 2], "big")
    idx += 2 + cipher_len
    if idx >= len(hello):
        return None

    compression_len = hello[idx]
    idx += 1 + compression_len
    if idx + 2 > len(hello):
        return None

    ext_total_len = int.from_bytes(hello[idx : idx + 2], "big")
    idx += 2
    ext_end = min(idx + ext_total_len, len(hello))

    while idx + 4 <= ext_end:
        ext_type = int.from_bytes(hello[idx : idx + 2], "big")
        ext_len = int.from_bytes(hello[idx + 2 : idx + 4], "big")
        ext_data = hello[idx + 4 : idx + 4 + ext_len]
        idx += 4 + ext_len

        if ext_type != 0x0000 or len(ext_data) < 5:
            continue

        name_list_len = int.from_bytes(ext_data[0:2], "big")
        pos = 2
        limit = min(2 + name_list_len, len(ext_data))
        while pos + 3 <= limit:
            name_type = ext_data[pos]
            name_len = int.from_bytes(ext_data[pos + 1 : pos + 3], "big")
            pos += 3
            if pos + name_len > limit:
                break
            if name_type == 0:
                try:
                    return ext_data[pos : pos + name_len].decode("utf-8")
                except UnicodeDecodeError:
                    return ext_data[pos : pos + name_len].decode("utf-8", "replace")
            pos += name_len
    return None


def hexdump_prefix(data: bytes, limit: int = 128) -> str:
    return binascii.hexlify(data[:limit]).decode("ascii")


def recv_probe(conn: socket.socket, read_size: int) -> None:
    conn.settimeout(3.0)
    data = conn.recv(read_size)
    print(f"bytes={len(data)}")
    print(f"hex={hexdump_prefix(data)}")
    sni = extract_tls_sni(data)
    if sni:
        print(f"tls_sni={sni}")
    elif data[:1] == b"\x10":
        print("hint=mqtt_connect_like")
    else:
        print("hint=unknown")


def build_ssl_context(args: argparse.Namespace) -> ssl.SSLContext:
    if not args.certfile or not args.keyfile:
        raise SystemExit("--tls requires --certfile and --keyfile")
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(certfile=args.certfile, keyfile=args.keyfile)
    return ctx


def main() -> int:
    args = parse_args()
    ssl_ctx = build_ssl_context(args) if args.tls else None

    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((args.host, args.port))
    srv.listen(5)
    srv.settimeout(args.timeout)

    print(
        f"listening host={args.host} port={args.port} timeout={args.timeout} "
        f"accept_count={args.accept_count} tls={args.tls}"
    )

    accepted = 0
    started = time.time()
    try:
        while accepted < args.accept_count:
            try:
                conn, addr = srv.accept()
            except socket.timeout:
                print("wait_timeout")
                break

            accepted += 1
            print(f"accept[{accepted}] addr={addr[0]}:{addr[1]}")

            wrapped = conn
            try:
                if ssl_ctx is not None:
                    wrapped = ssl_ctx.wrap_socket(conn, server_side=True)
                    print(
                        "tls_handshake_ok "
                        f"version={wrapped.version()} cipher={wrapped.cipher()}"
                    )
                recv_probe(wrapped, args.read_size)
            except ssl.SSLError as exc:
                print(f"tls_error={exc}")
            except socket.timeout:
                print("read_timeout")
            except Exception as exc:
                print(f"probe_error={exc!r}")
            finally:
                try:
                    wrapped.close()
                except Exception:
                    pass
    finally:
        srv.close()
        print(f"elapsed={time.time() - started:.2f}s")

    return 0


if __name__ == "__main__":
    sys.exit(main())
