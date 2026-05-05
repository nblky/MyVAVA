from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Query
from fastapi.responses import FileResponse, Response

from ..store import get_store
from ..api.runtime_shared import CLOUD_COVER_PNG, CLOUD_PREVIEW_MP4_PATH, VISIBLE_THUMBNAIL_PNG


router = APIRouter(tags=["cloud-platform-media"])


@router.api_route("/monitor/media/thumbnail.png", methods=["GET", "HEAD"])
@router.api_route("/monitor/media/thumbnail", methods=["GET", "HEAD"])
def monitor_thumbnail(
    station_sn: str = Query(default="", alias="stationSn"),
    camera_sn: str = Query(default="", alias="cameraSn"),
    file_date: str = Query(default="", alias="fileDate"),
    file_name: str = Query(default="", alias="fileName"),
    stream_code: str = Query(default="", alias="streamCode"),
):
    headers = {
        "Cache-Control": "no-store, no-cache, max-age=0, must-revalidate",
        "X-VAVA-Station-Sn": station_sn,
        "X-VAVA-Camera-Sn": camera_sn,
        "X-VAVA-File-Date": file_date,
        "X-VAVA-File-Name": file_name,
    }
    asset = get_store().cloud_media_asset_paths(
        stream_code=stream_code,
        device_sn=camera_sn,
        file_date=file_date,
        file_name=file_name,
    )
    thumb_path = Path(str(asset.get("thumbnailPath", "") or ""))
    if thumb_path.is_file():
        return FileResponse(thumb_path, media_type="image/png", headers=headers)
    return Response(content=VISIBLE_THUMBNAIL_PNG, media_type="image/png", headers=headers)


@router.api_route("/monitor/media/cloud-cover", methods=["GET", "HEAD"])
def monitor_cloud_cover(plan_id: str = Query(default="", alias="planId")):
    headers = {
        "Cache-Control": "public, max-age=300",
        "X-VAVA-Plan-Id": plan_id,
    }
    return Response(content=CLOUD_COVER_PNG, media_type="image/png", headers=headers)


@router.api_route("/monitor/media/cloud-play.mp4", methods=["GET", "HEAD"])
def monitor_cloud_play(
    station_sn: str = Query(default="", alias="stationSn"),
    camera_sn: str = Query(default="", alias="cameraSn"),
    file_date: str = Query(default="", alias="fileDate"),
    file_name: str = Query(default="", alias="fileName"),
    stream_code: str = Query(default="", alias="streamCode"),
):
    headers = {
        "Cache-Control": "no-store",
        "X-VAVA-Station-Sn": station_sn,
        "X-VAVA-Camera-Sn": camera_sn,
        "X-VAVA-File-Date": file_date,
        "X-VAVA-File-Name": file_name,
        "X-VAVA-Stream-Code": stream_code,
    }
    asset = get_store().cloud_media_asset_paths(
        stream_code=stream_code,
        device_sn=camera_sn,
        file_date=file_date,
        file_name=file_name,
    )
    mp4_path = Path(str(asset.get("mp4Path", "") or ""))
    if mp4_path.is_file():
        return FileResponse(
            mp4_path,
            media_type="video/mp4",
            headers=headers,
        )
    return FileResponse(
        CLOUD_PREVIEW_MP4_PATH,
        media_type="video/mp4",
        headers=headers,
    )
