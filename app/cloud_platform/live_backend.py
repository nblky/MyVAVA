from __future__ import annotations

import os
from typing import Any


def live_backend_name() -> str:
    return str(os.environ.get("VAVA_BROWSER_LIVE_BACKEND", "builtin_hls") or "builtin_hls").strip() or "builtin_hls"


def live_transport_name() -> str:
    return str(os.environ.get("VAVA_BROWSER_LIVE_TRANSPORT", "hls") or "hls").strip().lower() or "hls"


def live_url_template() -> str:
    return str(
        os.environ.get("VAVA_BROWSER_LIVE_URL_TEMPLATE", "/monitor/live/{camera_sn}/index.m3u8")
        or "/monitor/live/{camera_sn}/index.m3u8"
    ).strip() or "/monitor/live/{camera_sn}/index.m3u8"


def live_url_for_camera(camera_sn: str) -> str:
    return live_url_template().format(camera_sn=str(camera_sn or "").strip())


def live_backend_payload() -> dict[str, Any]:
    return {
        "backend": live_backend_name(),
        "transport": live_transport_name(),
        "urlTemplate": live_url_template(),
    }
