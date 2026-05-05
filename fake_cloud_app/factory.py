from __future__ import annotations

from fastapi import FastAPI

from app.api.android_compat import router as android_compat_router
from app.api.auth import router as auth_router
from app.api.device import router as device_router
from app.api.message import router as message_router
from app.api.p2p import router as p2p_router
from app.api.runtime_shared import router as runtime_shared_router
from app.config import get_settings

from .http import install_http_layer


APP_TITLE = "VAVA Fake Cloud Control"
APP_VERSION = "0.2.0"


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title=APP_TITLE,
        version=APP_VERSION,
        docs_url="/docs",
        redoc_url="/redoc",
    )

    app.include_router(runtime_shared_router)
    app.include_router(auth_router)
    app.include_router(device_router)
    app.include_router(message_router)
    app.include_router(p2p_router)
    app.include_router(android_compat_router)

    install_http_layer(app)

    @app.get("/")
    def root():
        return {
            "service": "vava-fake-cloud",
            "version": app.version,
            "docs": "/docs",
            "dbPath": str(settings.db_path),
            "projectDocsDir": str(settings.root_dir / "fake_cloud_app" / "docs"),
        }

    return app
