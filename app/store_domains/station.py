from __future__ import annotations

import sqlite3
from typing import Any

from ..station_did import (
    configured_station_did,
    default_station_did_storage,
    derive_station_did_storage,
    is_placeholder_station_did_value,
    normalize_station_did_storage,
    render_station_did_payload,
    static_test_station_did_pool,
    STATION_DID_KEYS,
)
from ..store_shared import DEFAULT_STATION_SN, DEFAULT_USER_ID, deep_get, iso_now, now_ts
from .base import BaseDomainService


class StationDomainService(BaseDomainService):
    def _station_tombstone_key(self, station_sn: str = "") -> str:
        store = self.store if hasattr(self, "store") else self
        return str(station_sn or "").strip()

    def _station_is_removed_unlocked(self, state: dict[str, Any], station_sn: str) -> bool:
        store = self.store if hasattr(self, "store") else self
        key = store._station_tombstone_key(station_sn)
        if not key:
            return False
        return key in {
            str(item or "").strip()
            for item in state.get("stationTombstones", [])
            if str(item or "").strip()
        }

    def _remember_removed_station_unlocked(self, state: dict[str, Any], station_sn: str) -> bool:
        store = self.store if hasattr(self, "store") else self
        key = store._station_tombstone_key(station_sn)
        if not key:
            return False
        tombstones = state.setdefault("stationTombstones", [])
        if key in tombstones:
            return False
        tombstones.append(key)
        if len(tombstones) > 500:
            del tombstones[:-500]
        return True

    def _forget_removed_station_unlocked(self, state: dict[str, Any], station_sn: str) -> bool:
        store = self.store if hasattr(self, "store") else self
        key = store._station_tombstone_key(station_sn)
        if not key:
            return False
        tombstones = state.setdefault("stationTombstones", [])
        filtered = [item for item in tombstones if store._station_tombstone_key(item) != key]
        if filtered == tombstones:
            return False
        state["stationTombstones"] = filtered
        return True

    def _remove_station_from_state_unlocked(
        self,
        state: dict[str, Any],
        station_sn: str,
    ) -> list[str]:
        store = self.store if hasattr(self, "store") else self
        removed_cameras: list[str] = []
        stations = state.setdefault("stations", {})
        station = stations.pop(station_sn, None)
        if not station:
            return removed_cameras
        mappings = state.setdefault("stationDidMappings", {})
        if isinstance(mappings, dict):
            mappings.pop(station_sn, None)
        cameras = state.setdefault("cameras", {})
        for camera_sn in list(cameras.keys()):
            camera = cameras.get(camera_sn, {})
            if camera.get("stationSn") != station_sn:
                continue
            removed_cameras.append(camera_sn)
            store._remember_removed_camera_unlocked(state, camera_sn)
            cameras.pop(camera_sn, None)
        store._remember_removed_station_unlocked(state, station_sn)
        return removed_cameras

    def _build_station(self, station_sn: str, station_name: str) -> dict[str, Any]:
        self = self.store
        station_value = str(station_sn or "").strip()
        if station_value == DEFAULT_STATION_SN:
            did = configured_station_did(self.settings, station_value)
        else:
            did = self._default_station_did_storage()
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
            "did": did,
            "didUserId": "",
            "statusObject": {
                "buzzer": 0,
                "freesize": 8025,
                "nas_freesize": 0,
                "nas_totolsize": 0,
                "nas_usedsize": 0,
                "nasstatus": 0,
                "sdstatus": 1,
                "session": 1,
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
                "f_firmnum": "P020201",
                "h_appversionin": "010000",
                "h_appversionout": "010000",
                "h_devicenum": "P020201",
            },
            "permissionObject": {
                "shareFlag": 0,
                "realtimeVideo": 1,
                "msgAndPlayback": 1,
            },
        }

    def _normalize_station_hardver(self, hardver: str) -> str:
        self = self.store
        mapping = {
            "VA-HS002": "VA-GW01",
            "VA-HS003": "VA-GW02",
            "VA-HS004": "VA-GW04",
        }
        return mapping.get(hardver, hardver or "VA-GW02")

    def _normalize_station_model(self, hardver: str, fallback: str = "") -> str:
        self = self.store
        mapping = {
            "VA-GW01": "VA-HS002",
            "VA-GW02": "P020201",
            "VA-GW04": "VA-HS004",
        }
        if fallback and fallback != "VA-HS003":
            return fallback
        return mapping.get(hardver, hardver or "P020201")

    def _default_station_did_storage(self) -> dict[str, str]:
        self = self.store
        return default_station_did_storage(
            init_code=self.settings.default_station_init_code,
            crc_key=self.settings.default_station_crc_key,
        )

    def _station_did_mappings_unlocked(self, state: dict[str, Any]) -> dict[str, dict[str, str]]:
        self = self.store
        raw = state.setdefault("stationDidMappings", {})
        if not isinstance(raw, dict):
            raw = {}
            state["stationDidMappings"] = raw
        normalized: dict[str, dict[str, str]] = {}
        for key, value in raw.items():
            station_sn = str(key or "").strip()
            did = normalize_station_did_storage(value)
            if not station_sn or not did:
                continue
            normalized[station_sn] = did
        if normalized != raw:
            state["stationDidMappings"] = normalized
        return state["stationDidMappings"]

    def _station_did_is_real_unlocked(self, did: dict[str, Any] | None) -> bool:
        self = self.store
        normalized = normalize_station_did_storage(did or {})
        if not normalized:
            return False
        for key in ("didCode", "syDid"):
            if is_placeholder_station_did_value(key, normalized.get(key)):
                return False
        return True

    def _station_did_mapping_for_station_unlocked(
        self,
        state: dict[str, Any],
        station_sn: str,
    ) -> dict[str, str]:
        self = self.store
        station_key = str(station_sn or "").strip()
        if not station_key:
            return {}
        mappings = self._station_did_mappings_unlocked(state)
        return normalize_station_did_storage(mappings.get(station_key, {}))

    def _station_did_from_pool_row_unlocked(self, row: Any) -> dict[str, str]:
        self = self.store
        if row is None:
            return {}
        try:
            did = {
                "didCode": str(row["did_code"] or "").strip(),
                "initCode": str(row["init_code"] or "").strip(),
                "crcKey": str(row["crc_key"] or "").strip(),
                "syDid": str(row["sy_did"] or "").strip(),
                "initString": str(row["init_string"] or "").strip(),
            }
        except Exception:
            return {}
        return normalize_station_did_storage(did)

    def _upsert_station_did_pool_entry_unlocked(
        self,
        conn: sqlite3.Connection,
        *,
        did: dict[str, Any],
        source: str = "seed",
        note: str = "",
        sort_order: int = 1000,
    ) -> tuple[str, bool]:
        self = self.store
        normalized = normalize_station_did_storage(did or {})
        if not self._station_did_is_real_unlocked(normalized):
            return "", False
        sy_did = str(normalized.get("syDid") or normalized.get("didCode") or "").split(",", 1)[0].strip()
        if not sy_did:
            return "", False
        did_code = str(normalized.get("didCode") or sy_did).strip()
        init_code = str(normalized.get("initCode") or normalized.get("initString") or "").strip()
        init_string = str(normalized.get("initString") or init_code).strip()
        crc_key = str(normalized.get("crcKey") or "").strip()
        now = iso_now()
        existing = conn.execute(
            """
            SELECT did_code, init_code, init_string, crc_key, enabled, sort_order, source, note
            FROM p2p_did_pool
            WHERE sy_did = ?
            LIMIT 1
            """,
            (sy_did,),
        ).fetchone()
        if existing is None:
            conn.execute(
                """
                INSERT INTO p2p_did_pool (
                    sy_did, did_code, init_code, init_string, crc_key,
                    enabled, sort_order, assigned_station_sn, assigned_user_id,
                    source, note, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, 1, ?, '', '', ?, ?, ?, ?)
                """,
                (
                    sy_did,
                    did_code,
                    init_code,
                    init_string,
                    crc_key,
                    int(sort_order),
                    str(source or "seed"),
                    str(note or ""),
                    now,
                    now,
                ),
            )
            return sy_did, True
        changed = False
        updates: dict[str, Any] = {}
        for key, value in {
            "did_code": did_code,
            "init_code": init_code,
            "init_string": init_string,
            "crc_key": crc_key,
            "enabled": 1,
            "sort_order": int(sort_order),
        }.items():
            if str(existing[key] if key in existing.keys() else "") != str(value):
                updates[key] = value
        source_value = str(source or "").strip()
        note_value = str(note or "").strip()
        if source_value and str(existing["source"] or "").strip() != source_value:
            updates["source"] = source_value
        if note_value and str(existing["note"] or "").strip() != note_value:
            updates["note"] = note_value
        if updates:
            updates["updated_at"] = now
            clause = ", ".join(f"{key} = ?" for key in updates)
            params = list(updates.values()) + [sy_did]
            conn.execute(f"UPDATE p2p_did_pool SET {clause} WHERE sy_did = ?", tuple(params))
            changed = True
        return sy_did, changed

    def _assign_station_did_pool_entry_unlocked(
        self,
        conn: sqlite3.Connection,
        *,
        sy_did: str,
        station_sn: str,
        user_id: str,
    ) -> tuple[bool, bool]:
        self = self.store
        sy_key = str(sy_did or "").strip()
        station_key = str(station_sn or "").strip()
        user_key = str(user_id or "").strip()
        if not sy_key or not station_key:
            return False, False
        row = conn.execute(
            """
            SELECT sy_did, assigned_station_sn, assigned_user_id
            FROM p2p_did_pool
            WHERE sy_did = ? AND enabled = 1
            LIMIT 1
            """,
            (sy_key,),
        ).fetchone()
        if row is None:
            return False, False
        owner_station = str(row["assigned_station_sn"] or "").strip()
        if owner_station and owner_station != station_key:
            return False, False
        changed = False
        now = iso_now()
        released = conn.execute(
            """
            UPDATE p2p_did_pool
            SET assigned_station_sn = '', assigned_user_id = '', updated_at = ?
            WHERE assigned_station_sn = ? AND sy_did <> ?
            """,
            (now, station_key, sy_key),
        )
        if int(released.rowcount or 0) > 0:
            changed = True
        owner_user = str(row["assigned_user_id"] or "").strip()
        if owner_station != station_key or owner_user != user_key:
            conn.execute(
                """
                UPDATE p2p_did_pool
                SET assigned_station_sn = ?, assigned_user_id = ?, updated_at = ?
                WHERE sy_did = ?
                """,
                (station_key, user_key, now, sy_key),
            )
            changed = True
        return True, changed

    def _release_station_did_pool_for_station_unlocked(
        self,
        conn: sqlite3.Connection,
        *,
        station_sn: str,
    ) -> bool:
        self = self.store
        station_key = str(station_sn or "").strip()
        if not station_key:
            return False
        cur = conn.execute(
            """
            UPDATE p2p_did_pool
            SET assigned_station_sn = '', assigned_user_id = '', updated_at = ?
            WHERE assigned_station_sn = ?
            """,
            (iso_now(), station_key),
        )
        return int(cur.rowcount or 0) > 0

    def _apply_station_did_to_state_unlocked(
        self,
        state: dict[str, Any],
        *,
        station_sn: str,
        user_id: str,
        did: dict[str, Any],
        source: str = "",
    ) -> bool:
        self = self.store
        station_key = str(station_sn or "").strip()
        user_key = str(user_id or "").strip()
        normalized = normalize_station_did_storage(did or {})
        if not station_key or not self._station_did_is_real_unlocked(normalized):
            return False
        _, changed = self._upsert_station_did_mapping_unlocked(
            state,
            station_sn=station_key,
            did=normalized,
        )
        stations = state.setdefault("stations", {})
        station = stations.get(station_key)
        if not isinstance(station, dict):
            station = self._build_station(station_key, station_key)
            stations[station_key] = station
            changed = True
        if normalize_station_did_storage(station.get("did", {})) != normalized:
            station["did"] = dict(normalized)
            changed = True
        if str(station.get("didUserId", "") or "").strip() != user_key:
            station["didUserId"] = user_key
            changed = True
        if source:
            meta = station.setdefault("meta", {})
            if str(meta.get("didSource", "") or "").strip() != source:
                meta["didSource"] = source
                changed = True
        return changed

    def _seed_station_did_pool_unlocked(
        self,
        conn: sqlite3.Connection,
        state: dict[str, Any],
    ) -> bool:
        self = self.store
        changed = False
        seed_items = static_test_station_did_pool(
            init_code=self.settings.default_station_init_code,
            crc_key=self.settings.default_station_crc_key,
        )
        for idx, did in enumerate(seed_items):
            _, row_changed = self._upsert_station_did_pool_entry_unlocked(
                conn,
                did=did,
                source="seed:init",
                note="default static test did",
                sort_order=idx,
            )
            if row_changed:
                changed = True
        stations = state.get("stations", {})
        if isinstance(stations, dict):
            seen_station_sns: set[str] = set()
            for station_sn in sorted(stations.keys()):
                station_key = str(station_sn or "").strip()
                if not station_key:
                    continue
                seen_station_sns.add(station_key)
                did = self._station_did_from_state_unlocked(state, station_key)
                if not self._station_did_is_real_unlocked(did):
                    continue
                sy_did, upsert_changed = self._upsert_station_did_pool_entry_unlocked(
                    conn,
                    did=did,
                    source="state:station",
                    sort_order=1000,
                )
                if upsert_changed:
                    changed = True
                if not sy_did:
                    continue
                station = stations.get(station_key, {})
                owner_user_id = str(
                    (station or {}).get("ownerUserId")
                    or (station or {}).get("didUserId")
                    or ""
                ).strip()
                ok, assign_changed = self._assign_station_did_pool_entry_unlocked(
                    conn,
                    sy_did=sy_did,
                    station_sn=station_key,
                    user_id=owner_user_id,
                )
                if ok and assign_changed:
                    changed = True
            assigned_rows = conn.execute(
                """
                SELECT sy_did, assigned_station_sn
                FROM p2p_did_pool
                WHERE assigned_station_sn <> ''
                """
            ).fetchall()
            for row in assigned_rows:
                assigned_station = str(row["assigned_station_sn"] or "").strip()
                if assigned_station and assigned_station in seen_station_sns:
                    continue
                cur = conn.execute(
                    """
                    UPDATE p2p_did_pool
                    SET assigned_station_sn = '', assigned_user_id = '', updated_at = ?
                    WHERE sy_did = ?
                    """,
                    (iso_now(), str(row["sy_did"] or "").strip()),
                )
                if int(cur.rowcount or 0) > 0:
                    changed = True
        return changed

    def _assign_station_did_from_pool_unlocked(
        self,
        conn: sqlite3.Connection,
        state: dict[str, Any],
        *,
        station_sn: str,
        user_id: str,
    ) -> tuple[dict[str, str], bool]:
        self = self.store
        station_key = str(station_sn or "").strip()
        user_key = str(user_id or "").strip()
        if not station_key:
            return {}, False
        changed = False

        def _try_candidate(candidate: dict[str, Any], *, source: str) -> dict[str, str]:
            nonlocal changed
            normalized = normalize_station_did_storage(candidate or {})
            if not self._station_did_is_real_unlocked(normalized):
                return {}
            sy_did, upsert_changed = self._upsert_station_did_pool_entry_unlocked(
                conn,
                did=normalized,
                source=source,
                sort_order=1000,
            )
            if upsert_changed:
                changed = True
            if not sy_did:
                return {}
            ok, assign_changed = self._assign_station_did_pool_entry_unlocked(
                conn,
                sy_did=sy_did,
                station_sn=station_key,
                user_id=user_key,
            )
            if not ok:
                return {}
            if assign_changed:
                changed = True
            if self._apply_station_did_to_state_unlocked(
                state,
                station_sn=station_key,
                user_id=user_key,
                did=normalized,
                source=f"pool:{source}",
            ):
                changed = True
            return normalized

        preferred_candidates = [
            (self._station_did_mapping_for_station_unlocked(state, station_key), "mapped"),
            (
                normalize_station_did_storage(
                    deep_get(state.get("stations", {}).get(station_key, {}), "reportedDid", {})
                ),
                "reported",
            ),
            (
                normalize_station_did_storage(
                    deep_get(state.get("stations", {}).get(station_key, {}), "did", {})
                ),
                "stored",
            ),
        ]
        for did_candidate, source in preferred_candidates:
            did = _try_candidate(did_candidate, source=source)
            if did:
                return did, changed

        assigned_row = conn.execute(
            """
            SELECT sy_did, did_code, init_code, init_string, crc_key
            FROM p2p_did_pool
            WHERE assigned_station_sn = ? AND enabled = 1
            ORDER BY sort_order ASC, updated_at DESC, sy_did ASC
            LIMIT 1
            """,
            (station_key,),
        ).fetchone()
        did = self._station_did_from_pool_row_unlocked(assigned_row)
        if self._station_did_is_real_unlocked(did):
            if self._apply_station_did_to_state_unlocked(
                state,
                station_sn=station_key,
                user_id=user_key,
                did=did,
                source="pool:station-assigned",
            ):
                changed = True
            return did, changed

        free_row = conn.execute(
            """
            SELECT sy_did, did_code, init_code, init_string, crc_key
            FROM p2p_did_pool
            WHERE assigned_station_sn = '' AND enabled = 1
            ORDER BY sort_order ASC, created_at ASC, sy_did ASC
            LIMIT 1
            """
        ).fetchone()
        free_did = self._station_did_from_pool_row_unlocked(free_row)
        if self._station_did_is_real_unlocked(free_did):
            did = _try_candidate(free_did, source="free")
            if did:
                return did, changed

        fallback = self._fallback_station_did_for_station_unlocked(state, station_key)
        did = _try_candidate(fallback, source="fallback")
        if did:
            return did, changed
        return {}, changed

    def _fallback_station_did_for_station_unlocked(
        self,
        state: dict[str, Any],
        station_sn: str,
    ) -> dict[str, str]:
        self = self.store
        station_key = str(station_sn or "").strip()
        if not station_key:
            return {}
        did_mode = str(self.settings.default_station_did_mode or "static").strip().lower()
        candidates: list[dict[str, str]] = []
        if station_key == DEFAULT_STATION_SN:
            candidates.append(configured_station_did(self.settings, station_key))
        elif did_mode in {"derived", "deterministic", "generated"}:
            candidates.append(configured_station_did(self.settings, station_key))
        else:
            candidates.extend(
                static_test_station_did_pool(
                    init_code=self.settings.default_station_init_code,
                    crc_key=self.settings.default_station_crc_key,
                )
            )
            candidates.append(configured_station_did(self.settings, station_key))
        used_sy_dids = self._issued_station_dids_unlocked(state, exclude_station_sn=station_key)
        for candidate in candidates:
            normalized = normalize_station_did_storage(candidate)
            if not self._station_did_is_real_unlocked(normalized):
                continue
            sy_did = str(normalized.get("syDid") or normalized.get("didCode") or "").split(",", 1)[0].strip()
            if sy_did and sy_did in used_sy_dids:
                continue
            return normalized
        return {}

    def _upsert_station_did_mapping_unlocked(
        self,
        state: dict[str, Any],
        *,
        station_sn: str,
        did: dict[str, Any],
    ) -> tuple[dict[str, str], bool]:
        self = self.store
        station_key = str(station_sn or "").strip()
        normalized = normalize_station_did_storage(did or {})
        if not station_key or not normalized:
            return {}, False
        mappings = self._station_did_mappings_unlocked(state)
        changed = mappings.get(station_key) != normalized
        if changed:
            mappings[station_key] = normalized
        stations = state.setdefault("stations", {})
        station = stations.get(station_key)
        if isinstance(station, dict):
            if normalize_station_did_storage(station.get("did", {})) != normalized:
                station["did"] = dict(normalized)
                changed = True
        return normalized, changed

    def _station_did_from_state_unlocked(
        self,
        state: dict[str, Any],
        station_sn: str,
    ) -> dict[str, str]:
        self = self.store
        station_key = str(station_sn or "").strip()
        if not station_key:
            return {}
        mapped = self._station_did_mapping_for_station_unlocked(state, station_key)
        if mapped:
            return mapped
        station = state.get("stations", {}).get(station_key, {})
        if isinstance(station, dict):
            reported_did = normalize_station_did_storage(station.get("reportedDid", {}))
            if self._station_did_is_real_unlocked(reported_did):
                return reported_did
            stored_did = normalize_station_did_storage(station.get("did", {}))
            if self._station_did_is_real_unlocked(stored_did):
                return stored_did
        fallback = self._fallback_station_did_for_station_unlocked(state, station_key)
        if fallback:
            return fallback
        return {}

    def _normalize_station_record(self, station_sn: str, station: dict[str, Any]) -> bool:
        self = self.store
        changed = False
        defaults = {
            "deviceSn": station_sn,
            "stationSn": station_sn,
            "deviceName": station.get("stationName") or station_sn,
            "stationName": station.get("deviceName") or station_sn,
            "bindId": f"bind-station-{station_sn}",
            "stationOnline": "1",
            "shareFlag": 0,
            "shareTime": "",
            "sharerName": "",
            "addTime": iso_now(),
            "didUserId": str(station.get("didUserId", "") or ""),
        }
        for key, value in defaults.items():
            if key not in station:
                station[key] = value
                changed = True

        permission = station.setdefault(
            "permissionObject",
            {"shareFlag": 0, "realtimeVideo": 1, "msgAndPlayback": 1},
        )
        for key, value in {"shareFlag": 0, "realtimeVideo": 1, "msgAndPlayback": 1}.items():
            if permission.get(key) != value:
                permission[key] = value
                changed = True

        expected_did = (
            configured_station_did(self.settings, station_sn)
            if station_sn == DEFAULT_STATION_SN
            else self._default_station_did_storage()
        )
        did = station.setdefault("did", {})
        required_keys = (
            STATION_DID_KEYS
            if station_sn == DEFAULT_STATION_SN
            else ("initCode", "initString", "crcKey")
        )
        for key in required_keys:
            value = expected_did.get(key, "")
            current = did.get(key)
            if not current or is_placeholder_station_did_value(key, current):
                did[key] = value
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
                "session": 1,
                "time": iso_now(),
                "timestamp": now_ts(),
                "totolsize": 8026,
                "upstatus": 0,
                "usedsize": 1,
            },
        )
        attr = station.setdefault("attrObject", {})
        hardver = self._normalize_station_hardver(attr.get("hardver", ""))
        if attr.get("hardver") != hardver:
            attr["hardver"] = hardver
            changed = True
        model = self._normalize_station_model(hardver, attr.get("f_firmnum", ""))
        for key, value in {
            "softver": "V3.0.0.61 V1",
            "f_appversionout": "020104",
            "f_appversionin": "020104",
            "f_firmnum": model,
            "h_appversionout": "010000",
            "h_appversionin": "010000",
            "h_devicenum": model,
        }.items():
            if not attr.get(key):
                attr[key] = value
                changed = True
        return changed

    def _station_did_from_state(
        self,
        station_sn: str,
        conn=None,
    ) -> dict[str, Any]:
        self = self.store
        close_conn = False
        if conn is None:
            conn = self._connect()
            close_conn = True
        try:
            state = self._load_state_unlocked(conn)
            did = self._station_did_from_state_unlocked(state, station_sn)
            if did:
                return did
            return self._default_station_did_storage()
        finally:
            if close_conn:
                conn.close()

    def station_did_payload(
        self,
        station_sn: str,
        conn=None,
    ) -> dict[str, Any]:
        self = self.store
        did = self._station_did_from_state(station_sn, conn=conn)
        return render_station_did_payload(did, token=self.settings.default_station_did_token)

    def station_did_for_index(self, station_sn: str) -> dict[str, Any]:
        self = self.store
        did = self.station_did_payload(station_sn)
        did_code = str(did.get("didCode", "")).split(",", 1)[0]
        did["didCode"] = did_code
        did["syDid"] = did_code
        return did

    def _resolve_user_for_station_binding_unlocked(
        self,
        conn,
        *,
        access_token: str = "",
        identifier: str = "",
        user_id: str = "",
        allow_default_fallback: bool = True,
    ):
        self = self.store
        if user_id:
            row = self._find_user(conn, user_id=user_id)
            if row:
                return row
        auth_row = self._find_auth_by_access_token(conn, access_token)
        if auth_row:
            row = self._find_user(conn, user_id=auth_row["user_id"])
            if row:
                return row
        if identifier:
            row = self._find_user(conn, identifier=identifier)
            if row:
                return row
        if allow_default_fallback:
            return self._find_user(conn, user_id=DEFAULT_USER_ID)
        return None

    def _upsert_station_binding_unlocked(
        self,
        conn,
        *,
        user_id: str,
        station_sn: str,
        role: str = "owner",
        bind_state: str = "bound",
    ) -> None:
        self = self.store
        now = iso_now()
        if role == "owner":
            conn.execute(
                """
                UPDATE user_station_bindings
                SET role = 'member', updated_at = ?
                WHERE station_sn = ? AND user_id <> ? AND lower(role) = 'owner'
                """,
                (now, station_sn, user_id),
            )
        conn.execute(
            """
            INSERT INTO user_station_bindings (
                user_id, station_sn, role, bind_state, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id, station_sn) DO UPDATE SET
                role = excluded.role,
                bind_state = excluded.bind_state,
                updated_at = excluded.updated_at
            """,
            (user_id, station_sn, role, bind_state, now, now),
        )

    def _bound_station_sns_for_user_unlocked(
        self,
        conn,
        *,
        access_token: str = "",
        identifier: str = "",
        user_id: str = "",
    ) -> set[str] | None:
        self = self.store
        scope_requested = bool(
            str(access_token or "").strip()
            or str(identifier or "").strip()
            or str(user_id or "").strip()
        )
        if not scope_requested:
            return None
        user_row = self._resolve_user_for_station_binding_unlocked(
            conn,
            access_token=access_token,
            identifier=identifier,
            user_id=user_id,
            allow_default_fallback=False,
        )
        if not user_row:
            return set()
        rows = conn.execute(
            """
            SELECT station_sn
            FROM user_station_bindings
            WHERE user_id = ? AND bind_state = 'bound'
            """,
            (str(user_row["user_id"] or "").strip(),),
        ).fetchall()
        return {
            str(row["station_sn"] or "").strip()
            for row in rows
            if str(row["station_sn"] or "").strip()
        }

    def _issued_station_dids_unlocked(
        self,
        state: dict[str, Any],
        *,
        exclude_station_sn: str = "",
    ) -> set[str]:
        self = self.store
        values: set[str] = set()
        stations = state.get("stations", {})
        if not isinstance(stations, dict):
            return values
        for current_station_sn, station in stations.items():
            current_station_sn = str(current_station_sn or "").strip()
            if not current_station_sn or current_station_sn == exclude_station_sn:
                continue
            if not isinstance(station, dict):
                continue
            for did_key in ("did", "reportedDid"):
                did = normalize_station_did_storage(station.get(did_key, {}))
                sy_did = str(did.get("syDid") or did.get("didCode") or "").split(",", 1)[0].strip()
                if sy_did:
                    values.add(sy_did)
        return values

    def _station_did_needs_issue_unlocked(
        self,
        station: dict[str, Any],
        *,
        user_id: str,
    ) -> bool:
        self = self.store
        expected_user_id = str(user_id or "").strip()
        if not expected_user_id:
            return False
        did = normalize_station_did_storage(station.get("did", {}))
        if not did:
            return True
        mode = str(self.settings.default_station_did_mode or "static").strip().lower()
        if mode in {"derived", "deterministic", "generated"}:
            # Keep DID stable per station in derived mode. Re-issuing DID on
            # account switch breaks live/pair routing and can make app clients
            # connect to stale peer identities.
            placeholder_keys = STATION_DID_KEYS
        else:
            # In static mode we only care whether DID itself is still a known
            # placeholder; init/crc defaults are valid and should not trigger
            # a DID re-issue.
            placeholder_keys = ("didCode", "syDid")
        return any(is_placeholder_station_did_value(key, did.get(key)) for key in placeholder_keys)

    def _generate_unique_station_did_unlocked(
        self,
        state: dict[str, Any],
        *,
        station_sn: str,
        user_id: str,
    ) -> dict[str, str]:
        self = self.store
        station_sn = str(station_sn or "").strip()
        user_id = str(user_id or "").strip()
        mode = str(self.settings.default_station_did_mode or "static").strip().lower()
        if mode not in {"derived", "deterministic", "generated"}:
            return configured_station_did(self.settings, station_sn)
        if not station_sn or not user_id:
            return configured_station_did(self.settings, station_sn)
        used_sy_dids = self._issued_station_dids_unlocked(state, exclude_station_sn=station_sn)
        seed_root = "|".join(
            part
            for part in (
                self.settings.default_station_did_seed,
                station_sn,
            )
            if str(part or "").strip()
        )
        if not seed_root:
            seed_root = station_sn
        for counter in range(1024):
            seed = seed_root if counter == 0 else f"{seed_root}|{counter}"
            did = derive_station_did_storage(
                station_sn,
                prefix=self.settings.default_station_did_prefix,
                seed=seed,
                token=self.settings.default_station_did_token,
                init_code=self.settings.default_station_init_code,
                crc_key=self.settings.default_station_crc_key,
            )
            sy_did = str(did.get("syDid") or "").strip()
            if sy_did and sy_did not in used_sy_dids:
                return did
        raise ValueError(f"unable to allocate unique did for station {station_sn}")

    def _ensure_station_did_unlocked(
        self,
        state: dict[str, Any],
        *,
        station_sn: str,
        user_id: str,
        force: bool = False,
        conn: sqlite3.Connection | None = None,
    ) -> tuple[dict[str, str], bool]:
        self = self.store
        station_sn = str(station_sn or "").strip()
        user_id = str(user_id or "").strip()
        stations = state.setdefault("stations", {})
        station = stations.setdefault(
            station_sn,
            self._build_station(station_sn, station_sn),
        )
        mapped = self._station_did_mapping_for_station_unlocked(state, station_sn)
        if mapped:
            changed = normalize_station_did_storage(station.get("did", {})) != mapped
            if changed:
                station["did"] = dict(mapped)
            if str(station.get("didUserId", "") or "").strip() != user_id:
                station["didUserId"] = user_id
                changed = True
            return mapped, changed
        # For additional stations we prefer captured device DID first, and
        # then fall back to deterministic test DID candidates.
        if station_sn != DEFAULT_STATION_SN:
            reported_did = normalize_station_did_storage(station.get("reportedDid", {}))
            if self._station_did_is_real_unlocked(reported_did):
                _, map_changed = self._upsert_station_did_mapping_unlocked(
                    state,
                    station_sn=station_sn,
                    did=reported_did,
                )
                if str(station.get("didUserId", "") or "").strip() != user_id:
                    station["didUserId"] = user_id
                    return reported_did, True
                return reported_did, map_changed
            if conn is not None:
                pooled_did, pool_changed = self._assign_station_did_from_pool_unlocked(
                    conn,
                    state,
                    station_sn=station_sn,
                    user_id=user_id,
                )
                if self._station_did_is_real_unlocked(pooled_did):
                    return pooled_did, pool_changed
            fallback_did = self._fallback_station_did_for_station_unlocked(state, station_sn)
            if fallback_did:
                _, map_changed = self._upsert_station_did_mapping_unlocked(
                    state,
                    station_sn=station_sn,
                    did=fallback_did,
                )
                changed = bool(map_changed)
                if normalize_station_did_storage(station.get("did", {})) != fallback_did:
                    station["did"] = dict(fallback_did)
                    changed = True
                if str(station.get("didUserId", "") or "").strip() != user_id:
                    station["didUserId"] = user_id
                    changed = True
                meta = station.setdefault("meta", {})
                if str(meta.get("didSource", "") or "").strip() != "fallback:test-did-pool":
                    meta["didSource"] = "fallback:test-did-pool"
                    changed = True
                return fallback_did, changed
            current_did = normalize_station_did_storage(station.get("did", {}))
            default_did = self._default_station_did_storage()
            changed = current_did != default_did
            if changed:
                station["did"] = dict(default_did)
            if str(station.get("didUserId", "") or "").strip() != user_id:
                station["didUserId"] = user_id
                changed = True
            return default_did, changed
        if not force:
            needs_issue = self._station_did_needs_issue_unlocked(station, user_id=user_id)
            current_did = normalize_station_did_storage(station.get("did", {}))
            current_sy_did = str(current_did.get("syDid") or current_did.get("didCode") or "").split(",", 1)[0].strip()
            # Keep one canonical DID per station. A duplicated syDid causes app
            # control/pair commands to hit the wrong base station.
            if (
                not needs_issue
                and current_sy_did
                and station_sn != DEFAULT_STATION_SN
                and current_sy_did in self._issued_station_dids_unlocked(state, exclude_station_sn=station_sn)
            ):
                needs_issue = True
            if not needs_issue:
                return current_did, False
        did = self._generate_unique_station_did_unlocked(
            state,
            station_sn=station_sn,
            user_id=user_id,
        )
        changed = normalize_station_did_storage(station.get("did", {})) != did
        if changed:
            station["did"] = did
        if str(station.get("didUserId", "") or "").strip() != user_id:
            station["didUserId"] = user_id
            changed = True
        return did, changed

    def ensure_station_did(
        self,
        *,
        station_sn: str,
        access_token: str = "",
        identifier: str = "",
        user_id: str = "",
        force: bool = False,
    ) -> tuple[dict[str, Any] | None, str | None]:
        self = self.store
        station_sn = str(station_sn or "").strip()
        if not station_sn:
            return None, "stationSn required"
        with self.lock, self._connect() as conn:
            user_row = self._resolve_user_for_station_binding_unlocked(
                conn,
                access_token=access_token,
                identifier=identifier,
                user_id=user_id,
                allow_default_fallback=not bool(
                    str(access_token or "").strip()
                    or str(identifier or "").strip()
                    or str(user_id or "").strip()
                ),
            )
            if not user_row:
                return None, "user not found"
            state = self._load_state_unlocked(conn)
            did, changed = self._ensure_station_did_unlocked(
                state,
                station_sn=station_sn,
                user_id=str(user_row["user_id"] or "").strip(),
                force=force,
                conn=conn,
            )
            if changed:
                self._save_state_unlocked(conn, state)
            return {
                "stationSn": station_sn,
                "userId": str(user_row["user_id"] or "").strip(),
                "did": render_station_did_payload(
                    did,
                    token=self.settings.default_station_did_token,
                ),
                "changed": changed,
            }, None

    def backfill_station_dids(self, *, force: bool = False) -> dict[str, Any]:
        self = self.store
        with self.lock, self._connect() as conn:
            state = self._load_state_unlocked(conn)
            changed = self._refresh_station_binding_cache_unlocked(conn, state)
            assigned: list[dict[str, Any]] = []
            stations = state.get("stations", {})
            if isinstance(stations, dict):
                for station_sn, station in stations.items():
                    if not isinstance(station, dict):
                        continue
                    owner_user_id = str(station.get("ownerUserId", "") or "").strip()
                    if not owner_user_id:
                        continue
                    did, did_changed = self._ensure_station_did_unlocked(
                        state,
                        station_sn=str(station_sn or "").strip(),
                        user_id=owner_user_id,
                        force=force,
                        conn=conn,
                    )
                    if did_changed:
                        changed = True
                    assigned.append(
                        {
                            "stationSn": str(station_sn or "").strip(),
                            "userId": owner_user_id,
                            "did": render_station_did_payload(
                                did,
                                token=self.settings.default_station_did_token,
                            ),
                            "changed": did_changed,
                        }
                    )
            if changed:
                self._save_state_unlocked(conn, state)
            return {
                "force": bool(force),
                "changed": bool(changed),
                "total": len(assigned),
                "items": assigned,
            }

    def upsert_station_did_mapping(
        self,
        *,
        station_sn: str,
        did: dict[str, Any],
        source: str = "",
    ) -> tuple[dict[str, Any] | None, str | None]:
        self = self.store
        station_sn = str(station_sn or "").strip()
        if not station_sn:
            return None, "stationSn required"
        normalized = normalize_station_did_storage(did or {})
        if not self._station_did_is_real_unlocked(normalized):
            return None, "invalid station DID payload"
        with self.lock, self._connect() as conn:
            state = self._load_state_unlocked(conn)
            mapping_did, changed = self._upsert_station_did_mapping_unlocked(
                state,
                station_sn=station_sn,
                did=normalized,
            )
            stations = state.setdefault("stations", {})
            station = stations.get(station_sn)
            if not station:
                station = self._build_station(station_sn, station_sn)
                stations[station_sn] = station
                changed = True
            if normalize_station_did_storage(station.get("did", {})) != mapping_did:
                station["did"] = dict(mapping_did)
                changed = True
            if source:
                meta = station.setdefault("meta", {})
                if str(meta.get("didSource", "") or "") != source:
                    meta["didSource"] = source
                    changed = True
            if changed:
                self._save_state_unlocked(conn, state)
            return {
                "stationSn": station_sn,
                "did": render_station_did_payload(
                    mapping_did,
                    token=self.settings.default_station_did_token,
                ),
                "changed": bool(changed),
            }, None

    def station_did_mapping_payload(self, station_sn: str = "") -> dict[str, Any]:
        self = self.store
        station_key = str(station_sn or "").strip()
        with self._connect() as conn:
            state = self._load_state_unlocked(conn)
            mappings = self._station_did_mappings_unlocked(state)
            if station_key:
                mapped = normalize_station_did_storage(mappings.get(station_key, {}))
                return {
                    "stationSn": station_key,
                    "mapped": bool(mapped),
                    "did": (
                        render_station_did_payload(
                            mapped,
                            token=self.settings.default_station_did_token,
                        )
                        if mapped
                        else {}
                    ),
                }
            items: list[dict[str, Any]] = []
            for sn, did in sorted(mappings.items()):
                normalized = normalize_station_did_storage(did)
                if not normalized:
                    continue
                items.append(
                    {
                        "stationSn": sn,
                        "did": render_station_did_payload(
                            normalized,
                            token=self.settings.default_station_did_token,
                        ),
                    }
                )
            return {"total": len(items), "items": items}

    def bind_station_to_user(
        self,
        *,
        station_sn: str,
        access_token: str = "",
        identifier: str = "",
        user_id: str = "",
        role: str = "owner",
    ) -> tuple[dict[str, Any] | None, str | None]:
        self = self.store
        station_sn = str(station_sn or "").strip()
        if not station_sn:
            return None, "stationSn required"
        role = str(role or "owner").strip().lower() or "owner"
        if role not in {"owner", "member", "viewer"}:
            return None, "unsupported role"
        with self.lock, self._connect() as conn:
            user_row = self._resolve_user_for_station_binding_unlocked(
                conn,
                access_token=access_token,
                identifier=identifier,
                user_id=user_id,
                allow_default_fallback=not bool(
                    str(access_token or "").strip()
                    or str(identifier or "").strip()
                    or str(user_id or "").strip()
                ),
            )
            if not user_row:
                return None, "user not found"
            state = self._load_state_unlocked(conn)
            self._forget_removed_station_unlocked(state, station_sn)
            stations = state.setdefault("stations", {})
            station = stations.get(station_sn)
            if not station:
                station = self._build_station(station_sn, station_sn)
                stations[station_sn] = station
                self._normalize_station_record(station_sn, station)
                self._sync_device_tables_unlocked(conn, state)
            target_user_id = str(user_row["user_id"] or "").strip()
            if role == "owner":
                existing_owner = conn.execute(
                    """
                    SELECT user_id
                    FROM user_station_bindings
                    WHERE station_sn = ? AND bind_state = 'bound' AND lower(role) = 'owner'
                    ORDER BY updated_at DESC, user_id ASC
                    LIMIT 1
                    """,
                    (station_sn,),
                ).fetchone()
                existing_owner_user_id = str(
                    (existing_owner["user_id"] if existing_owner else "") or ""
                ).strip()
                if existing_owner_user_id and existing_owner_user_id != target_user_id:
                    return None, "Station has been added by someone"
            assigned_did, did_changed = self._ensure_station_did_unlocked(
                state,
                station_sn=station_sn,
                user_id=target_user_id,
                conn=conn,
            )
            if station_sn != DEFAULT_STATION_SN and not self._station_did_is_real_unlocked(assigned_did):
                return None, "No available station P2P DID in pool"
            self._upsert_station_binding_unlocked(
                conn,
                user_id=target_user_id,
                station_sn=station_sn,
                role=role,
                bind_state="bound",
            )
            self._refresh_station_binding_cache_unlocked(conn, state)
            owner_user_id = str(
                deep_get(state.get("stations", {}).get(station_sn, {}), "ownerUserId", "")
                or ""
            ).strip()
            if owner_user_id:
                _, ensure_changed = self._ensure_station_did_unlocked(
                    state,
                    station_sn=station_sn,
                    user_id=owner_user_id,
                    conn=conn,
                )
                did_changed = did_changed or ensure_changed
            if did_changed:
                station = state.get("stations", {}).get(station_sn, {}) if isinstance(state.get("stations", {}), dict) else {}
            self._save_state_unlocked(conn, state)
            station = state.get("stations", {}).get(station_sn, {}) if isinstance(state.get("stations", {}), dict) else {}
            return {
                "stationSn": station_sn,
                "role": role,
                "userId": target_user_id,
                "username": str(user_row["username"] or user_row["email"] or "").strip(),
                "ownerUserId": str(station.get("ownerUserId", "") or ""),
                "ownerUsername": str(station.get("ownerUsername", "") or ""),
                "bindingCount": int(station.get("bindingCount", 0) or 0),
                "did": self.station_did_payload(station_sn, conn=conn),
            }, None

    def unbind_station_from_user(
        self,
        *,
        station_sn: str,
        access_token: str = "",
        identifier: str = "",
        user_id: str = "",
    ) -> tuple[dict[str, Any] | None, str | None]:
        self = self.store
        station_sn = str(station_sn or "").strip()
        if not station_sn:
            return None, "stationSn required"
        with self.lock, self._connect() as conn:
            user_row = self._resolve_user_for_station_binding_unlocked(
                conn,
                access_token=access_token,
                identifier=identifier,
                user_id=user_id,
                allow_default_fallback=not bool(
                    str(access_token or "").strip()
                    or str(identifier or "").strip()
                    or str(user_id or "").strip()
                ),
            )
            if not user_row:
                return None, "user not found"
            state = self._load_state_unlocked(conn)
            binding_row = conn.execute(
                """
                SELECT role, bind_state
                FROM user_station_bindings
                WHERE user_id = ? AND station_sn = ?
                LIMIT 1
                """,
                (str(user_row["user_id"] or "").strip(), station_sn),
            ).fetchone()
            removed = False
            previous_role = str((binding_row["role"] if binding_row else "") or "").strip()
            if binding_row is not None:
                cur = conn.execute(
                    """
                    DELETE FROM user_station_bindings
                    WHERE user_id = ? AND station_sn = ?
                    """,
                    (str(user_row["user_id"] or "").strip(), station_sn),
                )
                removed = int(cur.rowcount or 0) > 0
                if removed:
                    self._promote_station_owner_if_missing_unlocked(conn, station_sn)
            self._refresh_station_binding_cache_unlocked(conn, state)
            station = (
                state.get("stations", {}).get(station_sn, {})
                if isinstance(state.get("stations", {}), dict)
                else {}
            )
            binding_count = int(station.get("bindingCount", 0) or 0)
            removed_cameras: list[str] = []
            if removed and binding_count == 0:
                self._release_station_did_pool_for_station_unlocked(
                    conn,
                    station_sn=station_sn,
                )
                removed_cameras = self._remove_station_from_state_unlocked(state, station_sn)
                conn.execute("DELETE FROM pairing_sessions WHERE station_sn = ?", (station_sn,))
                conn.execute("DELETE FROM pairing_slots WHERE station_sn = ?", (station_sn,))
                conn.execute("DELETE FROM cameras WHERE station_sn = ?", (station_sn,))
                conn.execute("DELETE FROM stations WHERE station_sn = ?", (station_sn,))
                station = {}
            owner_user_id = str(station.get("ownerUserId", "") or "").strip()
            if owner_user_id:
                self._ensure_station_did_unlocked(
                    state,
                    station_sn=station_sn,
                    user_id=owner_user_id,
                    conn=conn,
                )
            self._save_state_unlocked(conn, state)
            return {
                "stationSn": station_sn,
                "userId": str(user_row["user_id"] or "").strip(),
                "username": str(user_row["username"] or user_row["email"] or "").strip(),
                "removed": removed,
                "previousRole": previous_role,
                "bindingCount": binding_count,
                "ownerUserId": str(station.get("ownerUserId", "") or ""),
                "ownerUsername": str(station.get("ownerUsername", "") or ""),
                "stationRetained": station_sn in state.get("stations", {}),
                "removedCameras": removed_cameras,
            }, None

    def station_bindings_for_user(
        self,
        *,
        access_token: str = "",
        identifier: str = "",
        user_id: str = "",
    ) -> tuple[list[dict[str, Any]] | None, str | None]:
        self = self.store
        with self.lock, self._connect() as conn:
            user_row = self._resolve_user_for_station_binding_unlocked(
                conn,
                access_token=access_token,
                identifier=identifier,
                user_id=user_id,
                allow_default_fallback=not bool(
                    str(access_token or "").strip()
                    or str(identifier or "").strip()
                    or str(user_id or "").strip()
                ),
            )
            if not user_row:
                return None, "user not found"
            rows = conn.execute(
                """
                SELECT usb.station_sn, usb.role, usb.bind_state, usb.created_at, usb.updated_at,
                       s.station_name, s.device_name, s.owner_user_id, s.owner_username, s.camera_total
                FROM user_station_bindings usb
                LEFT JOIN stations s ON s.station_sn = usb.station_sn
                WHERE usb.user_id = ?
                ORDER BY usb.updated_at DESC, usb.station_sn ASC
                """,
                (str(user_row["user_id"] or "").strip(),),
            ).fetchall()
            return [
                {
                    "stationSn": str(row["station_sn"] or ""),
                    "stationName": str(row["station_name"] or row["device_name"] or row["station_sn"] or ""),
                    "role": str(row["role"] or ""),
                    "bindState": str(row["bind_state"] or ""),
                    "cameraTotal": int(row["camera_total"] or 0),
                    "ownerUserId": str(row["owner_user_id"] or ""),
                    "ownerUsername": str(row["owner_username"] or ""),
                    "createdAt": str(row["created_at"] or ""),
                    "updatedAt": str(row["updated_at"] or ""),
                }
                for row in rows
            ], None

    def station_bind_check_payload(
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
            return {"stateCode": 400, "stateMsg": "stationSn required", "data": {}}
        requested_scope = bool(str(access_token or "").strip() or str(identifier or "").strip() or str(user_id or "").strip())
        with self._connect() as conn:
            resolved_user = self._resolve_user_for_station_binding_unlocked(
                conn,
                access_token=access_token,
                identifier=identifier,
                user_id=user_id,
                allow_default_fallback=not requested_scope,
            )
            state = self._load_state_unlocked(conn)
            if self._station_is_removed_unlocked(state, station_sn):
                return {
                    "stateCode": 200,
                    "stateMsg": "OK",
                    "data": {
                        "stationSn": station_sn,
                        "exists": 0,
                        "bound": 0,
                        "ownerUserId": "",
                        "ownerUsername": "",
                        "bindingCount": 0,
                    },
                }
            target_user_id = str((resolved_user["user_id"] if resolved_user else "") or "").strip()
            row = conn.execute(
                """
                SELECT s.station_sn, s.owner_user_id, s.owner_username,
                       COALESCE(b.binding_count, 0) AS binding_count
                FROM stations s
                LEFT JOIN (
                    SELECT station_sn, COUNT(*) AS binding_count
                    FROM user_station_bindings
                    WHERE bind_state = 'bound'
                    GROUP BY station_sn
                ) b ON b.station_sn = s.station_sn
                WHERE s.station_sn = ?
                LIMIT 1
                """,
                (station_sn,),
            ).fetchone()
            owner_user_id = str((row["owner_user_id"] if row else "") or "")
            owner_username = str((row["owner_username"] if row else "") or "")
            binding_count = int((row["binding_count"] if row else 0) or 0)
            if binding_count <= 0:
                return {
                    "stateCode": 200,
                    "stateMsg": "OK",
                    "data": {
                        "stationSn": station_sn,
                        "exists": 1 if row else 0,
                        "bound": 0,
                        "ownerUserId": "",
                        "ownerUsername": "",
                        "bindingCount": 0,
                    },
                }
            if target_user_id and owner_user_id == target_user_id:
                return {
                    "stateCode": 216003,
                    "stateMsg": "Station has been added to your account already",
                    "data": {
                        "stationSn": station_sn,
                        "exists": 1,
                        "bound": 1,
                        "ownerUserId": owner_user_id,
                        "ownerUsername": owner_username,
                        "bindingCount": binding_count,
                    },
                }
            return {
                "stateCode": 216004,
                "stateMsg": "Station has been added by someone",
                "data": {
                    "stationSn": station_sn,
                    "exists": 1,
                    "bound": 1,
                    "ownerUserId": owner_user_id,
                    "ownerUsername": owner_username,
                    "bindingCount": binding_count,
                },
            }

    def set_station(self, station_sn: str, station_name: str | None = None) -> dict[str, Any]:
        self = self.store
        with self.lock, self._connect() as conn:
            state = self._load_state_unlocked(conn)
            stations = state.setdefault("stations", {})
            self._forget_removed_station_unlocked(state, station_sn)
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
            self._save_state_unlocked(conn, state)
            return station

    def station_exists(self, station_sn: str) -> bool:
        self = self.store
        station_sn = str(station_sn or "").strip()
        if not station_sn:
            return False
        with self._connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM stations WHERE station_sn = ? LIMIT 1",
                (station_sn,),
            ).fetchone()
            if row:
                return True
            state = self._load_state_unlocked(conn)
            if self._station_is_removed_unlocked(state, station_sn):
                return False
            return station_sn in state.get("stations", {})

    def update_station_status(
        self,
        station_sn: str,
        body: dict[str, Any],
        *,
        access_token: str = "",
    ):
        self = self.store
        with self.lock, self._connect() as conn:
            state = self._load_state_unlocked(conn)
            if self._station_is_removed_unlocked(state, station_sn):
                return None
            station = state.setdefault("stations", {}).setdefault(
                station_sn,
                self._build_station(station_sn, station_sn),
            )
            self._normalize_station_record(station_sn, station)
            station["stationOnline"] = "1"
            # Device-side report-status may carry stale tokens from a previous
            # app account. Keep this disabled by default to avoid unexpected
            # ownership "snap back" after manual unbind/re-pair flows.
            if access_token and bool(self.settings.station_report_auto_bind_enabled):
                existing_binding = conn.execute(
                    """
                    SELECT 1
                    FROM user_station_bindings
                    WHERE station_sn = ? AND bind_state = 'bound'
                    LIMIT 1
                    """,
                    (station_sn,),
                ).fetchone()
                if not existing_binding:
                    user_row = self._resolve_user_for_station_binding_unlocked(
                        conn,
                        access_token=access_token,
                        allow_default_fallback=False,
                    )
                    if user_row:
                        target_user_id = str(user_row["user_id"] or "").strip()
                        owner_user_id = str(station.get("ownerUserId", "") or "").strip()
                        # If the station row still records an owner, only allow
                        # auto-bind when the token belongs to that same user.
                        if owner_user_id and owner_user_id != target_user_id:
                            user_row = None
                    if user_row:
                        self._upsert_station_binding_unlocked(
                            conn,
                            user_id=str(user_row["user_id"] or "").strip(),
                            station_sn=station_sn,
                            role="owner",
                            bind_state="bound",
                        )
            station_status = deep_get(body, "stationStatusObject", {}) or {}
            if station_status:
                station_status = dict(station_status)
                raw_session = deep_get(station_status, "session", None)
                try:
                    reported_session = (
                        int(raw_session)
                        if raw_session not in (None, "")
                        else 1
                    )
                except (TypeError, ValueError):
                    reported_session = 1
                if reported_session <= 0:
                    station_status["session"] = 1
                elif raw_session not in (None, ""):
                    station_status["session"] = reported_session
                station["statusObject"].update(station_status)
            else:
                station["statusObject"]["session"] = 1
            raw_session = deep_get(station["statusObject"], "session", None)
            try:
                current_session = (
                    int(raw_session)
                    if raw_session not in (None, "")
                    else 1
                )
            except (TypeError, ValueError):
                current_session = 1
            if current_session <= 0:
                station["statusObject"]["session"] = 1
            station["statusObject"]["timestamp"] = now_ts()
            station["statusObject"]["time"] = iso_now()
            station_meta = station.setdefault("meta", {})
            station_meta["lastReportStatusTs"] = int(station["statusObject"]["timestamp"] or 0)
            station_meta["lastReportStatusAt"] = str(station["statusObject"]["time"] or iso_now())
            for camera_status in deep_get(body, "cameraStatusObjectList", []) or []:
                camera_sn = deep_get(camera_status, "cameraSn", "")
                if not camera_sn:
                    continue
                channel = int(deep_get(camera_status, "channel", 0) or 0)
                cameras = state.setdefault("cameras", {})
                camera = cameras.get(camera_sn)
                if self._camera_is_removed_unlocked(state, camera_sn):
                    if camera is not None:
                        cameras.pop(camera_sn, None)
                    continue
                existing_station_sn = str((camera or {}).get("stationSn", "") or "").strip()
                # Station telemetry can be stale for a while after re-pairing.
                # Do not let report-status from another station steal an
                # already-associated camera record.
                if camera is not None and existing_station_sn and existing_station_sn != station_sn:
                    continue
                previous_status = dict((camera or {}).get("statusObject", {}) or {})
                if not camera:
                    camera = self._build_camera(camera_sn, camera_sn, station_sn, channel)
                    cameras[camera_sn] = camera
                camera["stationSn"] = station_sn
                camera["channel"] = channel
                camera["pending"] = False
                camera["stationOnline"] = "1"
                camera["statusObject"].update(camera_status)
                camera["statusObject"]["timestamp"] = now_ts()
                camera["statusObject"]["time"] = iso_now()
                self._normalize_camera_record(camera_sn, camera)
                previous_online = int(deep_get(previous_status, "online", 1) or 0)
                current_online = int(deep_get(camera["statusObject"], "online", previous_online) or 0)
                if previous_status and previous_online == 1 and current_online == 0:
                    self._append_system_notice_unlocked(
                        state,
                        camera_sn=camera_sn,
                        notice_type=2,
                        title="The camera is offline",
                        ext={
                            "msg": "offline",
                            "deviceTime": iso_now(),
                            "timestamp": now_ts(),
                        },
                    )
                previous_low_battery = self._camera_battery_low(previous_status)
                current_low_battery = self._camera_battery_low(camera["statusObject"])
                if previous_status and not previous_low_battery and current_low_battery:
                    self._append_system_notice_unlocked(
                        state,
                        camera_sn=camera_sn,
                        notice_type=2,
                        title="Camera has low battery, please charge",
                        ext={
                            "msg": "low battery",
                            "deviceTime": iso_now(),
                            "timestamp": now_ts(),
                        },
                    )
            self._save_state_unlocked(conn, state)
            return station

    def update_station_session(self, station_sn: str, active: bool, payload: dict[str, Any] | None = None):
        self = self.store
        with self.lock, self._connect() as conn:
            state = self._load_state_unlocked(conn)
            if self._station_is_removed_unlocked(state, station_sn):
                return None
            station = state.setdefault("stations", {}).setdefault(
                station_sn,
                self._build_station(station_sn, station_sn),
            )
            self._normalize_station_record(station_sn, station)
            station["stationOnline"] = "1"
            station["statusObject"]["session"] = 1 if active else 0
            station["statusObject"]["timestamp"] = now_ts()
            station["statusObject"]["time"] = iso_now()
            if payload:
                meta = station.setdefault("meta", {})
                meta["lastP2P"] = self._trim_log_value(payload)
            self._save_state_unlocked(conn, state)
            return station

    def update_station_attr(self, station_sn: str, body: dict[str, Any]):
        self = self.store
        with self.lock, self._connect() as conn:
            state = self._load_state_unlocked(conn)
            if self._station_is_removed_unlocked(state, station_sn):
                return None
            station = state.setdefault("stations", {}).setdefault(
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
            reported_did = (
                normalize_station_did_storage(station_attr)
                or normalize_station_did_storage(body)
            )
            if reported_did:
                station["reportedDid"] = reported_did
                if self._station_did_is_real_unlocked(reported_did):
                    self._upsert_station_did_mapping_unlocked(
                        state,
                        station_sn=station["stationSn"],
                        did=reported_did,
                    )
            station["stationOnline"] = "1"
            station["statusObject"]["timestamp"] = now_ts()
            station["statusObject"]["time"] = iso_now()
            self._normalize_station_record(station["stationSn"], station)
            camera_attr_items = []
            for key in ("cameraAttrObjectList", "cameraAttrList"):
                values = deep_get(body, key, []) or []
                if isinstance(values, list):
                    camera_attr_items.extend(item for item in values if isinstance(item, dict))
            single_camera_attr = deep_get(body, "cameraAttrObject", {}) or {}
            if isinstance(single_camera_attr, dict) and single_camera_attr:
                camera_attr_items.append(single_camera_attr)
            for camera_attr in camera_attr_items:
                camera_sn = str(
                    deep_get(camera_attr, "cameraSn")
                    or deep_get(camera_attr, "deviceSn")
                    or ""
                ).strip()
                if not camera_sn:
                    continue
                channel = int(deep_get(camera_attr, "channel", 0) or 0)
                cameras = state.setdefault("cameras", {})
                camera = cameras.get(camera_sn)
                if self._camera_is_removed_unlocked(state, camera_sn):
                    if camera is not None:
                        cameras.pop(camera_sn, None)
                    continue
                existing_station_sn = str((camera or {}).get("stationSn", "") or "").strip()
                # Same protection as report-status: ignore cross-station attr
                # updates for a camera that is already linked to another base.
                if camera is not None and existing_station_sn and existing_station_sn != station_sn:
                    continue
                previous_attr = dict((camera or {}).get("attrObject", {}) or {})
                if not camera:
                    camera = self._build_camera(camera_sn, camera_sn, station_sn, channel)
                    cameras[camera_sn] = camera
                camera["stationSn"] = station_sn
                camera["channel"] = channel
                camera["pending"] = False
                camera["attrObject"].update(camera_attr)
                camera["statusObject"]["timestamp"] = now_ts()
                camera["statusObject"]["time"] = iso_now()
                self._normalize_camera_record(camera_sn, camera)
                previous_version = self._camera_version_marker(previous_attr)
                current_version = self._camera_version_marker(camera["attrObject"])
                if previous_version and current_version and previous_version != current_version:
                    version_desc = self._normalize_notice_text(
                        deep_get(camera["attrObject"], "softver", "")
                        or deep_get(camera["attrObject"], "f_appversionout", "")
                        or current_version.replace("|", " / ")
                    )
                    self._append_system_notice_unlocked(
                        state,
                        camera_sn=camera_sn,
                        notice_type=7,
                        title="Firmware successfully updated",
                        content=f"Version {version_desc}" if version_desc else "",
                        ext={
                            "duration": 17,
                            "msg": "firmware update success",
                            "deviceTime": iso_now(),
                            "timestamp": now_ts(),
                        },
                    )
            self._save_state_unlocked(conn, state)
            return station

    def station_bind_list(
        self,
        *,
        access_token: str = "",
        identifier: str = "",
        user_id: str = "",
    ) -> list[dict[str, Any]]:
        self = self.store
        with self._connect() as conn:
            state = self._load_state_unlocked(conn)
            allowed_station_sns = self._bound_station_sns_for_user_unlocked(
                conn,
                access_token=access_token,
                identifier=identifier,
                user_id=user_id,
            )
            return [
                {
                    "stationSn": station_sn,
                    "cameraTotal": station.get("cameraTotal", 0),
                    "stationName": station.get("stationName", station.get("deviceName", station_sn)),
                    "deviceName": station.get("deviceName", station_sn),
                    "deviceSn": station_sn,
                    "stationOnline": str(station.get("stationOnline", "1") or "1"),
                    "statusObject": dict(station.get("statusObject", {}) or {}),
                    "ownerUserId": station.get("ownerUserId", ""),
                    "ownerUsername": station.get("ownerUsername", ""),
                    "bindingCount": int(station.get("bindingCount", 0) or 0),
                }
                for station_sn, station in sorted(state.get("stations", {}).items())
                if allowed_station_sns is None or station_sn in allowed_station_sns
            ]
