from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from ..config import resolve_live_hls_root
LIVE_QUALITY_OPTIONS = [
    {"value": "high", "label": "High"},
    {"value": "medium", "label": "Medium"},
    {"value": "low", "label": "Low"},
    {"value": "auto", "label": "Auto"},
]


def as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value or "").strip().lower()
    return text in {"1", "true", "yes", "on"}


def format_rate_text(kbps: float | None, *, empty: str = "Estimating...") -> str:
    if kbps is None or kbps <= 0:
        return empty
    if kbps >= 1000:
        return f"{kbps / 1000:.2f} Mbps"
    return f"{kbps:.0f} kbps"


def resolution_label(value: Any) -> str:
    mapping = {
        0: "1080p",
        1: "720p",
        2: "360p",
        4: "1080p",
        5: "720p",
        6: "360p",
    }
    try:
        return mapping.get(int(value), "Unknown")
    except (TypeError, ValueError):
        return "Unknown"


def video_codec_label(value: Any) -> str:
    mapping = {
        0: "H.264",
        1: "H.265",
    }
    try:
        return mapping.get(int(value), "Unknown")
    except (TypeError, ValueError):
        return "Unknown"


def audio_codec_label(value: Any) -> str:
    mapping = {
        1: "AAC",
        3: "AAC",
    }
    try:
        return mapping.get(int(value), "Unknown")
    except (TypeError, ValueError):
        return "Unknown"


def live_control_path(camera_sn: str) -> Path:
    return resolve_live_hls_root() / str(camera_sn or "").strip() / "control.json"


def load_live_control(camera_sn: str) -> dict[str, Any]:
    control_path = live_control_path(camera_sn)
    try:
        payload = json.loads(control_path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def live_hls_stats(camera_sn: str) -> dict[str, Any]:
    manifest_path = resolve_live_hls_root() / camera_sn / "hls" / "index.m3u8"
    if not manifest_path.is_file():
        return {
            "bitrateKbps": None,
            "bitrateText": "Clip fallback",
            "segmentCount": 0,
            "windowSeconds": 0.0,
            "lastSegment": "",
            "mediaSequence": 0,
            "isFresh": False,
            "ageSeconds": None,
        }

    try:
        manifest_text = manifest_path.read_text(encoding="utf-8", errors="ignore")
        manifest_age = max(0.0, time.time() - manifest_path.stat().st_mtime)
    except OSError:
        return {
            "bitrateKbps": None,
            "bitrateText": "Starting...",
            "segmentCount": 0,
            "windowSeconds": 0.0,
            "lastSegment": "",
            "mediaSequence": 0,
            "isFresh": False,
            "ageSeconds": None,
        }

    hls_dir = manifest_path.parent
    segments: list[dict[str, Any]] = []
    pending_duration: float | None = None
    media_sequence = 0
    for raw_line in manifest_text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("#EXT-X-MEDIA-SEQUENCE:"):
            try:
                media_sequence = int(line.split(":", 1)[1].strip())
            except (TypeError, ValueError):
                media_sequence = 0
            continue
        if line.startswith("#EXTINF:"):
            try:
                pending_duration = float(line.split(":", 1)[1].split(",", 1)[0].strip())
            except (TypeError, ValueError):
                pending_duration = None
            continue
        if line.startswith("#") or pending_duration is None:
            continue
        segment_name = Path(line).name
        segment_path = hls_dir / segment_name
        try:
            segment_bytes = segment_path.stat().st_size
        except OSError:
            segment_bytes = 0
        segments.append(
            {
                "name": segment_name,
                "duration": pending_duration,
                "bytes": segment_bytes,
            }
        )
        pending_duration = None

    recent_window = segments[-4:]
    window_seconds = round(sum(float(item.get("duration", 0) or 0) for item in recent_window), 2)
    recent_segments = [
        item
        for item in recent_window
        if float(item.get("duration", 0) or 0) > 0 and int(item.get("bytes", 0) or 0) > 0
    ]
    recent_seconds = sum(float(item.get("duration", 0) or 0) for item in recent_segments)
    recent_bytes = sum(int(item.get("bytes", 0) or 0) for item in recent_segments)
    bitrate_kbps = None
    if recent_seconds > 0 and recent_bytes > 0:
        bitrate_kbps = round((recent_bytes * 8.0) / recent_seconds / 1000.0, 1)
    return {
        "bitrateKbps": bitrate_kbps,
        "bitrateText": format_rate_text(bitrate_kbps, empty="Starting..."),
        "segmentCount": len(segments),
        "windowSeconds": window_seconds,
        "lastSegment": str((segments[-1].get("name", "") if segments else "") or ""),
        "mediaSequence": media_sequence,
        "isFresh": manifest_age <= 8.0 and len(segments) > 0,
        "ageSeconds": round(manifest_age, 2),
    }


def live_state_payload(live_control: dict[str, Any], live_stats: dict[str, Any]) -> dict[str, Any]:
    active = as_bool(live_control.get("active", 0))
    fresh = bool(live_stats.get("isFresh"))
    status = "ready" if fresh else ("starting" if active else "idle")
    age_seconds_raw = live_stats.get("ageSeconds")
    age_seconds = float(age_seconds_raw) if isinstance(age_seconds_raw, (int, float)) else None
    return {
        "status": status,
        "label": status,
        "ready": fresh,
        "fresh": fresh,
        "active": active,
        "manifestAgeSeconds": age_seconds,
        "segmentCount": int(live_stats.get("segmentCount", 0) or 0),
        "windowSeconds": float(live_stats.get("windowSeconds", 0.0) or 0.0),
        "mediaSequence": int(live_stats.get("mediaSequence", 0) or 0),
        "lastSegment": str(live_stats.get("lastSegment", "") or ""),
        "bitrateKbps": live_stats.get("bitrateKbps"),
        "bitrateText": str(live_stats.get("bitrateText", "") or "Starting..."),
        "quality": str(live_control.get("quality", "auto") or "auto").strip().lower() or "auto",
        "audioEnabled": as_bool(live_control.get("audioEnabled", 1)),
        "keepAlive": as_bool(live_control.get("keepAlive", 0)),
        "startReason": str(live_control.get("startReason", "") or "").strip(),
    }
