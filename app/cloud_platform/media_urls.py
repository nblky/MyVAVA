from __future__ import annotations

from urllib.parse import urlencode

from .live_backend import live_url_for_camera
from ..store import DEFAULT_CAMERA_SN, DEFAULT_STATION_SN

def parse_stream_code(value: object) -> tuple[str, str, str]:
    raw = str(value or "").strip()
    if not raw:
        return ("", "", "")
    parts = raw.split("|", 2)
    if len(parts) == 3:
        return tuple(parts)
    return ("", "", raw)


def cloud_video_url(
    *,
    station_sn: str = "",
    camera_sn: str = "",
    file_date: str = "",
    file_name: str = "",
    stream_code: str = "",
) -> str:
    stream_device_sn, stream_file_date, stream_file_name = parse_stream_code(stream_code)
    query = urlencode(
        {
            "stationSn": str(station_sn or DEFAULT_STATION_SN),
            "cameraSn": str(camera_sn or stream_device_sn or DEFAULT_CAMERA_SN),
            "fileDate": str(file_date or stream_file_date or ""),
            "fileName": str(file_name or stream_file_name or ""),
            "streamCode": str(stream_code or ""),
        }
    )
    return f"/monitor/media/cloud-play.mp4?{query}"


def thumb_url(
    *,
    station_sn: str,
    camera_sn: str,
    file_date: str,
    file_name: str,
    stream_code: str,
) -> str:
    return "/monitor/media/thumbnail.png?" + urlencode(
        {
            "stationSn": station_sn,
            "cameraSn": camera_sn,
            "fileDate": file_date,
            "fileName": file_name,
            "streamCode": stream_code,
        }
    )


def play_url(
    *,
    station_sn: str,
    camera_sn: str,
    file_date: str,
    file_name: str,
    stream_code: str,
) -> str:
    return "/monitor/media/cloud-play.mp4?" + urlencode(
        {
            "stationSn": station_sn,
            "cameraSn": camera_sn,
            "fileDate": file_date,
            "fileName": file_name,
            "streamCode": stream_code,
        }
    )


def live_url(camera_sn: str) -> str:
    return live_url_for_camera(camera_sn)
