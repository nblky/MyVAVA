from __future__ import annotations

import json

from fastapi import FastAPI, Request
from fastapi.exceptions import HTTPException
from fastapi.responses import JSONResponse

from app.store import get_store, iso_now


REQUEST_LOG_EXACT = {
    "/logs/item",
}

REQUEST_LOG_PREFIXES = (
    "/oauth",
    "/users",
    "/ipc",
    "/app",
    "/feedback",
    "/file",
    "/mi",
)

REQUEST_LOG_SKIP_PREFIXES = (
    "/debug",
    "/docs",
    "/redoc",
    "/openapi",
)

MAX_LOG_BODY_BYTES = 4096


def _should_probe_request(path: str) -> bool:
    if path in {"/", "/healthz", "/ping"}:
        return False
    if path.startswith(REQUEST_LOG_SKIP_PREFIXES):
        return False
    return path in REQUEST_LOG_EXACT or path.startswith(REQUEST_LOG_PREFIXES)


def _decode_probe_body(request: Request, raw_body: bytes) -> object:
    if not raw_body:
        return {}
    content_type = request.headers.get("content-type", "")
    if content_type.startswith("multipart/form-data"):
        return {
            "_multipart": True,
            "contentType": content_type,
            "size": len(raw_body),
        }
    if content_type.startswith("application/json") or not content_type:
        try:
            return json.loads(raw_body.decode("utf-8"))
        except Exception:
            pass
    text = raw_body[:MAX_LOG_BODY_BYTES].decode("utf-8", "replace")
    body = {"_raw": text}
    if len(raw_body) > MAX_LOG_BODY_BYTES:
        body["_truncatedBytes"] = len(raw_body) - MAX_LOG_BODY_BYTES
    return body


def install_http_layer(app: FastAPI) -> None:
    @app.middleware("http")
    async def request_probe(request: Request, call_next):
        entry = None
        if _should_probe_request(request.url.path):
            raw_body = b""
            if request.method not in {"GET", "HEAD"}:
                raw_body = await request.body()
            entry = {
                "ts": iso_now(),
                "method": request.method,
                "path": request.url.path,
                "query": dict(request.query_params),
                "body": _decode_probe_body(request, raw_body),
                "contentType": request.headers.get("content-type", ""),
                "remote": request.client.host if request.client else "",
                "ua": request.headers.get("user-agent", ""),
            }
        response = await call_next(request)
        if entry is not None:
            entry["statusCode"] = response.status_code
            get_store().append_request_log(entry)
        return response

    @app.exception_handler(HTTPException)
    async def http_exception_handler(_, exc: HTTPException):
        if isinstance(exc.detail, dict) and "stateCode" in exc.detail:
            return JSONResponse(status_code=200, content=exc.detail)
        return JSONResponse(
            status_code=exc.status_code,
            content={"stateCode": exc.status_code, "stateMsg": str(exc.detail), "data": {}},
        )
