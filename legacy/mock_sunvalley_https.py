#!/usr/bin/env python3
import argparse
import hashlib
import importlib.util
import json
import os
import secrets
import ssl
import sqlite3
import threading
import time
import uuid
from datetime import datetime, timezone
from functools import lru_cache
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse


HOST = "0.0.0.0"
PORT = 443
CERT = Path(__file__).with_name("sunvalley_multi_san_fullchain.crt")
KEY = Path(__file__).with_name("sunvalley_multi_san.key")
STATE_PATH = Path(__file__).with_name("sunvalley_state.json")
DB_PATH = Path(__file__).with_name("sunvalley_state.sqlite3")
ROUTES_DIR = Path(__file__).with_name("sunvalley_routes")

DEFAULT_STATION_SN = "64XI7DE3Q2115F3BBF02F9A80"
DEFAULT_STATION_NAME = "VAVA Base"
DEFAULT_CAMERA_SN = "64XIJEE3QF0EAC5DE2647496E"
DEFAULT_CAMERA_NAME = "Front Door"
DEFAULT_CHANNEL = 0
DEFAULT_SESSION_KEY = "VAVA_TEST_AUTH_KEY_2017"
DEFAULT_DID = "F02F-9A80-VAHS0031111A"
DEFAULT_DID_TOKEN = "VAVAHS003AUTH2019"
FORCE_DID = os.environ.get("VAVA_FORCE_DID", "").strip()
DEFAULT_INIT = "EBGAEIBIKHJJGFJKEOGCFAEPHPMAHONDGJFPBKCPAJJMLFKBDBAGCJPBGOLKIKLKAJMJKFDOOFMOBECEJIMM"
DEFAULT_CRC = "LOCAL-VAVA-CRC"
DEFAULT_ACCESS_TOKEN = "local-access-token"
DEFAULT_REFRESH_TOKEN = "local-refresh-token"
DEFAULT_USER_ID = "local-user-1"
DEFAULT_EMAIL = "local@vava.invalid"
DEFAULT_PASSWORD = os.environ.get("VAVA_DEFAULT_PASSWORD", "123456")
DEFAULT_VERIFY_CODE = os.environ.get("VAVA_DEFAULT_VERIFY_CODE", "123456")
ALLOW_ANY_6DIGIT_VERIFY_CODE = os.environ.get("VAVA_ALLOW_ANY_6DIGIT_VERIFY_CODE", "1").strip().lower() not in {"0", "false", "no", "off"}
DEFAULT_SSH_HOST = os.environ.get("VAVA_STATION_HOST", "vava-eth-199").strip() or "vava-eth-199"
VISIBLE_NOTICE_TYPES = {2, 4, 5, 6, 7, 8}
DEFAULT_VISIBLE_NOTICE_TYPE = 2
MESSAGE_SYNC_MIN_INTERVAL = max(
    float(os.environ.get("VAVA_MESSAGE_SYNC_MIN_INTERVAL", "30") or 30),
    5.0,
)
MESSAGE_SYNC_FULL_RESCAN_SECONDS = max(
    float(os.environ.get("VAVA_MESSAGE_SYNC_FULL_RESCAN_SECONDS", "300") or 300),
    30.0,
)
MESSAGE_SYNC_RECENT_DATES = max(
    int(os.environ.get("VAVA_MESSAGE_SYNC_RECENT_DATES", "3") or 3),
    1,
)


def now_ts() -> int:
    return int(time.time())


def iso_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def deep_get(obj, key, default=None):
    if isinstance(obj, dict):
        if key in obj:
            return obj[key]
        for variant in (key.lower(), key.upper()):
            if variant in obj:
                return obj[variant]
    return default


def ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def debug_log(message: str) -> None:
    print(f"[{iso_now()}] {message}", flush=True)


def build_route_context():
    return SimpleNamespace(
        deep_get=deep_get,
        iso_now=iso_now,
        now_ts=now_ts,
        DEFAULT_STATION_SN=DEFAULT_STATION_SN,
        DEFAULT_CAMERA_SN=DEFAULT_CAMERA_SN,
        DEFAULT_CHANNEL=DEFAULT_CHANNEL,
        DEFAULT_SESSION_KEY=DEFAULT_SESSION_KEY,
    )


@lru_cache(maxsize=1)
def _load_station_helper():
    helper_path = Path(__file__).with_name("vava_station_ctl.py")
    spec = importlib.util.spec_from_file_location("vava_station_ctl_helper", helper_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class RouteManager:
    def __init__(self, routes_dir: Path, context):
        self.routes_dir = Path(routes_dir)
        self.context = context
        self.routes_dir.mkdir(parents=True, exist_ok=True)
        self._module_paths = {
            "system_routes": self.routes_dir / "system_routes.py",
            "auth_routes": self.routes_dir / "auth_routes.py",
            "device_routes": self.routes_dir / "device_routes.py",
            "message_routes": self.routes_dir / "message_routes.py",
        }
        self._module_order = list(self._module_paths.keys())
        self._loaded = {}

    def _load_module(self, key):
        path = self._module_paths[key]
        record = self._loaded.get(key, {})
        if not path.exists():
            return record.get("module")
        try:
            mtime = path.stat().st_mtime_ns
        except OSError:
            return record.get("module")
        if record.get("module") is not None and record.get("mtime") == mtime:
            return record["module"]
        try:
            module_name = f"sunvalley_dynamic_{key}_{mtime}"
            spec = importlib.util.spec_from_file_location(module_name, path)
            module = importlib.util.module_from_spec(spec)
            assert spec and spec.loader
            spec.loader.exec_module(module)
            self._loaded[key] = {
                "module": module,
                "mtime": mtime,
                "path": str(path),
                "last_error": "",
                "loaded_at": iso_now(),
            }
            debug_log(f"routes.reload module={key} source={path}")
            return module
        except Exception as exc:
            debug_log(f"routes.reload.error module={key} source={path} error={exc!r}")
            if record.get("module") is not None:
                record["last_error"] = repr(exc)
                self._loaded[key] = record
                return record["module"]
            self._loaded[key] = {
                "module": None,
                "mtime": mtime,
                "path": str(path),
                "last_error": repr(exc),
                "loaded_at": record.get("loaded_at", ""),
            }
            return None

    def dispatch(self, handler, method, path, query, body, access_token):
        for key in self._module_order:
            module = self._load_module(key)
            if module is None:
                continue
            response = module.handle(handler, self.context, method, path, query, body, access_token)
            if response is not None:
                return response
        return None

    def snapshot(self):
        snapshot = []
        for key in self._module_order:
            self._load_module(key)
            record = self._loaded.get(key, {})
            snapshot.append(
                {
                    "module": key,
                    "path": record.get("path", str(self._module_paths[key])),
                    "loadedAt": record.get("loaded_at", ""),
                    "lastError": record.get("last_error", ""),
                }
            )
        return snapshot


class StateStore:
    def __init__(self, path: Path, db_path=None):
        self.path = path
        self.backup_path = path.with_suffix(path.suffix + ".bak")
        self.db_path = db_path or path.with_name("sunvalley_state.sqlite3")
        self.lock = threading.RLock()
        ensure_parent(self.db_path)
        self.db = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self._init_db()
        self.last_message_sync_at = 0.0
        self.last_message_sync_error = ""
        self._station_notice_cache = {}
        self.state = self._load()
        with self.lock:
            self._ensure_default_user_unlocked()
            self._sync_state_session_unlocked()
            self._save_unlocked(self.state)

    def _init_db(self):
        try:
            self.db.execute("PRAGMA journal_mode=WAL")
        except sqlite3.DatabaseError:
            pass
        self.db.execute("PRAGMA foreign_keys = ON")
        self.db.executescript(
            """
            CREATE TABLE IF NOT EXISTS kv_state (
                key TEXT PRIMARY KEY,
                value_json TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS users (
                user_id TEXT PRIMARY KEY,
                username TEXT NOT NULL UNIQUE,
                email TEXT UNIQUE,
                mobile TEXT UNIQUE,
                password_hash TEXT NOT NULL,
                password_md5 TEXT NOT NULL DEFAULT '',
                nickname TEXT NOT NULL DEFAULT '',
                avatar TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS auth_tokens (
                access_token TEXT PRIMARY KEY,
                refresh_token TEXT NOT NULL UNIQUE,
                user_id TEXT NOT NULL,
                expires_in TEXT NOT NULL,
                token_type TEXT NOT NULL,
                scope TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(user_id) REFERENCES users(user_id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_auth_tokens_user_id
            ON auth_tokens(user_id);

            CREATE TABLE IF NOT EXISTS verification_codes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                target TEXT NOT NULL,
                purpose TEXT NOT NULL,
                channel TEXT NOT NULL,
                code TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_verification_codes_target_purpose
            ON verification_codes(target, purpose, id DESC);
            """
        )
        user_columns = {
            row["name"] for row in self.db.execute("PRAGMA table_info(users)").fetchall()
        }
        if "password_md5" not in user_columns:
            self.db.execute("ALTER TABLE users ADD COLUMN password_md5 TEXT NOT NULL DEFAULT ''")
        self.db.commit()

    def _normalize_email(self, value):
        return str(value or "").strip().lower()

    def _normalize_mobile(self, value):
        return str(value or "").strip()

    def _password_md5(self, password):
        return hashlib.md5(str(password or "").encode("utf-8")).hexdigest()

    def _looks_like_md5(self, value):
        value = str(value or "").strip().lower()
        return len(value) == 32 and all(ch in "0123456789abcdef" for ch in value)

    def _looks_like_test_verify_code(self, value):
        value = str(value or "").strip()
        return len(value) == 6 and value.isdigit()

    def _canonical_username(self, username="", email="", mobile=""):
        username = str(username or "").strip()
        email = self._normalize_email(email)
        mobile = self._normalize_mobile(mobile)
        return username or email or mobile

    def _hash_password(self, password, salt=None):
        salt = salt or secrets.token_hex(8)
        raw = f"{salt}:{password or ''}".encode("utf-8")
        return f"sha256${salt}${hashlib.sha256(raw).hexdigest()}"

    def _verify_password(self, password, password_hash, password_md5=""):
        password = str(password or "")
        password_md5 = str(password_md5 or "").strip().lower()
        if password_md5:
            if self._looks_like_md5(password):
                return secrets.compare_digest(password.lower(), password_md5)
            return secrets.compare_digest(self._password_md5(password), password_md5)
        password_hash = str(password_hash or "")
        if password_hash.startswith("sha256$"):
            _, salt, digest = password_hash.split("$", 2)
            return secrets.compare_digest(
                self._hash_password(password, salt),
                f"sha256${salt}${digest}",
            )
        if password_hash.startswith("plain$"):
            return secrets.compare_digest(str(password or ""), password_hash.split("$", 1)[1])
        return secrets.compare_digest(str(password or ""), password_hash)

    def _state_user_from_row(self, row):
        if not row:
            return {}
        email = row["email"] or ""
        username = row["username"] or email
        return {
            "userid": row["user_id"],
            "username": username,
            "nickname": row["nickname"] or username,
            "avatar": row["avatar"] or "",
            "email": email or username,
            "mobile": row["mobile"] or "",
        }

    def _state_auth_from_row(self, row):
        if not row:
            return {}
        return {
            "access_token": row["access_token"],
            "refresh_token": row["refresh_token"],
            "expires_in": row["expires_in"],
            "token_type": row["token_type"],
            "scope": row["scope"],
        }

    def _set_active_session_unlocked(self, user_row, auth_row):
        self.state["user"] = self._state_user_from_row(user_row)
        self.state["auth"] = self._state_auth_from_row(auth_row)

    def _find_user_row_unlocked(self, user_id="", identifier=""):
        if user_id:
            return self.db.execute(
                "SELECT * FROM users WHERE user_id = ?",
                (str(user_id),),
            ).fetchone()
        identifier = str(identifier or "").strip()
        if not identifier:
            return None
        email = self._normalize_email(identifier)
        mobile = self._normalize_mobile(identifier)
        return self.db.execute(
            """
            SELECT * FROM users
            WHERE username = ?
               OR email = ?
               OR mobile = ?
            LIMIT 1
            """,
            (identifier, email, mobile),
        ).fetchone()

    def _find_auth_row_by_access_token_unlocked(self, access_token):
        access_token = str(access_token or "").strip()
        if not access_token:
            return None
        return self.db.execute(
            "SELECT * FROM auth_tokens WHERE access_token = ?",
            (access_token,),
        ).fetchone()

    def _find_auth_row_by_refresh_token_unlocked(self, refresh_token):
        refresh_token = str(refresh_token or "").strip()
        if not refresh_token:
            return None
        return self.db.execute(
            "SELECT * FROM auth_tokens WHERE refresh_token = ?",
            (refresh_token,),
        ).fetchone()

    def _find_latest_auth_row_for_user_unlocked(self, user_id):
        return self.db.execute(
            """
            SELECT * FROM auth_tokens
            WHERE user_id = ?
            ORDER BY updated_at DESC, rowid DESC
            LIMIT 1
            """,
            (str(user_id or ""),),
        ).fetchone()

    def _upsert_user_unlocked(
        self,
        *,
        user_id,
        username="",
        email="",
        mobile="",
        password_hash="",
        password_md5="",
        nickname="",
        avatar="",
    ):
        username = self._canonical_username(username, email, mobile)
        email = self._normalize_email(email)
        mobile = self._normalize_mobile(mobile)
        existing = self._find_user_row_unlocked(user_id=user_id) or self._find_user_row_unlocked(identifier=username)
        created_at = existing["created_at"] if existing else iso_now()
        updated_at = iso_now()
        effective_password_hash = password_hash or (
            existing["password_hash"] if existing else self._hash_password(DEFAULT_PASSWORD)
        )
        effective_password_md5 = (
            str(password_md5 or "").strip().lower()
            or (existing["password_md5"] if existing and "password_md5" in existing.keys() else "")
            or self._password_md5(DEFAULT_PASSWORD)
        )
        self.db.execute(
            """
            INSERT INTO users (
                user_id, username, email, mobile, password_hash, password_md5, nickname, avatar, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                username = excluded.username,
                email = excluded.email,
                mobile = excluded.mobile,
                password_hash = excluded.password_hash,
                password_md5 = excluded.password_md5,
                nickname = excluded.nickname,
                avatar = excluded.avatar,
                updated_at = excluded.updated_at
            """,
            (
                user_id,
                username,
                email or None,
                mobile or None,
                effective_password_hash,
                effective_password_md5,
                nickname or username,
                avatar or "",
                created_at,
                updated_at,
            ),
        )
        self.db.commit()
        return self._find_user_row_unlocked(user_id=user_id)

    def _issue_tokens_for_user_unlocked(
        self,
        user_id,
        access_token="",
        refresh_token="",
        expires_in="31536000",
        token_type="bearer",
        scope="all",
    ):
        access_token = str(access_token or "").strip() or f"access-{secrets.token_hex(16)}"
        refresh_token = str(refresh_token or "").strip() or f"refresh-{secrets.token_hex(16)}"
        now = iso_now()
        self.db.execute("DELETE FROM auth_tokens WHERE refresh_token = ? AND access_token != ?", (refresh_token, access_token))
        self.db.execute(
            """
            INSERT OR REPLACE INTO auth_tokens (
                access_token, refresh_token, user_id, expires_in, token_type, scope, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                access_token,
                refresh_token,
                user_id,
                str(expires_in or "31536000"),
                str(token_type or "bearer"),
                str(scope or "all"),
                now,
                now,
            ),
        )
        self.db.commit()
        return self._find_auth_row_by_access_token_unlocked(access_token)

    def _ensure_default_user_unlocked(self):
        user = self.state.get("user", {}) or {}
        auth = self.state.get("auth", {}) or {}
        user_id = str(user.get("userid") or DEFAULT_USER_ID)
        username = self._canonical_username(user.get("username"), user.get("email"), user.get("mobile"))
        email = self._normalize_email(user.get("email") or username or DEFAULT_EMAIL)
        mobile = self._normalize_mobile(user.get("mobile"))
        nickname = user.get("nickname") or "Local VAVA"
        avatar = user.get("avatar") or ""
        user_row = self._find_user_row_unlocked(user_id=user_id) or self._find_user_row_unlocked(identifier=username)
        if not user_row:
            user_row = self._upsert_user_unlocked(
                user_id=user_id,
                username=username or email or DEFAULT_EMAIL,
                email=email or DEFAULT_EMAIL,
                mobile=mobile,
                nickname=nickname,
                avatar=avatar,
                password_hash=self._hash_password(DEFAULT_PASSWORD),
                password_md5=self._password_md5(DEFAULT_PASSWORD),
            )
        elif not str(user_row["password_md5"] or "").strip():
            user_row = self._upsert_user_unlocked(
                user_id=user_row["user_id"],
                username=user_row["username"],
                email=user_row["email"] or "",
                mobile=user_row["mobile"] or "",
                password_hash=user_row["password_hash"],
                password_md5=self._password_md5(DEFAULT_PASSWORD),
                nickname=user_row["nickname"] or nickname,
                avatar=user_row["avatar"] or avatar,
            )
        access_token = auth.get("access_token") or DEFAULT_ACCESS_TOKEN
        refresh_token = auth.get("refresh_token") or DEFAULT_REFRESH_TOKEN
        auth_row = self._find_auth_row_by_access_token_unlocked(access_token)
        if not auth_row:
            auth_row = self._issue_tokens_for_user_unlocked(
                user_row["user_id"],
                access_token=access_token,
                refresh_token=refresh_token,
                expires_in=auth.get("expires_in", "31536000"),
                token_type=auth.get("token_type", "bearer"),
                scope=auth.get("scope", "all"),
            )
        self._set_active_session_unlocked(user_row, auth_row)

    def _sync_state_session_unlocked(self, access_token="", refresh_token="", user_id=""):
        auth_row = None
        user_row = None
        access_token = access_token or deep_get(self.state.get("auth", {}), "access_token", "")
        refresh_token = refresh_token or deep_get(self.state.get("auth", {}), "refresh_token", "")
        user_id = user_id or deep_get(self.state.get("user", {}), "userid", "")
        if access_token:
            auth_row = self._find_auth_row_by_access_token_unlocked(access_token)
        if not auth_row and refresh_token:
            auth_row = self._find_auth_row_by_refresh_token_unlocked(refresh_token)
        if auth_row:
            user_row = self._find_user_row_unlocked(user_id=auth_row["user_id"])
        if not user_row and user_id:
            user_row = self._find_user_row_unlocked(user_id=user_id)
        if not user_row:
            user_row = self.db.execute(
                "SELECT * FROM users ORDER BY updated_at DESC, rowid DESC LIMIT 1"
            ).fetchone()
        if user_row and not auth_row:
            auth_row = self._find_latest_auth_row_for_user_unlocked(user_row["user_id"])
        if user_row and not auth_row:
            auth_row = self._issue_tokens_for_user_unlocked(
                user_row["user_id"],
                access_token=DEFAULT_ACCESS_TOKEN,
                refresh_token=DEFAULT_REFRESH_TOKEN,
            )
        if user_row and auth_row:
            self._set_active_session_unlocked(user_row, auth_row)

    def _default_state(self):
        station = self._build_station(
            station_sn=DEFAULT_STATION_SN,
            station_name=DEFAULT_STATION_NAME,
        )
        camera = self._build_camera(
            camera_sn=DEFAULT_CAMERA_SN,
            camera_name=DEFAULT_CAMERA_NAME,
            station_sn=DEFAULT_STATION_SN,
            channel=DEFAULT_CHANNEL,
        )
        station["cameraTotal"] = 1
        return {
            "meta": {
                "createdAt": iso_now(),
                "updatedAt": iso_now(),
            },
            "auth": {
                "access_token": DEFAULT_ACCESS_TOKEN,
                "refresh_token": DEFAULT_REFRESH_TOKEN,
                "expires_in": "31536000",
                "token_type": "bearer",
                "scope": "all",
            },
            "user": {
                "userid": DEFAULT_USER_ID,
                "username": DEFAULT_EMAIL,
                "nickname": "Local VAVA",
                "avatar": "",
                "email": DEFAULT_EMAIL,
            },
            "stations": {
                DEFAULT_STATION_SN: station,
            },
            "cameras": {
                DEFAULT_CAMERA_SN: camera,
            },
            "requests": [],
            "events": [],
            "messages": [],
            "messageCounter": 1,
        }

    def _build_station(self, station_sn: str, station_name: str):
        return {
            "deviceSn": station_sn,
            "deviceName": station_name,
            "cameraTotal": 0,
            "bindId": f"bind-station-{station_sn}",
            "stationSn": station_sn,
            "stationName": station_name,
            "stationOnline": "1",
            "addTime": iso_now(),
            "shareFlag": 0,
            "shareTime": "",
            "sharerName": "",
            "did": {
                "didCode": DEFAULT_DID,
                "initCode": DEFAULT_INIT,
                "crcKey": DEFAULT_CRC,
                "syDid": DEFAULT_DID,
                "initString": DEFAULT_INIT,
            },
            "statusObject": {
                "buzzer": 0,
                "freesize": 8025,
                "nas_freesize": 0,
                "nas_totolsize": 0,
                "nas_usedsize": 0,
                "nasstatus": 0,
                "sdstatus": 1,
                "session": 0,
                "time": iso_now(),
                "timestamp": now_ts(),
                "totolsize": 8026,
                "upstatus": 0,
                "usedsize": 1,
            },
            "attrObject": {
                "hardver": "VA-GW02",
                "softver": "V3.0.0.61 V1",
                "f_appversionout": "020104",
                "f_appversionin": "020104",
                "f_code": "9c33e08830c243c597246c71e3c2f458",
                "f_secret": "237e61fdc48a46908736c499685e9f34",
                "f_firmnum": "P020201",
                "h_appversionin": "010000",
                "h_appversionout": "010000",
                "h_code": "84ae6a434949d557c9cb0a62a6ded241",
                "h_devicenum": "P020201",
                "h_secret": "951c76f433735365cdf6cee988f4bb58",
            },
            "permissionObject": {
                "shareFlag": 0,
                "realtimeVideo": 1,
                "msgAndPlayback": 1,
            },
        }

    def _build_camera(self, camera_sn: str, camera_name: str, station_sn: str, channel: int):
        return {
            "bindId": f"bind-camera-{camera_sn}",
            "cameraSn": camera_sn,
            "cameraName": camera_name,
            "stationSn": station_sn,
            "channel": channel,
            "addTime": iso_now(),
            "shareFlag": 0,
            "shareTime": "",
            "sharerName": "",
            "stationOnline": "1",
            "pending": False,
            "statusObject": {
                "armingstatus": 1,
                "channel": channel,
                "enable": 1,
                "lever": 81,
                "online": 1,
                "powermode": 0,
                "signal": -30,
                "time": iso_now(),
                "timestamp": now_ts(),
                "upstatus": 0,
                "video": 1,
                "voltage": 4031,
                "wakeup": 0,
            },
            "attrObject": {
                "channel": channel,
                "hardver": "VA-HS03",
                "softver": "1.4.0.54",
                "f_appversionout": "010454",
                "f_firmnum": "VA-HS03",
                "mac": "",
                "irmode": 1,
                "pirsensitivity": 2,
                "micstatus": 1,
                "speakerstatus": 1,
                "m_res": 2,
                "m_fps": 15,
                "m_bitrate": 400,
                "s_res": 0,
                "s_fps": 15,
                "s_bitrate": 1000,
                "audiochannel": 1,
                "audiocodec": 3,
                "audiorate": 8000,
                "audioframerate": 8,
                "audiobitper": 16,
                "videocodec": 0,
                "mirrormode": 0,
            },
            "settingsObject": {
                "mailSwitch": 1,
                "pushSwitch": 1,
                "voiceSwitch": 1,
            },
        }

    def _normalize_station_hardver(self, hardver: str) -> str:
        mapping = {
            "VA-HS002": "VA-GW01",
            "VA-HS003": "VA-GW02",
            "VA-HS004": "VA-GW04",
        }
        return mapping.get(hardver, hardver or "VA-GW02")

    def _normalize_station_model(self, hardver: str, fallback: str = "") -> str:
        mapping = {
            "VA-GW01": "VA-HS002",
            "VA-GW02": "P020201",
            "VA-GW04": "VA-HS004",
        }
        legacy_ui_model = {"VA-HS003"}
        if fallback and fallback not in legacy_ui_model:
            return fallback
        return mapping.get(hardver, hardver or "P020201")

    def _normalize_camera_hardver(self, hardver: str) -> str:
        mapping = {
            "VA-CM002": "VA-HS02",
            "VA-CM003": "VA-HS03",
            "VA-CM004": "VA-HS04",
            "VA-HS002": "VA-HS02",
            "VA-HS003": "VA-HS03",
            "VA-HS004": "VA-HS04",
        }
        return mapping.get(hardver, hardver or "VA-HS03")

    def _normalize_station_record(self, station_sn: str, station: dict) -> bool:
        changed = False
        station.setdefault("deviceSn", station_sn)
        station.setdefault("stationSn", station_sn)
        station.setdefault("deviceName", station.get("stationName") or station_sn)
        station.setdefault("stationName", station.get("deviceName") or station_sn)
        station.setdefault("bindId", f"bind-station-{station_sn}")
        station.setdefault("stationOnline", "1")
        station.setdefault("shareFlag", 0)
        station.setdefault("shareTime", "")
        station.setdefault("sharerName", "")
        station.setdefault("addTime", iso_now())
        station.setdefault(
            "permissionObject",
            {
                "shareFlag": 0,
                "realtimeVideo": 1,
                "msgAndPlayback": 1,
            },
        )
        permission = station.setdefault("permissionObject", {})
        permission.setdefault("shareFlag", 0)
        permission.setdefault("realtimeVideo", 1)
        permission.setdefault("msgAndPlayback", 1)
        station.setdefault(
            "did",
            {
                "didCode": DEFAULT_DID,
                "initCode": DEFAULT_INIT,
                "crcKey": DEFAULT_CRC,
                "syDid": DEFAULT_DID,
                "initString": DEFAULT_INIT,
            },
        )
        did = station.setdefault("did", {})
        did_defaults = {
            "didCode": DEFAULT_DID,
            "initCode": DEFAULT_INIT,
            "crcKey": DEFAULT_CRC,
            "syDid": DEFAULT_DID,
            "initString": DEFAULT_INIT,
        }
        for key, value in did_defaults.items():
            if not did.get(key):
                did[key] = value
                changed = True
        if did.get("didCode") == "LOCAL-VAVA-DID":
            did["didCode"] = DEFAULT_DID
            changed = True
        if did.get("syDid") == "LOCAL-VAVA-DID":
            did["syDid"] = DEFAULT_DID
            changed = True
        if did.get("initCode") == "LOCAL-VAVA-INIT":
            did["initCode"] = DEFAULT_INIT
            changed = True
        if did.get("initString") == "LOCAL-VAVA-INIT":
            did["initString"] = DEFAULT_INIT
            changed = True
        station.setdefault(
            "statusObject",
            {
                "buzzer": 0,
                "freesize": 8025,
                "nas_freesize": 0,
                "nas_totolsize": 0,
                "nas_usedsize": 0,
                "nasstatus": 0,
                "sdstatus": 1,
                "session": 0,
                "time": iso_now(),
                "timestamp": now_ts(),
                "totolsize": 8026,
                "upstatus": 0,
                "usedsize": 1,
            },
        )
        attr = station.setdefault("attrObject", {})
        original_hardver = attr.get("hardver", "")
        hardver = self._normalize_station_hardver(original_hardver)
        if original_hardver != hardver:
            attr["hardver"] = hardver
            changed = True
        model = self._normalize_station_model(hardver, attr.get("f_firmnum", ""))
        defaults = {
            "softver": "V3.0.0.61 V1",
            "f_appversionout": "020104",
            "f_appversionin": "020104",
            "f_firmnum": model,
            "h_appversionout": "010000",
            "h_appversionin": "010000",
            "h_devicenum": model,
        }
        for key, value in defaults.items():
            if not attr.get(key):
                attr[key] = value
                changed = True
        return changed

    def _normalize_camera_record(self, camera_sn: str, camera: dict) -> bool:
        changed = False
        camera.setdefault("cameraSn", camera_sn)
        camera.setdefault("cameraName", camera_sn)
        camera.setdefault("bindId", f"bind-camera-{camera_sn}")
        camera.setdefault("stationSn", DEFAULT_STATION_SN)
        camera.setdefault("channel", 0)
        camera.setdefault("addTime", iso_now())
        camera.setdefault("shareFlag", 0)
        camera.setdefault("shareTime", "")
        camera.setdefault("sharerName", "")
        camera.setdefault("stationOnline", "1")
        camera.setdefault("pending", False)
        camera.setdefault(
            "statusObject",
            {
                "armingstatus": 1,
                "channel": int(camera.get("channel", 0)),
                "enable": 1,
                "lever": 81,
                "online": 1,
                "powermode": 0,
                "signal": -30,
                "time": iso_now(),
                "timestamp": now_ts(),
                "upstatus": 0,
                "video": 1,
                "voltage": 4031,
                "wakeup": 0,
            },
        )
        attr = camera.setdefault("attrObject", {})
        original_hardver = attr.get("hardver", "")
        hardver = self._normalize_camera_hardver(original_hardver)
        if original_hardver != hardver:
            attr["hardver"] = hardver
            changed = True
        defaults = {
            "channel": int(camera.get("channel", 0)),
            "softver": "1.4.0.54",
            "f_appversionout": "010454",
            "f_firmnum": hardver,
            "mac": "",
            "irmode": 1,
            "pirsensitivity": 2,
            "micstatus": 1,
            "speakerstatus": 1,
            "m_res": 2,
            "m_fps": 15,
            "m_bitrate": 400,
            "s_res": 0,
            "s_fps": 15,
            "s_bitrate": 1000,
            "audiochannel": 1,
            "audiocodec": 3,
            "audiorate": 8000,
            "audioframerate": 8,
            "audiobitper": 16,
            "videocodec": 0,
            "mirrormode": 0,
        }
        for key, value in defaults.items():
            if attr.get(key) in (None, ""):
                attr[key] = value
                changed = True
        return changed

    def _notice_type_from_ext(self, source_notice_type, ext):
        try:
            source_notice_type = int(source_notice_type or 0)
        except (TypeError, ValueError):
            source_notice_type = 0
        try:
            file_type = int(deep_get(ext, "fileType", 0) or 0)
        except (TypeError, ValueError):
            file_type = 0
        if source_notice_type in VISIBLE_NOTICE_TYPES:
            return source_notice_type
        if file_type in VISIBLE_NOTICE_TYPES:
            return file_type
        if source_notice_type == 1:
            return 5 if file_type == 5 else DEFAULT_VISIBLE_NOTICE_TYPE
        return DEFAULT_VISIBLE_NOTICE_TYPE

    def _notice_title(self, notice_type):
        if int(notice_type or 0) in VISIBLE_NOTICE_TYPES:
            return "Motion detected"
        return f"Notice {notice_type}"

    def _parse_notice_add_timestamp_ms(self, device_time, raw_timestamp):
        try:
            value = int(raw_timestamp or 0)
            if value >= 10**12:
                return value
            if value > 0:
                return value * 1000
        except (TypeError, ValueError):
            pass
        if device_time and len(device_time) == 19:
            try:
                dt = datetime.strptime(device_time, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
                return int(dt.timestamp() * 1000)
            except ValueError:
                pass
        return now_ts() * 1000

    def _message_key(self, message):
        ext = message.get("extObject", {}) or {}
        return (
            str(message.get("deviceSn", "") or ""),
            str(deep_get(ext, "fileDate", "") or ""),
            str(deep_get(ext, "fileName", "") or ""),
        )

    def _normalize_message_record_unlocked(self, message, state=None):
        changed = False
        state = state or self.state
        ext = message.setdefault("extObject", {})
        camera_sn = str(message.get("deviceSn", "") or "")
        camera = state.get("cameras", {}).get(camera_sn, {})
        source_notice_type = int(message.get("sourceNoticeType", message.get("noticeType", 1)) or 1)
        visible_notice_type = self._notice_type_from_ext(source_notice_type, ext)
        if message.get("sourceNoticeType") != source_notice_type:
            message["sourceNoticeType"] = source_notice_type
            changed = True
        # The app's notification list only loads preview JPG thumbnails when
        # noticeType stays as the original source type (camera motion == 1).
        if int(message.get("noticeType", 0) or 0) != source_notice_type:
            message["noticeType"] = source_notice_type
            changed = True
        if int(message.get("visibleNoticeType", 0) or 0) != visible_notice_type:
            message["visibleNoticeType"] = visible_notice_type
            changed = True
        device_name = message.get("deviceName") or camera.get("cameraName") or camera_sn
        if message.get("deviceName") != device_name:
            message["deviceName"] = device_name
            changed = True
        station_sn = message.get("parentDeviceSn") or camera.get("stationSn", DEFAULT_STATION_SN)
        if message.get("parentDeviceSn") != station_sn:
            message["parentDeviceSn"] = station_sn
            changed = True
        title = message.get("title") or self._notice_title(visible_notice_type)
        normalized_title = self._notice_title(visible_notice_type) if str(title).startswith("Notice ") else title
        if message.get("title") != normalized_title:
            message["title"] = normalized_title
            changed = True
        if "content" not in message:
            message["content"] = ""
            changed = True
        permission = message.setdefault("permissionJson", {})
        for key, value in {"msgAndPlayback": 1, "realtimeVideo": 1, "shareFlag": 0}.items():
            if permission.get(key) != value:
                permission[key] = value
                changed = True
        message_share_flag = int(message.get("shareFlag", permission.get("shareFlag", 0)) or 0)
        if int(message.get("shareFlag", -1) or -1) != message_share_flag:
            message["shareFlag"] = message_share_flag
            changed = True
        settings = camera.get("settingsObject", {})
        default_mail = int(settings.get("mailSwitch", 1) or 1)
        default_msg = int(settings.get("pushSwitch", 1) or 1)
        defaults = {
            "channel": str(deep_get(ext, "channel", camera.get("channel", 0))),
            "deviceName": device_name,
            "deviceTime": self._format_notice_device_time(
                deep_get(ext, "deviceTime", message.get("deviceTime", ""))
            ),
            "duration": int(deep_get(ext, "duration", 10) or 10),
            "fileDate": str(deep_get(ext, "fileDate", "") or ""),
            "fileName": str(deep_get(ext, "fileName", "") or ""),
            "fileType": int(deep_get(ext, "fileType", 5) or 5),
            "mailSwitch": int(deep_get(ext, "mailSwitch", default_mail) or default_mail),
            "msg": str(deep_get(ext, "msg", message.get("content", "")) or ""),
            "msgSwitch": int(deep_get(ext, "msgSwitch", default_msg) or default_msg),
            "timestamp": str(deep_get(ext, "timestamp", now_ts()) or now_ts()),
            "timezone": str(deep_get(ext, "timezone", "8") or "8"),
            "timezoneex": int(deep_get(ext, "timezoneex", 9999) or 9999),
        }
        for key, value in defaults.items():
            if ext.get(key) != value:
                ext[key] = value
                changed = True
        device_time = ext.get("deviceTime", "")
        if message.get("deviceTime") != device_time:
            message["deviceTime"] = device_time
            changed = True
        add_timestamp = self._parse_notice_add_timestamp_ms(device_time, ext.get("timestamp"))
        if int(message.get("addTimestamp", 0) or 0) != add_timestamp:
            message["addTimestamp"] = add_timestamp
            changed = True
        if "noticeStatus" not in message:
            message["noticeStatus"] = 0
            changed = True
        if "sharerName" not in message:
            message["sharerName"] = ""
            changed = True
        return changed

    def _export_share_metadata_unlocked(self, payload):
        share_flag = int(payload.get("shareFlag", 0) or 0)
        payload["shareFlag"] = share_flag
        if share_flag == 0:
            if not str(payload.get("sharerName", "") or "").strip():
                payload.pop("sharerName", None)
            if not str(payload.get("shareTime", "") or "").strip():
                payload.pop("shareTime", None)
        return payload

    def _normalize_state_unlocked(self, state: dict) -> bool:
        changed = False
        meta = state.setdefault("meta", {})
        if "createdAt" not in meta:
            meta["createdAt"] = iso_now()
            changed = True
        if "updatedAt" not in meta:
            meta["updatedAt"] = iso_now()
            changed = True
        state.setdefault("requests", [])
        state.setdefault("events", [])
        state.setdefault("messages", [])
        state.setdefault("messageCounter", 1)
        state.setdefault("pushTokens", [])
        if not state.get("pushToken"):
            for request in reversed(state.get("requests", [])):
                if request.get("path") != "/ipc/msg/push/report-token":
                    continue
                push_token = str(deep_get(request.get("body", {}), "pushToken", "") or "").strip()
                if push_token:
                    state["pushToken"] = push_token
                    if push_token not in state["pushTokens"]:
                        state["pushTokens"].append(push_token)
                    changed = True
                    break
        for station_sn, station in state.setdefault("stations", {}).items():
            changed = self._normalize_station_record(station_sn, station) or changed
        for camera_sn, camera in state.setdefault("cameras", {}).items():
            changed = self._normalize_camera_record(camera_sn, camera) or changed
        max_notice_id = 0
        for message in state.get("messages", []):
            changed = self._normalize_message_record_unlocked(message, state) or changed
            max_notice_id = max(max_notice_id, int(message.get("noticeId", 0) or 0))
        desired_counter = max(max_notice_id + 1, int(state.get("messageCounter", 1) or 1))
        if state.get("messageCounter") != desired_counter:
            state["messageCounter"] = desired_counter
            changed = True
        for station_sn, station in state.get("stations", {}).items():
            count = len(
                [
                    camera
                    for camera in state.get("cameras", {}).values()
                    if camera.get("stationSn") == station_sn
                ]
            )
            if station.get("cameraTotal") != count:
                station["cameraTotal"] = count
                changed = True
        return changed

    def _load(self):
        state = self._load_state_from_db_unlocked()
        if state is not None:
            if self._normalize_state_unlocked(state):
                self._save_unlocked(state)
            return state
        candidates = [self.path, self.backup_path]
        for candidate in candidates:
            if not candidate.exists():
                continue
            try:
                with candidate.open("r", encoding="utf-8") as fh:
                    state = json.load(fh)
                if self._normalize_state_unlocked(state):
                    self._save_unlocked(state)
                return state
            except json.JSONDecodeError as exc:
                broken = candidate.with_suffix(candidate.suffix + f".broken-{int(time.time())}")
                try:
                    candidate.replace(broken)
                    debug_log(f"state file {candidate} was invalid JSON; moved to {broken}: {exc}")
                except OSError:
                    debug_log(f"state file {candidate} was invalid JSON and could not be moved: {exc}")
        state = self._default_state()
        self._normalize_state_unlocked(state)
        self._save_unlocked(state)
        return state

    def _load_state_from_db_unlocked(self):
        rows = self.db.execute(
            "SELECT key, value_json FROM kv_state ORDER BY key"
        ).fetchall()
        if not rows:
            return None
        state = {}
        for row in rows:
            try:
                state[row["key"]] = json.loads(row["value_json"])
            except json.JSONDecodeError as exc:
                debug_log(f"sqlite state row {row['key']} was invalid JSON: {exc}")
                return None
        return state

    def _save_state_to_db_unlocked(self, state):
        updated_at = iso_now()
        rows = [
            (
                str(key),
                json.dumps(value, ensure_ascii=False, sort_keys=True),
                updated_at,
            )
            for key, value in state.items()
        ]
        self.db.execute("DELETE FROM kv_state")
        self.db.executemany(
            "INSERT INTO kv_state (key, value_json, updated_at) VALUES (?, ?, ?)",
            rows,
        )
        self.db.commit()

    def _write_json_snapshot_unlocked(self, state):
        ensure_parent(self.path)
        payload = json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True)
        tmp_path = self.path.with_suffix(self.path.suffix + ".tmp")
        with tmp_path.open("w", encoding="utf-8") as fh:
            fh.write(payload)
        os.replace(tmp_path, self.path)
        with self.backup_path.open("w", encoding="utf-8") as fh:
            fh.write(payload)

    def _save_unlocked(self, state):
        state["meta"]["updatedAt"] = iso_now()
        self._save_state_to_db_unlocked(state)
        self._write_json_snapshot_unlocked(state)

    def _json_safe(self, value, seen=None, depth=0):
        if seen is None:
            seen = set()
        if value is None or isinstance(value, (str, int, float, bool)):
            return value
        if depth >= 8:
            return "<max-depth>"
        if isinstance(value, bytes):
            return f"<bytes:{len(value)}>"

        obj_id = id(value)
        if obj_id in seen:
            return "<circular-ref>"

        if isinstance(value, dict):
            seen.add(obj_id)
            out = {}
            for key, item in value.items():
                out[str(key)] = self._json_safe(item, seen, depth + 1)
            seen.remove(obj_id)
            return out

        if isinstance(value, (list, tuple, set)):
            seen.add(obj_id)
            out = [self._json_safe(item, seen, depth + 1) for item in list(value)[:200]]
            seen.remove(obj_id)
            return out

        return repr(value)

    def save(self):
        with self.lock:
            self._normalize_state_unlocked(self.state)
            self._save_unlocked(self.state)

    def record_request(self, entry, response=None):
        with self.lock:
            entry = self._json_safe(entry)
            if response is not None:
                if response is self.state:
                    response = {
                        "_omitted": "state snapshot omitted from request log",
                        "stations": list(self.state.get("stations", {}).keys()),
                        "cameras": list(self.state.get("cameras", {}).keys()),
                    }
                entry["response"] = self._json_safe(response)
            requests = self.state.setdefault("requests", [])
            requests.append(entry)
            del requests[:-200]
            self._save_unlocked(self.state)

    def add_event(self, message, payload=None):
        with self.lock:
            events = self.state.setdefault("events", [])
            events.append(
                {
                    "ts": iso_now(),
                    "message": message,
                    "payload": payload or {},
                }
            )
            del events[:-200]
            self._save_unlocked(self.state)

    def set_station(self, station_sn, station_name=None):
        with self.lock:
            stations = self.state.setdefault("stations", {})
            station = stations.get(station_sn)
            if not station:
                station = self._build_station(station_sn, station_name or station_sn)
                stations[station_sn] = station
            if station_name:
                station["deviceName"] = station_name
                station["stationName"] = station_name
            station["statusObject"]["timestamp"] = now_ts()
            station["statusObject"]["time"] = iso_now()
            self._normalize_station_record(station_sn, station)
            self._save_unlocked(self.state)
            return station

    def ensure_camera(self, camera_sn, station_sn, channel, camera_name=None, pending=False):
        with self.lock:
            self.state.setdefault("cameraTombstones", [])
            key = str(camera_sn or "").strip()
            if key:
                self.state["cameraTombstones"] = [
                    item for item in self.state["cameraTombstones"] if str(item or "").strip() != key
                ]
            cameras = self.state.setdefault("cameras", {})
            camera = cameras.get(camera_sn)
            if not camera:
                camera = self._build_camera(
                    camera_sn=camera_sn,
                    camera_name=camera_name or camera_sn,
                    station_sn=station_sn,
                    channel=channel,
                )
                cameras[camera_sn] = camera
            camera["stationSn"] = station_sn
            camera["channel"] = channel
            if camera_name:
                camera["cameraName"] = camera_name
            camera["pending"] = pending
            camera["statusObject"]["channel"] = channel
            camera["statusObject"]["timestamp"] = now_ts()
            camera["statusObject"]["time"] = iso_now()
            self._normalize_camera_record(camera_sn, camera)
            station = self.state.setdefault("stations", {}).setdefault(
                station_sn,
                self._build_station(station_sn, station_sn),
            )
            self._normalize_station_record(station_sn, station)
            station["cameraTotal"] = len(
                [c for c in cameras.values() if c.get("stationSn") == station_sn]
            )
            self._save_unlocked(self.state)
            return camera

    def mark_camera_bound(self, camera_sn, camera_name=None):
        with self.lock:
            camera = self.state.setdefault("cameras", {}).get(camera_sn)
            if not camera:
                return None
            if camera_name:
                camera["cameraName"] = camera_name
            camera["pending"] = False
            camera["statusObject"]["timestamp"] = now_ts()
            camera["statusObject"]["time"] = iso_now()
            self._save_unlocked(self.state)
            return camera

    def remove_station(self, station_sn):
        with self.lock:
            stations = self.state.setdefault("stations", {})
            station = stations.pop(station_sn, None)
            if not station:
                return None
            cameras = self.state.setdefault("cameras", {})
            removed_cameras = []
            for camera_sn in list(cameras.keys()):
                camera = cameras.get(camera_sn, {})
                if camera.get("stationSn") != station_sn:
                    continue
                removed_cameras.append(camera_sn)
                key = str(camera_sn or "").strip()
                if key and key not in self.state.setdefault("cameraTombstones", []):
                    self.state["cameraTombstones"].append(key)
                cameras.pop(camera_sn, None)
            self._save_unlocked(self.state)
            return {
                "station": station,
                "removedCameras": removed_cameras,
            }

    def remove_camera(self, camera_sn):
        with self.lock:
            cameras = self.state.setdefault("cameras", {})
            camera = cameras.pop(camera_sn, None)
            if not camera:
                return None
            key = str(camera_sn or "").strip()
            if key and key not in self.state.setdefault("cameraTombstones", []):
                self.state["cameraTombstones"].append(key)
            station_sn = camera.get("stationSn", "")
            if station_sn:
                station = self.state.setdefault("stations", {}).get(station_sn)
                if station:
                    station["cameraTotal"] = len(
                        [c for c in cameras.values() if c.get("stationSn") == station_sn]
                    )
                    station["statusObject"]["timestamp"] = now_ts()
                    station["statusObject"]["time"] = iso_now()
            self._save_unlocked(self.state)
            return camera

    def update_camera_settings(self, camera_sn, body):
        with self.lock:
            camera = self.state.setdefault("cameras", {}).get(camera_sn)
            if not camera:
                return None
            settings = camera.setdefault("settingsObject", {})
            mapping = {
                "mailSwitch": "mailSwitch",
                "pushSwitch": "pushSwitch",
                "voiceSwitch": "voiceSwitch",
            }
            for src, dst in mapping.items():
                value = deep_get(body, src)
                if value is not None:
                    settings[dst] = value
            name = deep_get(body, "cameraName")
            if name:
                camera["cameraName"] = name
            self._save_unlocked(self.state)
            return camera

    def update_station_status(self, station_sn, body):
        with self.lock:
            station = self.state.setdefault("stations", {}).setdefault(
                station_sn,
                self._build_station(station_sn, station_sn),
            )
            self._normalize_station_record(station_sn, station)
            station["stationOnline"] = "1"
            station_status = deep_get(body, "stationStatusObject", {}) or {}
            if station_status:
                station["statusObject"].update(station_status)
            station["statusObject"]["timestamp"] = now_ts()
            station["statusObject"]["time"] = iso_now()
            for camera_status in deep_get(body, "cameraStatusObjectList", []) or []:
                camera_sn = deep_get(camera_status, "cameraSn", "")
                if not camera_sn:
                    continue
                key = str(camera_sn or "").strip()
                if key and key in {
                    str(item or "").strip()
                    for item in self.state.setdefault("cameraTombstones", [])
                    if str(item or "").strip()
                }:
                    self.state.setdefault("cameras", {}).pop(camera_sn, None)
                    continue
                channel = int(deep_get(camera_status, "channel", 0) or 0)
                camera = self.ensure_camera(camera_sn, station_sn, channel, pending=False)
                camera["stationOnline"] = "1"
                camera["statusObject"].update(camera_status)
                camera["statusObject"]["timestamp"] = now_ts()
                camera["statusObject"]["time"] = iso_now()
            self._save_unlocked(self.state)
            return station

    def update_station_attr(self, station_sn, body):
        with self.lock:
            station = self.state.setdefault("stations", {}).setdefault(
                station_sn,
                self._build_station(station_sn, station_sn),
            )
            self._normalize_station_record(station_sn, station)
            station_attr = deep_get(body, "stationAttrObject", {}) or {}
            if station_attr:
                station["attrObject"].update(station_attr)
                sn = deep_get(station_attr, "sn")
                if sn:
                    station["deviceSn"] = sn
                    station["stationSn"] = sn
            station["stationOnline"] = "1"
            station["statusObject"]["timestamp"] = now_ts()
            station["statusObject"]["time"] = iso_now()
            self._normalize_station_record(station["stationSn"], station)
            self._save_unlocked(self.state)
            return station

    def _format_notice_device_time(self, raw: str) -> str:
        if not raw:
            return datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        raw = str(raw)
        if len(raw) == 14 and raw.isdigit():
            return (
                f"{raw[0:4]}-{raw[4:6]}-{raw[6:8]} "
                f"{raw[8:10]}:{raw[10:12]}:{raw[12:14]}"
            )
        return raw

    def _new_notice_id_unlocked(self):
        notice_id = int(self.state.get("messageCounter", 1) or 1)
        self.state["messageCounter"] = notice_id + 1
        return notice_id

    def _build_notice_message_unlocked(self, camera_sn, source_notice_type, ext=None, content=""):
        ext = ext or {}
        camera_sn = str(camera_sn or DEFAULT_CAMERA_SN)
        camera = self.state.setdefault("cameras", {}).get(camera_sn, {})
        device_name = camera.get("cameraName", camera_sn)
        device_time = self._format_notice_device_time(deep_get(ext, "deviceTime", ""))
        visible_notice_type = self._notice_type_from_ext(source_notice_type, ext)
        message = {
            "noticeId": self._new_notice_id_unlocked(),
            "noticeStatus": 0,
            "noticeType": int(source_notice_type or 1),
            "sourceNoticeType": int(source_notice_type or 1),
            "visibleNoticeType": visible_notice_type,
            "shareFlag": 0,
            "deviceSn": camera_sn,
            "deviceName": device_name,
            "parentDeviceSn": camera.get("stationSn", DEFAULT_STATION_SN),
            "title": self._notice_title(visible_notice_type),
            "content": content or "",
            "addTimestamp": self._parse_notice_add_timestamp_ms(device_time, deep_get(ext, "timestamp", now_ts())),
            "deviceTime": device_time,
            "sharerName": "",
            "permissionJson": {
                "msgAndPlayback": 1,
                "realtimeVideo": 1,
                "shareFlag": 0,
            },
            "extObject": {
                "channel": str(deep_get(ext, "channel", camera.get("channel", 0))),
                "deviceName": device_name,
                "deviceTime": device_time,
                "duration": int(deep_get(ext, "duration", 10) or 10),
                "fileDate": str(deep_get(ext, "fileDate", "")),
                "fileName": str(deep_get(ext, "fileName", "")),
                "fileType": int(deep_get(ext, "fileType", 5) or 5),
                "mailSwitch": int(
                    deep_get(
                        ext,
                        "mailSwitch",
                        camera.get("settingsObject", {}).get("mailSwitch", 1),
                    )
                    or 1
                ),
                "msg": str(deep_get(ext, "msg", "") or ""),
                "msgSwitch": int(
                    deep_get(
                        ext,
                        "msgSwitch",
                        camera.get("settingsObject", {}).get("pushSwitch", 1),
                    )
                    or 1
                ),
                "timestamp": str(deep_get(ext, "timestamp", now_ts())),
                "timezone": str(deep_get(ext, "timezone", "8")),
                "timezoneex": int(deep_get(ext, "timezoneex", 9999) or 9999),
            },
        }
        self._normalize_message_record_unlocked(message)
        return message

    def add_notice(self, body):
        with self.lock:
            camera_sn = deep_get(body, "deviceSn", DEFAULT_CAMERA_SN)
            notice_type = int(deep_get(body, "noticeType", 1) or 1)
            ext = deep_get(body, "extJson", {}) or {}
            message = self._build_notice_message_unlocked(
                camera_sn,
                notice_type,
                ext=ext,
                content=deep_get(body, "content", "") or "",
            )
            messages = self.state.setdefault("messages", [])
            messages.insert(0, message)
            del messages[500:]
            self._save_unlocked(self.state)
            return message

    def set_push_token(self, push_token, remote=""):
        push_token = str(push_token or "").strip()
        if not push_token:
            return False
        with self.lock:
            changed = False
            if self.state.get("pushToken") != push_token:
                self.state["pushToken"] = push_token
                changed = True
            tokens = self.state.setdefault("pushTokens", [])
            if push_token not in tokens:
                tokens.append(push_token)
                del tokens[:-10]
                changed = True
            meta = self.state.setdefault("meta", {})
            if remote and meta.get("lastPushTokenRemote") != remote:
                meta["lastPushTokenRemote"] = remote
                changed = True
            if changed:
                self._save_unlocked(self.state)
            return changed

    def _build_recording_notice(self, camera_sn, camera, file_date, entry):
        file_name = str(entry.get("filename", "") or "")
        if len(file_name) < 6:
            return None
        hhmmss = file_name.split("_", 1)[0]
        return {
            "deviceSn": camera_sn,
            "fileDate": file_date,
            "fileName": file_name,
            "duration": int(entry.get("duration", 10) or 10),
            "channel": int(entry.get("channel", camera.get("channel", 0)) or 0),
            "deviceTime": f"{file_date}{hhmmss}",
            "timestamp": int(time.time()),
            "fileType": 5 if "_U_" in file_name else DEFAULT_VISIBLE_NOTICE_TYPE,
        }

    def _collect_recording_notices(self, station_sn, host=DEFAULT_SSH_HOST, force_full=False):
        helper = _load_station_helper()
        parse_vava_idx_bytes = helper.parse_vava_idx_bytes
        read_ssh_bytes = helper.read_ssh_bytes
        record_root = f"/mnt/sd0/{station_sn}"
        root_idx = parse_vava_idx_bytes(read_ssh_bytes(host, f"{record_root}/vava.idx"))
        file_dates = sorted(
            {
                entry.get("date", "")
                for entry in root_idx.get("entries", [])
                if len(str(entry.get("date", ""))) == 8
            },
            reverse=True,
        )
        cameras = [
            (camera_sn, camera)
            for camera_sn, camera in self.state.get("cameras", {}).items()
            if camera.get("stationSn") == station_sn
        ]
        station_cache = self._station_notice_cache.setdefault(
            station_sn,
            {"dates": {}, "full_scan_at": 0.0, "file_dates": []},
        )
        cached_dates = station_cache.setdefault("dates", {})
        active_dates = set(file_dates)
        for cached_date in list(cached_dates):
            if cached_date not in active_dates:
                cached_dates.pop(cached_date, None)
        full_scan = bool(
            force_full
            or not cached_dates
            or (time.time() - float(station_cache.get("full_scan_at", 0.0) or 0.0))
            >= MESSAGE_SYNC_FULL_RESCAN_SECONDS
        )
        recent_dates = set(file_dates[:MESSAGE_SYNC_RECENT_DATES])
        notices = []
        for file_date in file_dates:
            date_cache = cached_dates.setdefault(file_date, {})
            refresh_date = full_scan or file_date in recent_dates
            for camera_sn, camera in cameras:
                cached_notices = date_cache.get(camera_sn)
                if cached_notices is not None and not refresh_date:
                    notices.extend(cached_notices)
                    continue
                idx_path = f"{record_root}/{file_date}/{camera_sn}/vava.idx"
                try:
                    record_idx = parse_vava_idx_bytes(read_ssh_bytes(host, idx_path))
                except BaseException:
                    if cached_notices is not None:
                        notices.extend(cached_notices)
                    continue
                camera_notices = []
                for entry in record_idx.get("entries", []):
                    notice = self._build_recording_notice(camera_sn, camera, file_date, entry)
                    if notice is not None:
                        camera_notices.append(notice)
                date_cache[camera_sn] = camera_notices
                notices.extend(camera_notices)
        if full_scan:
            station_cache["full_scan_at"] = time.time()
        station_cache["file_dates"] = file_dates
        notices.sort(key=lambda item: (item["fileDate"], item["fileName"]), reverse=True)
        return notices

    def refresh_messages_from_station_if_needed(self, force=False):
        if not DEFAULT_SSH_HOST:
            return 0
        with self.lock:
            if not force and time.time() - self.last_message_sync_at < MESSAGE_SYNC_MIN_INTERVAL:
                return 0
            self.last_message_sync_at = time.time()
            station_sns = list(self.state.get("stations", {}).keys()) or [DEFAULT_STATION_SN]
        try:
            candidates = []
            for station_sn in station_sns:
                candidates.extend(self._collect_recording_notices(station_sn, force_full=force))
        except BaseException as exc:
            self.last_message_sync_error = str(exc)
            return 0
        added = 0
        with self.lock:
            existing = {self._message_key(message) for message in self.state.get("messages", [])}
            messages = self.state.setdefault("messages", [])
            changed = False
            for candidate in candidates:
                key = (candidate["deviceSn"], candidate["fileDate"], candidate["fileName"])
                if key in existing:
                    continue
                ext = {
                    "channel": str(candidate["channel"]),
                    "deviceTime": candidate["deviceTime"],
                    "duration": candidate["duration"],
                    "fileDate": candidate["fileDate"],
                    "fileName": candidate["fileName"],
                    "fileType": candidate["fileType"],
                    "msg": "",
                    "timestamp": candidate["timestamp"],
                    "timezone": "8",
                    "timezoneex": 28800,
                }
                messages.insert(
                    0,
                    self._build_notice_message_unlocked(
                        candidate["deviceSn"],
                        candidate["fileType"],
                        ext=ext,
                    ),
                )
                existing.add(key)
                added += 1
                changed = True
            if changed:
                messages.sort(key=lambda item: int(item.get("addTimestamp", 0) or 0), reverse=True)
                del messages[500:]
                self._save_unlocked(self.state)
        self.last_message_sync_error = ""
        return added

    def _filtered_messages(self, date="", device_sn="", type_list=None):
        type_set = None
        if type_list:
            type_set = {int(item) for item in type_list}
        out = []
        for message in self.state.get("messages", []):
            source_type = int(message.get("sourceNoticeType", message.get("noticeType", 1)) or 1)
            visible_type = int(
                message.get(
                    "visibleNoticeType",
                    self._notice_type_from_ext(source_type, message.get("extObject", {}) or {}),
                )
                or 0
            )
            stored_type = int(message.get("noticeType", source_type) or source_type)
            # The app mixes "source" type 1 queries with normalized visible
            # types (2/5/...) depending on which notifications screen it loads.
            if type_set and not ({visible_type, source_type, stored_type} & type_set):
                continue
            if device_sn and message.get("deviceSn") != device_sn:
                continue
            msg_date = deep_get(message.get("extObject", {}), "fileDate", "") or ""
            if date:
                if len(date) == 6 and not msg_date.startswith(date):
                    continue
                if len(date) == 8 and msg_date != date:
                    continue
            out.append(message)
        return out

    def message_count_payload(self, type_list=None):
        self.refresh_messages_from_station_if_needed()
        messages = self._filtered_messages(type_list=type_list)
        return {
            "unreadCount": len(messages),
            "unreadTypeCount": len(
                {
                    int(
                        msg.get(
                            "visibleNoticeType",
                            self._notice_type_from_ext(
                                int(msg.get("sourceNoticeType", msg.get("noticeType", 1)) or 1),
                                msg.get("extObject", {}) or {},
                            ),
                        )
                        or 0
                    )
                    for msg in messages
                }
            ),
        }

    def message_filter_payload(self, date=""):
        self.refresh_messages_from_station_if_needed()
        messages = self._filtered_messages(date=date if len(date) == 6 else "")
        date_set = sorted(
            {
                deep_get(msg.get("extObject", {}), "fileDate", "")
                for msg in messages
                if deep_get(msg.get("extObject", {}), "fileDate", "")
            },
            reverse=True,
        )
        device_names = {}
        for camera_sn, camera in self.state.get("cameras", {}).items():
            device_names[camera_sn] = camera.get("cameraName", camera_sn)
        for msg in messages:
            device_names.setdefault(msg.get("deviceSn", ""), msg.get("deviceName", ""))
        device_list = [
            {
                "deviceSn": sn,
                "deviceName": name,
                "isSelect": False,
                "isTempSelect": False,
                "userId": self.state.get("user", {}).get("userid", DEFAULT_USER_ID),
            }
            for sn, name in sorted(device_names.items())
            if sn
        ]
        return {
            "dateSet": date_set,
            "deviceList": device_list,
        }

    def message_list_payload(self, date="", device_sn="", last_notice_id=0, page_size=20, type_list=None):
        self.refresh_messages_from_station_if_needed()
        messages = self._filtered_messages(date=date, device_sn=device_sn, type_list=type_list)
        if last_notice_id:
            messages = [msg for msg in messages if int(msg.get("noticeId", 0)) < int(last_notice_id)]
        page = messages[: max(int(page_size or 20), 1)]
        requested_types = {int(item) for item in (type_list or [])}
        exported = []
        for message in page:
            item = json.loads(json.dumps(message))
            source_type = int(item.get("sourceNoticeType", item.get("noticeType", 1)) or 1)
            visible_type = int(
                item.get(
                    "visibleNoticeType",
                    self._notice_type_from_ext(source_type, item.get("extObject", {}) or {}),
                )
                or 0
            )
            # Notifications page requests type 1 so it can resolve local JPG
            # thumbnails. Settings -> Messages requests visible types (2/4/5/7)
            # and needs noticeType rewritten accordingly for its adapter.
            if requested_types and 1 in requested_types and requested_types <= {1}:
                item["noticeType"] = source_type
            elif requested_types:
                item["noticeType"] = visible_type
            self._export_share_metadata_unlocked(item)
            exported.append(item)
        return {
            "totalCount": len(messages),
            "list": exported,
        }

    def remove_messages(self, notice_ids):
        with self.lock:
            if isinstance(notice_ids, str):
                raw_ids = [item.strip() for item in notice_ids.split(",") if item.strip()]
            else:
                raw_ids = [str(item).strip() for item in (notice_ids or []) if str(item).strip()]
            id_set = {int(item) for item in raw_ids}
            before = len(self.state.get("messages", []))
            self.state["messages"] = [
                msg for msg in self.state.get("messages", []) if int(msg.get("noticeId", 0)) not in id_set
            ]
            removed = before - len(self.state["messages"])
            self._save_unlocked(self.state)
            return removed

    def _resolve_auth_and_user_unlocked(self, access_token=""):
        auth_row = None
        user_row = None
        if access_token:
            auth_row = self._find_auth_row_by_access_token_unlocked(access_token)
        if auth_row:
            user_row = self._find_user_row_unlocked(user_id=auth_row["user_id"])
        if not user_row or not auth_row:
            self._sync_state_session_unlocked(access_token=access_token)
            auth = self.state.get("auth", {})
            user = self.state.get("user", {})
            auth_row = auth_row or self._find_auth_row_by_access_token_unlocked(auth.get("access_token"))
            if auth_row:
                user_row = self._find_user_row_unlocked(user_id=auth_row["user_id"])
            if not user_row and user.get("userid"):
                user_row = self._find_user_row_unlocked(user_id=user.get("userid"))
        return auth_row, user_row

    def auth_payload(self, access_token=""):
        with self.lock:
            auth_row, _ = self._resolve_auth_and_user_unlocked(access_token=access_token)
            if auth_row:
                return self._state_auth_from_row(auth_row)
            auth = self.state["auth"]
            return {
                "access_token": auth["access_token"],
                "expires_in": auth["expires_in"],
                "token_type": auth["token_type"],
                "scope": auth["scope"],
            }

    def login_payload(self, access_token=""):
        with self.lock:
            auth_row, user_row = self._resolve_auth_and_user_unlocked(access_token=access_token)
            if auth_row and user_row:
                auth = self._state_auth_from_row(auth_row)
                user = self._state_user_from_row(user_row)
            else:
                auth = self.state["auth"]
                user = self.state["user"]
            return {
                "access_token": auth["access_token"],
                "avatar": user.get("avatar", ""),
                "expires_in": auth["expires_in"],
                "nickname": user.get("nickname", ""),
                "refresh_token": auth["refresh_token"],
                "scope": auth["scope"],
                "token_type": auth["token_type"],
                "userid": user.get("userid", DEFAULT_USER_ID),
                "username": user.get("username", DEFAULT_EMAIL),
            }

    def issue_verification_code(self, target, purpose, channel):
        target = str(target or "").strip()
        if not target:
            return ""
        with self.lock:
            code = DEFAULT_VERIFY_CODE
            now = iso_now()
            self.db.execute(
                """
                INSERT INTO verification_codes (target, purpose, channel, code, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (target, purpose, channel, code, now, now),
            )
            self.db.commit()
            self.add_event(
                "user.verification_code",
                {"target": target, "purpose": purpose, "channel": channel},
            )
            return code

    def _verify_code_unlocked(self, target, purpose, code):
        target = str(target or "").strip()
        code = str(code or "").strip()
        if not target or not code:
            return True
        if ALLOW_ANY_6DIGIT_VERIFY_CODE and self._looks_like_test_verify_code(code):
            return True
        if code == DEFAULT_VERIFY_CODE:
            return True
        row = self.db.execute(
            """
            SELECT code FROM verification_codes
            WHERE target = ? AND purpose = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (target, purpose),
        ).fetchone()
        if not row:
            return True
        return secrets.compare_digest(code, row["code"])

    def verify_code(self, target, purpose, code):
        with self.lock:
            return self._verify_code_unlocked(target, purpose, code)

    def register_user(
        self,
        *,
        username="",
        email="",
        mobile="",
        password="",
        nickname="",
        avatar="",
        verify_code="",
        verify_purpose="",
    ):
        with self.lock:
            email = self._normalize_email(email)
            mobile = self._normalize_mobile(mobile)
            username = self._canonical_username(username, email, mobile)
            target = email or mobile or username
            if verify_purpose and not self._verify_code_unlocked(target, verify_purpose, verify_code):
                return None, "verify code error"
            if not username:
                return None, "username required"
            if self._find_user_row_unlocked(identifier=username):
                return None, "user already exists"
            user_id = f"user-{uuid.uuid4().hex[:12]}"
            user_row = self._upsert_user_unlocked(
                user_id=user_id,
                username=username,
                email=email,
                mobile=mobile,
                nickname=nickname or username,
                avatar=avatar or "",
                password_hash=self._hash_password(password or DEFAULT_PASSWORD),
                password_md5=(
                    str(password or "").strip().lower()
                    if self._looks_like_md5(password)
                    else self._password_md5(password or DEFAULT_PASSWORD)
                ),
            )
            auth_row = self._issue_tokens_for_user_unlocked(user_row["user_id"])
            self._set_active_session_unlocked(user_row, auth_row)
            self._save_unlocked(self.state)
            return self.login_payload(access_token=auth_row["access_token"]), None

    def login_user(self, identifier, password):
        with self.lock:
            user_row = self._find_user_row_unlocked(identifier=identifier)
            if not user_row:
                return None, "username or password error"
            if not self._verify_password(password, user_row["password_hash"], user_row["password_md5"]):
                return None, "username or password error"
            auth_row = self._issue_tokens_for_user_unlocked(user_row["user_id"])
            self._set_active_session_unlocked(user_row, auth_row)
            self._save_unlocked(self.state)
            return self.login_payload(access_token=auth_row["access_token"]), None

    def refresh_login(self, refresh_token):
        with self.lock:
            auth_row = self._find_auth_row_by_refresh_token_unlocked(refresh_token)
            if not auth_row:
                return None, "refresh token mismatch"
            user_row = self._find_user_row_unlocked(user_id=auth_row["user_id"])
            if not user_row:
                return None, "user not found"
            self._set_active_session_unlocked(user_row, auth_row)
            self._save_unlocked(self.state)
            return {
                "access_token": auth_row["access_token"],
                "refresh_token": auth_row["refresh_token"],
                "expires_in": auth_row["expires_in"],
            }, None

    def reset_password(self, identifier="", new_password="", verify_code="", verify_purpose="reset_password"):
        with self.lock:
            user_row = self._find_user_row_unlocked(identifier=identifier)
            if not user_row:
                return False, "user not found"
            target = user_row["email"] or user_row["mobile"] or user_row["username"]
            if not self._verify_code_unlocked(target, verify_purpose, verify_code):
                return False, "verify code error"
            self.db.execute(
                "UPDATE users SET password_hash = ?, password_md5 = ?, updated_at = ? WHERE user_id = ?",
                (
                    self._hash_password(new_password or DEFAULT_PASSWORD),
                    (
                        str(new_password or "").strip().lower()
                        if self._looks_like_md5(new_password)
                        else self._password_md5(new_password or DEFAULT_PASSWORD)
                    ),
                    iso_now(),
                    user_row["user_id"],
                ),
            )
            self.db.commit()
            return True, None

    def change_password(self, *, access_token="", identifier="", old_password="", new_password=""):
        with self.lock:
            auth_row = self._find_auth_row_by_access_token_unlocked(access_token)
            user_row = None
            if auth_row:
                user_row = self._find_user_row_unlocked(user_id=auth_row["user_id"])
            if not user_row and identifier:
                user_row = self._find_user_row_unlocked(identifier=identifier)
            if not user_row:
                return False, "user not found"
            if old_password and not self._verify_password(old_password, user_row["password_hash"], user_row["password_md5"]):
                return False, "old password error"
            self.db.execute(
                "UPDATE users SET password_hash = ?, password_md5 = ?, updated_at = ? WHERE user_id = ?",
                (
                    self._hash_password(new_password or DEFAULT_PASSWORD),
                    (
                        str(new_password or "").strip().lower()
                        if self._looks_like_md5(new_password)
                        else self._password_md5(new_password or DEFAULT_PASSWORD)
                    ),
                    iso_now(),
                    user_row["user_id"],
                ),
            )
            self.db.commit()
            return True, None

    def update_user_profile(self, *, access_token="", identifier="", nickname=None, avatar=None):
        with self.lock:
            auth_row = self._find_auth_row_by_access_token_unlocked(access_token)
            user_row = None
            if auth_row:
                user_row = self._find_user_row_unlocked(user_id=auth_row["user_id"])
            if not user_row and identifier:
                user_row = self._find_user_row_unlocked(identifier=identifier)
            if not user_row:
                return None, "user not found"
            next_nickname = user_row["nickname"] if nickname is None else str(nickname or "").strip()
            next_avatar = user_row["avatar"] if avatar is None else str(avatar or "").strip()
            self.db.execute(
                "UPDATE users SET nickname = ?, avatar = ?, updated_at = ? WHERE user_id = ?",
                (next_nickname or user_row["username"], next_avatar, iso_now(), user_row["user_id"]),
            )
            self.db.commit()
            user_row = self._find_user_row_unlocked(user_id=user_row["user_id"])
            if auth_row:
                self._set_active_session_unlocked(user_row, auth_row)
                self._save_unlocked(self.state)
            return self._state_user_from_row(user_row), None

    def station_did_payload(self, station_sn):
        station = self.state.get("stations", {}).get(station_sn, {})
        did = station.get("did", {})
        sy_did = did.get("syDid", did.get("didCode", DEFAULT_DID))
        if FORCE_DID:
            sy_did = FORCE_DID
        did_code = did.get("didCode", sy_did)
        if FORCE_DID:
            did_code = FORCE_DID
        if "," not in did_code:
            did_code = f"{sy_did},{DEFAULT_DID_TOKEN}"
        init_code = did.get("initCode", did.get("initString", DEFAULT_INIT))
        return {
            "didCode": did_code,
            "initCode": init_code,
            "crcKey": did.get("crcKey", DEFAULT_CRC),
            "syDid": sy_did,
            "initString": did.get("initString", init_code),
        }

    def station_did_for_index(self, station_sn):
        did = self.station_did_payload(station_sn)
        did_code = str(did.get("didCode", "")).split(",", 1)[0]
        did["didCode"] = did_code
        if FORCE_DID:
            did["syDid"] = did_code
        return did

    def profile_payload(self, access_token=""):
        with self.lock:
            _, user_row = self._resolve_auth_and_user_unlocked(access_token=access_token)
            if user_row:
                user = self._state_user_from_row(user_row)
            else:
                user = self.state["user"]
            return {
                "avatar": user.get("avatar", ""),
                "email": user.get("email", ""),
                "nickname": user.get("nickname", ""),
                "userid": user.get("userid", DEFAULT_USER_ID),
                "username": user.get("username", DEFAULT_EMAIL),
            }

    def consume_flag(self, key: str, default: bool = False) -> bool:
        with self.lock:
            meta = self.state.setdefault("meta", {})
            value = bool(meta.get(key, default))
            meta[key] = False
            self._save_unlocked(self.state)
            return value

    def station_bind_list(self):
        return [
            {
                "cameraTotal": station.get("cameraTotal", 0),
                "deviceName": station.get("deviceName", station_sn),
                "deviceSn": station_sn,
            }
            for station_sn, station in sorted(self.state.get("stations", {}).items())
        ]

    def my_devices_payload(self):
        station_list = []
        stations = self.camera_index_payload()["stationList"]
        cameras = self.camera_index_payload()["cameraList"]
        for station in stations:
            station_sn = station.get("stationSn", "")
            station_cameras = [
                camera
                for camera in cameras
                if camera.get("stationSn") == station_sn
            ]
            station_cameras.sort(key=lambda item: int(item.get("channel", 0)))
            entry = dict(station)
            entry["cameraList"] = station_cameras
            station_list.append(entry)
        return {"stationList": station_list}

    def camera_index_payload(self):
        stations = []
        for station_sn, station in sorted(self.state.get("stations", {}).items()):
            stations.append(
                self._export_share_metadata_unlocked(
                    {
                    "addTime": station.get("addTime", iso_now()),
                    "attrObject": station.get("attrObject", {}),
                    "bindId": station.get("bindId", f"bind-station-{station_sn}"),
                    "did": self.station_did_for_index(station_sn),
                    "permissionObject": station.get("permissionObject", {}),
                    "shareFlag": station.get("shareFlag", 0),
                    "shareTime": station.get("shareTime", ""),
                    "sharerName": station.get("sharerName", ""),
                    "stationName": station.get("stationName", station_sn),
                    "stationOnline": station.get("stationOnline", "1"),
                    "stationSn": station_sn,
                    "statusObject": station.get("statusObject", {}),
                    }
                )
            )
        cameras = []
        for camera_sn, camera in sorted(self.state.get("cameras", {}).items()):
            cameras.append(
                self._export_share_metadata_unlocked(
                    {
                    "addTime": camera.get("addTime", iso_now()),
                    "attrObject": camera.get("attrObject", {}),
                    "bindId": camera.get("bindId", f"bind-camera-{camera_sn}"),
                    "cameraName": camera.get("cameraName", camera_sn),
                    "cameraSn": camera_sn,
                    "channel": camera.get("channel", 0),
                    "connectType": 0,
                    "settingsObject": camera.get("settingsObject", {}),
                    "shareFlag": camera.get("shareFlag", 0),
                    "shareTime": camera.get("shareTime", ""),
                    "sharerName": camera.get("sharerName", ""),
                    "stationOnline": camera.get("stationOnline", "1"),
                    "stationSn": camera.get("stationSn", ""),
                    "statusObject": camera.get("statusObject", {}),
                    }
                )
            )
        return {
            "cameraList": cameras,
            "intervalSeconds": 10,
            "stationList": stations,
        }


class MockHandler(BaseHTTPRequestHandler):
    server_version = "VavaMock/0.1"

    @property
    def store(self) -> StateStore:
        return self.server.store

    def setup(self):
        super().setup()
        try:
            self.connection.settimeout(15)
        except Exception:
            pass
        debug_log(
            f"handler.setup remote={self.client_address[0]}:{self.client_address[1]}"
        )

    def handle(self):
        debug_log(
            f"handler.handle.start remote={self.client_address[0]}:{self.client_address[1]}"
        )
        try:
            super().handle()
        finally:
            debug_log(
                f"handler.handle.end remote={self.client_address[0]}:{self.client_address[1]}"
            )

    def finish(self):
        try:
            super().finish()
        finally:
            debug_log(
                f"handler.finish remote={self.client_address[0]}:{self.client_address[1]}"
            )

    def _read_body(self):
        length = int(self.headers.get("Content-Length", "0") or "0")
        return self.rfile.read(length) if length else b""

    def _parse_body(self, raw_body):
        if not raw_body:
            return {}
        text = raw_body.decode("utf-8", "replace")
        try:
            return json.loads(text)
        except Exception:
            parsed = parse_qs(text, keep_blank_values=True)
            if parsed:
                flattened = {}
                for key, values in parsed.items():
                    flattened[key] = values[0] if len(values) == 1 else values
                return flattened
            return {"_raw": text}

    def _json_response(self, payload=None, code=200):
        payload = payload or {"stateCode": 200, "stateMsg": "OK", "data": {}}
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _html_response(self, html: str, code: int = 200):
        body = html.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _ok(self, data=None):
        return {"stateCode": 200, "stateMsg": "OK", "data": data if data is not None else {}}

    def _request_access_token(self, query, body):
        auth_header = self.headers.get("Authorization", "")
        if auth_header.lower().startswith("bearer "):
            return auth_header.split(None, 1)[1].strip()
        token = deep_get(body, "access_token")
        if not token:
            token = deep_get(query, "access_token")
        if isinstance(token, list):
            token = token[0] if token else ""
        return str(token or "").strip()

    def _log_request(self, method, path, query, body):
        entry = {
            "ts": iso_now(),
            "method": method,
            "path": path,
            "query": query,
            "body": body,
            "remote": self.client_address[0],
        }
        print(
            f"[{entry['ts']}] {method} {path} query={json.dumps(query, ensure_ascii=False)} "
            f"body={json.dumps(body, ensure_ascii=False)}",
            flush=True,
        )
        return entry

    def _log_response(self, path, response):
        if path not in {
            "/ipc/device/camera/list-for-index",
            "/ipc/p2p/get-did",
            "/ipc/p2p/get-session-key",
            "/logs/item",
        }:
            return
        print(
            f"[{iso_now()}] RESP {path} "
            f"{json.dumps(response, ensure_ascii=False, sort_keys=True)}",
            flush=True,
        )

    def _route(self, method, path, query, body):
        access_token = self._request_access_token(query, body)
        route_manager = getattr(self.server, "route_manager", None)
        if route_manager is not None:
            response = route_manager.dispatch(self, method, path, query, body, access_token)
            if response is not None:
                return response
        return self._route_legacy(method, path, query, body)

    def _route_legacy(self, method, path, query, body):
        access_token = self._request_access_token(query, body)
        if path == "/oauth/login":
            grant_type = deep_get(body, "grant_type", "")
            if grant_type == "client_credentials":
                return self._ok(self.store.auth_payload(access_token=access_token))
            if deep_get(body, "auth_type", "") == "sn_password" or deep_get(body, "sn"):
                payload = self.store.login_payload(access_token=access_token)
                station_sn = deep_get(body, "sn", DEFAULT_STATION_SN)
                payload.update(self.store.station_did_payload(station_sn))
                return self._ok(payload)
            identifier = (
                deep_get(body, "username")
                or deep_get(body, "account")
                or deep_get(body, "email")
                or deep_get(body, "mobile")
                or deep_get(body, "phone")
            )
            password = deep_get(body, "password") or deep_get(body, "passwd") or deep_get(body, "pwd")
            if identifier:
                payload, error = self.store.login_user(identifier, password)
                if error:
                    return {"stateCode": 401, "stateMsg": error, "data": {}}
                return self._ok(payload)
            payload = self.store.login_payload(access_token=access_token)
            return self._ok(payload)

        if path == "/oauth/refresh-token":
            refresh_token = deep_get(body, "refresh_token")
            payload, error = self.store.refresh_login(refresh_token)
            if error:
                return {"stateCode": 401, "stateMsg": error, "data": {}}
            return self._ok(payload)

        if path == "/oauth/logout":
            return self._ok({})

        if path == "/users/detail":
            return self._ok(self.store.profile_payload(access_token=access_token))

        if path == "/users/send-register-email-verify-code":
            email = deep_get(body, "email") or deep_get(body, "username")
            self.store.issue_verification_code(email, "register_email", "email")
            return self._ok({})

        if path == "/users/send-register-sms-verify-code":
            mobile = deep_get(body, "mobile") or deep_get(body, "phone")
            self.store.issue_verification_code(mobile, "register_mobile", "sms")
            return self._ok({})

        if path == "/users/email-verify":
            email = deep_get(body, "email") or deep_get(body, "username")
            code = deep_get(body, "verifyCode") or deep_get(body, "code") or deep_get(body, "emailCode")
            if not self.store.verify_code(email, "register_email", code):
                return {"stateCode": 400, "stateMsg": "verify code error", "data": {}}
            return self._ok({})

        if path == "/users/mobile-verify":
            mobile = deep_get(body, "mobile") or deep_get(body, "phone")
            code = deep_get(body, "verifyCode") or deep_get(body, "code") or deep_get(body, "smsCode")
            if not self.store.verify_code(mobile, "register_mobile", code):
                return {"stateCode": 400, "stateMsg": "verify code error", "data": {}}
            return self._ok({})

        if path == "/users/email-password-register":
            payload, error = self.store.register_user(
                username=deep_get(body, "username") or deep_get(body, "email"),
                email=deep_get(body, "email") or deep_get(body, "username"),
                password=deep_get(body, "password") or deep_get(body, "passwd") or deep_get(body, "pwd"),
                nickname=deep_get(body, "nickname") or deep_get(body, "name"),
                avatar=deep_get(body, "avatar"),
                verify_code=deep_get(body, "verifyCode") or deep_get(body, "code") or deep_get(body, "emailCode"),
                verify_purpose="register_email",
            )
            if error:
                return {"stateCode": 400, "stateMsg": error, "data": {}}
            return self._ok(payload)

        if path == "/users/mobile-password-register":
            payload, error = self.store.register_user(
                username=deep_get(body, "username") or deep_get(body, "mobile") or deep_get(body, "phone"),
                mobile=deep_get(body, "mobile") or deep_get(body, "phone"),
                password=deep_get(body, "password") or deep_get(body, "passwd") or deep_get(body, "pwd"),
                nickname=deep_get(body, "nickname") or deep_get(body, "name"),
                avatar=deep_get(body, "avatar"),
                verify_code=deep_get(body, "verifyCode") or deep_get(body, "code") or deep_get(body, "smsCode"),
                verify_purpose="register_mobile",
            )
            if error:
                return {"stateCode": 400, "stateMsg": error, "data": {}}
            return self._ok(payload)

        if path in {
            "/users/send-reset-password-email",
            "/users/forget-password-send-email-code",
            "/users/send-reset-password-email-verify-code",
        }:
            email = deep_get(body, "email") or deep_get(body, "username")
            self.store.issue_verification_code(email, "reset_password", "email")
            return self._ok({})

        if path == "/users/send-reset-password-sms-verify-code":
            mobile = deep_get(body, "mobile") or deep_get(body, "phone")
            self.store.issue_verification_code(mobile, "reset_password", "sms")
            return self._ok({})

        if path in {"/users/reset-password", "/users/update-password-by-email-code"}:
            identifier = (
                deep_get(body, "username")
                or deep_get(body, "email")
                or deep_get(body, "mobile")
                or deep_get(body, "phone")
            )
            new_password = (
                deep_get(body, "newPassword")
                or deep_get(body, "password")
                or deep_get(body, "passwd")
                or deep_get(body, "pwd")
            )
            verify_code = deep_get(body, "verifyCode") or deep_get(body, "code") or deep_get(body, "emailCode")
            ok, error = self.store.reset_password(
                identifier=identifier,
                new_password=new_password,
                verify_code=verify_code,
                verify_purpose="reset_password",
            )
            if error:
                return {"stateCode": 400, "stateMsg": error, "data": {}}
            return self._ok({"success": ok})

        if path == "/users/change-password":
            ok, error = self.store.change_password(
                access_token=access_token,
                identifier=(
                    deep_get(body, "username")
                    or deep_get(body, "email")
                    or deep_get(body, "mobile")
                    or deep_get(body, "phone")
                ),
                old_password=deep_get(body, "oldPassword") or deep_get(body, "password"),
                new_password=deep_get(body, "newPassword") or deep_get(body, "confirmPassword"),
            )
            if error:
                return {"stateCode": 400, "stateMsg": error, "data": {}}
            return self._ok({"success": ok})

        if path == "/users/update":
            payload, error = self.store.update_user_profile(
                access_token=access_token,
                identifier=deep_get(body, "username") or deep_get(body, "email") or deep_get(body, "mobile"),
                nickname=deep_get(body, "nickname"),
                avatar=deep_get(body, "avatar"),
            )
            if error:
                return {"stateCode": 400, "stateMsg": error, "data": {}}
            return self._ok(payload)

        if path == "/users/collectAppVersion":
            return self._ok({})

        if path == "/app/ota/upgrade/task/latest/rule":
            # Returning an empty object makes the Android app pop a broken
            # "new version detected" dialog with vnull / 0KB. The app treats
            # JSON null as "no app update".
            return {"stateCode": 200, "stateMsg": "OK", "data": None}

        if path == "/ipc/device/station/report-status":
            station_sn = deep_get(body, "stationSn", DEFAULT_STATION_SN)
            self.store.update_station_status(station_sn, body)
            return self._ok({})

        if path == "/ipc/device/station/report-attr":
            station_sn = deep_get(body, "stationSn", DEFAULT_STATION_SN)
            attr_sn = deep_get(body, "stationAttrObject", {}).get("sn")
            self.store.update_station_attr(attr_sn or station_sn, body)
            return self._ok({})

        if path == "/ipc/device/station/add":
            station_sn = deep_get(body, "stationSn", DEFAULT_STATION_SN)
            station_name = deep_get(body, "stationName", station_sn)
            station = self.store.set_station(station_sn, station_name)
            self.store.add_event("station.add", {"stationSn": station_sn, "stationName": station_name})
            return self._ok({"stationSn": station_sn, "stationName": station_name, "station": station})

        if path == "/ipc/device/station/set":
            station_sn = deep_get(body, "stationSn", DEFAULT_STATION_SN)
            station_name = deep_get(body, "stationName")
            station = self.store.set_station(station_sn, station_name)
            self.store.add_event("station.set", {"stationSn": station_sn, "stationName": station_name})
            return self._ok({"stationSn": station_sn, "stationName": station_name, "station": station})

        if path == "/ipc/device/station/check-bind-status":
            station_sn = deep_get(body, "stationSn", "")
            self.store.set_station(station_sn or DEFAULT_STATION_SN, None)
            return self._ok({})

        if path == "/ipc/device/station/list-bind-station":
            return self._ok(self.store.station_bind_list())

        if path == "/ipc/device/station/remove":
            station_sn = deep_get(body, "stationSn", DEFAULT_STATION_SN)
            removed = self.store.remove_station(station_sn)
            self.store.add_event(
                "station.remove",
                {
                    "stationSn": station_sn,
                    "removed": bool(removed),
                    "removedCameras": (removed or {}).get("removedCameras", []),
                },
            )
            return self._ok(
                {
                    "stationSn": station_sn,
                    "removed": bool(removed),
                }
            )

        if path == "/ipc/device/camera/check-bind-status":
            station_sn = deep_get(body, "stationSn", DEFAULT_STATION_SN)
            camera_sn = deep_get(body, "cameraSn", DEFAULT_CAMERA_SN)
            channel = int(deep_get(body, "channel", DEFAULT_CHANNEL))
            self.store.ensure_camera(camera_sn, station_sn, channel, pending=False)
            return self._ok({})

        if path == "/ipc/device/camera/remove":
            camera_sn = deep_get(body, "cameraSn", DEFAULT_CAMERA_SN)
            removed = self.store.remove_camera(camera_sn)
            self.store.add_event(
                "camera.remove",
                {
                    "cameraSn": camera_sn,
                    "removed": bool(removed),
                },
            )
            return self._ok(
                {
                    "cameraSn": camera_sn,
                    "removed": bool(removed),
                }
            )

        if path == "/ipc/device/camera/check-bind-status-by-iot":
            station_sn = deep_get(body, "stationSn", query.get("stationSn", [DEFAULT_STATION_SN])[0])
            camera_sn = deep_get(body, "cameraSn", query.get("cameraSn", [DEFAULT_CAMERA_SN])[0])
            channel = int(deep_get(body, "channel", query.get("channel", [DEFAULT_CHANNEL])[0]))
            self.store.ensure_camera(camera_sn, station_sn, channel, pending=True)
            return self._ok({"cameraSn": camera_sn, "stationSn": station_sn, "channel": channel})

        if path == "/ipc/device/camera/add-blind":
            station_sn = deep_get(body, "stationSn", query.get("stationSn", [DEFAULT_STATION_SN])[0])
            camera_sn = deep_get(body, "cameraSn", query.get("cameraSn", [DEFAULT_CAMERA_SN])[0])
            channel = int(deep_get(body, "channel", query.get("channel", [DEFAULT_CHANNEL])[0]))
            self.store.ensure_camera(camera_sn, station_sn, channel, pending=True)
            self.store.add_event(
                "camera.add_blind",
                {"cameraSn": camera_sn, "stationSn": station_sn, "channel": channel},
            )
            return self._ok({"cameraSn": camera_sn, "stationSn": station_sn, "channel": channel})

        if path == "/ipc/device/camera/set":
            camera_sn = deep_get(body, "cameraSn", DEFAULT_CAMERA_SN)
            camera_name = deep_get(body, "cameraName")
            if camera_name is not None:
                self.store.mark_camera_bound(camera_sn, camera_name)
                self.store.add_event(
                    "camera.rename_or_bind",
                    {"cameraSn": camera_sn, "cameraName": camera_name},
                )
            self.store.update_camera_settings(camera_sn, body)
            return self._ok({})

        if path == "/ipc/device/camera/list-for-index":
            if (
                self.client_address[0] in ("127.0.0.1", "::1")
                and self.store.consume_flag("force_app_index_empty_once", True)
            ):
                self.store.add_event(
                    "app.list_for_index.reset_once",
                    {"remote": self.client_address[0]},
                )
                return self._ok(
                    {
                        "cameraList": [],
                        "intervalSeconds": 10,
                        "stationList": [],
                    }
                )
            return self._ok(self.store.camera_index_payload())

        if path == "/ipc/device/camera/list-for-my-devices":
            return self._ok(self.store.my_devices_payload())

        if path == "/ipc/device/camera/list-for-mgt":
            return self._ok(self.store.camera_index_payload())

        if path == "/ipc/device/camera/list-for-station":
            station_sn = deep_get(body, "stationSn", query.get("stationSn", [DEFAULT_STATION_SN])[0])
            # The station polls this path continuously and uses it to rebuild its
            # runtime camera table. Returning only "pending" cameras causes the
            # just-bound camera to disappear from the station as soon as the app
            # finishes naming it, which then leads to wakeup/video staying at 0
            # and later CHANNEL_NOCAMERA(9). Keep the payload minimal, but expose
            # all cameras that belong to the station.
            cameras = []
            for camera_sn, camera in sorted(self.store.state.get("cameras", {}).items()):
                if camera.get("stationSn") != station_sn:
                    continue
                cameras.append(
                    {
                        "cameraSn": camera_sn,
                        "channel": int(camera.get("channel", 0)),
                    }
                )
            cameras.sort(key=lambda item: int(item.get("channel", 0)))
            return self._ok(
                {
                    "cameraList": cameras,
                    "intervalSeconds": 10,
                }
            )

        if path == "/ipc/device/camera/list-sn-status":
            requested = str(deep_get(body, "sn", "") or "")
            requested_set = {item.strip() for item in requested.split(",") if item.strip()}
            payload = []
            for camera in self.store.camera_index_payload()["cameraList"]:
                if requested_set and camera["cameraSn"] not in requested_set:
                    continue
                payload.append(
                    {
                        "deviceSn": camera["cameraSn"],
                        "statusObject": camera["statusObject"],
                    }
                )
            return self._ok({"snList": payload})

        if path == "/ipc/msg/notice/add":
            notice = self.store.add_notice(body)
            self.store.add_event("notice.add", {"noticeId": notice.get("noticeId", 0)})
            return self._ok({"noticeId": notice.get("noticeId", 0)})

        if path == "/ipc/msg/push/report-token":
            push_token = str(deep_get(body, "pushToken", "") or "")
            if self.store.set_push_token(push_token, self.client_address[0]):
                self.store.add_event(
                    "push_token.reported",
                    {"pushToken": push_token[-12:] if len(push_token) > 12 else push_token},
                )
            return self._ok({})

        if path in ("/ipc/msg/notice/count", "/ipc/msg/notice/v3/count"):
            return self._ok(self.store.message_count_payload(deep_get(body, "typeList", [])))

        if path == "/ipc/msg/notice/v4/condition":
            return self._ok(self.store.message_filter_payload(str(deep_get(body, "date", "") or "")))

        if path == "/ipc/msg/notice/v4/page":
            return self._ok(
                self.store.message_list_payload(
                    date=str(deep_get(body, "date", "") or ""),
                    device_sn=str(deep_get(body, "deviceSn", "") or ""),
                    last_notice_id=int(deep_get(body, "lastNoticeId", 0) or 0),
                    page_size=int(deep_get(body, "pageSize", 20) or 20),
                    type_list=deep_get(body, "typeList", []),
                )
            )

        if path == "/ipc/msg/notice/remove":
            removed = self.store.remove_messages(deep_get(body, "noticeIds", ""))
            return self._ok({"removed": removed})

        if path == "/ipc/p2p/check-session-key":
            station_sn = str(
                deep_get(body, "stationSn")
                or deep_get(body, "deviceSn")
                or deep_get(body, "sn")
                or DEFAULT_STATION_SN
            )
            provided_key = str(
                deep_get(body, "sessionKey")
                or deep_get(body, "session_key")
                or deep_get(body, "key")
                or deep_get(body, "authKey")
                or ""
            )
            expected_key = str(
                deep_get(
                    self.store.state.get("stations", {}).get(station_sn, {}),
                    "sessionKey",
                    DEFAULT_SESSION_KEY,
                )
            )
            is_valid = bool(provided_key) and provided_key == expected_key
            return self._ok(
                {
                    "stationSn": station_sn,
                    "sessionKey": provided_key,
                    "valid": 1 if is_valid else 0,
                    "expired": 0,
                    "owner": 1,
                }
            )

        if path == "/ipc/p2p/get-session-key":
            station_sn = deep_get(body, "stationSn", DEFAULT_STATION_SN)
            return self._ok(
                [
                    {
                        "expire": 31536000,
                        "sessionKey": DEFAULT_SESSION_KEY,
                        "stationSn": station_sn,
                    }
                ]
            )

        if path == "/ipc/device/upgrade/check-version":
            return self._ok({"stationFirmList": []})

        if path == "/logs/item":
            return self._ok({})

        if path == "/ipc/p2p/get-did":
            station_sn = deep_get(body, "stationSn", query.get("stationSn", [DEFAULT_STATION_SN])[0])
            return self._ok(self.store.station_did_payload(station_sn))

        if path == "/debug/state":
            return self._ok(self.store.state)

        return self._ok({})

    def do_GET(self):
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        body = {}
        if parsed.path in {"/ipc", "/ipc/"}:
            self._html_response(
                """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>What can I do?</title>
  <style>
    body {
      margin: 0;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      background: #f5f5f7;
      color: #111827;
    }
    main {
      max-width: 720px;
      margin: 0 auto;
      padding: 28px 20px 40px;
    }
    h1 {
      font-size: 28px;
      margin: 0 0 12px;
    }
    p {
      line-height: 1.6;
      margin: 0 0 14px;
      color: #374151;
    }
    .card {
      background: #fff;
      border-radius: 16px;
      padding: 18px 18px 6px;
      box-shadow: 0 8px 24px rgba(15, 23, 42, 0.08);
    }
    ul {
      margin: 0;
      padding-left: 20px;
      line-height: 1.7;
      color: #374151;
    }
    li + li {
      margin-top: 8px;
    }
  </style>
</head>
<body>
  <main>
    <h1>What can I do?</h1>
    <p>The camera is currently unavailable for live view.</p>
    <div class="card">
      <ul>
        <li>Make sure the camera is powered on and has enough battery.</li>
        <li>Keep the camera close to the base station and check signal strength.</li>
        <li>Wait a moment for the base station to wake the camera, then try Live again.</li>
      </ul>
    </div>
  </main>
</body>
</html>"""
            )
            return
        entry = self._log_request("GET", parsed.path, query, body)
        response = self._route("GET", parsed.path, query, body)
        self._log_response(parsed.path, response)
        self.store.record_request(entry, response)
        self._json_response(response)

    def do_POST(self):
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        raw = self._read_body()
        body = self._parse_body(raw)
        entry = self._log_request("POST", parsed.path, query, body)
        response = self._route("POST", parsed.path, query, body)
        self._log_response(parsed.path, response)
        self.store.record_request(entry, response)
        self._json_response(response)

    def log_message(self, fmt, *args):
        return


class MockServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, addr, handler_cls, store, route_manager=None):
        super().__init__(addr, handler_cls)
        self.store = store
        self.route_manager = route_manager

    def get_request(self):
        request, client_address = super().get_request()
        try:
            request.settimeout(15)
        except Exception:
            pass
        debug_log(f"server.accept remote={client_address[0]}:{client_address[1]}")
        return request, client_address

    def process_request_thread(self, request, client_address):
        debug_log(
            f"server.thread.start remote={client_address[0]}:{client_address[1]}"
        )
        try:
            super().process_request_thread(request, client_address)
        finally:
            debug_log(
                f"server.thread.end remote={client_address[0]}:{client_address[1]}"
            )

    def finish_request(self, request, client_address):
        debug_log(
            f"server.finish_request remote={client_address[0]}:{client_address[1]}"
        )
        return super().finish_request(request, client_address)

    def shutdown_request(self, request):
        debug_log("server.shutdown_request")
        return super().shutdown_request(request)

    def handle_error(self, request, client_address):
        debug_log(f"server.error remote={client_address[0]}:{client_address[1]}")
        return super().handle_error(request, client_address)


def seed_state(store: StateStore, args):
    # Keep the mock empty unless requests explicitly create devices. This makes
    # it possible to re-run the app's first-time initialization flow.
    return


def parse_args():
    parser = argparse.ArgumentParser(description="Local Sunvalley/VAVA mock HTTPS service")
    parser.add_argument("--host", default=HOST)
    parser.add_argument("--port", type=int, default=PORT)
    parser.add_argument("--cert", type=Path, default=CERT)
    parser.add_argument("--key", type=Path, default=KEY)
    parser.add_argument("--state", type=Path, default=STATE_PATH)
    parser.add_argument("--db", type=Path, default=DB_PATH)
    parser.add_argument("--routes-dir", type=Path, default=ROUTES_DIR)
    parser.add_argument("--station-sn", default=DEFAULT_STATION_SN)
    parser.add_argument("--station-name", default=DEFAULT_STATION_NAME)
    parser.add_argument("--camera-sn", default=DEFAULT_CAMERA_SN)
    parser.add_argument("--camera-name", default=DEFAULT_CAMERA_NAME)
    parser.add_argument("--camera-channel", type=int, default=DEFAULT_CHANNEL)
    return parser.parse_args()


def main():
    args = parse_args()
    store = StateStore(args.state, args.db)
    route_manager = RouteManager(args.routes_dir, build_route_context())
    seed_state(store, args)
    httpd = MockServer((args.host, args.port), MockHandler, store, route_manager=route_manager)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    # The station firmware uses an old TLS stack; relax the server policy so the
    # base station can complete the HTTPS handshake.
    if hasattr(ssl, "TLSVersion"):
        try:
            ctx.minimum_version = ssl.TLSVersion.TLSv1
        except ValueError:
            pass
    if hasattr(ssl, "OP_NO_TLSv1_3"):
        ctx.options |= ssl.OP_NO_TLSv1_3
    try:
        ctx.set_ciphers("ALL:@SECLEVEL=0")
    except ssl.SSLError:
        pass
    ctx.load_cert_chain(certfile=str(args.cert), keyfile=str(args.key))
    httpd.socket = ctx.wrap_socket(httpd.socket, server_side=True)
    debug_log(
        f"mock sunvalley https listening on {args.host}:{args.port} state={args.state} db={args.db} routes={args.routes_dir}"
    )
    httpd.serve_forever()


if __name__ == "__main__":
    main()
