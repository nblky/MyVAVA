from __future__ import annotations

"""Shared runtime/media router used by the fake-cloud compatibility surface."""

import base64
import json
import socket
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from fastapi import APIRouter, Body, Query, Request
from fastapi.responses import FileResponse, Response

from ..config import get_settings
from ..store import DEFAULT_CAMERA_SN, DEFAULT_STATION_SN, deep_get, get_store, iso_now

from .deps import _identity, _payload, _bearer_token, _access_token, _user_id


router = APIRouter(tags=["runtime-shared"])
settings = get_settings()

THUMBNAIL_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO/qm7kAAAAASUVORK5CYII="
)
ASSETS_DIR = Path(__file__).resolve().parents[1] / "assets"


def _load_png_asset(name: str) -> bytes:
    asset_path = ASSETS_DIR / name
    try:
        return asset_path.read_bytes()
    except OSError:
        return THUMBNAIL_PNG


CLOUD_COVER_PNG = _load_png_asset("cloud_bg.png")
VISIBLE_THUMBNAIL_PNG = CLOUD_COVER_PNG or THUMBNAIL_PNG
CLOUD_PREVIEW_MP4_PATH = ASSETS_DIR / "cloud_preview.mp4"


def _ok(data=None):
    payload = data if data is not None else {}
    return {
        "stateCode": 200,
        "stateMsg": "OK",
        "code": 200,
        "msg": "OK",
        "data": payload,
    }


def _scoped_token(token: str, *, identifier: str = "", user_id: str = "") -> str:
    if str(token or "").strip() or str(identifier or "").strip() or str(user_id or "").strip():
        return str(token or "").strip()
    # Guardrail: never fan out all-account data when the client omits identity context.
    return "__scope_required__"


def _rtmp_host_for_request(request: Request, configured_host: str) -> str:
    client_host = request.client.host if request.client else ""
    if not client_host or client_host in {"127.0.0.1", "::1"}:
        return configured_host
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.connect((client_host, 9))
            local_ip = str(sock.getsockname()[0] or "").strip()
        finally:
            sock.close()
    except OSError:
        return configured_host
    return local_ip or configured_host


def _request_debug_payload(request: Request, body: dict[str, Any]) -> dict[str, Any]:
    return {
        "method": request.method,
        "path": request.url.path,
        "host": request.headers.get("host", ""),
        "query": dict(request.query_params),
        "body": body,
    }


def _is_ios_request(request: Request) -> bool:
    ua = request.headers.get("user-agent", "").lower()
    return "iphone" in ua or "ios" in ua


def _ios_cloud_thumb_cache_path(stream_code: str, file_date: str) -> str:
    safe_stream = str(stream_code or "").replace("|", "_")
    safe_date = str(file_date or "").strip() or "unknown"
    return f"cloud/thumbs/{safe_date}/{safe_stream}.png"


def _ios_cloud_video_cache_path(stream_code: str, file_date: str) -> str:
    safe_stream = str(stream_code or "").replace("|", "_")
    safe_date = str(file_date or "").strip() or "unknown"
    return f"cloud/videos/{safe_date}/{safe_stream}.mp4"


def _parse_stream_code(value: Any) -> tuple[str, str, str]:
    raw = str(value or "").strip()
    if not raw:
        return ("", "", "")
    parts = raw.split("|", 2)
    if len(parts) == 3:
        return tuple(parts)
    return ("", "", raw)


def _cloud_video_url(
    *,
    station_sn: str = "",
    camera_sn: str = "",
    file_date: str = "",
    file_name: str = "",
    stream_code: str = "",
) -> str:
    stream_device_sn, stream_file_date, stream_file_name = _parse_stream_code(stream_code)
    query = urlencode(
        {
            "stationSn": str(station_sn or DEFAULT_STATION_SN),
            "cameraSn": str(camera_sn or stream_device_sn or DEFAULT_CAMERA_SN),
            "fileDate": str(file_date or stream_file_date or ""),
            "fileName": str(file_name or stream_file_name or ""),
            "streamCode": str(stream_code or ""),
        }
    )
    return f"{settings.public_base_url.rstrip('/')}/debug/cloud-play.mp4?{query}"


def _ios_cloud_record_payload(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    payload: list[dict[str, Any]] = []
    for item in items:
        stream_code = str(item.get("streamCode", "") or item.get("id", "") or "")
        file_date = str(item.get("date", "") or "")
        file_name = str(item.get("file", "") or "")
        device_time = str(
            item.get("deviceTime", "")
            or item.get("mediaRecordStartTime", "")
            or item.get("createTime", "")
            or ""
        )
        thumb_url = str(
            item.get("coverImageSignedUrl", "")
            or item.get("coverImageUrl", "")
            or item.get("thumbUrl", "")
            or ""
        )
        thumb_cache_path = _ios_cloud_thumb_cache_path(stream_code, file_date)
        video_url = _cloud_video_url(
            station_sn=str(item.get("stationSN", "") or DEFAULT_STATION_SN),
            camera_sn=str(item.get("deviceSN", "") or ""),
            file_date=file_date,
            file_name=file_name,
            stream_code=stream_code,
        )
        video_cache_path = _ios_cloud_video_cache_path(stream_code, file_date)
        try:
            parsed_time = datetime.strptime(device_time, "%Y-%m-%d %H:%M:%S")
            timestamp = parsed_time.timestamp()
            time_value = int(parsed_time.strftime("%H%M%S"))
        except ValueError:
            timestamp = 0.0
            time_value = 0
        raw_duration = item.get("duration", 0)
        try:
            duration_value = float(raw_duration or 0)
        except (TypeError, ValueError):
            duration_value = 0.0
        duration_text = str(int(duration_value)) if duration_value.is_integer() else str(duration_value)
        payload.append(
            {
                "streamCode": stream_code,
                "mediaRecordStartTime": str(item.get("mediaRecordStartTime", "") or device_time),
                "fileExpireTime": str(item.get("fileExpireTime", "") or ""),
                "timeZone": str(item.get("timeZone", "8") or "8"),
                "coverImagePath": thumb_url,
                "coverImageSignedUrl": thumb_url,
                "coverImageUrl": thumb_url,
                "coverUrl": thumb_url,
                "imageUrl": thumb_url,
                "imgUrl": thumb_url,
                "snapshotUrl": thumb_url,
                "thumbUrl": thumb_url,
                "thumbnailPath": thumb_url,
                "thumbnailUrl": thumb_url,
                "duration": duration_text,
                "deviceSN": str(item.get("deviceSN", "") or ""),
                "deviceName": str(item.get("deviceName", "") or item.get("cameraName", "") or ""),
                "createTime": str(item.get("createTime", "") or device_time),
                "modifyTime": str(item.get("modifyTime", "") or device_time),
                "id": str(item.get("id", "") or stream_code),
                "mp4FileSize": int(item.get("mp4FileSize", 0) or 0),
                "stationSN": str(item.get("stationSN", "") or ""),
                "stationDeviceCode": str(item.get("stationDeviceCode", "") or item.get("stationSN", "") or ""),
                "cameraDeviceCode": str(item.get("cameraDeviceCode", "") or item.get("deviceSN", "") or ""),
                "cameraName": str(item.get("cameraName", "") or item.get("deviceName", "") or ""),
                "date": file_date,
                "file": file_name,
                "thumbPath": thumb_url,
                "thumbCachePath": thumb_cache_path,
                "thumbnailCachePath": thumb_cache_path,
                "videoPath": video_url,
                "videoCachePath": video_cache_path,
                "videoUrl": video_url,
                "playUrl": video_url,
                "type": 1,
                "channel": int(item.get("channel", 0) or 0),
                "time": time_value,
                "isFromShare": False,
                "isThumbDownloaded": False,
                "isDeleted": False,
                "timestamp": timestamp,
                "timezone": int(item.get("timeZone", 8) or 8),
                "deviceTime": device_time,
                "tiggerType": int(item.get("tiggerType", 0) or 0),
            }
        )
    return payload


@router.get("/healthz")
@router.get("/ping")
def healthz():
    store = get_store()
    return {
        "ok": True,
        "time": iso_now(),
        "service": "vava-fastapi",
        "summary": store.state_summary(),
    }


@router.get("/debug/routes")
def debug_routes(request: Request):
    routes = sorted(
        {
            route.path
            for route in request.app.routes
            if getattr(route, "path", "")
        }
    )
    return {
        "service": "vava-fastapi",
        "count": len(routes),
        "routes": routes,
    }


@router.get("/debug/state-summary")
def state_summary():
    return get_store().state_summary()


@router.get("/debug/events")
def debug_events(
    limit: int = Query(default=20, ge=1, le=200),
    prefix: str = Query(default=""),
):
    return _ok(
        {
            "items": get_store().recent_events(limit=limit, prefix=prefix),
            "limit": limit,
            "prefix": prefix,
        }
    )


@router.get("/debug/push-status")
def debug_push_status(limit: int = Query(default=20, ge=1, le=200)):
    data = get_store().push_status_payload(event_limit=limit)
    data["pushEnabled"] = bool(settings.push_enabled)
    data["notifyWebhookUrl"] = bool(settings.notify_webhook_url)
    data["ntfyTopicUrl"] = bool(settings.ntfy_topic_url)
    data["fcmConfigured"] = bool(settings.fcm_server_key)
    data["apnsConfigured"] = bool(
        settings.apns_cert_path
        or (
            settings.apns_auth_key_path
            and settings.apns_key_id
            and settings.apns_team_id
        )
    )
    return _ok(data)


@router.get("/debug/pairing/slots")
def debug_pairing_slots(
    station_sn: str = Query(default="", alias="stationSn"),
):
    return _ok(
        {
            "stationSn": station_sn,
            "items": get_store().pairing_slot_list(station_sn=station_sn),
        }
    )


@router.get("/debug/pairing/sessions")
def debug_pairing_sessions(
    station_sn: str = Query(default="", alias="stationSn"),
    camera_sn: str = Query(default="", alias="cameraSn"),
    status: str = Query(default=""),
):
    return _ok(
        {
            "stationSn": station_sn,
            "cameraSn": camera_sn,
            "status": status,
            "items": get_store().pairing_session_list(
                station_sn=station_sn,
                camera_sn=camera_sn,
                status=status,
            ),
        }
    )


@router.api_route("/debug/thumbnail.png", methods=["GET", "HEAD"])
@router.api_route("/debug/thumbnail", methods=["GET", "HEAD"])
def debug_thumbnail(
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


@router.api_route("/debug/cloud-cover", methods=["GET", "HEAD"])
def debug_cloud_cover(plan_id: str = Query(default="", alias="planId")):
    headers = {
        "Cache-Control": "public, max-age=300",
        "X-VAVA-Plan-Id": plan_id,
    }
    return Response(content=CLOUD_COVER_PNG, media_type="image/png", headers=headers)


@router.api_route("/debug/cloud-play.mp4", methods=["GET", "HEAD"])
def debug_cloud_play(
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


@router.post("/logs/item")
def logs_item(payload: dict[str, Any] | None = Body(default=None)):
    body = _payload(payload)
    p2p = body.get("iot-bi-p2p")
    if isinstance(p2p, dict):
        station_sn = str(
            p2p.get("device_sn")
            or p2p.get("stationSn")
            or p2p.get("sn")
            or ""
        ).strip()
        if station_sn:
            p2p_state_raw = str(p2p.get("p2p_state", "") or "").strip()
            try:
                p2p_state = int(p2p_state_raw)
            except (TypeError, ValueError):
                p2p_state = None
            # p2p_state uses positive values for active sessions (1 or a session handle),
            # 0/negative are transient errors and should not flip station offline.
            if p2p_state is not None and p2p_state > 0:
                get_store().update_station_session(station_sn, True, payload=p2p)
    return _ok({})


@router.post("/ipc/storage/camera/management/info")
def storage_camera_management_info(
    request: Request,
    payload: dict[str, Any] | None = Body(default=None),
):
    body = _payload(payload)
    token = _access_token(request, body)
    identifier = _identity(request, body)
    user_id = _user_id(request, body)
    token = _scoped_token(token, identifier=identifier, user_id=user_id)
    return _ok(
        get_store().storage_camera_management_payload(
            access_token=token,
            identifier=identifier,
            user_id=user_id,
        )
    )


@router.post("/ipc/storage/service/info")
def storage_service_info(
    request: Request,
    payload: dict[str, Any] | None = Body(default=None),
):
    body = _payload(payload)
    token = _access_token(request, body)
    identifier = _identity(request, body)
    user_id = _user_id(request, body)
    token = _scoped_token(token, identifier=identifier, user_id=user_id)
    return _ok(
        get_store().storage_service_info_payload(
            access_token=token,
            identifier=identifier,
            user_id=user_id,
        )
    )


@router.post("/ipc/storage/video/list")
def storage_video_list(request: Request, payload: dict[str, Any] | None = Body(default=None)):
    body = _payload(payload)
    token = _access_token(request, body)
    identifier = _identity(request, body)
    user_id = _user_id(request, body)
    token = _scoped_token(token, identifier=identifier, user_id=user_id)
    items = get_store().storage_video_list_payload(
        date=str(deep_get(body, "date", "") or ""),
        device_sn=str(deep_get(body, "deviceSn", "") or ""),
        page=int(deep_get(body, "page", 1) or 1),
        size=int(deep_get(body, "size", 20) or 20),
        media_record_start_time=str(deep_get(body, "mediaRecordStartTime", "") or ""),
        access_token=token,
        identifier=identifier,
        user_id=user_id,
    )
    if _is_ios_request(request):
        return _ok(_ios_cloud_record_payload(items))
    return _ok(items)


@router.post("/ipc/storage/camera/unbind/list")
def storage_camera_unbind_list(
    request: Request,
    payload: dict[str, Any] | None = Body(default=None),
):
    body = _payload(payload)
    token = _access_token(request, body)
    identifier = _identity(request, body)
    user_id = _user_id(request, body)
    token = _scoped_token(token, identifier=identifier, user_id=user_id)
    return _ok(
        get_store().storage_camera_unbind_list_payload(
            access_token=token,
            identifier=identifier,
            user_id=user_id,
        )
    )


@router.post("/ipc/storage/video/signed/play/url")
def storage_video_signed_play_url(payload: dict[str, Any] | None = Body(default=None)):
    body = _payload(payload)
    signed_url = _cloud_video_url(
        station_sn=str(body.get("stationSn", "") or DEFAULT_STATION_SN),
        camera_sn=str(body.get("cameraSn", "") or body.get("deviceSn", "") or DEFAULT_CAMERA_SN),
        file_date=str(body.get("fileDate", "") or body.get("date", "") or ""),
        file_name=str(body.get("fileName", "") or ""),
        stream_code=str(body.get("streamCode", "") or ""),
    )
    return _ok(
        {
            "signedUrl": signed_url,
            "url": signed_url,
            "videoUrl": signed_url,
            "playUrl": signed_url,
            "videoPath": signed_url,
        }
    )


@router.api_route("/ipc/connection/token/get", methods=["GET", "POST"])
@router.api_route("/connection/token/get", methods=["GET", "POST"])
@router.api_route("/token/get", methods=["GET", "POST"])
def connection_token_get(
    request: Request,
    payload: dict[str, Any] | None = Body(default=None),
):
    body = _payload(payload)
    device_sn = str(
        body.get("deviceSn")
        or body.get("deviceSN")
        or body.get("sn")
        or request.query_params.get("deviceSn", "")
        or request.query_params.get("deviceSN", "")
        or request.query_params.get("sn", "")
        or DEFAULT_STATION_SN
    ).strip()
    token = f"storage-{uuid.uuid4().hex}"
    configured_host = str(settings.cloud_storage_host).strip() or "storage.sunvalleycloud.com"
    host = _rtmp_host_for_request(request, configured_host)
    port = int(settings.cloud_storage_port or 1935)
    scheme = str(settings.cloud_storage_scheme).strip() or "rtmp"
    app_name = str(settings.cloud_storage_app).strip() or "live"
    stream_name = device_sn
    signed_url = (
        f"{scheme}://{host}:{port}/{app_name}/{stream_name}"
        f"?token={token}&devicesn={device_sn}"
    )
    response_payload = {
        "deviceSN": device_sn,
        "deviceSn": device_sn,
        "host": host,
        "port": port,
        "schema": scheme,
        "protocol": scheme,
        "rtmpAppName": app_name,
        "rtmpStreamName": stream_name,
        "signedUrl": signed_url,
        "streamType": int(settings.cloud_storage_stream_type or 0),
        "token": token,
        "url": signed_url,
    }
    get_store().add_event(
        "cloud.connection_token.request",
        {
            "remote": request.client.host if request.client else "",
            **_request_debug_payload(request, body),
            "deviceSn": device_sn,
            "host": host,
            "port": port,
            "rtmpAppName": app_name,
            "rtmpStreamName": stream_name,
        },
    )
    return {
        "stateCode": 200,
        "stateMsg": "OK",
        "data": response_payload,
        **response_payload,
    }


@router.get("/debug/state")
def debug_state():
    return {"stateCode": 200, "stateMsg": "OK", "data": get_store().dump_state()}
