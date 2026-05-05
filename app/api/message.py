from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Header, Request

from ..push import classify_push_token, get_push_dispatcher
from ..store import deep_get, get_store

from .deps import _identity, _ok, _payload, _bearer_token, _access_token


router = APIRouter(tags=["message"])
MESSAGE_TYPE_LIST = [2, 4, 5, 7]


def _coerce_str_list(value: Any) -> list[str]:
    if value in (None, ""):
        return []
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    if isinstance(value, (list, tuple, set)):
        out: list[str] = []
        for item in value:
            text = str(item or "").strip()
            if text:
                out.append(text)
        return out
    text = str(value).strip()
    return [text] if text else []



def _notice_remove_payload(
    *,
    removed: int,
    notice_ids: Any,
    device_sn: str,
    file_date: str,
    file_names: Any,
    access_token: str = "",
    identifier: str = "",
    user_id: str = "",
) -> dict[str, Any]:
    store = get_store()
    requested_notice_ids = _coerce_str_list(notice_ids)
    requested_file_names = _coerce_str_list(file_names)
    message_total = int(
        store.message_list_payload(
            page_size=1,
            type_list=MESSAGE_TYPE_LIST,
            access_token=access_token,
            identifier=identifier,
            user_id=user_id,
        ).get("totalCount", 0)
        or 0
    )
    playback_total = int(
        store.message_list_payload(
            page_size=1,
            type_list=[1],
            access_token=access_token,
            identifier=identifier,
            user_id=user_id,
        ).get("totalCount", 0)
        or 0
    )
    counts = store.message_count_payload(
        MESSAGE_TYPE_LIST,
        access_token=access_token,
        identifier=identifier,
        user_id=user_id,
    )
    is_playback_delete = bool(str(device_sn or "").strip() or str(file_date or "").strip() or requested_file_names)
    visible_total = playback_total if is_playback_delete else message_total
    payload = {
        "removed": int(removed or 0),
        "removedCount": int(removed or 0),
        "removedSuccess": bool(removed),
        "noticeIds": ",".join(requested_notice_ids),
        "noticeIdList": requested_notice_ids,
        "requestedCount": len(requested_notice_ids) or len(requested_file_names),
        "deviceSn": str(device_sn or "").strip(),
        "fileDate": str(file_date or "").strip(),
        "fileNames": requested_file_names,
        "totalCount": visible_total,
        "msgTotalCount": message_total,
        "playbackTotalCount": playback_total,
        "unreadCount": int(counts.get("unreadCount", 0) or 0),
        "unreadTypeCount": int(counts.get("unreadTypeCount", 0) or 0),
    }
    # iOS binary strings show camel-case variants too, so expose both spellings.
    payload["unReadCount"] = payload["unreadCount"]
    payload["unReadTypeCount"] = payload["unreadTypeCount"]
    return payload


@router.post("/ipc/msg/notice/add")
def notice_add(request: Request, payload: dict[str, Any] | None = Body(default=None)):
    body = _payload(payload)
    store = get_store()
    notice = store.add_notice(body)
    ext = notice.get("extObject", {}) or {}
    store.add_event(
        "notice.add",
        {
            "remote": request.client.host if request.client else "",
            "noticeId": notice.get("noticeId", 0),
            "body": body,
            "normalized": {
                "deviceSn": notice.get("deviceSn", ""),
                "parentDeviceSn": notice.get("parentDeviceSn", ""),
                "noticeType": notice.get("noticeType", 0),
                "visibleNoticeType": notice.get("visibleNoticeType", 0),
                "deviceTime": notice.get("deviceTime", ""),
                "fileDate": deep_get(ext, "fileDate", ""),
                "fileName": deep_get(ext, "fileName", ""),
                "duration": deep_get(ext, "duration", 0),
                "fileType": deep_get(ext, "fileType", 0),
                "timestamp": deep_get(ext, "timestamp", ""),
                "timezone": deep_get(ext, "timezone", ""),
                "timezoneex": deep_get(ext, "timezoneex", ""),
                "channel": deep_get(ext, "channel", ""),
                "msg": deep_get(ext, "msg", ""),
                "title": notice.get("title", ""),
            },
        },
    )
    try:
        dispatch = get_push_dispatcher().dispatch_notice(store, notice)
        store.add_event("push.dispatch", dispatch)
    except BaseException as exc:
        store.add_event(
            "push.dispatch.error",
            {
                "noticeId": notice.get("noticeId", 0),
                "error": str(exc),
            },
        )
    return _ok({"noticeId": notice.get("noticeId", 0)})


@router.post("/ipc/msg/push/report-token")
def push_report_token(request: Request, payload: dict[str, Any] | None = Body(default=None)):
    body = _payload(payload)
    push_token = str(deep_get(body, "pushToken", "") or "")
    remote = request.client.host if request.client else "fastapi"
    store = get_store()
    if store.set_push_token(push_token, remote):
        suffix = push_token[-12:] if len(push_token) > 12 else push_token
        store.add_event(
            "push_token.reported",
            {
                "pushToken": suffix,
                "platform": classify_push_token(push_token),
                "remote": remote,
            },
        )
    return _ok({})


@router.post("/ipc/msg/notice/count")
@router.post("/ipc/msg/notice/v3/count")
def notice_count(
    request: Request,
    payload: dict[str, Any] | None = Body(default=None),
    authorization: str | None = Header(default=None),
    access_token: str | None = None,
):
    body = _payload(payload)
    token = _access_token(request, body, access_token=access_token, authorization=authorization)
    identifier = _identity(request, body)
    user_id = str(deep_get(body, "userId", "") or deep_get(body, "userid", "") or "").strip()
    counts = get_store().message_count_payload(
        deep_get(body, "typeList", []),
        access_token=token,
        identifier=identifier,
        user_id=user_id,
    )
    counts["unReadCount"] = int(counts.get("unreadCount", 0) or 0)
    counts["unReadTypeCount"] = int(counts.get("unreadTypeCount", 0) or 0)
    return _ok(counts)


@router.post("/ipc/msg/notice/v4/condition")
def notice_condition(
    request: Request,
    payload: dict[str, Any] | None = Body(default=None),
    authorization: str | None = Header(default=None),
    access_token: str | None = None,
):
    body = _payload(payload)
    token = _access_token(request, body, access_token=access_token, authorization=authorization)
    identifier = _identity(request, body)
    user_id = str(deep_get(body, "userId", "") or deep_get(body, "userid", "") or "").strip()
    return _ok(
        get_store().message_filter_payload(
            str(deep_get(body, "date", "") or ""),
            access_token=token,
            identifier=identifier,
            user_id=user_id,
        )
    )


@router.post("/ipc/msg/notice/v4/page")
def notice_page(
    request: Request,
    payload: dict[str, Any] | None = Body(default=None),
    authorization: str | None = Header(default=None),
    access_token: str | None = None,
):
    body = _payload(payload)
    token = _access_token(request, body, access_token=access_token, authorization=authorization)
    identifier = _identity(request, body)
    user_id = str(deep_get(body, "userId", "") or deep_get(body, "userid", "") or "").strip()
    return _ok(
        get_store().message_list_payload(
            date=str(deep_get(body, "date", "") or ""),
            device_sn=str(deep_get(body, "deviceSn", "") or ""),
            last_notice_id=int(deep_get(body, "lastNoticeId", 0) or 0),
            page_size=int(deep_get(body, "pageSize", 20) or 20),
            type_list=deep_get(body, "typeList", []),
            access_token=token,
            identifier=identifier,
            user_id=user_id,
        )
    )


@router.post("/ipc/msg/notice/remove")
def notice_remove(
    request: Request,
    payload: dict[str, Any] | None = Body(default=None),
    authorization: str | None = Header(default=None),
    access_token: str | None = None,
):
    body = _payload(payload)
    token = _access_token(request, body, access_token=access_token, authorization=authorization)
    identifier = _identity(request, body)
    user_id = str(deep_get(body, "userId", "") or deep_get(body, "userid", "") or "").strip()
    notice_ids = (
        deep_get(body, "noticeIds")
        or deep_get(body, "noticeId")
        or deep_get(body, "ids")
        or []
    )
    file_names = deep_get(body, "fileNames") or deep_get(body, "fileName") or []
    removed = get_store().remove_messages_matching(
        notice_ids=notice_ids,
        device_sn=str(deep_get(body, "deviceSn", "") or ""),
        file_date=str(deep_get(body, "fileDate", "") or ""),
        file_names=file_names,
        access_token=token,
        identifier=identifier,
        user_id=user_id,
    )
    return _ok(
        _notice_remove_payload(
            removed=removed,
            notice_ids=notice_ids,
            device_sn=str(deep_get(body, "deviceSn", "") or ""),
            file_date=str(deep_get(body, "fileDate", "") or ""),
            file_names=file_names,
            access_token=token,
            identifier=identifier,
            user_id=user_id,
        )
    )
