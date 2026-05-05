from __future__ import annotations

import uuid
from typing import Any
from urllib.parse import urlencode

from fastapi import APIRouter, Body, Request

from ..config import get_settings
from ..store import DEFAULT_CAMERA_SN, DEFAULT_STATION_SN, deep_get, get_store

from .deps import _identity, _ok, _payload, _bearer_token, _access_token, _user_id


router = APIRouter(tags=["android-compat"])
settings = get_settings()


def _scoped_token(token: str, *, identifier: str = "", user_id: str = "") -> str:
    if str(token or "").strip() or str(identifier or "").strip() or str(user_id or "").strip():
        return str(token or "").strip()
    # Guardrail: avoid returning or mutating cross-account data without scope.
    return "__scope_required__"


def _placeholder_purchase_plans() -> list[dict[str, Any]]:
    return get_store().storage_purchase_plan_payloads()


def _placeholder_payment_info(kind: str, payload: dict[str, Any]) -> dict[str, Any]:
    return get_store().create_storage_order(kind=kind, payload=payload)


def _is_ios_request(request: Request) -> bool:
    ua = request.headers.get("user-agent", "").lower()
    return "iphone" in ua or "ios" in ua


def _ios_cloud_plan_numeric_id(plan: dict[str, Any]) -> int:
    camera_count = max(int(plan.get("cameraCount", 1) or 1), 1)
    storage_period = int(plan.get("storagePeriod", 30) or 30)
    base = 10000 if storage_period == 30 else 10100
    return base + min(camera_count, 99)


def _ios_renew_plan_payload(plans: list[dict[str, Any]]) -> list[dict[str, Any]]:
    payload: list[dict[str, Any]] = []
    for plan in plans:
        numeric_id = _ios_cloud_plan_numeric_id(plan)
        price_text = str(plan.get("price", "0.00") or "0.00")
        try:
            unit_price = float(price_text)
        except ValueError:
            unit_price = 0.0
        storage_service_url = str(plan.get("storageServiceUrl", "") or "")
        payload.append(
            {
                "id": numeric_id,
                "serviceId": numeric_id,
                "planId": str(plan.get("planId", "") or plan.get("id", "") or ""),
                "appId": str(plan.get("appId", "") or ""),
                "storageServiceName": str(plan.get("storageServiceName", "") or ""),
                "storageServiceType": int(plan.get("storageServiceType", 1) or 1),
                "storagePeriod": int(plan.get("storagePeriod", 30) or 30),
                "effectiveMonths": str(plan.get("effectiveMonths", 1) or 1),
                "is_free": 0,
                "isFree": 0,
                "price": price_text,
                "priceDecimal": price_text,
                "autoRenew": int(plan.get("autoRenew", 0) or 0),
                "cameraCount": str(plan.get("cameraCount", 1) or 1),
                "unitFree": str(plan.get("unitFree", 0) or 0),
                "unitPrice": unit_price,
                "sku": str(plan.get("sku", "") or ""),
                "storageServiceUrl": storage_service_url,
                "coverImageSignedUrl": storage_service_url,
                "coverImageUrl": storage_service_url,
                "Desc": str(plan.get("description", "") or plan.get("Desc", "") or ""),
                "description": str(plan.get("description", "") or ""),
                "stockNum": str(plan.get("stockNum", 9999) or 9999),
                "isLimitedTime": int(plan.get("isLimitedTime", 0) or 0),
                "isNew": int(plan.get("isNew", 0) or 0),
                "isOff": int(plan.get("isOff", 0) or 0),
                "showState": str(plan.get("showState", "0") or "0"),
                "off": str(plan.get("off", "0") or "0"),
                "productionIdentifier": str(plan.get("productionIdentifier", "") or ""),
                "remark": str(plan.get("remark", "") or ""),
            }
        )
    return payload


def _message_days() -> list[str]:
    state = get_store().dump_state()
    days = {
        str(deep_get(message.get("extObject", {}), "fileDate", "") or "").strip()
        for message in state.get("messages", [])
    }
    return sorted([day for day in days if day], reverse=True)


def _cloud_video_url(payload: dict[str, Any]) -> str:
    station_sn = str(deep_get(payload, "stationSn", DEFAULT_STATION_SN) or DEFAULT_STATION_SN)
    camera_sn = str(
        deep_get(payload, "deviceSn")
        or deep_get(payload, "cameraSn")
        or DEFAULT_CAMERA_SN
    )
    return (
        f"{settings.public_base_url.rstrip('/')}/debug/cloud-play.mp4?"
        + urlencode(
            {
                "stationSn": station_sn,
                "cameraSn": camera_sn,
                "fileDate": str(deep_get(payload, "fileDate") or deep_get(payload, "date") or ""),
                "fileName": str(deep_get(payload, "fileName", "") or ""),
                "streamCode": str(deep_get(payload, "streamCode", "") or ""),
            }
        )
    )


def _placeholder_signed_url(payload: dict[str, Any], kind: str) -> str:
    station_sn = str(deep_get(payload, "stationSn", DEFAULT_STATION_SN) or DEFAULT_STATION_SN)
    camera_sn = str(
        deep_get(payload, "deviceSn")
        or deep_get(payload, "cameraSn")
        or DEFAULT_CAMERA_SN
    )
    stream_code = str(deep_get(payload, "streamCode", "") or "")
    if kind in {"download", "play"}:
        return _cloud_video_url(payload)
    file_date = str(
        deep_get(payload, "fileDate")
        or deep_get(payload, "date")
        or ""
    )
    file_name = str(
        deep_get(payload, "fileName")
        or deep_get(payload, "streamCode")
        or deep_get(payload, "id")
        or kind
    )
    base_url = settings.public_base_url.rstrip("/")
    return (
        f"{base_url}/debug/thumbnail?"
        + urlencode(
            {
                "stationSn": station_sn,
                "cameraSn": camera_sn,
                "fileDate": file_date,
                "fileName": file_name,
                "kind": kind,
            }
        )
    )


@router.post("/feedback/submit")
def feedback_submit(payload: dict[str, Any] | None = Body(default=None)):
    body = _payload(payload)
    get_store().add_event(
        "feedback.submit",
        {
            "contact": str(
                deep_get(body, "email")
                or deep_get(body, "contact")
                or deep_get(body, "mobile")
                or ""
            ),
            "hasImage": bool(deep_get(body, "imageUri", "") or deep_get(body, "image_url", "")),
        },
    )
    return _ok({})


@router.post("/file/upload/single")
async def file_upload_single(request: Request):
    content_type = request.headers.get("content-type", "")
    size = int(request.headers.get("content-length") or 0)
    if size <= 0:
        size = len(await request.body())
    image_uri = (
        f"{settings.public_base_url.rstrip('/')}/debug/thumbnail?"
        + urlencode({"source": "feedback", "uploadId": uuid.uuid4().hex[:12]})
    )
    get_store().add_event(
        "file.upload.single",
        {"contentType": content_type, "size": size, "imageUri": image_uri},
    )
    return _ok({"imageUri": image_uri})


@router.post("/ipc/device/camera/list-for-share")
def camera_list_for_share():
    return _ok({"stationList": []})


@router.post("/ipc/device/camera/list-speaker-volume")
def camera_list_speaker_volume():
    return _ok(
        [
            {
                "itemCode": "low",
                "itemDesc": "Low",
                "itemType": "speakerVolume",
                "itemValue": 30,
            },
            {
                "itemCode": "middle",
                "itemDesc": "Middle",
                "itemType": "speakerVolume",
                "itemValue": 70,
            },
            {
                "itemCode": "high",
                "itemDesc": "High",
                "itemType": "speakerVolume",
                "itemValue": 90,
            },
        ]
    )


@router.post("/ipc/device/share/add")
def device_share_add(payload: dict[str, Any] | None = Body(default=None)):
    body = _payload(payload)
    get_store().add_event(
        "device.share.add",
        {
            "deviceSn": str(deep_get(body, "deviceSn", "") or ""),
            "receiver": str(
                deep_get(body, "receiverEmail")
                or deep_get(body, "receiverName")
                or deep_get(body, "email")
                or ""
            ),
        },
    )
    return _ok({})


@router.post("/ipc/device/share/check-receiver-mail")
def device_share_check_receiver_mail(payload: dict[str, Any] | None = Body(default=None)):
    body = _payload(payload)
    get_store().add_event(
        "device.share.check_receiver_mail",
        {"receiver": str(deep_get(body, "email", "") or deep_get(body, "receiverEmail", "") or "")},
    )
    return _ok({})


@router.post("/ipc/device/share/edit")
def device_share_edit(payload: dict[str, Any] | None = Body(default=None)):
    body = _payload(payload)
    get_store().add_event(
        "device.share.edit",
        {"deviceSn": str(deep_get(body, "deviceSn", "") or ""), "bindId": str(deep_get(body, "bindId", "") or "")},
    )
    return _ok({})


@router.post("/ipc/device/share/list-invite")
def device_share_list_invite():
    return _ok({"inviteList": []})


@router.post("/ipc/device/share/remove-device")
def device_share_remove_device(payload: dict[str, Any] | None = Body(default=None)):
    body = _payload(payload)
    get_store().add_event(
        "device.share.remove_device",
        {"deviceSn": str(deep_get(body, "deviceSn", "") or ""), "bindId": str(deep_get(body, "bindId", "") or "")},
    )
    return _ok({})


@router.post("/ipc/device/share/remove-invite")
def device_share_remove_invite(payload: dict[str, Any] | None = Body(default=None)):
    body = _payload(payload)
    get_store().add_event(
        "device.share.remove_invite",
        {"receiverId": str(deep_get(body, "receiverId", "") or ""), "bindId": str(deep_get(body, "bindId", "") or "")},
    )
    return _ok({})


@router.post("/ipc/device/station/is_exist_by_sn")
def device_station_is_exist_by_sn(payload: dict[str, Any] | None = Body(default=None)):
    body = _payload(payload)
    station_sn = str(
        deep_get(body, "stationSn")
        or deep_get(body, "deviceSn")
        or deep_get(body, "sn")
        or ""
    )
    exists = 1 if station_sn and get_store().station_exists(station_sn) else 0
    return _ok({"exists": exists, "stationSn": station_sn})


@router.post("/ipc/storage/camera/bind")
def storage_camera_bind(
    request: Request,
    payload: dict[str, Any] | None = Body(default=None),
):
    body = _payload(payload)
    token = _access_token(request, body)
    identifier = _identity(request, body)
    user_id = _user_id(request, body)
    token = _scoped_token(token, identifier=identifier, user_id=user_id)
    requested = (
        deep_get(body, "deviceSnSet")
        or deep_get(body, "deviceSnList")
        or deep_get(body, "deviceSn")
        or []
    )
    if isinstance(requested, str):
        requested_items = [item.strip() for item in requested.split(",") if item.strip()]
    else:
        requested_items = [
            str(item).strip() for item in (requested or []) if str(item).strip()
        ]
    changed = get_store().set_cloud_storage_bound(
        requested_items,
        bound=True,
        access_token=token,
        identifier=identifier,
        user_id=user_id,
    )
    get_store().add_event(
        "storage.camera.bind",
        {
            "deviceSnSet": requested_items,
            "changed": changed,
            "serviceId": str(deep_get(body, "serviceId", "") or ""),
        },
    )
    return _ok({"changed": changed})


@router.post("/ipc/storage/camera/unbind")
def storage_camera_unbind(
    request: Request,
    payload: dict[str, Any] | None = Body(default=None),
):
    body = _payload(payload)
    token = _access_token(request, body)
    identifier = _identity(request, body)
    user_id = _user_id(request, body)
    token = _scoped_token(token, identifier=identifier, user_id=user_id)
    requested = (
        deep_get(body, "deviceSnSet")
        or deep_get(body, "deviceSnList")
        or deep_get(body, "deviceSn")
        or []
    )
    if isinstance(requested, str):
        requested_items = [item.strip() for item in requested.split(",") if item.strip()]
    else:
        requested_items = [
            str(item).strip() for item in (requested or []) if str(item).strip()
        ]
    changed = get_store().set_cloud_storage_bound(
        requested_items,
        bound=False,
        access_token=token,
        identifier=identifier,
        user_id=user_id,
    )
    get_store().add_event(
        "storage.camera.unbind",
        {
            "deviceSnSet": requested_items,
            "changed": changed,
            "serviceId": str(deep_get(body, "serviceId", "") or ""),
        },
    )
    return _ok({"changed": changed})


@router.post("/ipc/storage/service/activate")
def storage_service_activate(
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


@router.post("/ipc/storage/service/purchase")
def storage_service_purchase(payload: dict[str, Any] | None = Body(default=None)):
    body = _payload(payload)
    result = _placeholder_payment_info("purchase", body)
    get_store().add_event("storage.service.purchase", result)
    return _ok(result)


@router.post("/ipc/storage/service/purchase/list")
def storage_service_purchase_list(payload: dict[str, Any] | None = Body(default=None)):
    body = _payload(payload)
    return _ok(
        get_store().storage_purchase_plan_page_payload(
            current_page=int(deep_get(body, "currentPage", 1) or 1),
            page_size=int(deep_get(body, "pageSize", 20) or 20),
        )
    )


@router.post("/ipc/storage/service/renew")
def storage_service_renew(payload: dict[str, Any] | None = Body(default=None)):
    body = _payload(payload)
    result = _placeholder_payment_info("renew", body)
    get_store().add_event("storage.service.renew", result)
    return _ok(result)


@router.post("/ipc/storage/service/renew/list")
def storage_service_renew_list(
    request: Request,
    payload: dict[str, Any] | None = Body(default=None),
):
    body = _payload(payload)
    plans = get_store().storage_renew_plan_page_payload(
        current_page=int(deep_get(body, "currentPage", 1) or 1),
        page_size=int(deep_get(body, "pageSize", 20) or 20),
        service_id=str(deep_get(body, "serviceId", "") or ""),
    )
    if _is_ios_request(request):
        return _ok(_ios_renew_plan_payload(plans))
    return _ok(plans)


@router.post("/ipc/storage/video/dailyNum")
def storage_video_daily_num(
    request: Request,
    payload: dict[str, Any] | None = Body(default=None),
):
    body = _payload(payload)
    token = _access_token(request, body)
    identifier = _identity(request, body)
    user_id = _user_id(request, body)
    token = _scoped_token(token, identifier=identifier, user_id=user_id)
    return _ok(
        {
            "daysWithData": get_store().storage_video_days(
                device_sn=str(deep_get(body, "deviceSn", "") or ""),
                access_token=token,
                identifier=identifier,
                user_id=user_id,
            )
        }
    )


@router.post("/ipc/storage/video/delete")
def storage_video_delete(
    request: Request,
    payload: dict[str, Any] | None = Body(default=None),
):
    body = _payload(payload)
    token = _access_token(request, body)
    identifier = _identity(request, body)
    user_id = _user_id(request, body)
    token = _scoped_token(token, identifier=identifier, user_id=user_id)
    requested = (
        deep_get(body, "streamCodes")
        or deep_get(body, "idList")
        or deep_get(body, "ids")
        or deep_get(body, "videoIds")
        or []
    )
    if isinstance(requested, str):
        requested_items = [item.strip() for item in requested.split(",") if item.strip()]
    else:
        requested_items = [
            str(item).strip() for item in (requested or []) if str(item).strip()
        ]
    removed = get_store().remove_cloud_media(
        stream_codes=requested_items,
        device_sn=str(deep_get(body, "deviceSn", "") or ""),
        file_date=str(deep_get(body, "fileDate", "") or deep_get(body, "date", "") or ""),
        file_names=deep_get(body, "fileNames") or deep_get(body, "files") or [],
        access_token=token,
        identifier=identifier,
        user_id=user_id,
    )
    get_store().add_event(
        "storage.video.delete",
        {"requestedCount": len(requested_items), "removed": removed},
    )
    return _ok({"requestedCount": len(requested_items), "removed": removed})


@router.post("/ipc/storage/video/signed/download/url")
def storage_video_signed_download_url(payload: dict[str, Any] | None = Body(default=None)):
    body = _payload(payload)
    return _ok({"signedUrl": _cloud_video_url(body)})


@router.post("/mi/authentication/sendMailCodeForModify")
def mi_send_mail_code_for_modify(payload: dict[str, Any] | None = Body(default=None)):
    body = _payload(payload)
    target = str(
        deep_get(body, "email")
        or deep_get(body, "username")
        or deep_get(body, "account")
        or ""
    )
    get_store().issue_verification_code(target, "reset_password", "email")
    return _ok({})


@router.post("/mi/storage/pay/checkByPayId")
def mi_storage_pay_check_by_pay_id(payload: dict[str, Any] | None = Body(default=None)):
    body = _payload(payload)
    order_no = str(deep_get(body, "orderNo", "") or "")
    payment_id = str(
        deep_get(body, "paymentId")
        or deep_get(body, "payId")
        or ""
    )
    return _ok(
        get_store().storage_payment_status_payload(
            order_no=order_no,
            payment_id=payment_id,
        )
    )


@router.post("/mi/storage/pay/page")
def mi_storage_pay_page(payload: dict[str, Any] | None = Body(default=None)):
    body = _payload(payload)
    return _ok(
        get_store().storage_payment_page_payload(
            current_page=int(deep_get(body, "currentPage", 1) or 1),
            page_size=int(deep_get(body, "pageSize", 20) or 20),
        )
    )


@router.post("/mi/storage/pay/paypalOrder")
def mi_storage_pay_paypal_order(payload: dict[str, Any] | None = Body(default=None)):
    body = _payload(payload)
    result = get_store().finalize_storage_order(
        order_no=str(deep_get(body, "orderNo", "") or ""),
        payment_id=str(deep_get(body, "paymentId", "") or ""),
    )
    get_store().add_event("storage.service.paypal_order", result)
    return _ok(result)
