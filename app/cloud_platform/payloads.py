from __future__ import annotations

from typing import Any

from ..store import get_store, iso_now
from .camera_payloads import build_camera_payloads
from .message_payloads import VISIBLE_NOTICE_TYPES, clip_payload, message_key, message_payload
from .station_payloads import build_station_payload, camera_runtime_counts


def build_monitor_payload(
    *,
    message_date: str = "",
    message_device_sn: str = "",
    playback_date: str = "",
    playback_device_sn: str = "",
) -> dict[str, Any]:
    store = get_store()
    state_summary = store.state_summary()
    kv_state = state_summary.get("kvState", {}) if isinstance(state_summary, dict) else {}
    cloud_media_count = int(kv_state.get("cloudMedia", 0) or 0)
    message_count = int(kv_state.get("messages", 0) or 0)
    camera_index = store.camera_index_payload()
    raw_station_list = camera_index.get("stationList", [])
    raw_camera_list = camera_index.get("cameraList", [])
    visible_station_list = [
        item
        for item in raw_station_list
        if isinstance(item, dict)
        and (
            int(item.get("bindingCount", 0) or 0) > 0
            or str(item.get("ownerUserId", "") or "").strip()
        )
    ]
    if visible_station_list:
        visible_station_sns = {
            str(item.get("stationSn", "") or item.get("deviceSn", "") or "").strip()
            for item in visible_station_list
            if str(item.get("stationSn", "") or item.get("deviceSn", "") or "").strip()
        }
        camera_index = {
            **camera_index,
            "stationList": visible_station_list,
            "cameraList": [
                item
                for item in raw_camera_list
                if isinstance(item, dict)
                and str(item.get("stationSn", "") or "").strip() in visible_station_sns
            ],
        }
    all_raw_messages = store.message_list_payload(page_size=200, type_list=VISIBLE_NOTICE_TYPES).get("list", [])
    raw_messages = store.message_list_payload(
        date=message_date,
        device_sn=message_device_sn,
        page_size=60,
        type_list=VISIBLE_NOTICE_TYPES,
    ).get("list", [])
    messages = [
        message_payload(item)
        for item in raw_messages
        if isinstance(item, dict)
    ]
    message_filters = store.message_filter_payload(date=message_date)
    message_index = {
        message_key(
            item.get("deviceSn", ""),
            ((item.get("extObject", {}) if isinstance(item.get("extObject"), dict) else {}).get("fileDate", "")),
            ((item.get("extObject", {}) if isinstance(item.get("extObject"), dict) else {}).get("fileName", "")),
        ): item
        for item in all_raw_messages
        if isinstance(item, dict)
    }
    station_list = camera_index.get("stationList", [])
    station_map = {
        str(item.get("stationSn", "") or item.get("deviceSn", "") or "").strip(): item
        for item in station_list
        if isinstance(item, dict)
    }
    cameras = build_camera_payloads(
        store=store,
        camera_index=camera_index,
        messages=messages,
        message_index=message_index,
        station_map=station_map,
    )
    station_payload = build_station_payload(station_list, cameras)
    runtime_counts = camera_runtime_counts(cameras)
    camera_order = [str(item.get("cameraSn", "") or "") for item in cameras if str(item.get("cameraSn", "") or "")]
    playback_recent_raw = store.storage_video_list_payload(
        date=playback_date,
        device_sn=playback_device_sn,
        page=1,
        size=80,
    )
    playback_recent_clips = [
        clip_payload(item, str(item.get("cameraName", "") or item.get("deviceName", "") or item.get("deviceSN", "") or "Clip"))
        | {
            "cameraName": str(item.get("cameraName", "") or item.get("deviceName", "") or item.get("deviceSN", "") or ""),
            "channel": next(
                (
                    int(camera.get("channel", 0) or 0)
                    for camera in cameras
                    if str(camera.get("cameraSn", "") or "") == str(item.get("deviceSN", "") or "")
                ),
                0,
            ),
        }
        for item in playback_recent_raw
        if isinstance(item, dict)
    ]
    playback_date_set = store.storage_video_days(device_sn=playback_device_sn)
    playback_device_list = [
        {
            "deviceSn": str(item.get("cameraSn", "") or ""),
            "deviceName": str(item.get("cameraName", "") or item.get("cameraSn", "") or ""),
        }
        for item in cameras
        if str(item.get("cameraSn", "") or "")
    ]
    views_payload = {
        "home": {
            "metrics": {
                "cameraCount": len(cameras),
                "onlineCameraCount": runtime_counts["onlineCameraCount"],
                "liveReadyCount": runtime_counts["liveReadyCount"],
                "liveActiveCount": runtime_counts["liveActiveCount"],
                "livePrewarmCount": runtime_counts["livePrewarmCount"],
                "liveStartingCount": runtime_counts["liveStartingCount"],
                "cloudMediaCount": cloud_media_count,
                "messageCount": message_count,
            },
            "defaultCameraSn": camera_order[0] if camera_order else "",
            "cameraOrder": camera_order,
        },
        "station": {
            "stationSn": str(station_payload.get("stationSn", "") or ""),
            "stationName": str(station_payload.get("stationName", "") or ""),
            "online": bool(station_payload.get("online", False)),
            "sessionActive": bool(station_payload.get("sessionActive", False)),
            "storage": station_payload.get("storage", {}),
            "nas": station_payload.get("nas", {}),
            "network": station_payload.get("network", {}),
            "firmware": station_payload.get("firmware", {}),
        },
        "playback": {
            "clipCount": len(playback_recent_clips),
            "recentClips": playback_recent_clips,
            "cameraOrder": camera_order,
            "dateSet": playback_date_set,
            "deviceList": playback_device_list,
            "selectedDate": str(playback_date or "").strip(),
            "selectedDeviceSn": str(playback_device_sn or "").strip(),
        },
        "messages": {
            "dateSet": message_filters.get("dateSet", []) if isinstance(message_filters, dict) else [],
            "deviceList": message_filters.get("deviceList", []) if isinstance(message_filters, dict) else [],
            "selectedDate": str(message_date or "").strip(),
            "selectedDeviceSn": str(message_device_sn or "").strip(),
            "filteredCount": len(messages),
        },
    }
    summary_payload = {
        "cloudMediaCount": cloud_media_count,
        "messageCount": message_count,
        **runtime_counts,
    }
    return {
        "generatedAt": iso_now(),
        "summary": summary_payload,
        "station": station_payload,
        "cameras": cameras,
        "messages": messages[:60],
        "events": store.recent_events(limit=12),
        "views": views_payload,
        "playback": views_payload["playback"],
    }
