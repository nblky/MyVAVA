from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Header, Request

from ..store import DEFAULT_STATION_SN, deep_get, get_store

from .deps import _first_bound_station_for_token, _ok, _payload, _bearer_token, _access_token


router = APIRouter(tags=["p2p"])


def _resolve_station_sn(
    request: Request,
    body: dict[str, Any],
    *,
    token: str = "",
    default: str = DEFAULT_STATION_SN,
) -> str:
    station_sn = str(
        deep_get(body, "stationSn")
        or deep_get(body, "deviceSn")
        or deep_get(body, "sn")
        or request.query_params.get("stationSn", "")
        or request.query_params.get("deviceSn", "")
        or request.query_params.get("sn", "")
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


@router.post("/ipc/p2p/check-session-key")
def p2p_check_session_key(
    request: Request,
    payload: dict[str, Any] | None = Body(default=None),
    authorization: str | None = Header(default=None),
    access_token: str | None = None,
):
    body = _payload(payload)
    token = _access_token(request, body, access_token=access_token, authorization=authorization)
    station_sn = _resolve_station_sn(request, body, token=token, default=DEFAULT_STATION_SN)
    provided_key = str(
        deep_get(body, "sessionKey")
        or deep_get(body, "session_key")
        or deep_get(body, "key")
        or deep_get(body, "authKey")
        or ""
    )
    return _ok(get_store().check_session_key_payload(station_sn=station_sn, provided_key=provided_key))


@router.post("/ipc/p2p/get-session-key")
def p2p_get_session_key(
    request: Request,
    payload: dict[str, Any] | None = Body(default=None),
    authorization: str | None = Header(default=None),
    access_token: str | None = None,
):
    body = _payload(payload)
    token = _access_token(request, body, access_token=access_token, authorization=authorization)
    station_sn = _resolve_station_sn(request, body, token=token, default=DEFAULT_STATION_SN)
    return _ok(get_store().session_key_payload(station_sn))


@router.api_route("/ipc/p2p/get-did", methods=["GET", "POST"])
def p2p_get_did(
    request: Request,
    payload: dict[str, Any] | None = Body(default=None),
    authorization: str | None = Header(default=None),
    access_token: str | None = None,
):
    body = _payload(payload)
    token = _access_token(request, body, access_token=access_token, authorization=authorization)
    station_sn = _resolve_station_sn(request, body, token=token, default=DEFAULT_STATION_SN)
    return _ok(get_store().station_did_payload(station_sn))
