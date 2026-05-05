from __future__ import annotations

from typing import Any

from fastapi import Request

from ..store import deep_get, get_store


def _ok(data=None):
    return {"stateCode": 200, "stateMsg": "OK", "data": data if data is not None else {}}


def _payload(payload: dict[str, Any] | None) -> dict[str, Any]:
    return payload or {}


def _bearer_token(authorization: str | None) -> str:
    if authorization and authorization.lower().startswith("bearer "):
        return authorization.split(None, 1)[1].strip()
    return ""


def _access_token(
    request: Request,
    body: dict[str, Any],
    *,
    access_token: str | None = None,
    authorization: str | None = None,
) -> str:
    return str(
        access_token
        or request.query_params.get("access_token", "")
        or deep_get(body, "access_token")
        or deep_get(body, "accessToken")
        or _bearer_token(authorization or request.headers.get("authorization", ""))
        or ""
    ).strip()


def _identity(request: Request, body: dict[str, Any]) -> str:
    for key in ("username", "email", "mobile", "phone", "account"):
        value = deep_get(body, key)
        if value in (None, ""):
            value = request.query_params.get(key, "")
        if value not in (None, ""):
            return str(value).strip()
    return ""


def _user_id(request: Request, body: dict[str, Any]) -> str:
    return str(
        deep_get(body, "userId")
        or deep_get(body, "userid")
        or request.query_params.get("userId", "")
        or request.query_params.get("userid", "")
        or ""
    ).strip()


def _first_bound_station_for_token(token: str) -> str:
    token = str(token or "").strip()
    if not token:
        return ""
    bindings, _ = get_store().station_bindings_for_user(access_token=token)
    if not bindings:
        return ""
    owner_binding = next(
        (
            item
            for item in bindings
            if str((item or {}).get("role", "") or "").strip().lower() == "owner"
        ),
        None,
    )
    selected = owner_binding or bindings[0]
    return str((selected or {}).get("stationSn", "") or "").strip()
