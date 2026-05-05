from __future__ import annotations

from typing import Any

from .live_state import as_bool


def build_station_payload(station_list: list[dict[str, Any]], cameras: list[dict[str, Any]]) -> dict[str, Any]:
    station = station_list[0] if station_list and isinstance(station_list[0], dict) else {}
    station_status = station.get("statusObject", {}) if isinstance(station.get("statusObject"), dict) else {}
    station_attr = station.get("attrObject", {}) if isinstance(station.get("attrObject"), dict) else {}
    station_online = as_bool(station.get("online", station.get("stationOnline", 1)))
    station_buzzer_on = as_bool(station_status.get("buzzer", 0))
    total_size = int(station_status.get("totolsize", 0) or 0)
    used_size = int(station_status.get("usedsize", 0) or 0)
    free_size = int(station_status.get("freesize", max(total_size - used_size, 0)) or 0)
    nas_total = int(station_status.get("nas_totolsize", 0) or 0)
    nas_used = int(station_status.get("nas_usedsize", 0) or 0)
    nas_free = int(station_status.get("nas_freesize", max(nas_total - nas_used, 0)) or 0)
    nas_status = int(station_status.get("nasstatus", 0) or 0)
    sd_status = int(station_status.get("sdstatus", 0) or 0)
    session_active = as_bool(station_status.get("session", 0))
    storage_text = f"{used_size}/{total_size} GB" if total_size > 0 else "unknown"
    storage_free_text = f"{free_size} GB free" if total_size > 0 else "unknown"
    if nas_status and nas_total > 0:
        nas_text = f"mounted · {nas_used}/{nas_total} GB"
    elif as_bool(station_attr.get("nas_ctrl", 0)):
        nas_text = "configured"
    else:
        nas_text = "disabled"
    if nas_total > 0:
        nas_text = f"{nas_text} · {nas_free} GB free"
    if sd_status == 1:
        tf_card_text = "mounted"
    elif sd_status == 0:
        tf_card_text = "missing"
    else:
        tf_card_text = f"status {sd_status}"
    timezone_text = " · ".join(
        part
        for part in [
            str(station_attr.get("timezoom", "") or "").strip(),
            str(station_attr.get("region", "") or "").strip(),
        ]
        if part
    ) or "unknown"
    firmware_text = " · ".join(
        part
        for part in [
            str(station_attr.get("hardver", "") or "").strip(),
            str(station_attr.get("softver", "") or "").strip(),
        ]
        if part
    ) or "unknown"
    station_sn = str(station.get("stationSn", "") or station.get("deviceSn", "") or "")
    return {
        "stationName": str(station.get("stationName", "") or station.get("deviceName", "") or ""),
        "stationSn": station_sn,
        "online": station_online,
        "onlineText": "station online" if station_online else "station offline",
        "buzzerOn": station_buzzer_on,
        "buzzerText": "ringing" if station_buzzer_on else "idle",
        "sessionActive": session_active,
        "sessionText": "active" if session_active else "idle",
        "cameraCount": len([item for item in cameras if str(item.get("stationSn", "") or "") == station_sn]) or len(cameras),
        "tfCardText": tf_card_text,
        "storageText": storage_text,
        "storageFreeText": storage_free_text,
        "nasText": nas_text,
        "firmwareText": firmware_text,
        "appBuildText": str(station_attr.get("f_appversionout", "") or station_attr.get("f_appversionin", "") or "").strip() or "unknown",
        "timezoneText": timezone_text,
        "ipText": str(station_attr.get("ip", "") or "").strip() or "unknown",
        "macText": str(station_attr.get("mac", "") or "").strip() or "unknown",
        "ntpText": str(station_attr.get("ntp_describe", "") or station_attr.get("ntp", "") or "").strip() or "unknown",
        "alarmText": "armed" if as_bool(station_attr.get("alarmmodeenable", 0)) else "disarmed",
        "lastSeenText": str(station_status.get("time", "") or station.get("addTime", "") or ""),
        "storage": {
            "totalGb": total_size,
            "usedGb": used_size,
            "freeGb": free_size,
            "sdStatus": sd_status,
            "tfCardText": tf_card_text,
        },
        "nas": {
            "status": nas_status,
            "totalGb": nas_total,
            "usedGb": nas_used,
            "freeGb": nas_free,
            "text": nas_text,
        },
        "network": {
            "ip": str(station_attr.get("ip", "") or "").strip() or "unknown",
            "mac": str(station_attr.get("mac", "") or "").strip() or "unknown",
            "ntp": str(station_attr.get("ntp_describe", "") or station_attr.get("ntp", "") or "").strip() or "unknown",
            "timezone": timezone_text,
        },
        "firmware": {
            "text": firmware_text,
            "hardver": str(station_attr.get("hardver", "") or "").strip(),
            "softver": str(station_attr.get("softver", "") or "").strip(),
            "appBuild": str(station_attr.get("f_appversionout", "") or station_attr.get("f_appversionin", "") or "").strip() or "unknown",
        },
    }


def camera_runtime_counts(cameras: list[dict[str, Any]]) -> dict[str, int]:
    online_camera_count = sum(1 for item in cameras if as_bool(item.get("online", False)))
    live_ready_count = sum(
        1
        for item in cameras
        if as_bool((item.get("liveState", {}) if isinstance(item.get("liveState"), dict) else {}).get("ready", False))
    )
    live_active_count = sum(
        1
        for item in cameras
        if as_bool((item.get("liveState", {}) if isinstance(item.get("liveState"), dict) else {}).get("active", False))
    )
    live_prewarm_count = sum(
        1
        for item in cameras
        if as_bool((item.get("liveState", {}) if isinstance(item.get("liveState"), dict) else {}).get("keepAlive", False))
    )
    return {
        "cameraCount": len(cameras),
        "onlineCameraCount": online_camera_count,
        "liveReadyCount": live_ready_count,
        "liveActiveCount": live_active_count,
        "livePrewarmCount": live_prewarm_count,
        "liveStartingCount": max(live_active_count - live_ready_count, 0),
    }
