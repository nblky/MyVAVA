#!/usr/bin/env python3
import argparse
import socket
import threading
from contextlib import closing


BUFFER_SIZE = 65536
LOCAL_DOMAIN_SUFFIXES = (
    "sunvalleycloud.com",
)


def recv_until_headers(conn):
    data = b""
    while b"\r\n\r\n" not in data and len(data) < 65536:
        chunk = conn.recv(4096)
        if not chunk:
            break
        data += chunk
    return data


def parse_connect_target(request_bytes):
    try:
        head = request_bytes.decode("iso-8859-1", "replace")
    except Exception:
        return None, None, None
    lines = head.split("\r\n")
    if not lines:
        return None, None, None
    parts = lines[0].split()
    if len(parts) < 3:
        return None, None, None
    method, target, version = parts[0], parts[1], parts[2]
    return method.upper(), target, version


def pipe(src, dst):
    try:
        while True:
            data = src.recv(BUFFER_SIZE)
            if not data:
                break
            dst.sendall(data)
    except OSError:
        pass
    finally:
        try:
            dst.shutdown(socket.SHUT_WR)
        except OSError:
            pass


def should_route_local(host):
    host = (host or "").lower().rstrip(".")
    return any(host == suffix or host.endswith("." + suffix) for suffix in LOCAL_DOMAIN_SUFFIXES)


def handle_client(conn, addr, args):
    with closing(conn):
        request = recv_until_headers(conn)
        method, target, version = parse_connect_target(request)
        if method != "CONNECT" or not target:
            conn.sendall(
                b"HTTP/1.1 405 Method Not Allowed\r\n"
                b"Connection: close\r\n"
                b"Content-Length: 0\r\n\r\n"
            )
            print(f"[proxy] reject {addr[0]}:{addr[1]} non-CONNECT request", flush=True)
            return

        host = target
        port = 443
        if ":" in target:
            host, port_str = target.rsplit(":", 1)
            try:
                port = int(port_str)
            except ValueError:
                port = 443

        if should_route_local(host):
            route_host = args.upstream_host
            route_port = args.upstream_port
            route_desc = f"{route_host}:{route_port}"
        else:
            route_host = host
            route_port = port
            route_desc = f"{route_host}:{route_port} direct"

        try:
            upstream = socket.create_connection((route_host, route_port), timeout=5)
        except OSError as exc:
            conn.sendall(
                b"HTTP/1.1 502 Bad Gateway\r\n"
                b"Connection: close\r\n"
                b"Content-Length: 0\r\n\r\n"
            )
            print(
                f"[proxy] fail {addr[0]}:{addr[1]} -> {host}:{port} "
                f"(upstream {route_desc} unavailable: {exc})",
                flush=True,
            )
            return

        with closing(upstream):
            conn.sendall(
                b"HTTP/1.1 200 Connection Established\r\n"
                b"Proxy-Agent: sunvalley-local-proxy\r\n\r\n"
            )
            print(
                f"[proxy] tunnel {addr[0]}:{addr[1]} requested {host}:{port} "
                f"-> {route_desc}",
                flush=True,
            )

            t1 = threading.Thread(target=pipe, args=(conn, upstream), daemon=True)
            t2 = threading.Thread(target=pipe, args=(upstream, conn), daemon=True)
            t1.start()
            t2.start()
            t1.join()
            t2.join()


def serve(args):
    family = socket.AF_INET6 if ":" in args.listen_host else socket.AF_INET
    with socket.socket(family, socket.SOCK_STREAM) as server:
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind((args.listen_host, args.listen_port))
        server.listen(64)
        print(
            f"[proxy] listening on {args.listen_host}:{args.listen_port}, "
            f"forwarding CONNECT tunnels to {args.upstream_host}:{args.upstream_port}",
            flush=True,
        )
        while True:
            conn, addr = server.accept()
            threading.Thread(
                target=handle_client,
                args=(conn, addr, args),
                daemon=True,
            ).start()


def build_parser():
    parser = argparse.ArgumentParser(
        description="Minimal HTTP CONNECT proxy that tunnels app HTTPS traffic to the local Sunvalley mock."
    )
    parser.add_argument("--listen-host", default="0.0.0.0")
    parser.add_argument("--listen-port", type=int, default=8888)
    parser.add_argument("--upstream-host", default="127.0.0.1")
    parser.add_argument("--upstream-port", type=int, default=443)
    return parser


def main():
    args = build_parser().parse_args()
    serve(args)


if __name__ == "__main__":
    main()
