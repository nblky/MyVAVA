from __future__ import annotations

from typing import Any

from .media_urls import play_url, thumb_url


VISIBLE_NOTICE_TYPES = (2, 4, 5, 6, 7, 8)


def message_key(device_sn: str, file_date: str, file_name: str) -> tuple[str, str, str]:
    return (str(device_sn or "").strip(), str(file_date or "").strip(), str(file_name or "").strip())


def message_payload(message: dict[str, Any]) -> dict[str, Any]:
    ext = message.get("extObject", {}) if isinstance(message.get("extObject"), dict) else {}
    device_sn = str(message.get("deviceSn", "") or "").strip()
    file_date = str(ext.get("fileDate", "") or "").strip()
    file_name = str(ext.get("fileName", "") or "").strip()
    station_sn = str(message.get("parentDeviceSn", "") or "").strip()
    stream_code = (
        f"{device_sn}|{file_date}|{file_name}"
        if device_sn and file_date and file_name
        else ""
    )
    thumb = ""
    play = ""
    if stream_code:
        thumb = thumb_url(
            station_sn=station_sn,
            camera_sn=device_sn,
            file_date=file_date,
            file_name=file_name,
            stream_code=stream_code,
        )
        play = play_url(
            station_sn=station_sn,
            camera_sn=device_sn,
            file_date=file_date,
            file_name=file_name,
            stream_code=stream_code,
        )
    return {
        "noticeId": int(message.get("noticeId", 0) or 0),
        "title": str(message.get("title", "") or "Notice"),
        "subtitle": str(ext.get("msg", "") or file_name or ""),
        "deviceName": str(message.get("deviceName", "") or device_sn),
        "deviceSn": device_sn,
        "deviceTime": str(message.get("deviceTime", "") or ""),
        "thumbUrl": thumb,
        "playUrl": play,
        "visibleNoticeType": int(message.get("visibleNoticeType", 0) or 0),
    }


def clip_payload(item: dict[str, Any], title: str) -> dict[str, Any]:
    station_sn = str(item.get("stationSN", "") or item.get("stationDeviceCode", "") or "")
    camera_sn = str(item.get("deviceSN", "") or item.get("cameraDeviceCode", "") or "")
    file_date = str(item.get("date", "") or "")
    file_name = str(item.get("file", "") or "")
    stream_code = str(item.get("streamCode", "") or "")
    duration = float(item.get("duration", 0) or 0)
    return {
        "stationSn": station_sn,
        "cameraSn": camera_sn,
        "fileDate": file_date,
        "streamCode": stream_code,
        "title": title or str(item.get("cameraName", "") or camera_sn or "Clip"),
        "startTime": str(item.get("mediaRecordStartTime", "") or ""),
        "fileName": file_name,
        "duration": duration,
        "durationText": f"{duration:.1f}s" if duration else "-",
        "thumbUrl": thumb_url(
            station_sn=station_sn,
            camera_sn=camera_sn,
            file_date=file_date,
            file_name=file_name,
            stream_code=stream_code,
        ),
        "playUrl": play_url(
            station_sn=station_sn,
            camera_sn=camera_sn,
            file_date=file_date,
            file_name=file_name,
            stream_code=stream_code,
        ),
    }
