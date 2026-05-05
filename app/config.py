from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from .store_shared import DEFAULT_CRC, DEFAULT_DID_TOKEN, DEFAULT_INIT


ROOT_DIR = Path(__file__).resolve().parents[1]
DEFAULT_LIVE_HLS_ROOT = ROOT_DIR / "data" / "live_hls"
MACOS_RAMDISK_LIVE_HLS_ROOT = Path("/Volumes/VAVA_LIVE_RAM/live_hls")


def resolve_live_hls_root() -> Path:
    env_value = os.environ.get("VAVA_LIVE_HLS_ROOT", "").strip()
    if env_value:
        return Path(env_value).expanduser()
    if MACOS_RAMDISK_LIVE_HLS_ROOT.is_dir():
        return MACOS_RAMDISK_LIVE_HLS_ROOT
    return DEFAULT_LIVE_HLS_ROOT


@dataclass(frozen=True)
class Settings:
    root_dir: Path = ROOT_DIR
    app_dir: Path = ROOT_DIR / "app"
    data_dir: Path = ROOT_DIR / "data"
    live_hls_root: Path = resolve_live_hls_root()
    legacy_dir: Path = ROOT_DIR / "legacy"
    db_path: Path = ROOT_DIR / "data" / "sunvalley_state.sqlite3"
    state_path: Path = ROOT_DIR / "data" / "sunvalley_state.json"
    host: str = os.environ.get("VAVA_FASTAPI_HOST", "0.0.0.0")
    port: int = int(os.environ.get("VAVA_FASTAPI_PORT", "18080"))
    public_base_url: str = os.environ.get(
        "VAVA_PUBLIC_BASE_URL", "https://mi-api-pro.sunvalleycloud.com"
    )
    cloud_storage_scheme: str = os.environ.get("VAVA_CLOUD_STORAGE_SCHEME", "rtmp")
    cloud_storage_host: str = os.environ.get(
        "VAVA_CLOUD_STORAGE_HOST", "storage.sunvalleycloud.com"
    )
    cloud_storage_port: int = int(os.environ.get("VAVA_CLOUD_STORAGE_PORT", "1935"))
    cloud_storage_app: str = os.environ.get("VAVA_CLOUD_STORAGE_APP", "live")
    cloud_storage_stream_type: int = int(
        os.environ.get("VAVA_CLOUD_STORAGE_STREAM_TYPE", "0")
    )
    station_sync_host: str = os.environ.get("VAVA_STATION_SYNC_HOST", "vava-eth-199")
    station_sync_enabled: bool = os.environ.get(
        "VAVA_STATION_SYNC_ENABLED", "1"
    ).strip().lower() not in {"0", "false", "no", "off"}
    # Security hardening default: do not auto-claim station ownership from
    # device-side report-status tokens unless explicitly enabled.
    station_report_auto_bind_enabled: bool = os.environ.get(
        "VAVA_STATION_REPORT_AUTO_BIND_ENABLED",
        os.environ.get("VAVA_STATION_REPORT_AUTO_BIND", "0"),
    ).strip().lower() not in {"0", "false", "no", "off"}
    default_verify_code: str = os.environ.get("VAVA_DEFAULT_VERIFY_CODE", "123456")
    default_password: str = os.environ.get("VAVA_DEFAULT_PASSWORD", "123456")
    default_station_did_mode: str = os.environ.get(
        "VAVA_DEFAULT_STATION_DID_MODE", "static"
    ).strip().lower()
    default_station_did_seed: str = os.environ.get(
        "VAVA_DEFAULT_STATION_DID_SEED", ""
    ).strip()
    default_station_did_prefix: str = os.environ.get(
        "VAVA_DEFAULT_STATION_DID_PREFIX", "PPCS"
    ).strip()
    default_station_did_token: str = os.environ.get(
        "VAVA_DEFAULT_STATION_DID_TOKEN", DEFAULT_DID_TOKEN
    ).strip()
    default_station_init_code: str = os.environ.get(
        "VAVA_DEFAULT_STATION_INIT_CODE", DEFAULT_INIT
    ).strip()
    default_station_crc_key: str = os.environ.get(
        "VAVA_DEFAULT_STATION_CRC_KEY", DEFAULT_CRC
    ).strip()
    default_station_did_json: str = os.environ.get(
        "VAVA_DEFAULT_STATION_DID_JSON", ""
    ).strip()
    allow_any_6digit_verify_code: bool = os.environ.get(
        "VAVA_ALLOW_ANY_6DIGIT_VERIFY_CODE", "1"
    ).strip().lower() not in {"0", "false", "no", "off"}
    push_enabled: bool = os.environ.get("VAVA_PUSH_ENABLED", "1").strip().lower() not in {
        "0",
        "false",
        "no",
        "off",
    }
    push_timeout_seconds: float = float(os.environ.get("VAVA_PUSH_TIMEOUT_SECONDS", "5"))
    fcm_server_key: str = os.environ.get("VAVA_FCM_SERVER_KEY", "").strip()
    fcm_endpoint: str = os.environ.get(
        "VAVA_FCM_ENDPOINT", "https://fcm.googleapis.com/fcm/send"
    ).strip()
    apns_topic: str = os.environ.get("VAVA_APNS_TOPIC", "com.vava.home").strip()
    apns_env: str = os.environ.get("VAVA_APNS_ENV", "production").strip().lower()
    apns_key_id: str = os.environ.get("VAVA_APNS_KEY_ID", "").strip()
    apns_team_id: str = os.environ.get("VAVA_APNS_TEAM_ID", "").strip()
    apns_auth_key_path: str = os.environ.get("VAVA_APNS_AUTH_KEY_PATH", "").strip()
    apns_cert_path: str = os.environ.get("VAVA_APNS_CERT_PATH", "").strip()
    apns_key_path: str = os.environ.get("VAVA_APNS_KEY_PATH", "").strip()
    notify_webhook_url: str = os.environ.get("VAVA_NOTIFY_WEBHOOK_URL", "").strip()
    notify_webhook_auth: str = os.environ.get("VAVA_NOTIFY_WEBHOOK_AUTH", "").strip()
    ntfy_topic_url: str = os.environ.get("VAVA_NTFY_TOPIC_URL", "").strip()
    ntfy_auth: str = os.environ.get("VAVA_NTFY_AUTH", "").strip()
    ntfy_title_prefix: str = os.environ.get("VAVA_NTFY_TITLE_PREFIX", "VAVA").strip()


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
