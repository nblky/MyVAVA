from __future__ import annotations

import json
import os
import sqlite3
import threading
from functools import lru_cache
from pathlib import Path
from typing import Any

from .config import Settings, get_settings
from .store_domains import (
    init_store_domains,
    install_domain_delegates,
)
from .store_shared import (
    DEFAULT_CAMERA_NAME,
    DEFAULT_CAMERA_SN,
    DEFAULT_CHANNEL,
    DEFAULT_EMAIL,
    DEFAULT_PRODUCT_LINE_ID,
    DEFAULT_STATION_NAME,
    DEFAULT_STATION_SN,
    DEFAULT_USER_ID,
    deep_get,
    iso_now,
    stable_uid,
)

class AuthStore:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.db_path = Path(settings.db_path)
        self.state_path = Path(settings.state_path)
        self.backup_path = self.state_path.with_suffix(self.state_path.suffix + ".bak")
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.last_message_sync_at = 0.0
        self.last_message_sync_error = ""
        init_store_domains(self)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute("PRAGMA foreign_keys = ON")
            conn.executescript(
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
                    first_name TEXT NOT NULL DEFAULT '',
                    last_name TEXT NOT NULL DEFAULT '',
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

                CREATE TABLE IF NOT EXISTS verification_codes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    target TEXT NOT NULL,
                    purpose TEXT NOT NULL,
                    channel TEXT NOT NULL,
                    code TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS request_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    method TEXT NOT NULL,
                    path TEXT NOT NULL,
                    remote TEXT NOT NULL DEFAULT '',
                    ua TEXT NOT NULL DEFAULT '',
                    query_json TEXT NOT NULL,
                    body_json TEXT NOT NULL,
                    status_code INTEGER NOT NULL DEFAULT 0
                );

                CREATE TABLE IF NOT EXISTS stations (
                    station_sn TEXT PRIMARY KEY,
                    device_name TEXT NOT NULL DEFAULT '',
                    station_name TEXT NOT NULL DEFAULT '',
                    bind_id TEXT NOT NULL DEFAULT '',
                    camera_total INTEGER NOT NULL DEFAULT 0,
                    share_flag INTEGER NOT NULL DEFAULT 0,
                    share_time TEXT NOT NULL DEFAULT '',
                    sharer_name TEXT NOT NULL DEFAULT '',
                    station_online TEXT NOT NULL DEFAULT '1',
                    add_time TEXT NOT NULL,
                    did_json TEXT NOT NULL DEFAULT '{}',
                    status_json TEXT NOT NULL DEFAULT '{}',
                    attr_json TEXT NOT NULL DEFAULT '{}',
                    permission_json TEXT NOT NULL DEFAULT '{}',
                    owner_user_id TEXT NOT NULL DEFAULT '',
                    owner_username TEXT NOT NULL DEFAULT '',
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS cameras (
                    camera_sn TEXT PRIMARY KEY,
                    station_sn TEXT NOT NULL,
                    camera_name TEXT NOT NULL DEFAULT '',
                    bind_id TEXT NOT NULL DEFAULT '',
                    channel INTEGER NOT NULL DEFAULT 0,
                    add_time TEXT NOT NULL,
                    share_flag INTEGER NOT NULL DEFAULT 0,
                    share_time TEXT NOT NULL DEFAULT '',
                    sharer_name TEXT NOT NULL DEFAULT '',
                    station_online TEXT NOT NULL DEFAULT '1',
                    pending INTEGER NOT NULL DEFAULT 0,
                    cloud_storage_bound INTEGER NOT NULL DEFAULT 1,
                    status_json TEXT NOT NULL DEFAULT '{}',
                    attr_json TEXT NOT NULL DEFAULT '{}',
                    settings_json TEXT NOT NULL DEFAULT '{}',
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(station_sn) REFERENCES stations(station_sn) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS user_station_bindings (
                    user_id TEXT NOT NULL,
                    station_sn TEXT NOT NULL,
                    role TEXT NOT NULL DEFAULT 'owner',
                    bind_state TEXT NOT NULL DEFAULT 'bound',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (user_id, station_sn),
                    FOREIGN KEY(user_id) REFERENCES users(user_id) ON DELETE CASCADE,
                    FOREIGN KEY(station_sn) REFERENCES stations(station_sn) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS pairing_sessions (
                    session_id TEXT PRIMARY KEY,
                    station_sn TEXT NOT NULL DEFAULT '',
                    camera_sn TEXT NOT NULL DEFAULT '',
                    channel INTEGER NOT NULL DEFAULT 0,
                    stage TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT '',
                    payload_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS pairing_slots (
                    station_sn TEXT NOT NULL,
                    slot_index INTEGER NOT NULL,
                    active_flag INTEGER NOT NULL DEFAULT 0,
                    pending_pairlist_flag INTEGER NOT NULL DEFAULT 0,
                    peer_ipv4 TEXT NOT NULL DEFAULT '',
                    camera_mac TEXT NOT NULL DEFAULT '',
                    camera_sn TEXT NOT NULL DEFAULT '',
                    last_session_id TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (station_sn, slot_index),
                    FOREIGN KEY(station_sn) REFERENCES stations(station_sn) ON DELETE CASCADE
                );

                -- P2P DID pool used by station binding flow.
                -- Init script seeds test DID rows so add-station can allocate
                -- from DB first, then persist station<->DID mapping.
                CREATE TABLE IF NOT EXISTS p2p_did_pool (
                    sy_did TEXT PRIMARY KEY,
                    did_code TEXT NOT NULL,
                    init_code TEXT NOT NULL,
                    init_string TEXT NOT NULL,
                    crc_key TEXT NOT NULL,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    sort_order INTEGER NOT NULL DEFAULT 1000,
                    assigned_station_sn TEXT NOT NULL DEFAULT '',
                    assigned_user_id TEXT NOT NULL DEFAULT '',
                    source TEXT NOT NULL DEFAULT 'seed',
                    note TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_cameras_station_sn
                    ON cameras(station_sn);

                CREATE INDEX IF NOT EXISTS idx_user_station_bindings_station_sn
                    ON user_station_bindings(station_sn);

                CREATE INDEX IF NOT EXISTS idx_pairing_sessions_station_camera
                    ON pairing_sessions(station_sn, camera_sn);

                CREATE INDEX IF NOT EXISTS idx_pairing_slots_station_camera
                    ON pairing_slots(station_sn, camera_sn);

                CREATE INDEX IF NOT EXISTS idx_p2p_did_pool_assigned_station
                    ON p2p_did_pool(assigned_station_sn);
                """
            )
            user_columns = {
                row["name"] for row in conn.execute("PRAGMA table_info(users)").fetchall()
            }
            if "password_md5" not in user_columns:
                conn.execute(
                    "ALTER TABLE users ADD COLUMN password_md5 TEXT NOT NULL DEFAULT ''"
                )
            if "first_name" not in user_columns:
                conn.execute(
                    "ALTER TABLE users ADD COLUMN first_name TEXT NOT NULL DEFAULT ''"
                )
            if "last_name" not in user_columns:
                conn.execute(
                    "ALTER TABLE users ADD COLUMN last_name TEXT NOT NULL DEFAULT ''"
                )
            conn.commit()
            with self.lock:
                self.ensure_default_user(conn=conn)
                state = self._load_state_unlocked(conn)
                if self._seed_station_did_pool_unlocked(conn, state):
                    self._save_state_unlocked(conn, state)
        self.backfill_station_dids()

    def _write_json_snapshot(self, state: dict[str, Any]) -> None:
        payload = json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True)
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self.state_path.with_name(
            f"{self.state_path.name}.{os.getpid()}.{threading.get_ident()}.tmp"
        )
        with tmp_path.open("w", encoding="utf-8") as fh:
            fh.write(payload)
        os.replace(tmp_path, self.state_path)
        with self.backup_path.open("w", encoding="utf-8") as fh:
            fh.write(payload)

    def _station_owner_rows_unlocked(self, conn: sqlite3.Connection) -> dict[str, sqlite3.Row]:
        rows = conn.execute(
            """
            SELECT usb.station_sn, usb.user_id, u.username
            FROM user_station_bindings usb
            JOIN users u ON u.user_id = usb.user_id
            WHERE usb.bind_state = 'bound'
            ORDER BY CASE WHEN lower(usb.role) = 'owner' THEN 0 ELSE 1 END, usb.updated_at DESC
            """
        ).fetchall()
        owner_map: dict[str, sqlite3.Row] = {}
        for row in rows:
            station_sn = str(row["station_sn"] or "").strip()
            if station_sn and station_sn not in owner_map:
                owner_map[station_sn] = row
        return owner_map

    def _refresh_station_binding_cache_unlocked(
        self,
        conn: sqlite3.Connection,
        state: dict[str, Any],
    ) -> bool:
        changed = False
        owner_map = self._station_owner_rows_unlocked(conn)
        bindings_by_station: dict[str, int] = {}
        for row in conn.execute(
            "SELECT station_sn, COUNT(*) AS cnt FROM user_station_bindings WHERE bind_state = 'bound' GROUP BY station_sn"
        ).fetchall():
            bindings_by_station[str(row["station_sn"] or "").strip()] = int(row["cnt"] or 0)
        for station_sn, station in state.get("stations", {}).items():
            if not isinstance(station, dict):
                continue
            owner_row = owner_map.get(station_sn)
            owner_user_id = str((owner_row["user_id"] if owner_row else "") or "").strip()
            owner_username = str((owner_row["username"] if owner_row else "") or "").strip()
            binding_count = int(bindings_by_station.get(station_sn, 0) or 0)
            next_values = {
                "ownerUserId": owner_user_id,
                "ownerUsername": owner_username,
                "bindingCount": binding_count,
            }
            for key, value in next_values.items():
                if station.get(key) != value:
                    station[key] = value
                    changed = True
        return changed

    def _ensure_default_station_binding_unlocked(
        self,
        conn: sqlite3.Connection,
        state: dict[str, Any],
    ) -> bool:
        stations = state.get("stations", {})
        if not isinstance(stations, dict) or not stations:
            return False
        if DEFAULT_STATION_SN not in stations:
            return False
        tombstones = {
            str(item or "").strip()
            for item in state.get("stationTombstones", [])
            if str(item or "").strip()
        }
        if DEFAULT_STATION_SN in tombstones:
            return False
        # If this station is already bound to any user, never force-insert the
        # default account binding. Otherwise deleting from local@vava.invalid
        # can immediately "come back" on the next save cycle.
        row = conn.execute(
            """
            SELECT 1 FROM user_station_bindings
            WHERE station_sn = ? AND bind_state = 'bound'
            LIMIT 1
            """,
            (DEFAULT_STATION_SN,),
        ).fetchone()
        if row:
            return False
        row = conn.execute(
            """
            SELECT 1 FROM user_station_bindings
            WHERE user_id = ? AND station_sn = ?
            LIMIT 1
            """,
            (DEFAULT_USER_ID, DEFAULT_STATION_SN),
        ).fetchone()
        if row:
            return False
        now = iso_now()
        conn.execute(
            """
            INSERT INTO user_station_bindings (
                user_id, station_sn, role, bind_state, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (DEFAULT_USER_ID, DEFAULT_STATION_SN, "owner", "bound", now, now),
        )
        return True

    def _prune_orphan_relationship_rows_unlocked(
        self,
        conn: sqlite3.Connection,
        state: dict[str, Any],
    ) -> bool:
        changed = False
        station_sns = sorted(
            str(station_sn).strip()
            for station_sn, station in state.get("stations", {}).items()
            if str(station_sn).strip() and isinstance(station, dict)
        )
        if station_sns:
            placeholders = ",".join("?" for _ in station_sns)
            for table in ("user_station_bindings", "pairing_slots", "pairing_sessions"):
                cur = conn.execute(
                    f"DELETE FROM {table} WHERE station_sn NOT IN ({placeholders})",
                    tuple(station_sns),
                )
                if int(cur.rowcount or 0) > 0:
                    changed = True
        else:
            for table in ("user_station_bindings", "pairing_slots", "pairing_sessions"):
                cur = conn.execute(f"DELETE FROM {table}")
                if int(cur.rowcount or 0) > 0:
                    changed = True
        return changed

    def _promote_station_owner_if_missing_unlocked(
        self,
        conn: sqlite3.Connection,
        station_sn: str,
    ) -> bool:
        station_sn = str(station_sn or "").strip()
        if not station_sn:
            return False
        owner_row = conn.execute(
            """
            SELECT 1
            FROM user_station_bindings
            WHERE station_sn = ? AND bind_state = 'bound' AND lower(role) = 'owner'
            LIMIT 1
            """,
            (station_sn,),
        ).fetchone()
        if owner_row:
            return False
        candidate = conn.execute(
            """
            SELECT user_id
            FROM user_station_bindings
            WHERE station_sn = ? AND bind_state = 'bound'
            ORDER BY updated_at DESC, user_id ASC
            LIMIT 1
            """,
            (station_sn,),
        ).fetchone()
        if not candidate:
            return False
        conn.execute(
            """
            UPDATE user_station_bindings
            SET role = 'owner', updated_at = ?
            WHERE station_sn = ? AND user_id = ?
            """,
            (
                iso_now(),
                station_sn,
                str(candidate["user_id"] or "").strip(),
            ),
        )
        return True

    def _sync_device_tables_unlocked(
        self,
        conn: sqlite3.Connection,
        state: dict[str, Any],
    ) -> bool:
        binding_cache_changed = self._refresh_station_binding_cache_unlocked(conn, state)
        station_sns: set[str] = set()
        for station_sn, station in state.get("stations", {}).items():
            if not isinstance(station, dict):
                continue
            station_sns.add(station_sn)
            conn.execute(
                """
                INSERT INTO stations (
                    station_sn, device_name, station_name, bind_id, camera_total,
                    share_flag, share_time, sharer_name, station_online, add_time,
                    did_json, status_json, attr_json, permission_json,
                    owner_user_id, owner_username, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(station_sn) DO UPDATE SET
                    device_name = excluded.device_name,
                    station_name = excluded.station_name,
                    bind_id = excluded.bind_id,
                    camera_total = excluded.camera_total,
                    share_flag = excluded.share_flag,
                    share_time = excluded.share_time,
                    sharer_name = excluded.sharer_name,
                    station_online = excluded.station_online,
                    add_time = excluded.add_time,
                    did_json = excluded.did_json,
                    status_json = excluded.status_json,
                    attr_json = excluded.attr_json,
                    permission_json = excluded.permission_json,
                    owner_user_id = excluded.owner_user_id,
                    owner_username = excluded.owner_username,
                    updated_at = excluded.updated_at
                """,
                (
                    station_sn,
                    str(station.get("deviceName", "") or station.get("stationName", "") or station_sn),
                    str(station.get("stationName", "") or station.get("deviceName", "") or station_sn),
                    str(station.get("bindId", "") or f"bind-station-{station_sn}"),
                    int(station.get("cameraTotal", 0) or 0),
                    int(station.get("shareFlag", 0) or 0),
                    str(station.get("shareTime", "") or ""),
                    str(station.get("sharerName", "") or ""),
                    str(station.get("stationOnline", "1") or "1"),
                    str(station.get("addTime", "") or iso_now()),
                    json.dumps(station.get("did", {}) or {}, ensure_ascii=False, sort_keys=True),
                    json.dumps(station.get("statusObject", {}) or {}, ensure_ascii=False, sort_keys=True),
                    json.dumps(station.get("attrObject", {}) or {}, ensure_ascii=False, sort_keys=True),
                    json.dumps(station.get("permissionObject", {}) or {}, ensure_ascii=False, sort_keys=True),
                    str(station.get("ownerUserId", "") or ""),
                    str(station.get("ownerUsername", "") or ""),
                    str(state.get("meta", {}).get("updatedAt", "") or iso_now()),
                ),
            )

        camera_sns: set[str] = set()
        for camera_sn, camera in state.get("cameras", {}).items():
            if not isinstance(camera, dict):
                continue
            camera_sns.add(camera_sn)
            conn.execute(
                """
                INSERT INTO cameras (
                    camera_sn, station_sn, camera_name, bind_id, channel,
                    add_time, share_flag, share_time, sharer_name, station_online,
                    pending, cloud_storage_bound, status_json, attr_json, settings_json, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(camera_sn) DO UPDATE SET
                    station_sn = excluded.station_sn,
                    camera_name = excluded.camera_name,
                    bind_id = excluded.bind_id,
                    channel = excluded.channel,
                    add_time = excluded.add_time,
                    share_flag = excluded.share_flag,
                    share_time = excluded.share_time,
                    sharer_name = excluded.sharer_name,
                    station_online = excluded.station_online,
                    pending = excluded.pending,
                    cloud_storage_bound = excluded.cloud_storage_bound,
                    status_json = excluded.status_json,
                    attr_json = excluded.attr_json,
                    settings_json = excluded.settings_json,
                    updated_at = excluded.updated_at
                """,
                (
                    camera_sn,
                    str(camera.get("stationSn", "") or DEFAULT_STATION_SN),
                    str(camera.get("cameraName", "") or camera_sn),
                    str(camera.get("bindId", "") or f"bind-camera-{camera_sn}"),
                    int(camera.get("channel", 0) or 0),
                    str(camera.get("addTime", "") or iso_now()),
                    int(camera.get("shareFlag", 0) or 0),
                    str(camera.get("shareTime", "") or ""),
                    str(camera.get("sharerName", "") or ""),
                    str(camera.get("stationOnline", "1") or "1"),
                    1 if bool(camera.get("pending", False)) else 0,
                    int(camera.get("cloudStorageBound", 1) or 0),
                    json.dumps(camera.get("statusObject", {}) or {}, ensure_ascii=False, sort_keys=True),
                    json.dumps(camera.get("attrObject", {}) or {}, ensure_ascii=False, sort_keys=True),
                    json.dumps(camera.get("settingsObject", {}) or {}, ensure_ascii=False, sort_keys=True),
                    str(state.get("meta", {}).get("updatedAt", "") or iso_now()),
                ),
            )
        if station_sns:
            placeholders = ",".join("?" for _ in station_sns)
            conn.execute(
                f"DELETE FROM stations WHERE station_sn NOT IN ({placeholders})",
                tuple(sorted(station_sns)),
            )
        else:
            conn.execute("DELETE FROM stations")
        if camera_sns:
            placeholders = ",".join("?" for _ in camera_sns)
            conn.execute(
                f"DELETE FROM cameras WHERE camera_sn NOT IN ({placeholders})",
                tuple(sorted(camera_sns)),
            )
        else:
            conn.execute("DELETE FROM cameras")
        return binding_cache_changed

    def _sync_state_tables_and_bindings_unlocked(
        self,
        conn: sqlite3.Connection,
        state: dict[str, Any],
    ) -> bool:
        changed = self._sync_device_tables_unlocked(conn, state)
        if self._prune_orphan_relationship_rows_unlocked(conn, state):
            changed = True
        if self._ensure_default_station_binding_unlocked(conn, state):
            changed = True
        if self._refresh_station_binding_cache_unlocked(conn, state):
            changed = True
        if changed:
            self._sync_device_tables_unlocked(conn, state)
        return changed

    def _load_state_unlocked(self, conn: sqlite3.Connection) -> dict[str, Any]:
        rows = conn.execute("SELECT key, value_json FROM kv_state ORDER BY key").fetchall()
        if rows:
            state: dict[str, Any] = {}
            try:
                for row in rows:
                    state[row["key"]] = json.loads(row["value_json"])
            except json.JSONDecodeError:
                state = self._default_state()
                self._save_state_unlocked(conn, state)
                return state
            changed = self._normalize_state_unlocked(state)
            if self._sync_state_tables_and_bindings_unlocked(conn, state):
                changed = True
            if changed:
                self._save_state_unlocked(conn, state)
            else:
                conn.commit()
            return state
        state = self._default_state()
        self._save_state_unlocked(conn, state)
        return state

    def _save_state_unlocked(self, conn: sqlite3.Connection, state: dict[str, Any]) -> None:
        self._normalize_state_unlocked(state)
        state.setdefault("meta", {})["updatedAt"] = iso_now()
        self._sync_state_tables_and_bindings_unlocked(conn, state)
        rows = [
            (
                str(key),
                json.dumps(value, ensure_ascii=False, sort_keys=True),
                state["meta"]["updatedAt"],
            )
            for key, value in state.items()
        ]
        conn.execute("DELETE FROM kv_state")
        conn.executemany(
            "INSERT INTO kv_state (key, value_json, updated_at) VALUES (?, ?, ?)",
            rows,
        )
        self._sync_device_tables_unlocked(conn, state)
        conn.commit()
        self._write_json_snapshot(state)

    def _default_state(self) -> dict[str, Any]:
        station = self._build_station(DEFAULT_STATION_SN, DEFAULT_STATION_NAME)
        camera = self._build_camera(
            DEFAULT_CAMERA_SN,
            DEFAULT_CAMERA_NAME,
            DEFAULT_STATION_SN,
            DEFAULT_CHANNEL,
        )
        station["cameraTotal"] = 1
        state = {
            "meta": {
                "createdAt": iso_now(),
                "updatedAt": iso_now(),
            },
            "auth": {
                "access_token": "local-access-token",
                "refresh_token": "local-refresh-token",
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
                "firstName": "",
                "first_name": "",
                "lastName": "",
                "last_name": "",
                "mobile": "",
                "name": "Local VAVA",
            },
            "stations": {
                DEFAULT_STATION_SN: station,
            },
            "stationDidMappings": {
                DEFAULT_STATION_SN: dict(station.get("did", {})),
            },
            "cameras": {
                DEFAULT_CAMERA_SN: camera,
            },
            "requests": [],
            "events": [],
            "messages": [],
            "messageCounter": 1,
            "pushToken": "",
            "pushTokens": [],
            "cloudMedia": [],
            "storageOrders": [],
        }
        state["storageService"] = self._default_storage_service_unlocked(state)
        return state

    def _normalize_state_unlocked(self, state: dict[str, Any]) -> bool:
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
        if not isinstance(state.setdefault("messageTombstones", []), list):
            state["messageTombstones"] = []
            changed = True
        if not isinstance(state.setdefault("stationTombstones", []), list):
            state["stationTombstones"] = []
            changed = True
        if not isinstance(state.setdefault("cameraTombstones", []), list):
            state["cameraTombstones"] = []
            changed = True
        if not isinstance(state.setdefault("stationDidMappings", {}), dict):
            state["stationDidMappings"] = {}
            changed = True
        state.setdefault("messageCounter", 1)
        state.setdefault("pushToken", "")
        state.setdefault("pushTokens", [])
        if not isinstance(state.setdefault("pushRegistrations", []), list):
            state["pushRegistrations"] = []
            changed = True
        if not isinstance(state.setdefault("storageOrders", []), list):
            state["storageOrders"] = []
            changed = True
        if not isinstance(state.setdefault("cloudMedia", []), list):
            state["cloudMedia"] = []
            changed = True
        state.setdefault("auth", {})
        user = state.setdefault("user", {})
        for key, value in {
            "address": "",
            "birthday": "",
            "city": "",
            "country": "",
            "firstName": "",
            "first_name": "",
            "headPortrait": user.get("headPortrait", user.get("avatar", "")) or "",
            "industry": "",
            "lastName": "",
            "last_name": "",
            "mobile": user.get("mobile", "") or "",
            "name": user.get("name", "") or user.get("nickname", "") or "",
            "productLineId": DEFAULT_PRODUCT_LINE_ID,
            "province": "",
            "sex": int(user.get("sex", 0) or 0),
            "tenantId": user.get("tenantId", "") or "",
            "uid": int(user.get("uid", stable_uid(user.get("userid", DEFAULT_USER_ID))) or 0),
            "userId": user.get("userId", user.get("userid", DEFAULT_USER_ID)) or DEFAULT_USER_ID,
        }.items():
            if key not in user:
                user[key] = value
                changed = True
        for station_sn, station in state.setdefault("stations", {}).items():
            changed = self._normalize_station_record(station_sn, station) or changed
        mappings_before = (
            dict(state.get("stationDidMappings", {}))
            if isinstance(state.get("stationDidMappings", {}), dict)
            else state.get("stationDidMappings")
        )
        normalized_mappings = self._station_did_mappings_unlocked(state)
        if mappings_before != normalized_mappings:
            changed = True
        for camera_sn, camera in state.setdefault("cameras", {}).items():
            changed = self._normalize_camera_record(camera_sn, camera) or changed
        normalized_cloud_media: list[dict[str, Any]] = []
        for item in state.get("cloudMedia", []):
            if not isinstance(item, dict):
                changed = True
                continue
            item_copy = json.loads(json.dumps(item))
            changed = self._normalize_cloud_media_record_unlocked(item_copy) or changed
            normalized_cloud_media.append(item_copy)
        normalized_cloud_media.sort(
            key=lambda item: (
                str(item.get("updatedAt", "") or ""),
                str(item.get("streamCode", "") or ""),
            ),
            reverse=True,
        )
        if state.get("cloudMedia") != normalized_cloud_media[:200]:
            state["cloudMedia"] = normalized_cloud_media[:200]
            changed = True
        normalized_tombstones: list[str] = []
        seen_tombstones: set[str] = set()
        for raw in state.get("messageTombstones", []):
            key = self._message_tombstone_key(stream_code=raw)
            if not key or key in seen_tombstones:
                if raw:
                    changed = True
                continue
            seen_tombstones.add(key)
            normalized_tombstones.append(key)
        if state.get("messageTombstones") != normalized_tombstones[-1000:]:
            state["messageTombstones"] = normalized_tombstones[-1000:]
            changed = True
        normalized_camera_tombstones: list[str] = []
        seen_camera_tombstones: set[str] = set()
        for raw in state.get("cameraTombstones", []):
            key = self._camera_tombstone_key(raw)
            if not key or key in seen_camera_tombstones:
                if raw:
                    changed = True
                continue
            seen_camera_tombstones.add(key)
            normalized_camera_tombstones.append(key)
        if state.get("cameraTombstones") != normalized_camera_tombstones[-1000:]:
            state["cameraTombstones"] = normalized_camera_tombstones[-1000:]
            changed = True
        normalized_station_tombstones: list[str] = []
        seen_station_tombstones: set[str] = set()
        for raw in state.get("stationTombstones", []):
            key = str(raw or "").strip()
            if not key or key in seen_station_tombstones:
                if raw:
                    changed = True
                continue
            seen_station_tombstones.add(key)
            normalized_station_tombstones.append(key)
        if state.get("stationTombstones") != normalized_station_tombstones[-500:]:
            state["stationTombstones"] = normalized_station_tombstones[-500:]
            changed = True
        changed = self._normalize_storage_state_unlocked(state) or changed
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

install_domain_delegates(AuthStore)


@lru_cache(maxsize=1)
def get_store() -> AuthStore:
    return AuthStore(get_settings())
