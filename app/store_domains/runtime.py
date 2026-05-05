from __future__ import annotations

import json
from typing import Any

from ..store_shared import DEFAULT_SESSION_KEY, deep_get, iso_now, now_ts
from .base import BaseDomainService


class RuntimeDomainService(BaseDomainService):
    def _user_id_for_access_token_unlocked(
        self,
        conn,
        access_token: str,
    ) -> str:
        token = str(access_token or "").strip()
        if not token:
            return ""
        row = conn.execute(
            """
            SELECT user_id
            FROM auth_tokens
            WHERE access_token = ?
            LIMIT 1
            """,
            (token,),
        ).fetchone()
        return str((row["user_id"] if row else "") or "").strip()

    def _coerce_switch_value(self, value: Any) -> Any:
        self = self.store
        if value is None:
            return None
        if isinstance(value, bool):
            return 1 if value else 0
        raw = str(value).strip().lower()
        if raw in {"1", "true", "on", "yes"}:
            return 1
        if raw in {"0", "false", "off", "no"}:
            return 0
        try:
            return 1 if int(raw) else 0
        except (TypeError, ValueError):
            return value

    def _trim_log_value(self, value: Any, depth: int = 0) -> Any:
        self = self.store
        if depth >= 4:
            return "<max-depth>"
        if isinstance(value, dict):
            out: dict[str, Any] = {}
            for index, (key, item) in enumerate(value.items()):
                if index >= 40:
                    out["<truncated>"] = f"{len(value) - 40} more keys"
                    break
                out[str(key)] = self._trim_log_value(item, depth + 1)
            return out
        if isinstance(value, (list, tuple, set)):
            items = list(value)
            out = [self._trim_log_value(item, depth + 1) for item in items[:30]]
            if len(items) > 30:
                out.append(f"<truncated {len(items) - 30} items>")
            return out
        if isinstance(value, bytes):
            return f"<bytes:{len(value)}>"
        if isinstance(value, str):
            return value if len(value) <= 600 else value[:600] + "...<truncated>"
        return value

    def consume_flag(self, key: str, default: bool = False) -> bool:
        self = self.store
        with self.lock, self._connect() as conn:
            state = self._load_state_unlocked(conn)
            meta = state.setdefault("meta", {})
            value = bool(meta.get(key, default))
            meta[key] = False
            self._save_state_unlocked(conn, state)
            return value

    def add_request(self, entry: dict[str, Any]) -> None:
        self = self.store
        with self.lock, self._connect() as conn:
            state = self._load_state_unlocked(conn)
            requests = state.setdefault("requests", [])
            requests.append(self._trim_log_value(entry))
            del requests[:-200]
            self._save_state_unlocked(conn, state)

    def append_request_log(self, entry: dict[str, Any]) -> None:
        self = self.store
        payload = self._trim_log_value(entry)
        with self.lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO request_logs (
                    created_at, method, path, remote, ua, query_json, body_json, status_code
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(payload.get("ts", iso_now()) or iso_now()),
                    str(payload.get("method", "") or ""),
                    str(payload.get("path", "") or ""),
                    str(payload.get("remote", "") or ""),
                    str(payload.get("ua", "") or ""),
                    json.dumps(payload.get("query", {}), ensure_ascii=False, sort_keys=True),
                    json.dumps(payload.get("body", {}), ensure_ascii=False, sort_keys=True),
                    int(payload.get("statusCode", 0) or 0),
                ),
            )
            conn.commit()

    def add_event(self, message: str, payload: dict[str, Any] | None = None) -> None:
        self = self.store
        with self.lock, self._connect() as conn:
            state = self._load_state_unlocked(conn)
            events = state.setdefault("events", [])
            events.append({"ts": iso_now(), "message": message, "payload": payload or {}})
            del events[:-200]
            self._save_state_unlocked(conn, state)

    def session_key_payload(self, station_sn: str) -> list[dict[str, Any]]:
        self = self.store
        with self._connect() as conn:
            state = self._load_state_unlocked(conn)
            session_key = str(
                deep_get(
                    state.get("stations", {}).get(station_sn, {}),
                    "sessionKey",
                    DEFAULT_SESSION_KEY,
                )
            )
            return [
                {
                    "expire": 31536000,
                    "sessionKey": session_key,
                    "stationSn": station_sn,
                }
            ]

    def check_session_key_payload(
        self,
        *,
        station_sn: str,
        provided_key: str,
    ) -> dict[str, Any]:
        self = self.store
        with self._connect() as conn:
            state = self._load_state_unlocked(conn)
            expected_key = str(
                deep_get(
                    state.get("stations", {}).get(station_sn, {}),
                    "sessionKey",
                    DEFAULT_SESSION_KEY,
                )
            )
            is_valid = bool(provided_key) and provided_key == expected_key
            owner_row = conn.execute(
                """
                SELECT COUNT(*) AS cnt
                FROM user_station_bindings
                WHERE station_sn = ? AND bind_state = 'bound'
                """,
                (str(station_sn or "").strip(),),
            ).fetchone()
            owner = 1 if int((owner_row["cnt"] if owner_row else 0) or 0) > 0 else 0
            return {
                "stationSn": station_sn,
                "sessionKey": provided_key,
                "valid": 1 if is_valid else 0,
                "expired": 0,
                "owner": owner,
            }

    def remember_station_access_token(self, station_sn: str, access_token: str) -> None:
        self = self.store
        station_sn = str(station_sn or "").strip()
        access_token = str(access_token or "").strip()
        if not station_sn or not access_token:
            return
        with self.lock, self._connect() as conn:
            state = self._load_state_unlocked(conn)
            token_map = state.setdefault("stationAccessTokens", {})
            token_map[access_token] = {
                "stationSn": station_sn,
                "updatedAt": iso_now(),
            }
            station_map = state.setdefault("stationAccessTokensByStation", {})
            station_map[station_sn] = {
                "accessToken": access_token,
                "updatedAt": iso_now(),
            }
            if len(token_map) > 100:
                items = list(token_map.items())[-100:]
                state["stationAccessTokens"] = dict(items)
            if len(station_map) > 100:
                items = list(station_map.items())[-100:]
                state["stationAccessTokensByStation"] = dict(items)
            self._save_state_unlocked(conn, state)

    def station_sn_for_access_token(self, access_token: str) -> str:
        self = self.store
        access_token = str(access_token or "").strip()
        if not access_token:
            return ""
        with self._connect() as conn:
            state = self._load_state_unlocked(conn)
            token_map = state.get("stationAccessTokens", {})
            entry = token_map.get(access_token)
            if isinstance(entry, dict):
                return str(entry.get("stationSn") or "")
            return ""

    def _normalize_station_pairing_locks(
        self,
        locks: Any,
        *,
        now_ts_value: int,
        keep_seconds: int = 600,
    ) -> dict[str, dict[str, Any]]:
        self = self.store
        if not isinstance(locks, dict):
            return {}
        normalized: dict[str, dict[str, Any]] = {}
        for raw_station_sn, raw_entry in locks.items():
            station_sn = str(raw_station_sn or "").strip()
            if not station_sn or not isinstance(raw_entry, dict):
                continue
            try:
                issued_at_ts = int(raw_entry.get("issuedAtTs", 0) or 0)
            except (TypeError, ValueError):
                issued_at_ts = 0
            if issued_at_ts <= 0:
                continue
            if now_ts_value - issued_at_ts > max(int(keep_seconds or 0), 60):
                continue
            normalized[station_sn] = {
                "stationSn": station_sn,
                "issuedAt": str(raw_entry.get("issuedAt") or iso_now()),
                "issuedAtTs": issued_at_ts,
                "accessToken": str(raw_entry.get("accessToken", "") or ""),
                "identifier": str(raw_entry.get("identifier", "") or ""),
                "userId": str(raw_entry.get("userId", "") or ""),
            }
        return normalized

    def mark_station_pairing_lock(
        self,
        *,
        station_sn: str,
        access_token: str = "",
        identifier: str = "",
        user_id: str = "",
    ) -> dict[str, Any]:
        self = self.store
        station_sn = str(station_sn or "").strip()
        if not station_sn:
            return {}
        token = str(access_token or "").strip()
        account = str(identifier or "").strip()
        explicit_uid = str(user_id or "").strip()
        timestamp = now_ts()
        with self.lock, self._connect() as conn:
            uid = explicit_uid or self._user_id_for_access_token_unlocked(conn, token)
            state = self._load_state_unlocked(conn)
            existing = state.get("stationPairingLocks", {})
            locks = self._normalize_station_pairing_locks(existing, now_ts_value=timestamp)
            entry = {
                "stationSn": station_sn,
                "issuedAt": iso_now(),
                "issuedAtTs": timestamp,
                "accessToken": token,
                "identifier": account,
                "userId": uid,
            }
            locks[station_sn] = entry
            state["stationPairingLocks"] = locks
            self._save_state_unlocked(conn, state)
            return json.loads(json.dumps(entry))

    def station_pairing_gate_payload(
        self,
        *,
        station_sn: str,
        access_token: str = "",
        identifier: str = "",
        user_id: str = "",
        min_lock_age_seconds: int = 8,
        max_lock_age_seconds: int = 180,
    ) -> dict[str, Any]:
        self = self.store
        station_sn = str(station_sn or "").strip()
        token = str(access_token or "").strip()
        account = str(identifier or "").strip()
        explicit_uid = str(user_id or "").strip()
        min_age = max(int(min_lock_age_seconds or 0), 0)
        max_age = max(int(max_lock_age_seconds or 0), max(min_age, 30))
        now_value = now_ts()
        with self.lock, self._connect() as conn:
            uid = explicit_uid or self._user_id_for_access_token_unlocked(conn, token)
            state = self._load_state_unlocked(conn)
            raw_locks = state.get("stationPairingLocks", {})
            locks = self._normalize_station_pairing_locks(raw_locks, now_ts_value=now_value)
            if raw_locks != locks:
                state["stationPairingLocks"] = locks
                self._save_state_unlocked(conn, state)
            entry = locks.get(station_sn) if station_sn else None
            station = state.get("stations", {}).get(station_sn, {})
            try:
                station_status_ts = int(
                    deep_get(station, "meta", {}).get("lastReportStatusTs", 0) or 0
                )
            except (TypeError, ValueError):
                station_status_ts = 0
            if not isinstance(entry, dict):
                return {
                    "ready": False,
                    "reason": "lock-required",
                    "stationSn": station_sn,
                    "lockAgeSeconds": -1,
                    "minLockAgeSeconds": min_age,
                    "maxLockAgeSeconds": max_age,
                    "stationStatusTs": station_status_ts,
                    "stationStatusAfterLock": 0,
                }
            lock_token = str(entry.get("accessToken", "") or "")
            lock_account = str(entry.get("identifier", "") or "")
            lock_uid = str(entry.get("userId", "") or "")
            if not lock_uid and lock_token:
                lock_uid = self._user_id_for_access_token_unlocked(conn, lock_token)
            # Scope lock to the account/session that requested lock to avoid
            # cross-account "borrowed" lock tickets. Allow token rotation
            # inside the same account by comparing resolved user_id first.
            same_user = bool(uid and lock_uid and uid == lock_uid)
            if token and lock_token and lock_token != token and not same_user:
                return {
                    "ready": False,
                    "reason": "lock-owner-mismatch",
                    "stationSn": station_sn,
                    "lockAgeSeconds": -1,
                    "minLockAgeSeconds": min_age,
                    "maxLockAgeSeconds": max_age,
                    "stationStatusTs": station_status_ts,
                    "stationStatusAfterLock": 0,
                }
            if uid and lock_uid and lock_uid != uid:
                return {
                    "ready": False,
                    "reason": "lock-owner-mismatch",
                    "stationSn": station_sn,
                    "lockAgeSeconds": -1,
                    "minLockAgeSeconds": min_age,
                    "maxLockAgeSeconds": max_age,
                    "stationStatusTs": station_status_ts,
                    "stationStatusAfterLock": 0,
                }
            if account and lock_account and lock_account.lower() != account.lower():
                return {
                    "ready": False,
                    "reason": "lock-owner-mismatch",
                    "stationSn": station_sn,
                    "lockAgeSeconds": -1,
                    "minLockAgeSeconds": min_age,
                    "maxLockAgeSeconds": max_age,
                    "stationStatusTs": station_status_ts,
                    "stationStatusAfterLock": 0,
                }
            try:
                lock_ts = int(entry.get("issuedAtTs", 0) or 0)
            except (TypeError, ValueError):
                lock_ts = 0
            if lock_ts <= 0:
                return {
                    "ready": False,
                    "reason": "lock-required",
                    "stationSn": station_sn,
                    "lockAgeSeconds": -1,
                    "minLockAgeSeconds": min_age,
                    "maxLockAgeSeconds": max_age,
                    "stationStatusTs": station_status_ts,
                    "stationStatusAfterLock": 0,
                }
            lock_age = max(now_value - lock_ts, 0)
            if lock_age > max_age:
                return {
                    "ready": False,
                    "reason": "lock-expired",
                    "stationSn": station_sn,
                    "lockAgeSeconds": lock_age,
                    "minLockAgeSeconds": min_age,
                    "maxLockAgeSeconds": max_age,
                    "stationStatusTs": station_status_ts,
                    "stationStatusAfterLock": 0,
                }
            station_status_after_lock = (
                1 if station_status_ts > 0 and station_status_ts >= lock_ts else 0
            )
            if lock_age < min_age and not station_status_after_lock:
                return {
                    "ready": False,
                    "reason": "wait-for-station-status",
                    "stationSn": station_sn,
                    "lockAgeSeconds": lock_age,
                    "minLockAgeSeconds": min_age,
                    "maxLockAgeSeconds": max_age,
                    "stationStatusTs": station_status_ts,
                    "stationStatusAfterLock": station_status_after_lock,
                }
            return {
                "ready": True,
                "reason": "ok",
                "stationSn": station_sn,
                "lockAgeSeconds": lock_age,
                "minLockAgeSeconds": min_age,
                "maxLockAgeSeconds": max_age,
                "stationStatusTs": station_status_ts,
                "stationStatusAfterLock": station_status_after_lock,
            }

    def state_summary(self) -> dict[str, Any]:
        self = self.store
        summary = {
            "dbPath": str(self.db_path),
            "dbExists": self.db_path.exists(),
            "statePath": str(self.state_path),
            "stateExists": self.state_path.exists(),
        }
        with self._connect() as conn:
            tables = {}
            for table in (
                "kv_state",
                "users",
                "auth_tokens",
                "verification_codes",
                "request_logs",
                "stations",
                "cameras",
                "user_station_bindings",
                "pairing_sessions",
                "pairing_slots",
            ):
                tables[table] = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            summary["tables"] = tables
            kv_state = {}
            for row in conn.execute("SELECT key, value_json FROM kv_state ORDER BY key").fetchall():
                try:
                    value = json.loads(row["value_json"])
                except json.JSONDecodeError:
                    value = None
                if isinstance(value, dict):
                    kv_state[row["key"]] = len(value)
                elif isinstance(value, list):
                    kv_state[row["key"]] = len(value)
                else:
                    kv_state[row["key"]] = value
            summary["kvState"] = kv_state
        return summary

    def recent_events(
        self,
        *,
        limit: int = 20,
        prefix: str = "",
    ) -> list[dict[str, Any]]:
        self = self.store
        with self._connect() as conn:
            state = self._load_state_unlocked(conn)
            events = list(state.get("events", []))
            if prefix:
                events = [
                    item
                    for item in events
                    if str(item.get("message", "") or "").startswith(prefix)
                ]
            return json.loads(json.dumps(events[-max(int(limit or 20), 1) :]))

    def push_status_payload(self, *, event_limit: int = 20) -> dict[str, Any]:
        self = self.store
        with self._connect() as conn:
            state = self._load_state_unlocked(conn)
            current_token = str(state.get("pushToken", "") or "").strip()
            registrations = self.push_registrations()
            events = [
                item
                for item in state.get("events", [])
                if str(item.get("message", "") or "").startswith("push.")
            ]
            return {
                "pushToken": current_token[-12:] if len(current_token) > 12 else current_token,
                "pushTokenPlatform": self._classify_push_token(current_token) if current_token else "unknown",
                "rawPushTokenCount": len(state.get("pushTokens", [])),
                "registrationCount": len(registrations),
                "registrations": registrations,
                "recentEvents": json.loads(
                    json.dumps(events[-max(int(event_limit or 20), 1) :])
                ),
                "lastPushTokenRemote": str(
                    state.get("meta", {}).get("lastPushTokenRemote", "") or ""
                ),
            }

    def dump_state(self) -> dict[str, Any]:
        self = self.store
        with self._connect() as conn:
            return self._load_state_unlocked(conn)
