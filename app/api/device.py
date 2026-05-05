from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Header, HTTPException, Request

from ..store import (
    DEFAULT_CAMERA_SN,
    DEFAULT_CHANNEL,
    DEFAULT_STATION_SN,
    deep_get,
    get_store,
)

from .deps import _first_bound_station_for_token, _identity, _ok, _payload, _bearer_token, _access_token


router = APIRouter(tags=["device"])
STATION_ADD_MIN_LOCK_AGE_SECONDS = 8
STATION_ADD_MAX_LOCK_AGE_SECONDS = 180
CAMERA_PAIR_MAX_LOCK_AGE_SECONDS = 180


def _first_present(body: dict[str, Any], *keys: str, default=None):
    for key in keys:
        value = deep_get(body, key)
        if value not in (None, ""):
            return value
    return default


def _error(code: int, message: str):
    raise HTTPException(status_code=200, detail={"stateCode": code, "stateMsg": message, "data": {}})


def _station_bind_error_code(error: str) -> int:
    normalized = str(error or "").strip().lower()
    if normalized == "station has been added by someone":
        return 216004
    if normalized == "station has been added to your account already":
        return 216003
    return 400


def _station_pairing_error(gate_payload: dict[str, Any]) -> tuple[int, str]:
    reason = str((gate_payload or {}).get("reason", "") or "").strip().lower()
    if reason == "lock-expired":
        return 400, "Pairing request expired, please retry from add base station flow"
    if reason == "lock-required":
        return 400, "Pairing step not initialized, please tap Next again on the pairing screen"
    if reason == "wait-for-station-status":
        return 400, "Base station is preparing pairing mode, please wait and tap Next again"
    return 400, "Please put base station into pairing mode and tap Next again"


def _camera_pairing_error(gate_payload: dict[str, Any]) -> tuple[int, str]:
    reason = str((gate_payload or {}).get("reason", "") or "").strip().lower()
    if reason == "lock-expired":
        return 400, "Camera pairing request expired, please trigger base station pairing mode again"
    if reason == "lock-required":
        return 400, "Base station is not in camera pairing mode, please long-press reset+sync first"
    if reason == "wait-for-station-status":
        return 400, "Base station is preparing camera pairing mode, please retry in a moment"
    return 400, "Base station is not ready for camera pairing"


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(default)


def _resolve_station_sn(
    request: Request,
    body: dict[str, Any],
    *,
    token: str = "",
    default: str = DEFAULT_STATION_SN,
) -> str:
    # Station firmware often omits stationSn and only carries access_token.
    # Resolve by token->station map first, then by bound stations, to avoid
    # falling back to DEFAULT_STATION_SN (which can point to another station).
    station_sn = str(
        _first_present(body, "stationSn", "baseSn", "gatewaySn", "deviceSn", "sn", default="")
        or request.query_params.get("stationSn", "")
        or request.query_params.get("baseSn", "")
        or request.query_params.get("gatewaySn", "")
        or request.query_params.get("deviceSn", "")
        or request.query_params.get("sn", "")
        or ""
    ).strip()
    resolved = bool(station_sn)
    if not station_sn:
        station_sn = str(
            deep_get(body, "stationAttrObject", {}).get("sn", "")
            or deep_get(body, "stationAttrObject", {}).get("deviceSn", "")
            or ""
        ).strip()
        resolved = bool(station_sn)
    if not station_sn and token:
        station_sn = get_store().station_sn_for_access_token(token)
        resolved = bool(station_sn)
    if not station_sn and token:
        station_sn = _first_bound_station_for_token(token)
        resolved = bool(station_sn)
    station_sn = station_sn or str(default or DEFAULT_STATION_SN)
    if token and station_sn and resolved:
        get_store().remember_station_access_token(station_sn, token)
    return station_sn


def _resolve_camera_pair_station_sn(
    request: Request,
    body: dict[str, Any],
    *,
    token: str = "",
) -> str:
    station_sn = str(
        _first_present(body, "stationSn", "baseSn", "gatewaySn", default="")
        or request.query_params.get("stationSn", "")
        or request.query_params.get("baseSn", "")
        or request.query_params.get("gatewaySn", "")
        or ""
    ).strip()
    if station_sn and token:
        get_store().remember_station_access_token(station_sn, token)
        return station_sn
    if not station_sn and token:
        station_sn = get_store().station_sn_for_access_token(token)
    if station_sn:
        if token:
            get_store().remember_station_access_token(station_sn, token)
        return station_sn
    if token:
        bindings, _ = get_store().station_bindings_for_user(access_token=token)
        unique_station_sns = sorted(
            {
                str((item or {}).get("stationSn", "") or "").strip()
                for item in bindings
                if str((item or {}).get("stationSn", "") or "").strip()
            }
        )
        if len(unique_station_sns) == 1:
            station_sn = unique_station_sns[0]
            get_store().remember_station_access_token(station_sn, token)
            return station_sn
    return ""


@router.api_route("/app/ota/upgrade/task/latest/rule", methods=["GET", "POST"])
def ota_upgrade_rule():
    return {"stateCode": 200, "stateMsg": "OK", "data": None}


@router.post("/ipc/device/station/report-status")
def station_report_status(
    request: Request,
    payload: dict[str, Any] | None = Body(default=None),
    authorization: str | None = Header(default=None),
    access_token: str | None = None,
):
    body = _payload(payload)
    token = _access_token(request, body, access_token=access_token, authorization=authorization)
    station_sn = _resolve_station_sn(request, body, token=token, default=DEFAULT_STATION_SN)
    get_store().update_station_status(station_sn, body, access_token=token)
    return _ok(
        {
            "intervalSeconds": 10,
            "pIntervalSec": 10,
            "slowIntervalSeconds": 60,
        }
    )


@router.post("/ipc/device/station/report-attr")
def station_report_attr(
    request: Request,
    payload: dict[str, Any] | None = Body(default=None),
    authorization: str | None = Header(default=None),
    access_token: str | None = None,
):
    body = _payload(payload)
    token = _access_token(request, body, access_token=access_token, authorization=authorization)
    station_sn = _resolve_station_sn(request, body, token=token, default=DEFAULT_STATION_SN)
    attr_sn = str(
        deep_get(body, "stationAttrObject", {}).get("sn")
        or deep_get(body, "stationAttrObject", {}).get("deviceSn")
        or ""
    ).strip()
    get_store().update_station_attr(attr_sn or station_sn, body)
    return _ok({})


@router.post("/ipc/device/station/add")
def station_add(
    request: Request,
    payload: dict[str, Any] | None = Body(default=None),
    authorization: str | None = Header(default=None),
    access_token: str | None = None,
):
    body = _payload(payload)
    station_sn = _first_present(body, "stationSn", "deviceSn", "sn", default=DEFAULT_STATION_SN)
    station_name = _first_present(body, "stationName", "deviceName", "name", default=station_sn)
    store = get_store()
    token = _access_token(request, body, access_token=access_token, authorization=authorization)
    identifier = _identity(request, body)
    user_id = str(deep_get(body, "userId", "") or deep_get(body, "userid", "") or "").strip()
    if token and station_sn:
        store.remember_station_access_token(str(station_sn), token)
    if token or identifier or user_id:
        gate_payload = store.station_pairing_gate_payload(
            station_sn=station_sn,
            access_token=token,
            identifier=identifier,
            user_id=user_id,
            min_lock_age_seconds=STATION_ADD_MIN_LOCK_AGE_SECONDS,
            max_lock_age_seconds=STATION_ADD_MAX_LOCK_AGE_SECONDS,
        )
        # Some app builds skip the explicit `/station/lock` request and call
        # add directly after check-bind. Bootstrap a pairing lock once so the
        # next add retry can pass the same gate policy.
        if not bool((gate_payload or {}).get("ready", False)) and str(
            (gate_payload or {}).get("reason", "") or ""
        ).strip().lower() == "lock-required":
            store.mark_station_pairing_lock(
                station_sn=station_sn,
                access_token=token,
                identifier=identifier,
                user_id=user_id,
            )
            gate_payload = store.station_pairing_gate_payload(
                station_sn=station_sn,
                access_token=token,
                identifier=identifier,
                user_id=user_id,
                min_lock_age_seconds=STATION_ADD_MIN_LOCK_AGE_SECONDS,
                max_lock_age_seconds=STATION_ADD_MAX_LOCK_AGE_SECONDS,
            )
        if not bool((gate_payload or {}).get("ready", False)):
            store.add_event(
                "station.add.blocked",
                {
                    "stationSn": station_sn,
                    "reason": str((gate_payload or {}).get("reason", "") or ""),
                    "lockAgeSeconds": int((gate_payload or {}).get("lockAgeSeconds", -1) or -1),
                    "stationStatusAfterLock": int((gate_payload or {}).get("stationStatusAfterLock", 0) or 0),
                },
            )
            code, message = _station_pairing_error(gate_payload)
            _error(code, message)
    role = str(deep_get(body, "role", "owner") or "owner")
    bind_result = None
    if token or identifier or user_id:
        bind_result, error = store.bind_station_to_user(
            station_sn=station_sn,
            access_token=token,
            identifier=identifier,
            user_id=user_id,
            role=role,
        )
        if error:
            _error(_station_bind_error_code(error), error)
    station = store.set_station(station_sn, station_name)
    store.add_event("station.add", {"stationSn": station_sn, "stationName": station_name})
    return _ok(
        {
            "stationSn": station_sn,
            "stationName": station_name,
            "station": station,
            "binding": bind_result or {},
        }
    )


@router.post("/ipc/device/station/set")
def station_set(payload: dict[str, Any] | None = Body(default=None)):
    body = _payload(payload)
    station_sn = _first_present(body, "stationSn", "deviceSn", "sn", default=DEFAULT_STATION_SN)
    station_name = _first_present(body, "stationName", "deviceName", "name")
    station = get_store().set_station(station_sn, station_name)
    get_store().add_event("station.set", {"stationSn": station_sn, "stationName": station_name})
    return _ok({"stationSn": station_sn, "stationName": station_name, "station": station})


@router.post("/ipc/device/station/check-bind-status")
def station_check_bind_status(
    request: Request,
    payload: dict[str, Any] | None = Body(default=None),
    authorization: str | None = Header(default=None),
    access_token: str | None = None,
):
    body = _payload(payload)
    station_sn = str(
        deep_get(body, "stationSn", "")
        or request.query_params.get("stationSn", "")
        or request.query_params.get("deviceSn", "")
        or request.query_params.get("sn", "")
        or ""
    ).strip()
    token = _access_token(request, body, access_token=access_token, authorization=authorization)
    identifier = _identity(request, body)
    # App usually calls `/ipc/device/station/lock` immediately after this step
    # and may omit stationSn there. Keep a token->station hint from the scanned
    # SN so lock/add flows can target the intended base station.
    if station_sn and token:
        get_store().remember_station_access_token(station_sn, token)
    # Some app variants skip `/station/lock`; seed a pairing lock here to keep
    # add flow compatible while still requiring the lock gate timing checks.
    if station_sn and (token or identifier):
        get_store().mark_station_pairing_lock(
            station_sn=station_sn,
            access_token=token,
            identifier=identifier,
            user_id=str(deep_get(body, "userId", "") or deep_get(body, "userid", "") or "").strip(),
        )
    result = get_store().station_bind_check_payload(
        station_sn=station_sn or DEFAULT_STATION_SN,
        access_token=token,
        identifier=identifier,
        user_id=str(deep_get(body, "userId", "") or deep_get(body, "userid", "") or "").strip(),
    )
    return result


@router.post("/ipc/device/station/lock")
def station_lock(
    request: Request,
    payload: dict[str, Any] | None = Body(default=None),
    authorization: str | None = Header(default=None),
    access_token: str | None = None,
):
    body = _payload(payload)
    token = _access_token(request, body, access_token=access_token, authorization=authorization)
    identifier = _identity(request, body)
    user_id = str(deep_get(body, "userId", "") or deep_get(body, "userid", "") or "").strip()
    station_sn = str(
        _first_present(body, "stationSn", "baseSn", "gatewaySn", "deviceSn", "sn", default="")
        or request.query_params.get("stationSn", "")
        or request.query_params.get("baseSn", "")
        or request.query_params.get("gatewaySn", "")
        or request.query_params.get("deviceSn", "")
        or request.query_params.get("sn", "")
        or ""
    ).strip()
    if not station_sn and token:
        station_sn = get_store().station_sn_for_access_token(token)
    if not station_sn and token:
        station_sn = _first_bound_station_for_token(token)
    station_sn = station_sn or DEFAULT_STATION_SN
    if token and station_sn:
        get_store().remember_station_access_token(station_sn, token)
    pairing_lock = get_store().mark_station_pairing_lock(
        station_sn=station_sn,
        access_token=token,
        identifier=identifier,
        user_id=user_id,
    )
    station = get_store().update_station_session(station_sn, True) or get_store().set_station(station_sn, station_sn)
    get_store().add_event("station.lock", {"stationSn": station_sn})
    return _ok(
        {
            "stationSn": station_sn,
            "locked": 0,
            "stationOnline": str((station or {}).get("stationOnline", "1") or "1"),
            "statusObject": dict((station or {}).get("statusObject", {}) or {}),
            "pairingLockAt": str((pairing_lock or {}).get("issuedAt", "") or ""),
        }
    )


@router.api_route("/ipc/device/station/bind", methods=["GET", "POST"])
@router.api_route("/ipc/device/station/claim", methods=["GET", "POST"])
def station_bind_or_claim(
    request: Request,
    payload: dict[str, Any] | None = Body(default=None),
    authorization: str | None = Header(default=None),
    access_token: str | None = None,
):
    body = _payload(payload)
    token = _access_token(request, body, access_token=access_token, authorization=authorization)
    identifier = _identity(request, body)
    station_sn = str(_first_present(body, "stationSn", "deviceSn", "sn", default="") or "").strip()
    user_id = str(deep_get(body, "userId", "") or deep_get(body, "userid", "") or "").strip()
    if not station_sn:
        _error(400, "stationSn required")
    if token:
        get_store().remember_station_access_token(station_sn, token)
    gate_payload = get_store().station_pairing_gate_payload(
        station_sn=station_sn,
        access_token=token,
        identifier=identifier,
        user_id=user_id,
        min_lock_age_seconds=STATION_ADD_MIN_LOCK_AGE_SECONDS,
        max_lock_age_seconds=STATION_ADD_MAX_LOCK_AGE_SECONDS,
    )
    if not bool((gate_payload or {}).get("ready", False)):
        get_store().add_event(
            "station.bind.blocked",
            {
                "stationSn": station_sn,
                "reason": str((gate_payload or {}).get("reason", "") or ""),
                "lockAgeSeconds": int((gate_payload or {}).get("lockAgeSeconds", -1) or -1),
                "stationStatusAfterLock": int((gate_payload or {}).get("stationStatusAfterLock", 0) or 0),
            },
        )
        code, message = _station_pairing_error(gate_payload)
        _error(code, message)
    result, error = get_store().bind_station_to_user(
        station_sn=station_sn,
        access_token=token,
        identifier=identifier,
        user_id=user_id,
        role=str(deep_get(body, "role", "owner") or "owner"),
    )
    if error:
        _error(_station_bind_error_code(error), error)
    return _ok(result)


@router.api_route("/ipc/device/station/list-bind-station", methods=["GET", "POST"])
def station_list_bind_station(
    request: Request,
    payload: dict[str, Any] | None = Body(default=None),
    authorization: str | None = Header(default=None),
    access_token: str | None = None,
):
    body = _payload(payload)
    token = _access_token(request, body, access_token=access_token, authorization=authorization)
    identifier = _identity(request, body)
    return _ok(get_store().station_bind_list(access_token=token, identifier=identifier))


@router.post("/ipc/device/station/remove")
def station_remove(
    request: Request,
    payload: dict[str, Any] | None = Body(default=None),
    authorization: str | None = Header(default=None),
    access_token: str | None = None,
):
    body = _payload(payload)
    station_sn = _first_present(body, "stationSn", "deviceSn", "sn", default=DEFAULT_STATION_SN)
    token = _access_token(request, body, access_token=access_token, authorization=authorization)
    identifier = _identity(request, body)
    user_id = str(deep_get(body, "userId", "") or deep_get(body, "userid", "") or "").strip()
    store = get_store()
    if token or identifier or user_id:
        removed, error = store.unbind_station_from_user(
            station_sn=station_sn,
            access_token=token,
            identifier=identifier,
            user_id=user_id,
        )
        if error:
            _error(400, error)
        store.add_event(
            "station.unbind",
            {
                "stationSn": station_sn,
                "removed": bool((removed or {}).get("removed", False)),
                "bindingCount": int((removed or {}).get("bindingCount", 0) or 0),
                "ownerUserId": str((removed or {}).get("ownerUserId", "") or ""),
                "ownerUsername": str((removed or {}).get("ownerUsername", "") or ""),
            },
        )
        return _ok(removed or {"stationSn": station_sn, "removed": False})
    removed = store.remove_station(station_sn)
    store.add_event(
        "station.remove.global",
        {
            "stationSn": station_sn,
            "removed": bool(removed),
            "removedCameras": (removed or {}).get("removedCameras", []),
        },
    )
    return _ok(
        {
            "stationSn": station_sn,
            "removed": bool(removed),
            "removedCameras": (removed or {}).get("removedCameras", []),
            "mode": "global",
        }
    )


@router.post("/ipc/device/camera/check-bind-status")
def camera_check_bind_status(
    request: Request,
    payload: dict[str, Any] | None = Body(default=None),
    authorization: str | None = Header(default=None),
    access_token: str | None = None,
):
    body = _payload(payload)
    station_sn = deep_get(body, "stationSn", DEFAULT_STATION_SN)
    camera_sn = deep_get(body, "cameraSn", DEFAULT_CAMERA_SN)
    token = _access_token(request, body, access_token=access_token, authorization=authorization)
    identifier = _identity(request, body)
    result = get_store().camera_bind_check_payload(
        camera_sn=camera_sn,
        station_sn=station_sn,
        access_token=token,
        identifier=identifier,
        user_id=str(deep_get(body, "userId", "") or deep_get(body, "userid", "") or "").strip(),
    )
    return result


@router.post("/ipc/device/camera/remove")
def camera_remove(payload: dict[str, Any] | None = Body(default=None)):
    body = _payload(payload)
    camera_sn = deep_get(body, "cameraSn", DEFAULT_CAMERA_SN)
    removed = get_store().remove_camera(camera_sn)
    get_store().add_event("camera.remove", {"cameraSn": camera_sn, "removed": bool(removed)})
    return _ok({"cameraSn": camera_sn, "removed": bool(removed)})


@router.api_route("/ipc/device/camera/check-bind-status-by-iot", methods=["GET", "POST"])
def camera_check_bind_status_by_iot(
    request: Request,
    payload: dict[str, Any] | None = Body(default=None),
):
    body = _payload(payload)
    token = _access_token(request, body)
    station_sn = _resolve_camera_pair_station_sn(request, body, token=token)
    if not station_sn:
        _error(400, "stationSn required for camera pairing")
    gate_payload = get_store().station_pairing_gate_payload(
        station_sn=station_sn,
        min_lock_age_seconds=0,
        max_lock_age_seconds=CAMERA_PAIR_MAX_LOCK_AGE_SECONDS,
    )
    if not bool((gate_payload or {}).get("ready", False)):
        reason = str((gate_payload or {}).get("reason", "") or "").strip().lower()
        if reason in ("lock-required", "lock-expired"):
            identifier = _identity(request, body)
            user_id = str(deep_get(body, "userId", "") or deep_get(body, "userid", "") or "").strip()
            get_store().mark_station_pairing_lock(
                station_sn=station_sn,
                access_token=token,
                identifier=identifier,
                user_id=user_id,
            )
            gate_payload = get_store().station_pairing_gate_payload(
                station_sn=station_sn,
                min_lock_age_seconds=0,
                max_lock_age_seconds=CAMERA_PAIR_MAX_LOCK_AGE_SECONDS,
            )
    if not bool((gate_payload or {}).get("ready", False)):
        get_store().add_event(
            "camera.check_bind_status_by_iot.blocked",
            {
                "stationSn": station_sn,
                "reason": str((gate_payload or {}).get("reason", "") or ""),
            },
        )
        code, message = _camera_pairing_error(gate_payload)
        _error(code, message)
    camera_sn = _first_present(body, "cameraSn", "deviceSn", "sn", default=None)
    if camera_sn in (None, ""):
        camera_sn = (
            request.query_params.get("cameraSn", "")
            or request.query_params.get("deviceSn", "")
            or request.query_params.get("sn", "")
            or DEFAULT_CAMERA_SN
        )
    channel_raw = _first_present(body, "channel", "slotIndex", default=None)
    if channel_raw is None:
        channel_raw = request.query_params.get("channel", request.query_params.get("slotIndex", DEFAULT_CHANNEL))
    channel = int(channel_raw)
    camera_mac = str(
        deep_get(body, "cameraMac")
        or deep_get(body, "mac")
        or request.query_params.get("cameraMac", "")
        or request.query_params.get("mac", "")
        or ""
    ).strip()
    peer_ipv4 = str(
        deep_get(body, "peerIpv4")
        or deep_get(body, "peerIp")
        or request.query_params.get("peerIpv4", "")
        or request.query_params.get("peerIp", "")
        or ""
    ).strip()
    session_id = f"{station_sn}:{camera_sn}:{channel}"
    slot, error = get_store().reserve_pairing_slot(
        station_sn=station_sn,
        camera_sn=camera_sn,
        preferred_channel=channel,
        camera_mac=camera_mac,
        peer_ipv4=peer_ipv4,
        session_id=session_id,
    )
    if error:
        return {
            "stateCode": 409,
            "stateMsg": error,
            "data": {
                "cameraSn": camera_sn,
                "stationSn": station_sn,
                "channel": channel,
            },
        }
    effective_channel = int((slot or {}).get("slotIndex", channel) or channel)
    get_store().ensure_camera(camera_sn, station_sn, effective_channel, pending=True)
    get_store().upsert_pairing_session(
        session_id=session_id,
        station_sn=station_sn,
        camera_sn=camera_sn,
        channel=effective_channel,
        stage="check-bind-status-by-iot",
        status="pending",
        payload={"request": body, "slot": slot},
    )
    return _ok({"cameraSn": camera_sn, "stationSn": station_sn, "channel": effective_channel, "slot": slot})


@router.api_route("/ipc/device/camera/add-blind", methods=["GET", "POST"])
def camera_add_blind(
    request: Request,
    payload: dict[str, Any] | None = Body(default=None),
):
    body = _payload(payload)
    token = _access_token(request, body)
    station_sn = _resolve_camera_pair_station_sn(request, body, token=token)
    if not station_sn:
        _error(400, "stationSn required for camera pairing")
    gate_payload = get_store().station_pairing_gate_payload(
        station_sn=station_sn,
        min_lock_age_seconds=0,
        max_lock_age_seconds=CAMERA_PAIR_MAX_LOCK_AGE_SECONDS,
    )
    if not bool((gate_payload or {}).get("ready", False)):
        reason = str((gate_payload or {}).get("reason", "") or "").strip().lower()
        if reason in ("lock-required", "lock-expired"):
            identifier = _identity(request, body)
            user_id = str(deep_get(body, "userId", "") or deep_get(body, "userid", "") or "").strip()
            get_store().mark_station_pairing_lock(
                station_sn=station_sn,
                access_token=token,
                identifier=identifier,
                user_id=user_id,
            )
            gate_payload = get_store().station_pairing_gate_payload(
                station_sn=station_sn,
                min_lock_age_seconds=0,
                max_lock_age_seconds=CAMERA_PAIR_MAX_LOCK_AGE_SECONDS,
            )
    if not bool((gate_payload or {}).get("ready", False)):
        get_store().add_event(
            "camera.add_blind.blocked",
            {
                "stationSn": station_sn,
                "reason": str((gate_payload or {}).get("reason", "") or ""),
            },
        )
        code, message = _camera_pairing_error(gate_payload)
        _error(code, message)
    camera_sn = _first_present(body, "cameraSn", "deviceSn", "sn", default=None)
    if camera_sn in (None, ""):
        camera_sn = (
            request.query_params.get("cameraSn", "")
            or request.query_params.get("deviceSn", "")
            or request.query_params.get("sn", "")
            or DEFAULT_CAMERA_SN
        )
    channel_raw = _first_present(body, "channel", "slotIndex", default=None)
    if channel_raw is None:
        channel_raw = request.query_params.get("channel", request.query_params.get("slotIndex", DEFAULT_CHANNEL))
    channel = int(channel_raw)
    existing_slots = [
        slot
        for slot in get_store().pairing_slot_list(station_sn)
        if str((slot or {}).get("cameraSn", "") or "").strip() == str(camera_sn or "").strip()
    ]
    if not existing_slots:
        _error(409, "Pairing context missing, please retry camera scan step")
    expected_slot = next(
        (
            slot
            for slot in existing_slots
            if _as_int((slot or {}).get("slotIndex", -1), -1) == channel
        ),
        None,
    )
    if expected_slot is None:
        expected_slot = existing_slots[0]
    if int((expected_slot or {}).get("pendingPairlistFlag", 0) or 0) != 1:
        _error(409, "Camera pairing session not pending, please retry camera scan step")
    channel = _as_int((expected_slot or {}).get("slotIndex", channel), channel)
    camera_mac = str(
        deep_get(body, "cameraMac")
        or deep_get(body, "mac")
        or request.query_params.get("cameraMac", "")
        or request.query_params.get("mac", "")
        or ""
    ).strip()
    peer_ipv4 = str(
        deep_get(body, "peerIpv4")
        or deep_get(body, "peerIp")
        or request.query_params.get("peerIpv4", "")
        or request.query_params.get("peerIp", "")
        or ""
    ).strip()
    session_id = f"{station_sn}:{camera_sn}:{channel}"
    slot, error = get_store().reserve_pairing_slot(
        station_sn=station_sn,
        camera_sn=camera_sn,
        preferred_channel=channel,
        camera_mac=camera_mac,
        peer_ipv4=peer_ipv4,
        session_id=session_id,
    )
    if error:
        return {
            "stateCode": 409,
            "stateMsg": error,
            "data": {
                "cameraSn": camera_sn,
                "stationSn": station_sn,
                "channel": channel,
            },
        }
    effective_channel = int((slot or {}).get("slotIndex", channel) or channel)
    get_store().ensure_camera(camera_sn, station_sn, effective_channel, pending=True)
    get_store().upsert_pairing_session(
        session_id=session_id,
        station_sn=station_sn,
        camera_sn=camera_sn,
        channel=effective_channel,
        stage="add-blind",
        status="accepted",
        payload={"request": body, "slot": slot},
    )
    get_store().add_event(
        "camera.add_blind",
        {"cameraSn": camera_sn, "stationSn": station_sn, "channel": effective_channel},
    )
    return _ok({"cameraSn": camera_sn, "stationSn": station_sn, "channel": effective_channel, "slot": slot})


@router.post("/ipc/device/camera/set")
def camera_set(payload: dict[str, Any] | None = Body(default=None)):
    body = _payload(payload)
    store = get_store()
    camera_sn = _first_present(body, "cameraSn", "deviceSn", "sn", default=DEFAULT_CAMERA_SN)
    camera_name = _first_present(body, "cameraName", "deviceName", "name")
    station_sn_hint = _first_present(body, "stationSn", "baseSn", "gatewaySn", default="")
    channel_hint_raw = _first_present(body, "channel", "slotIndex", default=None)
    try:
        channel_hint = int(channel_hint_raw) if channel_hint_raw is not None else None
    except (TypeError, ValueError):
        channel_hint = None
    camera = None
    if camera_name is not None:
        camera = store.mark_camera_bound(camera_sn, camera_name)
        store.add_event(
            "camera.rename_or_bind",
            {"cameraSn": camera_sn, "cameraName": camera_name},
        )
    camera = store.update_camera_settings(camera_sn, body) or camera
    pairing_context = None
    if not camera:
        pairing_context = store.resolve_pairing_context(
            camera_sn=camera_sn,
            station_sn=station_sn_hint,
            channel=channel_hint,
        )
        if pairing_context is not None:
            resolved_station_sn = str((pairing_context or {}).get("stationSn", "") or "").strip()
            resolved_channel = _as_int((pairing_context or {}).get("channel", -1), -1)
            has_session_hint = bool(
                str((pairing_context or {}).get("stage", "") or "").strip()
                or str((pairing_context or {}).get("status", "") or "").strip()
            )
            has_slot_hint = any(
                str((slot or {}).get("cameraSn", "") or "").strip() == str(camera_sn or "").strip()
                and str((slot or {}).get("stationSn", "") or "").strip() == resolved_station_sn
                and _as_int((slot or {}).get("slotIndex", -1), -1) == resolved_channel
                for slot in store.pairing_slot_list(resolved_station_sn)
            )
            # `resolve_pairing_context()` can synthesize a context from station/channel
            # hints alone. Require at least one real slot/session hint.
            if not has_session_hint and not has_slot_hint:
                pairing_context = None
        if pairing_context is None and station_sn_hint and channel_hint is not None:
            # Keep camera->station binding strictly on the blind-pair slot/session
            # state machine. Do not auto-create a synthetic context from app input.
            candidate_slots = [
                slot
                for slot in store.pairing_slot_list(station_sn_hint)
                if str((slot or {}).get("cameraSn", "") or "").strip() == str(camera_sn or "").strip()
                and _as_int((slot or {}).get("activeFlag", 0), 0) == 1
            ]
            candidate_slot = next(
                (
                    slot
                    for slot in candidate_slots
                    if _as_int((slot or {}).get("slotIndex", -1), -1) == int(channel_hint)
                ),
                None,
            )
            if candidate_slot is None and candidate_slots:
                candidate_slot = candidate_slots[0]
            if candidate_slot is not None:
                pairing_context = {
                    "stationSn": station_sn_hint,
                    "channel": _as_int((candidate_slot or {}).get("slotIndex", channel_hint), channel_hint),
                    "sessionId": str(
                        (candidate_slot or {}).get("lastSessionId", "")
                        or f"{station_sn_hint}:{camera_sn}:{_as_int((candidate_slot or {}).get('slotIndex', channel_hint), channel_hint)}"
                    ),
                    "cameraMac": str((candidate_slot or {}).get("cameraMac", "") or ""),
                    "peerIpv4": str((candidate_slot or {}).get("peerIpv4", "") or ""),
                }
        if pairing_context is not None:
            camera = store.ensure_camera(
                camera_sn,
                str(pairing_context.get("stationSn", "") or ""),
                int(pairing_context.get("channel", 0) or 0),
                camera_name=camera_name or camera_sn,
                pending=False,
            )
            if camera_name is not None:
                camera = store.mark_camera_bound(camera_sn, camera_name) or camera
            camera = store.update_camera_settings(camera_sn, body) or camera
            store.add_event(
                "camera.set.recovered_context",
                {
                    "cameraSn": camera_sn,
                    "stationSn": str(pairing_context.get("stationSn", "") or ""),
                    "channel": int(pairing_context.get("channel", 0) or 0),
                    "sessionId": str(pairing_context.get("sessionId", "") or ""),
                },
            )
    if camera:
        resolved_station_sn = str(camera.get("stationSn", "") or "")
        resolved_channel = int(camera.get("channel", 0) or 0)
        session_id = str(
            (pairing_context or {}).get("sessionId", "")
            or f"{resolved_station_sn}:{camera_sn}:{resolved_channel}"
        )
        slot = store.activate_pairing_slot(
            station_sn=str(camera.get("stationSn", "") or ""),
            camera_sn=str(camera_sn or ""),
            channel=int(camera.get("channel", 0) or 0),
            camera_mac=str(
                deep_get(body, "cameraMac", "")
                or deep_get(body, "mac", "")
                or (pairing_context or {}).get("cameraMac", "")
                or ""
            ),
            peer_ipv4=str(
                deep_get(body, "peerIpv4", "")
                or deep_get(body, "peerIp", "")
                or (pairing_context or {}).get("peerIpv4", "")
                or ""
            ),
            session_id=session_id,
        )
        store.upsert_pairing_session(
            session_id=session_id,
            station_sn=resolved_station_sn,
            camera_sn=str(camera_sn or ""),
            channel=resolved_channel,
            stage="camera-set",
            status="bound" if not bool(camera.get("pending", False)) else "pending",
            payload={
                "request": body,
                "cameraName": camera.get("cameraName", camera_name or camera_sn),
                "slot": slot,
                "recoveredContext": bool(pairing_context),
            },
        )
    if not camera:
        _error(409, "Camera pairing session missing, please retry blind pairing flow")
    return _ok(
        {
            "cameraSn": camera_sn,
            "cameraName": (camera or {}).get("cameraName", camera_name or camera_sn),
            "settingsObject": (camera or {}).get("settingsObject", {}),
        }
    )


@router.post("/ipc/device/camera/list-for-index")
def camera_list_for_index(
    request: Request,
    payload: dict[str, Any] | None = Body(default=None),
    authorization: str | None = Header(default=None),
    access_token: str | None = None,
):
    body = _payload(payload)
    if request.client and request.client.host in ("127.0.0.1", "::1"):
        if get_store().consume_flag("force_app_index_empty_once", True):
            get_store().add_event(
                "app.list_for_index.reset_once",
                {"remote": request.client.host},
            )
            return _ok({"cameraList": [], "intervalSeconds": 10, "stationList": []})
    token = _access_token(request, body, access_token=access_token, authorization=authorization)
    identifier = _identity(request, body)
    return _ok(get_store().camera_index_payload(access_token=token, identifier=identifier))


@router.api_route("/ipc/device/camera/list-for-my-devices", methods=["GET", "POST"])
def camera_list_for_my_devices(
    request: Request,
    payload: dict[str, Any] | None = Body(default=None),
    authorization: str | None = Header(default=None),
    access_token: str | None = None,
):
    body = _payload(payload)
    token = _access_token(request, body, access_token=access_token, authorization=authorization)
    identifier = _identity(request, body)
    return _ok(get_store().my_devices_payload(access_token=token, identifier=identifier))


@router.api_route("/ipc/device/camera/list-for-mgt", methods=["GET", "POST"])
def camera_list_for_mgt(
    request: Request,
    payload: dict[str, Any] | None = Body(default=None),
    authorization: str | None = Header(default=None),
    access_token: str | None = None,
):
    body = _payload(payload)
    token = _access_token(request, body, access_token=access_token, authorization=authorization)
    identifier = _identity(request, body)
    return _ok(get_store().camera_index_payload(access_token=token, identifier=identifier))


@router.api_route("/ipc/device/camera/list-for-station", methods=["GET", "POST"])
def camera_list_for_station(
    request: Request,
    payload: dict[str, Any] | None = Body(default=None),
):
    body = _payload(payload)
    token = _access_token(request, body)
    station_sn = _resolve_station_sn(request, body, token=token, default=DEFAULT_STATION_SN)
    # The base station polls this endpoint with the mobile app's cached access_token.
    # Once that token expires or no longer maps to a bound user, auth-scoped filtering
    # collapses the station camera table to empty, and the firmware starts returning
    # CHANNEL_NOCAMERA(9) for every live/camera command.
    if station_sn:
        get_store().update_station_session(station_sn, True)
    return _ok(get_store().station_camera_list_payload(station_sn))


@router.api_route("/ipc/device/camera/list-sn-status", methods=["GET", "POST"])
def camera_list_sn_status(
    request: Request,
    payload: dict[str, Any] | None = Body(default=None),
    authorization: str | None = Header(default=None),
    access_token: str | None = None,
):
    body = _payload(payload)
    token = _access_token(request, body, access_token=access_token, authorization=authorization)
    identifier = _identity(request, body)
    return _ok(
        get_store().camera_status_payload(
            str(deep_get(body, "sn", request.query_params.get("sn", "")) or ""),
            access_token=token,
            identifier=identifier,
        )
    )


@router.post("/ipc/device/upgrade/check-version")
def device_upgrade_check_version():
    return _ok({"stationFirmList": []})
