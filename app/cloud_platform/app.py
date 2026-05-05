from __future__ import annotations

from fastapi import FastAPI

from ..api.auth import router as auth_router
from ..api.runtime_shared import router as runtime_shared_router
from .live_runtime import router as live_runtime_router
from .media_runtime import router as media_runtime_router
from .web_router import router as web_router


app = FastAPI(
    title="VAVA Cloud Platform",
    version="0.1.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

# Reused building blocks:
# - auth_router: browser login/session
# - runtime_shared_router: shared media/playback/debug endpoints reused by browser cloud platform
# - live_runtime_router: browser-only live runtime/control surface
# - media_runtime_router: browser-only media URLs under /monitor/media/*
app.include_router(auth_router)
app.include_router(runtime_shared_router)
app.include_router(live_runtime_router)
app.include_router(media_runtime_router)
app.include_router(web_router)


@app.get("/")
def root():
    return {
        "service": "vava-cloud-platform",
        "version": app.version,
        "docs": "/docs",
        "monitor": "/monitor",
    }
