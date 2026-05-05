from __future__ import annotations

"""Browser monitor surface for the local web cloud platform."""

from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse, HTMLResponse

from .monitor_bundle import ASSET_ROOT, load_monitor_bundle, render_monitor_shell
from .payloads import build_monitor_payload


router = APIRouter(tags=["cloud-platform-web"])

ASSET_MEDIA_TYPES = {
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".html": "text/html; charset=utf-8",
}


def _no_store_headers() -> dict[str, str]:
    return {"Cache-Control": "no-store, no-cache, max-age=0, must-revalidate"}


@router.get("/monitor", response_class=HTMLResponse)
def monitor_page():
    bundle = load_monitor_bundle()
    return HTMLResponse(
        render_monitor_shell(bundle),
        headers=_no_store_headers(),
    )


@router.get("/monitor/assets/{asset_name:path}")
def monitor_asset(asset_name: str):
    relative = Path(asset_name)
    if not asset_name or relative.is_absolute() or ".." in relative.parts:
        raise HTTPException(status_code=404, detail="asset not found")

    asset_path = (ASSET_ROOT / relative).resolve()
    try:
        asset_path.relative_to(ASSET_ROOT.resolve())
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="asset not found") from exc
    if not asset_path.is_file():
        raise HTTPException(status_code=404, detail="asset not found")

    media_type = ASSET_MEDIA_TYPES.get(asset_path.suffix.lower(), "application/octet-stream")
    return FileResponse(
        asset_path,
        media_type=media_type,
        headers=_no_store_headers(),
    )


@router.get("/monitor/data")
def monitor_data(
    message_date: str = Query(default="", alias="messageDate"),
    message_device_sn: str = Query(default="", alias="messageDeviceSn"),
    playback_date: str = Query(default="", alias="playbackDate"),
    playback_device_sn: str = Query(default="", alias="playbackDeviceSn"),
):
    return build_monitor_payload(
        message_date=message_date,
        message_device_sn=message_device_sn,
        playback_date=playback_date,
        playback_device_sn=playback_device_sn,
    )
