from __future__ import annotations

from typing import Any

from .live_state import (
    LIVE_QUALITY_OPTIONS,
    as_bool,
    audio_codec_label,
    live_hls_stats,
    live_state_payload,
    load_live_control,
    resolution_label,
    video_codec_label,
)
from .live_backend import live_backend_name, live_transport_name
from .message_payloads import clip_payload, message_key
from .media_urls import live_url


def build_camera_payloads(
    *,
    store: Any,
    camera_index: dict[str, Any],
    messages: list[dict[str, Any]],
    message_index: dict[tuple[str, str, str], dict[str, Any]],
    station_map: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    cameras: list[dict[str, Any]] = []
    for camera in camera_index.get("cameraList", []):
        if not isinstance(camera, dict):
            continue
        camera_sn = str(camera.get("cameraSn", "") or "").strip()
        station_sn = str(camera.get("stationSn", "") or "").strip()
        status = camera.get("statusObject", {}) if isinstance(camera.get("statusObject"), dict) else {}
        attr = camera.get("attrObject", {}) if isinstance(camera.get("attrObject"), dict) else {}
        station = station_map.get(station_sn, {}) if isinstance(station_map.get(station_sn, {}), dict) else {}
        station_status = station.get("statusObject", {}) if isinstance(station.get("statusObject"), dict) else {}

        recent_raw = store.storage_video_list_payload(device_sn=camera_sn, page=1, size=8)
        recent_clips: list[dict[str, Any]] = []
        for item in recent_raw:
            file_date = str(item.get("date", "") or "")
            file_name = str(item.get("file", "") or "")
            matched_message = message_index.get(message_key(camera_sn, file_date, file_name))
            title = str((matched_message or {}).get("title", "") or camera.get("cameraName", "") or "Clip")
            recent_clips.append(clip_payload(item, title))

        latest_message = next((item for item in messages if item.get("deviceSn") == camera_sn), None)
        live_control = load_live_control(camera_sn)
        live_stats = live_hls_stats(camera_sn)
        live_state = live_state_payload(live_control, live_stats)
        live_available = bool(live_state["ready"])
        current_quality = str(live_control.get("quality", "auto") or "auto").strip().lower()
        quality_label_map = {item["value"]: item["label"] for item in LIVE_QUALITY_OPTIONS}
        detection_flags = {
            "armed": as_bool(attr.get("alarmmodeenable", 0)),
            "human": as_bool(attr.get("pd_enable", 0)),
            "face": as_bool(attr.get("fd_enable", 0)),
            "mic": as_bool(attr.get("micstatus", 0)),
        }
        detection_parts = [key for key, enabled in detection_flags.items() if enabled]
        video_res = resolution_label(attr.get("m_res", attr.get("s_res", -1)))
        video_codec = video_codec_label(attr.get("videocodec", -1))
        audio_codec = audio_codec_label(attr.get("audiocodec", -1))
        video_fps = int(attr.get("m_fps", attr.get("s_fps", 0)) or 0)
        audio_rate = int(attr.get("audiorate", 0) or 0)
        battery_percent = int(status.get("lever", 0) or 0)
        signal_dbm = int(status.get("signal", 0) or 0)
        stream_profile_text = " · ".join(
            part
            for part in [
                video_res if video_res != "Unknown" else "",
                f"{video_fps} fps" if video_fps > 0 else "",
                video_codec if video_codec != "Unknown" else "",
            ]
            if part
        ) or "Unknown"
        audio_profile_text = " · ".join(
            part
            for part in [
                audio_codec if audio_codec != "Unknown" else "",
                f"{audio_rate} Hz" if audio_rate > 0 else "",
            ]
            if part
        ) or "Unknown"
        if live_control.get("active") and live_control.get("audioEnabled") is False:
            audio_profile_text = "Muted for low-latency web live"
        station_buzzer_on = as_bool(station_status.get("buzzer", 0))
        cameras.append(
            {
                "cameraSn": camera_sn,
                "cameraName": str(camera.get("cameraName", "") or camera_sn),
                "stationSn": station_sn,
                "channel": int(camera.get("channel", 0) or 0),
                "online": as_bool(status.get("online", 0)),
                "batteryPercent": battery_percent,
                "batteryText": f"{battery_percent}%",
                "signalDbm": signal_dbm,
                "signalText": f"{signal_dbm} dBm",
                "lastClipText": recent_clips[0]["startTime"] if recent_clips else "No clip",
                "lastClipStartTime": str((recent_clips[0].get("startTime", "") if recent_clips else "") or ""),
                "detectionText": ", ".join(detection_parts) if detection_parts else "basic",
                "detectionFlags": detection_flags,
                "modeLabel": "Cloud event deck" if as_bool(camera.get("yunFlag", 0)) else "Local only",
                "liveAvailable": live_available,
                "liveFresh": live_available,
                "liveUrl": live_url(camera_sn),
                "liveTransport": live_transport_name(),
                "liveBackend": live_backend_name(),
                "liveLabel": str(live_state["label"]),
                "liveState": live_state,
                "playbackMode": "P2P live HLS" if live_available else "click-to-start P2P",
                "liveRateKbps": live_stats["bitrateKbps"],
                "liveRateText": live_stats["bitrateText"],
                "liveWindowText": f"{float(live_stats['windowSeconds'] or 0):.1f}s / {int(live_stats['segmentCount'] or 0)} seg",
                "streamProfileText": stream_profile_text,
                "audioProfileText": audio_profile_text,
                "stationBuzzerOn": station_buzzer_on,
                "stationBuzzerText": "ringing" if station_buzzer_on else "idle",
                "currentQuality": current_quality if current_quality in quality_label_map else "auto",
                "currentQualityLabel": quality_label_map.get(current_quality, "Auto"),
                "qualityOptions": LIVE_QUALITY_OPTIONS,
                "playbackSummary": {
                    "clipCount": len(recent_clips),
                    "latestClipStartTime": str((recent_clips[0].get("startTime", "") if recent_clips else "") or ""),
                },
                "latestClip": recent_clips[0] if recent_clips else None,
                "recentClips": recent_clips,
                "latestMessage": latest_message,
            }
        )

    cameras.sort(
        key=lambda item: (
            str(item.get("stationSn", "") or ""),
            int(item.get("channel", 0) or 0),
            str(item.get("cameraName", "") or ""),
        )
    )
    return cameras
