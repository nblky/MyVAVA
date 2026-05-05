#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from bitstring import BitStream

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.config import get_settings  # noqa: E402
from app.store import get_store  # noqa: E402
from pyrtmp import StreamClosedException  # noqa: E402
from pyrtmp.amf.serializers import AMF0Deserializer, AMF0Serializer  # noqa: E402
from pyrtmp.amf.types import AMF0  # noqa: E402
from pyrtmp.flv import FLVFileWriter, FLVMediaType  # noqa: E402
from pyrtmp.messages import Chunk  # noqa: E402
from pyrtmp.messages.audio import AudioMessage  # noqa: E402
from pyrtmp.messages.command import CommandMessage, NCCreateStream, NCConnect, NSCloseStream, NSDeleteStream, NSPublish  # noqa: E402
from pyrtmp.messages.data import DataMessage, MetaDataMessage  # noqa: E402
from pyrtmp.messages.factory import MessageFactory  # noqa: E402
from pyrtmp.messages.protocol_control import SetChunkSize, SetPeerBandwidth, WindowAcknowledgementSize  # noqa: E402
from pyrtmp.messages.user_control import PingResponse, StreamBegin, UserControlMessage  # noqa: E402
from pyrtmp.messages.video import VideoMessage  # noqa: E402
from pyrtmp.rtmp import RTMPProtocol, SimpleRTMPController  # noqa: E402
from pyrtmp.session_manager import SessionManager  # noqa: E402


logger = logging.getLogger("vava.rtmp_ingest")
LOG_DIR = ROOT_DIR / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)
LOG_PATH = LOG_DIR / "rtmp_ingest.log"
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    force=True,
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(LOG_PATH),
    ],
)

AMF0_UNDEFINED = object()
CLOUD_AES_KEY = b"vavalic2".ljust(16, b"\x00")
ENCRYPTED_KEYFRAME_MAGIC = bytes.fromhex("27be9b5b")


def iso_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _flatten_named_values(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return {str(key): item for key, item in value.items()}
    if isinstance(value, list):
        out: dict[str, Any] = {}
        for item in value:
            if isinstance(item, dict):
                for key, subvalue in item.items():
                    out[str(key)] = subvalue
        return out
    return {}


def _safe_name(value: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in {"-", "_", "."} else "_" for ch in str(value or ""))
    return cleaned.strip("._") or "unknown"


def _ffmpeg_bin() -> str:
    return shutil.which("ffmpeg") or "/opt/homebrew/bin/ffmpeg"


def _openssl_bin() -> str:
    return (
        shutil.which("openssl")
        or "/usr/bin/openssl"
        or "/opt/homebrew/opt/openssl@3/bin/openssl"
    )


def _run_ffmpeg(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        capture_output=True,
        text=True,
        check=False,
        timeout=180,
    )


def _run_ffmpeg_bytes(args: list[str], input_bytes: bytes) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        args,
        input=input_bytes,
        capture_output=True,
        check=False,
        timeout=180,
    )


def _aes_128_ecb_decrypt_partial(data: bytes) -> bytes:
    full_blocks = (len(data) // 16) * 16
    if full_blocks <= 0:
        return data
    result = subprocess.run(
        [
            _openssl_bin(),
            "enc",
            "-aes-128-ecb",
            "-d",
            "-nopad",
            "-nosalt",
            "-K",
            CLOUD_AES_KEY.hex(),
        ],
        input=data[:full_blocks],
        capture_output=True,
        check=False,
        timeout=60,
    )
    if result.returncode != 0 and not result.stdout:
        raise RuntimeError(result.stderr.decode("utf-8", errors="ignore").strip() or "openssl decrypt failed")
    return bytes(result.stdout) + data[full_blocks:]


def _find_start_code(data: bytes, offset: int = 0) -> tuple[int, int] | None:
    limit = len(data) - 3
    index = max(0, offset)
    while index <= limit:
        if data[index] == 0 and data[index + 1] == 0:
            if data[index + 2] == 1:
                return (index, 3)
            if index + 3 < len(data) and data[index + 2] == 0 and data[index + 3] == 1:
                return (index, 4)
        index += 1
    return None


def _annexb_to_avcc(data: bytes) -> bytes:
    chunks = bytearray()
    cursor = 0
    while True:
        found = _find_start_code(data, cursor)
        if found is None:
            break
        start, start_code_size = found
        nal_start = start + start_code_size
        next_found = _find_start_code(data, nal_start)
        nal_end = next_found[0] if next_found is not None else len(data)
        nal = data[nal_start:nal_end]
        if nal:
            chunks.extend(len(nal).to_bytes(4, "big"))
            chunks.extend(nal)
        cursor = nal_end
    return bytes(chunks) if chunks else data


def _sanitize_cloud_video_payload(payload: bytes) -> tuple[bytes, bool]:
    working = payload
    decrypted = False
    if working.startswith(ENCRYPTED_KEYFRAME_MAGIC):
        working = _aes_128_ecb_decrypt_partial(working)
        decrypted = True
    if working.startswith(b"\x00\x00\x00\x01") or working.startswith(b"\x00\x00\x01"):
        working = _annexb_to_avcc(working)
    return (working, decrypted)


def _parse_adts_header(data: bytes) -> tuple[int, int] | None:
    if len(data) < 7 or data[0] != 0xFF or (data[1] & 0xF0) != 0xF0:
        return None
    header_len = 7 if (data[1] & 0x01) else 9
    if len(data) < header_len:
        return None
    frame_len = ((data[3] & 0x03) << 11) | (data[4] << 3) | ((data[5] & 0xE0) >> 5)
    if frame_len < header_len or frame_len > len(data):
        return None
    return (header_len, frame_len)


def _sanitize_cloud_audio_payload(payload: bytes) -> tuple[bytes, bool]:
    if not payload:
        return (payload, False)

    candidate = payload
    decrypted = False
    if candidate[0] != 0xFF:
        candidate = bytes([0xFF]) + _aes_128_ecb_decrypt_partial(candidate[1:])
        decrypted = True

    adts = _parse_adts_header(candidate)
    if adts is None:
        return (payload, False)

    header_len, frame_len = adts
    raw_aac = candidate[header_len:frame_len]
    if not raw_aac:
        return (payload, False)
    return (raw_aac, decrypted)


def _cloud_media_decode_root() -> Path:
    root = get_settings().data_dir / "cloud_media" / "decode"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _live_hls_root() -> Path:
    root = get_settings().live_hls_root
    root.mkdir(parents=True, exist_ok=True)
    return root


def _cloud_media_movie_path(flv_path: Path, suffix: str) -> Path:
    movie_dir = _cloud_media_decode_root() / "movies"
    movie_dir.mkdir(parents=True, exist_ok=True)
    return movie_dir / f"{flv_path.stem}{suffix}"


def _cloud_media_image_path(flv_path: Path, suffix: str) -> Path:
    image_dir = _cloud_media_decode_root() / "imgs"
    image_dir.mkdir(parents=True, exist_ok=True)
    return image_dir / f"{flv_path.stem}{suffix}"


def _sanitize_cloud_flv_bytes(raw: bytes) -> tuple[bytes, dict[str, int]]:
    if len(raw) < 13 or raw[:3] != b"FLV":
        return (raw, {})

    sanitized = bytearray()
    sanitized.extend(raw[:13])
    offset = 13
    patched_video_tags = 0
    decrypted_keyframes = 0
    patched_audio_tags = 0
    decrypted_audio_tags = 0

    while offset + 11 <= len(raw):
        tag_type = raw[offset]
        data_size = int.from_bytes(raw[offset + 1 : offset + 4], "big")
        body_start = offset + 11
        body_end = body_start + data_size
        if body_end + 4 > len(raw):
            logger.warning("rtmp sanitize truncated flv=%s offset=%s size=%s", flv_path, offset, data_size)
            return None

        timestamp_ext = raw[offset + 4 : offset + 8]
        stream_id = raw[offset + 8 : offset + 11]
        body = bytes(raw[body_start:body_end])
        new_body = body

        if tag_type == 9 and len(body) >= 5 and body[1] == 1:
            new_payload, decrypted = _sanitize_cloud_video_payload(body[5:])
            if decrypted:
                decrypted_keyframes += 1
            if new_payload != body[5:]:
                patched_video_tags += 1
                new_body = body[:5] + new_payload
        elif tag_type == 8 and len(body) >= 3 and body[1] == 1:
            new_payload, decrypted = _sanitize_cloud_audio_payload(body[2:])
            if decrypted:
                decrypted_audio_tags += 1
            if new_payload != body[2:]:
                patched_audio_tags += 1
                new_body = body[:2] + new_payload

        sanitized.append(tag_type)
        sanitized.extend(len(new_body).to_bytes(3, "big"))
        sanitized.extend(timestamp_ext)
        sanitized.extend(stream_id)
        sanitized.extend(new_body)
        sanitized.extend((len(new_body) + 11).to_bytes(4, "big"))
        offset = body_end + 4

    stats = {
        "patchedVideoTags": patched_video_tags,
        "decryptedKeyframes": decrypted_keyframes,
        "patchedAudioTags": patched_audio_tags,
        "decryptedAudioTags": decrypted_audio_tags,
    }
    if patched_video_tags <= 0 and decrypted_keyframes <= 0 and patched_audio_tags <= 0 and decrypted_audio_tags <= 0:
        return (raw, stats)
    return (bytes(sanitized), stats)


def _sanitize_live_video_tag_body(body: bytes) -> bytes:
    if len(body) < 5 or body[1] != 1:
        return body
    payload, _ = _sanitize_cloud_video_payload(body[5:])
    if payload == body[5:]:
        return body
    return body[:5] + payload


def _sanitize_live_audio_tag_body(body: bytes) -> bytes:
    if len(body) < 3 or body[1] != 1:
        return body
    payload, _ = _sanitize_cloud_audio_payload(body[2:])
    if payload == body[2:]:
        return body
    return body[:2] + payload


def _clean_directory(directory: Path) -> None:
    if not directory.exists():
        return
    for child in directory.iterdir():
        try:
            if child.is_dir():
                shutil.rmtree(child)
            else:
                child.unlink()
        except FileNotFoundError:
            continue


class LiveHLSPublisher:
    def __init__(self, *, device_sn: str) -> None:
        self.device_sn = _safe_name(device_sn)
        self.output_dir = _live_hls_root() / self.device_sn
        self.output_dir.mkdir(parents=True, exist_ok=True)
        _clean_directory(self.output_dir)
        self.playlist_path = self.output_dir / "index.m3u8"
        self.log_path = LOG_DIR / f"live_hls_{self.device_sn}.log"
        self._log_handle = self.log_path.open("ab")
        self.process = subprocess.Popen(
            [
                _ffmpeg_bin(),
                "-hide_banner",
                "-loglevel",
                "warning",
                "-nostdin",
                "-fflags",
                "nobuffer",
                "-flags",
                "low_delay",
                "-analyzeduration",
                "0",
                "-probesize",
                "32k",
                "-f",
                "flv",
                "-i",
                "pipe:0",
                "-c",
                "copy",
                "-muxdelay",
                "0",
                "-muxpreload",
                "0",
                "-f",
                "hls",
                "-hls_time",
                "1",
                "-hls_init_time",
                "1",
                "-hls_list_size",
                "6",
                "-hls_delete_threshold",
                "1",
                "-hls_flags",
                "delete_segments+append_list+omit_endlist+independent_segments+program_date_time",
                "-hls_segment_type",
                "fmp4",
                "-hls_fmp4_init_filename",
                "init.mp4",
                "-hls_segment_filename",
                str(self.output_dir / "seg_%06d.m4s"),
                str(self.playlist_path),
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=self._log_handle,
            bufsize=0,
        )
        self._write_header()

    def _write_header(self) -> None:
        self.write_raw(b"FLV\x01\x05\x00\x00\x00\x09\x00\x00\x00\x00")

    def write_raw(self, payload: bytes) -> bool:
        if self.process.poll() is not None:
            return False
        stdin = self.process.stdin
        if stdin is None:
            return False
        try:
            stdin.write(payload)
            stdin.flush()
            return True
        except (BrokenPipeError, OSError):
            return False

    def write_tag(self, tag_type: int, timestamp: int, payload: bytes) -> bool:
        ts = int(timestamp or 0)
        header = bytearray()
        header.append(tag_type & 0xFF)
        header.extend(len(payload).to_bytes(3, "big"))
        header.extend((ts & 0xFFFFFF).to_bytes(3, "big"))
        header.append((ts >> 24) & 0xFF)
        header.extend(b"\x00\x00\x00")
        packet = bytes(header) + bytes(payload) + (len(payload) + 11).to_bytes(4, "big")
        return self.write_raw(packet)

    def close(self) -> None:
        try:
            if self.process.stdin is not None:
                self.process.stdin.close()
        except OSError:
            pass
        try:
            self.process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(timeout=5)
        finally:
            self._log_handle.close()

    def cleanup_files(self) -> None:
        _clean_directory(self.output_dir)

    @property
    def relative_playlist_path(self) -> str:
        return f"{self.device_sn}/index.m3u8"


def _postprocess_capture(flv_path: Path) -> tuple[Path | None, Path | None]:
    ffmpeg_bin = _ffmpeg_bin()
    mp4_path = _cloud_media_movie_path(flv_path, ".mp4")
    thumb_path = _cloud_media_image_path(flv_path, ".png")
    clean_path = _cloud_media_movie_path(flv_path, ".clean.flv")
    if clean_path.exists():
        clean_path.unlink()

    source_path = flv_path
    source_bytes: bytes | None = None

    try:
        raw = flv_path.read_bytes()
        sanitized_bytes, sanitize_stats = _sanitize_cloud_flv_bytes(raw)
        if sanitize_stats:
            logger.info(
                "rtmp sanitize flv src=%s dst=<memory> patchedVideoTags=%s decryptedKeyframes=%s patchedAudioTags=%s decryptedAudioTags=%s",
                flv_path,
                sanitize_stats.get("patchedVideoTags", 0),
                sanitize_stats.get("decryptedKeyframes", 0),
                sanitize_stats.get("patchedAudioTags", 0),
                sanitize_stats.get("decryptedAudioTags", 0),
            )
        if sanitized_bytes != raw:
            source_bytes = sanitized_bytes
    except Exception as exc:
        logger.warning("rtmp sanitize failed for %s: %r", flv_path, exc)

    copy_args = [
        ffmpeg_bin,
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
    ]
    if source_bytes is not None:
        copy_args.extend(["-f", "flv", "-i", "pipe:0"])
        copy_args.extend(["-c", "copy", "-movflags", "+faststart", str(mp4_path)])
        copy_result = _run_ffmpeg_bytes(copy_args, source_bytes)
    else:
        copy_args.extend(
            ["-i", str(source_path), "-c", "copy", "-movflags", "+faststart", str(mp4_path)]
        )
        copy_result = _run_ffmpeg(copy_args)
    if copy_result.returncode != 0:
        stderr = copy_result.stderr.decode("utf-8", errors="ignore").strip() if isinstance(copy_result.stderr, bytes) else copy_result.stderr.strip()
        logger.warning("ffmpeg remux failed for %s via %s: %s", flv_path, source_path, stderr)
        mp4_path = None

    if mp4_path is not None:
        thumb_result = _run_ffmpeg(
            [
                ffmpeg_bin,
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-i",
                str(mp4_path),
                "-frames:v",
                "1",
                "-vf",
                "scale='min(640,iw)':-2",
                str(thumb_path),
            ]
        )
    elif source_bytes is not None:
        thumb_result = _run_ffmpeg_bytes(
            [
                ffmpeg_bin,
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-f",
                "flv",
                "-i",
                "pipe:0",
                "-frames:v",
                "1",
                "-vf",
                "scale='min(640,iw)':-2",
                str(thumb_path),
            ],
            source_bytes,
        )
    else:
        thumb_result = _run_ffmpeg(
            [
                ffmpeg_bin,
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-i",
                str(source_path),
                "-frames:v",
                "1",
                "-vf",
                "scale='min(640,iw)':-2",
                str(thumb_path),
            ]
        )
    if thumb_result.returncode != 0:
        stderr = thumb_result.stderr.decode("utf-8", errors="ignore").strip() if isinstance(thumb_result.stderr, bytes) else thumb_result.stderr.strip()
        logger.warning("ffmpeg thumbnail failed for %s: %s", flv_path, stderr)
        thumb_path = None
    return (mp4_path if mp4_path and mp4_path.is_file() else None, thumb_path if thumb_path and thumb_path.is_file() else None)


def _decode_amf0_values(payload: bytes) -> list[Any]:
    data = BitStream(payload)
    values: list[Any] = []
    while data.pos < data.len:
        pos = data.pos
        try:
            values.append(AMF0Deserializer.from_stream(data))
        except Exception:
            data.pos = pos
            break
    return values


def _build_command_response(
    *,
    transaction_id: Any,
    values: list[Any],
    command_name: str = "_result",
    chunk_id: int = 3,
    msg_stream_id: int = 0,
) -> Chunk:
    data = BitStream()
    _write_amf0_value(data, command_name)
    _write_amf0_value(data, transaction_id)
    for value in values:
        _write_amf0_value(data, value)
    return Chunk(
        chunk_type=0,
        chunk_id=chunk_id,
        timestamp=0,
        msg_length=len(data.bytes),
        msg_type_id=0x14,
        msg_stream_id=msg_stream_id,
        payload=data.bytes,
    )


def _write_amf0_value(data: BitStream, value: Any) -> None:
    if value is AMF0_UNDEFINED:
        data.append(f"uint:8={int(AMF0.NULL) + 1}")
        return
    AMF0Serializer.create_object(data, value)


def _session_server_ip(session: SessionManager) -> str:
    sockname = session.writer.get_extra_info("sockname")
    if isinstance(sockname, tuple) and sockname:
        return str(sockname[0] or "")
    return ""


def _payload_head_hex(payload: bytes, limit: int = 32) -> str:
    return bytes(payload[:limit]).hex()


def _decode_metadata_message(message: Chunk) -> tuple[str, dict[str, Any]] | None:
    values = _decode_amf0_values(bytes(getattr(message, "payload", b"") or b""))
    if not values:
        return None
    name = str(values[0] or "")
    if name == "@setDataFrame" and len(values) >= 3 and isinstance(values[2], dict):
        return (str(values[1] or ""), values[2])
    if name == "onMetaData" and len(values) >= 2 and isinstance(values[1], dict):
        return (name, values[1])
    return None


def _describe_outbound_chunk(chunk: Chunk) -> str:
    msg_type = int(getattr(chunk, "msg_type_id", -1) or -1)
    stream_id = int(getattr(chunk, "msg_stream_id", 0) or 0)
    chunk_id = int(getattr(chunk, "chunk_id", 0) or 0)
    payload = bytes(getattr(chunk, "payload", b"") or b"")
    base = f"chunkId={chunk_id} msgType={msg_type} stream={stream_id}"
    if msg_type in {0x11, 0x14}:
        values = _decode_amf0_values(payload)
        if values:
            command_name = str(values[0] or "")
            transaction_id = values[1] if len(values) > 1 else 0
            arguments = values[3:] if len(values) > 3 else []
            return f"{base} command={command_name} tx={transaction_id} args={arguments}"
    if msg_type == 4 and len(payload) >= 2:
        event_type = int.from_bytes(payload[:2], "big", signed=False)
        return f"{base} userControl={event_type} payload={payload.hex()}"
    if msg_type == 1 and len(payload) >= 4:
        return f"{base} setChunkSize={int.from_bytes(payload[:4], 'big', signed=False)}"
    if msg_type == 5 and len(payload) >= 4:
        return f"{base} windowAckSize={int.from_bytes(payload[:4], 'big', signed=False)}"
    if msg_type == 6 and len(payload) >= 5:
        ack_size = int.from_bytes(payload[:4], "big", signed=False)
        limit_type = payload[4]
        return f"{base} setPeerBandwidth={ack_size} limit={limit_type}"
    return f"{base} len={len(payload)} head={_payload_head_hex(payload)}"


def _build_connect_response(message: NCConnect, server_ip: str) -> Chunk:
    return _build_command_response(
        transaction_id=message.transaction_id,
        values=[
            {
                "fmsVer": "FMS/3,0,123",
                "capabilities": 31,
            },
            {
                "level": "status",
                "code": "NetConnection.Connect.Success",
                "description": "Connection succeeds",
                "objectEncoding": 0,
                "serverIp": server_ip or "0.0.0.0",
            },
        ],
        chunk_id=int(getattr(message, "chunk_id", 3) or 3),
        msg_stream_id=0,
    )


def _build_on_bw_done() -> Chunk:
    return _build_command_response(
        command_name="onBWDone",
        transaction_id=0,
        values=[None],
        chunk_id=3,
        msg_stream_id=0,
    )


def _build_release_stream_response(transaction_id: Any) -> Chunk:
    return _build_command_response(
        transaction_id=transaction_id,
        values=[None, AMF0_UNDEFINED],
        chunk_id=3,
        msg_stream_id=0,
    )


def _build_on_fc_publish(stream_name: str = "") -> Chunk:
    return _build_command_response(
        command_name="onFCPublish",
        transaction_id=0,
        values=[
            None,
            {
                "level": "status",
                "code": "NetStream.Publish.Start",
                "description": f"FCPublish accepted{': ' + stream_name if stream_name else ''}",
            },
        ],
        chunk_id=3,
        msg_stream_id=0,
    )


def _write_chunk_type0(session: SessionManager, chunk: Chunk) -> None:
    session.previous_chunk_for_writing = None
    logger.info(
        "rtmp send peer=%s %s",
        session.peername,
        _describe_outbound_chunk(chunk),
    )
    session.write_chunk_to_stream(chunk)


class VAVARTMPIngestController(SimpleRTMPController):
    MEDIA_IDLE_SECONDS = 8.0

    def __init__(self, output_dir: Path) -> None:
        super().__init__()
        self.output_dir = output_dir

    def _open_capture(self, session: SessionManager) -> Path:
        started_at_ms = int(time.time() * 1000)
        started_at = iso_now()
        device_sn = str(
            session.state.get("deviceSn")
            or session.state.get("publishingName")
            or ""
        ).strip()
        token = str(session.state.get("token", "") or "").strip()
        capture_seq = int(session.state.get("captureSeq", 0) or 0) + 1
        stem = "_".join(
            [
                datetime.now().strftime("%Y%m%d-%H%M%S"),
                _safe_name(device_sn),
                _safe_name(token[:12] or "no-token"),
                f"seg{capture_seq:03d}",
            ]
        )
        self.output_dir.mkdir(parents=True, exist_ok=True)
        flv_path = self.output_dir / f"{stem}.flv"
        session.state["captureSeq"] = capture_seq
        session.state["startedAt"] = started_at
        session.state["startedAtMs"] = started_at_ms
        session.state["flvPath"] = str(flv_path)
        session.state["flvWriter"] = FLVFileWriter(str(flv_path))
        session.state["metadata"] = {}
        session.state["videoFrames"] = 0
        session.state["audioFrames"] = 0
        session.state["_loggedMetadata"] = False
        session.state["lastMediaAtMono"] = time.monotonic()
        logger.info(
            "rtmp capture-open peer=%s device=%s seq=%s file=%s",
            session.peername,
            device_sn,
            capture_seq,
            flv_path,
        )
        return flv_path

    def _touch_media(self, session: SessionManager) -> None:
        session.state["lastMediaAtMono"] = time.monotonic()

    def _store_pending_metadata(self, session: SessionManager, payload: bytes, meta: dict[str, Any]) -> None:
        session.state["pendingMetadataPayload"] = bytes(payload)
        session.state["pendingMetadata"] = dict(meta)

    def _flush_pending_metadata(self, session: SessionManager) -> None:
        writer: FLVFileWriter | None = session.state.get("flvWriter")
        payload = bytes(session.state.pop("pendingMetadataPayload", b"") or b"")
        meta = session.state.pop("pendingMetadata", None)
        if not writer or not payload:
            if isinstance(meta, dict):
                session.state["metadata"] = meta
            return
        writer.write(0, payload, FLVMediaType.OBJECT)
        if isinstance(meta, dict):
            session.state["metadata"] = meta

    def _ensure_capture(self, session: SessionManager) -> None:
        if not session.state.get("flvWriter"):
            self._open_capture(session)
            self._flush_pending_metadata(session)
        self._touch_media(session)

    async def _maybe_finalize_idle_capture(self, session: SessionManager) -> None:
        if not session.state.get("flvWriter"):
            return
        last_media_at = float(session.state.get("lastMediaAtMono", 0.0) or 0.0)
        if last_media_at <= 0:
            return
        if (time.monotonic() - last_media_at) < self.MEDIA_IDLE_SECONDS:
            return
        await self._finalize_capture(session, reason="media_idle")

    async def client_callback(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        session = SessionManager(reader=reader, writer=writer)
        logger.debug("Client connected %s", session.peername)

        try:
            await self.on_handshake(session)
            logger.debug("Handshake! %s", session.peername)

            async for chunk in session.read_chunks_from_stream():
                try:
                    message = MessageFactory.from_chunk(chunk)
                except NotImplementedError:
                    logger.info(
                        "rtmp raw peer=%s msgType=%s stream=%s len=%s head=%s",
                        session.peername,
                        chunk.msg_type_id,
                        chunk.msg_stream_id,
                        chunk.msg_length,
                        _payload_head_hex(chunk.payload),
                    )
                    get_store().add_event(
                        "cloud.rtmp.raw",
                        {
                            "peer": str(session.peername),
                            "deviceSn": str(session.state.get("deviceSn", "") or ""),
                            "msgTypeId": int(chunk.msg_type_id),
                            "msgStreamId": int(chunk.msg_stream_id),
                            "payloadLength": int(chunk.msg_length),
                            "payloadHeadHex": _payload_head_hex(chunk.payload),
                        },
                    )
                    await self.on_unknown_message(session, chunk)
                    continue

                if isinstance(message, NCConnect):
                    await self.on_nc_connect(session, message)
                elif isinstance(message, WindowAcknowledgementSize):
                    await self.on_window_acknowledgement_size(session, message)
                elif isinstance(message, NCCreateStream):
                    await self.on_nc_create_stream(session, message)
                elif isinstance(message, NSPublish):
                    await self.on_ns_publish(session, message)
                elif isinstance(message, MetaDataMessage):
                    await self.on_metadata(session, message)
                elif isinstance(message, DataMessage):
                    await self.on_data_message(session, message)
                elif isinstance(message, SetChunkSize):
                    await self.on_set_chunk_size(session, message)
                elif isinstance(message, VideoMessage):
                    await self.on_video_message(session, message)
                elif isinstance(message, AudioMessage):
                    await self.on_audio_message(session, message)
                elif isinstance(message, NSCloseStream):
                    await self.on_ns_close_stream(session, message)
                elif isinstance(message, NSDeleteStream):
                    await self.on_ns_delete_stream(session, message)
                else:
                    await self.on_unknown_message(session, message)

        except StreamClosedException as ex:
            logger.debug("Client disconnected %s", session.peername)
            await self.on_stream_closed(session, ex)
        except Exception as ex:
            logger.exception(ex)
        finally:
            await self.cleanup(session)

        writer.close()

    async def on_nc_connect(self, session: SessionManager, message: NCConnect) -> None:
        connect_args = _flatten_named_values(message.optional_user_arguments)
        command_object = _flatten_named_values(message.command_object)
        session.state["connectArgs"] = connect_args
        session.state["commandObject"] = command_object
        session.state["serverIp"] = _session_server_ip(session)
        session.state["deviceSn"] = str(
            connect_args.get("devicesn")
            or connect_args.get("deviceSn")
            or connect_args.get("sn")
            or command_object.get("devicesn")
            or command_object.get("deviceSn")
            or ""
        ).strip()
        session.state["token"] = str(
            connect_args.get("token")
            or command_object.get("token")
            or ""
        ).strip()
        session.state["app"] = str(
            connect_args.get("appname")
            or command_object.get("appname")
            or command_object.get("app")
            or ""
        ).strip()
        session.state["tcUrl"] = str(command_object.get("tcUrl") or "").strip()
        logger.info(
            "rtmp connect peer=%s device=%s app=%s token=%s",
            session.peername,
            session.state.get("deviceSn", ""),
            session.state.get("app", ""),
            session.state.get("token", "")[:12],
        )
        logger.info(
            "rtmp connect detail peer=%s command=%s optional=%s",
            session.peername,
            command_object,
            connect_args,
        )
        get_store().add_event(
            "cloud.rtmp.connect",
            {
                "peer": str(session.peername),
                "deviceSn": str(session.state.get("deviceSn", "") or ""),
                "app": str(session.state.get("app", "") or ""),
                "tokenPrefix": str(session.state.get("token", "") or "")[:12],
                "tcUrl": str(session.state.get("tcUrl", "") or ""),
                "commandObject": command_object,
                "optionalArgs": connect_args,
            },
        )
        ack_window = 2500000
        chunk_size = 60000
        _write_chunk_type0(session, WindowAcknowledgementSize(ack_window_size=ack_window))
        _write_chunk_type0(session, SetPeerBandwidth(ack_window_size=ack_window, limit_type=2))
        _write_chunk_type0(session, StreamBegin(stream_id=0))
        _write_chunk_type0(session, SetChunkSize(chunk_size=chunk_size))
        session.writer_chunk_size = chunk_size
        _write_chunk_type0(
            session,
            _build_connect_response(
                message,
                server_ip=str(session.state.get("serverIp", "") or ""),
            ),
        )
        _write_chunk_type0(session, _build_on_bw_done())
        await session.drain()

    async def on_window_acknowledgement_size(
        self,
        session: SessionManager,
        message: WindowAcknowledgementSize,
    ) -> None:
        await super().on_window_acknowledgement_size(session, message)

    async def on_nc_create_stream(self, session: SessionManager, message: NCCreateStream) -> None:
        _write_chunk_type0(session, message.create_response())
        await session.drain()

    async def on_ns_publish(self, session: SessionManager, message: NSPublish) -> None:
        device_sn = str(
            session.state.get("deviceSn")
            or message.publishing_name
            or ""
        ).strip()
        session.state["publishingName"] = str(message.publishing_name or "").strip()
        session.state["publishingType"] = str(message.publishing_type or "").strip()
        session.state["captureSeq"] = 0
        session.state.pop("startedAt", None)
        session.state.pop("startedAtMs", None)
        session.state.pop("flvPath", None)
        session.state.pop("flvWriter", None)
        session.state["metadata"] = {}
        session.state["videoFrames"] = 0
        session.state["audioFrames"] = 0
        session.state.pop("pendingMetadataPayload", None)
        session.state.pop("pendingMetadata", None)
        session.state.pop("lastMediaAtMono", None)
        session.state["_loggedMetadata"] = False
        logger.info(
            "rtmp publish peer=%s device=%s publish=%s type=%s",
            session.peername,
            device_sn,
            session.state["publishingName"],
            session.state["publishingType"],
        )
        get_store().add_event(
            "cloud.rtmp.publish",
            {
                "peer": str(session.peername),
                "deviceSn": device_sn,
                "publishingName": str(session.state.get("publishingName", "") or ""),
                "publishingType": str(session.state.get("publishingType", "") or ""),
            },
        )
        _write_chunk_type0(session, StreamBegin(stream_id=1))
        _write_chunk_type0(session, message.create_response())
        await session.drain()

    async def on_metadata(self, session: SessionManager, message: MetaDataMessage) -> None:
        writer: FLVFileWriter | None = session.state.get("flvWriter")
        if writer:
            writer.write(message.timestamp, message.to_raw_meta(), FLVMediaType.OBJECT)
            self._touch_media(session)
            session.state["metadata"] = message.meta
        else:
            self._store_pending_metadata(session, message.to_raw_meta(), message.meta)
        if not session.state.get("_loggedMetadata"):
            session.state["_loggedMetadata"] = True
            logger.info(
                "rtmp metadata peer=%s device=%s event=%s keys=%s",
                session.peername,
                str(session.state.get("deviceSn", "") or ""),
                str(getattr(message, "event", "") or ""),
                sorted(str(key) for key in getattr(message, "meta", {}).keys()) if isinstance(getattr(message, "meta", {}), dict) else [],
            )

    async def on_data_message(self, session: SessionManager, message: DataMessage) -> None:
        decoded = _decode_metadata_message(message)
        if decoded is None:
            await self.on_unknown_message(session, message)
            return
        event_name, meta = decoded
        writer: FLVFileWriter | None = session.state.get("flvWriter")
        if writer:
            writer.write(message.timestamp, message.payload, FLVMediaType.OBJECT)
            self._touch_media(session)
            session.state["metadata"] = meta
        else:
            self._store_pending_metadata(session, message.payload, meta)
        if not session.state.get("_loggedMetadata"):
            session.state["_loggedMetadata"] = True
            logger.info(
                "rtmp metadata peer=%s device=%s event=%s keys=%s",
                session.peername,
                str(session.state.get("deviceSn", "") or ""),
                event_name,
                sorted(str(key) for key in meta.keys()),
            )

    async def on_set_chunk_size(self, session: SessionManager, message: SetChunkSize) -> None:
        logger.info(
            "rtmp set-chunk peer=%s device=%s size=%s",
            session.peername,
            str(session.state.get("deviceSn", "") or ""),
            int(message.chunk_size),
        )
        await super().on_set_chunk_size(session, message)

    async def on_video_message(self, session: SessionManager, message: VideoMessage) -> None:
        self._ensure_capture(session)
        writer: FLVFileWriter | None = session.state.get("flvWriter")
        if writer:
            writer.write(message.timestamp, message.payload, FLVMediaType.VIDEO)
            session.state["videoFrames"] = int(session.state.get("videoFrames", 0) or 0) + 1
        if int(session.state.get("videoFrames", 0) or 0) == 1:
            logger.info(
                "rtmp first-video peer=%s device=%s len=%s head=%s",
                session.peername,
                str(session.state.get("deviceSn", "") or ""),
                len(message.payload),
                _payload_head_hex(message.payload),
            )

    async def on_audio_message(self, session: SessionManager, message: AudioMessage) -> None:
        self._ensure_capture(session)
        writer: FLVFileWriter | None = session.state.get("flvWriter")
        if writer:
            writer.write(message.timestamp, message.payload, FLVMediaType.AUDIO)
            session.state["audioFrames"] = int(session.state.get("audioFrames", 0) or 0) + 1
        if int(session.state.get("audioFrames", 0) or 0) == 1:
            logger.info(
                "rtmp first-audio peer=%s device=%s len=%s head=%s",
                session.peername,
                str(session.state.get("deviceSn", "") or ""),
                len(message.payload),
                _payload_head_hex(message.payload),
            )

    async def on_ns_close_stream(self, session: SessionManager, message: NSCloseStream) -> None:
        await super().on_ns_close_stream(session, message)

    async def on_ns_delete_stream(self, session: SessionManager, message: NSDeleteStream) -> None:
        await super().on_ns_delete_stream(session, message)

    async def on_unknown_message(self, session: SessionManager, message: Chunk) -> None:
        if isinstance(message, UserControlMessage):
            event_type = int(getattr(message, "event_type", -1) or -1)
            payload = bytes(getattr(message, "payload", b"") or b"")
            logger.info(
                "rtmp user-control peer=%s event=%s payload=%s",
                session.peername,
                event_type,
                payload.hex(),
            )
            if event_type == 6 and len(payload) >= 6:
                timestamp = int.from_bytes(payload[2:6], "big", signed=False)
                _write_chunk_type0(session, PingResponse(timestamp=timestamp))
                await session.drain()
                await self._maybe_finalize_idle_capture(session)
                get_store().add_event(
                    "cloud.rtmp.ping",
                    {
                        "peer": str(session.peername),
                        "deviceSn": str(session.state.get("deviceSn", "") or ""),
                        "eventType": event_type,
                        "timestamp": timestamp,
                    },
                )
                return
        if isinstance(message, CommandMessage):
            values = _decode_amf0_values(message.payload)
            command_name = str(values[0]) if values else str(message.command_name)
            transaction_id = values[1] if len(values) > 1 else 0
            arguments = values[3:] if len(values) > 3 else []
            logger.info(
                "rtmp command peer=%s command=%s tx=%s args=%s",
                session.peername,
                command_name,
                transaction_id,
                arguments,
            )
            if command_name in {"releaseStream", "FCPublish"}:
                if command_name == "releaseStream":
                    _write_chunk_type0(session, _build_release_stream_response(transaction_id))
                    await session.drain()
                elif command_name == "FCPublish":
                    stream_name = str(arguments[0] if arguments else session.state.get("publishingName", "") or "")
                    _write_chunk_type0(session, _build_on_fc_publish(stream_name))
                    await session.drain()
                get_store().add_event(
                    "cloud.rtmp.fmle",
                    {
                        "peer": str(session.peername),
                        "deviceSn": str(session.state.get("deviceSn", "") or ""),
                        "command": command_name,
                        "transactionId": transaction_id,
                        "args": arguments,
                    },
                )
                return
        logger.info(
            "rtmp unknown peer=%s type=%s msgType=%s stream=%s len=%s head=%s",
            session.peername,
            message.__class__.__name__,
            int(getattr(message, "msg_type_id", -1) or -1),
            int(getattr(message, "msg_stream_id", 0) or 0),
            int(getattr(message, "msg_length", 0) or 0),
            _payload_head_hex(bytes(getattr(message, "payload", b"") or b"")),
        )

    async def on_stream_closed(self, session: SessionManager, exception: Exception) -> None:
        if exception is not None:
            logger.warning("rtmp stream closed peer=%s error=%r", session.peername, exception)
        await self._finalize(session)

    async def cleanup(self, session: SessionManager) -> None:
        await self._finalize(session)

    async def _finalize_capture(self, session: SessionManager, *, reason: str) -> None:
        writer: FLVFileWriter | None = session.state.pop("flvWriter", None)
        if not writer:
            return
        writer.close()

        flv_path = Path(str(session.state.get("flvPath", "") or ""))
        if not flv_path.is_file() or flv_path.stat().st_size <= 13:
            get_store().add_event(
                "cloud.rtmp.finalize_skipped",
                {
                    "peer": str(session.peername),
                    "deviceSn": str(session.state.get("deviceSn", "") or ""),
                    "publishingName": str(session.state.get("publishingName", "") or ""),
                    "flvPath": str(flv_path),
                    "flvExists": flv_path.is_file(),
                    "flvSize": flv_path.stat().st_size if flv_path.is_file() else 0,
                    "reason": reason,
                },
            )
            session.state.pop("startedAt", None)
            session.state.pop("startedAtMs", None)
            session.state.pop("flvPath", None)
            session.state.pop("lastMediaAtMono", None)
            return

        ended_at_ms = int(time.time() * 1000)
        started_at_ms = int(session.state.get("startedAtMs", 0) or 0)
        mp4_path, thumb_path = _postprocess_capture(flv_path)
        record = get_store().register_cloud_media_capture(
            device_sn=str(session.state.get("deviceSn", "") or session.state.get("publishingName", "") or ""),
            started_at_ms=started_at_ms,
            started_at=str(session.state.get("startedAt", "") or ""),
            ended_at=iso_now(),
            duration=max(0.0, (ended_at_ms - started_at_ms) / 1000.0) if started_at_ms else 0,
            token=str(session.state.get("token", "") or ""),
            app=str(session.state.get("app", "") or ""),
            tc_url=str(session.state.get("tcUrl", "") or ""),
            publishing_name=str(session.state.get("publishingName", "") or ""),
            publishing_type=str(session.state.get("publishingType", "") or ""),
            flv_path=str(flv_path),
            mp4_path=str(mp4_path) if mp4_path else "",
            thumbnail_path=str(thumb_path) if thumb_path else "",
            metadata=session.state.get("metadata", {}),
        )
        logger.info(
            "rtmp finalized reason=%s stream=%s mp4=%s thumb=%s frames(video=%s,audio=%s)",
            reason,
            record.get("streamCode", ""),
            record.get("mp4Path", ""),
            record.get("thumbnailPath", ""),
            session.state.get("videoFrames", 0),
            session.state.get("audioFrames", 0),
        )
        get_store().add_event(
            "cloud.rtmp.finalized",
            {
                "peer": str(session.peername),
                "deviceSn": str(record.get("deviceSn", "") or ""),
                "streamCode": str(record.get("streamCode", "") or ""),
                "mp4Path": str(record.get("mp4Path", "") or ""),
                "thumbnailPath": str(record.get("thumbnailPath", "") or ""),
                "videoFrames": int(session.state.get("videoFrames", 0) or 0),
                "audioFrames": int(session.state.get("audioFrames", 0) or 0),
                "reason": reason,
            },
        )
        session.state.pop("startedAt", None)
        session.state.pop("startedAtMs", None)
        session.state.pop("flvPath", None)
        session.state.pop("lastMediaAtMono", None)

    async def _finalize(self, session: SessionManager) -> None:
        if session.state.get("_finalized"):
            return
        session.state["_finalized"] = True
        await self._finalize_capture(session, reason="stream_closed")


async def main() -> None:
    parser = argparse.ArgumentParser(description="VAVA RTMP ingest server")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=1935)
    parser.add_argument(
        "--output-dir",
        default=str(get_settings().data_dir / "cloud_media" / "incoming"),
    )
    args = parser.parse_args()

    output_dir = Path(args.output_dir).resolve()
    logger.info(
        "RTMP ingest python executable=%s sys.prefix=%s sys.base_prefix=%s",
        sys.executable,
        sys.prefix,
        sys.base_prefix,
    )
    logger.info("RTMP ingest raw output dir=%s", output_dir)
    controller = VAVARTMPIngestController(output_dir=output_dir)
    loop = asyncio.get_event_loop()
    server = await loop.create_server(
        lambda: RTMPProtocol(controller=controller),
        host=args.host,
        port=args.port,
    )
    sockets = ", ".join(str(sock.getsockname()) for sock in server.sockets or [])
    logger.info("RTMP ingest listening on %s", sockets)
    try:
        await server.serve_forever()
    finally:
        server.close()
        await server.wait_closed()


if __name__ == "__main__":
    asyncio.run(main())
