#!/usr/bin/env python3
"""Minimal TLS MQTT 3.1.1 mock broker for VAVA station reversing.

Purpose:
- accept the station's TLS MQTT connection on port 9903
- log CONNECT metadata
- reply with CONNACK
- log SUBSCRIBE topics and reply with SUBACK
- keep the connection alive via PINGRESP
"""

from __future__ import annotations

import argparse
import socket
import ssl
import struct
import sys
import time
from dataclasses import dataclass
from typing import Iterable, Optional


PACKET_NAMES = {
    1: "CONNECT",
    2: "CONNACK",
    3: "PUBLISH",
    4: "PUBACK",
    5: "PUBREC",
    6: "PUBREL",
    7: "PUBCOMP",
    8: "SUBSCRIBE",
    9: "SUBACK",
    10: "UNSUBSCRIBE",
    11: "UNSUBACK",
    12: "PINGREQ",
    13: "PINGRESP",
    14: "DISCONNECT",
}


@dataclass
class Packet:
    packet_type: int
    flags: int
    payload: bytes


@dataclass
class BrokerConfig:
    timeout: float
    publish_on_subscribe: bool
    publish_topic: Optional[str]
    publish_payload: bytes
    publish_qos: int
    publish_retain: bool
    publish_delay: float
    publish_packet_id: int


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Minimal TLS MQTT mock broker")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=9903)
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--certfile", required=True)
    parser.add_argument("--keyfile", required=True)
    parser.add_argument("--accept-count", type=int, default=1)
    parser.add_argument("--publish-on-subscribe", action="store_true")
    parser.add_argument(
        "--publish-topic",
        help="topic to publish after SUBACK; defaults to the first subscribed topic",
    )
    parser.add_argument(
        "--publish-payload",
        default='{"mock":"hello-from-vava-server"}',
        help="UTF-8 payload used with --publish-on-subscribe",
    )
    parser.add_argument("--publish-qos", type=int, choices=[0, 1], default=1)
    parser.add_argument("--publish-retain", action="store_true")
    parser.add_argument("--publish-delay", type=float, default=0.2)
    parser.add_argument("--publish-packet-id", type=int, default=100)
    return parser.parse_args()


def recv_exact(sock: socket.socket, n: int) -> bytes:
    out = bytearray()
    while len(out) < n:
        chunk = sock.recv(n - len(out))
        if not chunk:
            raise ConnectionError("socket closed")
        out.extend(chunk)
    return bytes(out)


def recv_remaining_length(sock: socket.socket) -> int:
    multiplier = 1
    value = 0
    while True:
        encoded = recv_exact(sock, 1)[0]
        value += (encoded & 0x7F) * multiplier
        if (encoded & 0x80) == 0:
            return value
        multiplier *= 128
        if multiplier > 128 * 128 * 128:
            raise ValueError("malformed remaining length")


def recv_packet(sock: socket.socket) -> Packet:
    first = recv_exact(sock, 1)[0]
    packet_type = first >> 4
    flags = first & 0x0F
    remaining = recv_remaining_length(sock)
    payload = recv_exact(sock, remaining)
    return Packet(packet_type=packet_type, flags=flags, payload=payload)


def encode_remaining_length(value: int) -> bytes:
    out = bytearray()
    while True:
        encoded = value % 128
        value //= 128
        if value > 0:
            encoded |= 0x80
        out.append(encoded)
        if value == 0:
            return bytes(out)


def send_packet(sock: socket.socket, packet_type: int, flags: int, payload: bytes) -> None:
    first = bytes([(packet_type << 4) | flags])
    sock.sendall(first + encode_remaining_length(len(payload)) + payload)


def read_utf8_field(buf: bytes, offset: int) -> tuple[str, int]:
    if offset + 2 > len(buf):
        raise ValueError("short utf8 field")
    size = struct.unpack(">H", buf[offset : offset + 2])[0]
    offset += 2
    if offset + size > len(buf):
        raise ValueError("truncated utf8 field")
    return buf[offset : offset + size].decode("utf-8", "replace"), offset + size


def encode_utf8_field(text: str) -> bytes:
    data = text.encode("utf-8")
    return struct.pack(">H", len(data)) + data


def parse_connect(payload: bytes) -> dict[str, object]:
    protocol_name, off = read_utf8_field(payload, 0)
    if off + 4 > len(payload):
        raise ValueError("short connect header")
    protocol_level = payload[off]
    flags = payload[off + 1]
    keepalive = struct.unpack(">H", payload[off + 2 : off + 4])[0]
    off += 4

    client_id, off = read_utf8_field(payload, off)
    will_flag = bool(flags & 0x04)
    username_flag = bool(flags & 0x80)
    password_flag = bool(flags & 0x40)

    will_topic = None
    will_message = None
    if will_flag:
        will_topic, off = read_utf8_field(payload, off)
        will_message, off = read_utf8_field(payload, off)

    username = None
    password = None
    if username_flag:
        username, off = read_utf8_field(payload, off)
    if password_flag:
        password, off = read_utf8_field(payload, off)

    return {
        "protocol_name": protocol_name,
        "protocol_level": protocol_level,
        "flags": flags,
        "keepalive": keepalive,
        "client_id": client_id,
        "will_topic": will_topic,
        "will_message": will_message,
        "username": username,
        "password": password,
    }


def parse_subscribe(payload: bytes) -> tuple[int, list[tuple[str, int]]]:
    if len(payload) < 2:
        raise ValueError("short subscribe packet")
    packet_id = struct.unpack(">H", payload[:2])[0]
    off = 2
    topics: list[tuple[str, int]] = []
    while off < len(payload):
        topic, off = read_utf8_field(payload, off)
        if off >= len(payload):
            raise ValueError("missing qos in subscribe packet")
        qos = payload[off]
        off += 1
        topics.append((topic, qos))
    return packet_id, topics


def payload_hex(data: bytes, limit: int = 256) -> str:
    return data[:limit].hex()


def send_publish(
    sock: socket.socket,
    topic: str,
    payload: bytes,
    qos: int,
    retain: bool,
    packet_id: int,
) -> None:
    flags = (qos << 1) | (1 if retain else 0)
    variable_header = bytearray(encode_utf8_field(topic))
    if qos > 0:
        variable_header.extend(struct.pack(">H", packet_id))
    send_packet(sock, 3, flags, bytes(variable_header) + payload)


def log_connect(info: dict[str, object]) -> None:
    print("connect:")
    print(f"  protocol={info['protocol_name']} level={info['protocol_level']}")
    print(f"  flags=0x{info['flags']:02x} keepalive={info['keepalive']}")
    print(f"  client_id={info['client_id']}")
    print(f"  username={info['username']}")
    print(f"  password={info['password']}")
    if info["will_topic"] is not None:
        print(f"  will_topic={info['will_topic']}")
        print(f"  will_message={info['will_message']}")


def grant_qos(topics: Iterable[tuple[str, int]]) -> bytes:
    return bytes(min(qos, 1) for _, qos in topics)


def handle_client(conn: ssl.SSLSocket, config: BrokerConfig) -> None:
    conn.settimeout(config.timeout)
    pkt = recv_packet(conn)
    if pkt.packet_type != 1:
        raise ValueError(f"expected CONNECT, got {PACKET_NAMES.get(pkt.packet_type, pkt.packet_type)}")
    info = parse_connect(pkt.payload)
    log_connect(info)
    send_packet(conn, 2, 0, b"\x00\x00")
    print("sent CONNACK rc=0")

    while True:
        try:
            pkt = recv_packet(conn)
        except socket.timeout:
            print("idle_timeout")
            return
        except ConnectionError:
            print("peer_closed")
            return

        name = PACKET_NAMES.get(pkt.packet_type, f"TYPE-{pkt.packet_type}")
        print(f"packet type={name} flags=0x{pkt.flags:x} len={len(pkt.payload)}")

        if pkt.packet_type == 8:
            packet_id, topics = parse_subscribe(pkt.payload)
            for topic, qos in topics:
                print(f"  subscribe packet_id={packet_id} topic={topic} qos={qos}")
            send_packet(conn, 9, 0, struct.pack(">H", packet_id) + grant_qos(topics))
            print(f"sent SUBACK packet_id={packet_id}")
            if config.publish_on_subscribe and topics:
                topic = config.publish_topic or topics[0][0]
                if config.publish_delay > 0:
                    time.sleep(config.publish_delay)
                send_publish(
                    conn,
                    topic=topic,
                    payload=config.publish_payload,
                    qos=config.publish_qos,
                    retain=config.publish_retain,
                    packet_id=config.publish_packet_id,
                )
                print(
                    "sent PUBLISH "
                    f"topic={topic} qos={config.publish_qos} retain={int(config.publish_retain)} "
                    f"packet_id={config.publish_packet_id if config.publish_qos else 0} "
                    f"payload={config.publish_payload.decode('utf-8', 'replace')}"
                )
        elif pkt.packet_type == 12:
            send_packet(conn, 13, 0, b"")
            print("sent PINGRESP")
        elif pkt.packet_type == 4:
            packet_id = struct.unpack(">H", pkt.payload[:2])[0] if len(pkt.payload) >= 2 else -1
            print(f"  puback packet_id={packet_id}")
        elif pkt.packet_type == 3:
            print(f"  publish raw={payload_hex(pkt.payload)}")
        elif pkt.packet_type == 14:
            print("disconnect")
            return
        else:
            print(f"  raw={payload_hex(pkt.payload)}")


def main() -> int:
    args = parse_args()
    config = BrokerConfig(
        timeout=args.timeout,
        publish_on_subscribe=args.publish_on_subscribe,
        publish_topic=args.publish_topic,
        publish_payload=args.publish_payload.encode("utf-8"),
        publish_qos=args.publish_qos,
        publish_retain=args.publish_retain,
        publish_delay=args.publish_delay,
        publish_packet_id=args.publish_packet_id,
    )
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(certfile=args.certfile, keyfile=args.keyfile)

    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind((args.host, args.port))
    server.listen(5)
    server.settimeout(args.timeout)

    print(
        f"listening host={args.host} port={args.port} "
        f"timeout={args.timeout} accept_count={args.accept_count}"
    )
    started = time.time()

    accepted = 0
    try:
        while accepted < args.accept_count:
            try:
                raw_conn, addr = server.accept()
            except socket.timeout:
                print("wait_timeout")
                break

            accepted += 1
            print(f"accept[{accepted}] addr={addr[0]}:{addr[1]}")
            try:
                with ctx.wrap_socket(raw_conn, server_side=True) as conn:
                    print(f"tls version={conn.version()} cipher={conn.cipher()}")
                    handle_client(conn, config)
            except ssl.SSLError as exc:
                print(f"tls_error={exc}")
            except Exception as exc:
                print(f"client_error={exc!r}")
    finally:
        server.close()
        print(f"elapsed={time.time() - started:.2f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
