from __future__ import annotations

import json
import uuid
from typing import Any

from ..store_shared import iso_now
from .base import BaseDomainService


class PairingDomainService(BaseDomainService):
    def _ensure_station_row_unlocked(
        self,
        conn,
        state: dict[str, Any],
        station_sn: str,
    ) -> dict[str, Any]:
        self = self.store
        stations = state.setdefault("stations", {})
        station = stations.get(station_sn)
        if not station:
            station = self._build_station(station_sn, station_sn)
            stations[station_sn] = station
            self._normalize_station_record(station_sn, station)
            self._sync_device_tables_unlocked(conn, state)
        return station

    def _pairing_slot_payload_from_row(self, row) -> dict[str, Any]:
        return {
            "stationSn": str(row["station_sn"] or ""),
            "slotIndex": int(row["slot_index"] or 0),
            "activeFlag": int(row["active_flag"] or 0),
            "pendingPairlistFlag": int(row["pending_pairlist_flag"] or 0),
            "peerIpv4": str(row["peer_ipv4"] or ""),
            "cameraMac": str(row["camera_mac"] or ""),
            "cameraSn": str(row["camera_sn"] or ""),
            "lastSessionId": str(row["last_session_id"] or ""),
            "createdAt": str(row["created_at"] or ""),
            "updatedAt": str(row["updated_at"] or ""),
        }

    def upsert_pairing_session(
        self,
        *,
        session_id: str,
        station_sn: str = "",
        camera_sn: str = "",
        channel: int = 0,
        stage: str = "",
        status: str = "",
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self = self.store
        session_id = str(session_id or "").strip() or f"pair-{uuid.uuid4().hex[:12]}"
        with self.lock, self._connect() as conn:
            now = iso_now()
            payload_json = json.dumps(payload or {}, ensure_ascii=False, sort_keys=True)
            conn.execute(
                """
                INSERT INTO pairing_sessions (
                    session_id, station_sn, camera_sn, channel, stage, status, payload_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(session_id) DO UPDATE SET
                    station_sn = excluded.station_sn,
                    camera_sn = excluded.camera_sn,
                    channel = excluded.channel,
                    stage = excluded.stage,
                    status = excluded.status,
                    payload_json = excluded.payload_json,
                    updated_at = excluded.updated_at
                """,
                (
                    session_id,
                    str(station_sn or "").strip(),
                    str(camera_sn or "").strip(),
                    int(channel or 0),
                    str(stage or "").strip(),
                    str(status or "").strip(),
                    payload_json,
                    now,
                    now,
                ),
            )
            conn.commit()
            return {
                "sessionId": session_id,
                "stationSn": str(station_sn or "").strip(),
                "cameraSn": str(camera_sn or "").strip(),
                "channel": int(channel or 0),
                "stage": str(stage or "").strip(),
                "status": str(status or "").strip(),
                "updatedAt": now,
            }

    def pairing_session_list(
        self,
        *,
        station_sn: str = "",
        camera_sn: str = "",
        status: str = "",
    ) -> list[dict[str, Any]]:
        self = self.store
        with self._connect() as conn:
            clauses: list[str] = []
            params: list[Any] = []
            if station_sn:
                clauses.append("station_sn = ?")
                params.append(str(station_sn or "").strip())
            if camera_sn:
                clauses.append("camera_sn = ?")
                params.append(str(camera_sn or "").strip())
            if status:
                clauses.append("status = ?")
                params.append(str(status or "").strip())
            where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
            rows = conn.execute(
                f"""
                SELECT session_id, station_sn, camera_sn, channel, stage, status, payload_json, created_at, updated_at
                FROM pairing_sessions
                {where_sql}
                ORDER BY updated_at DESC, session_id DESC
                """,
                tuple(params),
            ).fetchall()
            payload: list[dict[str, Any]] = []
            for row in rows:
                try:
                    details = json.loads(str(row["payload_json"] or "{}"))
                except json.JSONDecodeError:
                    details = {}
                payload.append(
                    {
                        "sessionId": str(row["session_id"] or ""),
                        "stationSn": str(row["station_sn"] or ""),
                        "cameraSn": str(row["camera_sn"] or ""),
                        "channel": int(row["channel"] or 0),
                        "stage": str(row["stage"] or ""),
                        "status": str(row["status"] or ""),
                        "payload": details if isinstance(details, dict) else {},
                        "createdAt": str(row["created_at"] or ""),
                        "updatedAt": str(row["updated_at"] or ""),
                    }
                )
            return payload

    def pairing_slot_list(self, station_sn: str = "") -> list[dict[str, Any]]:
        self = self.store
        station_sn = str(station_sn or "").strip()
        with self._connect() as conn:
            if station_sn:
                rows = conn.execute(
                    """
                    SELECT station_sn, slot_index, active_flag, pending_pairlist_flag,
                           peer_ipv4, camera_mac, camera_sn, last_session_id, created_at, updated_at
                    FROM pairing_slots
                    WHERE station_sn = ?
                    ORDER BY slot_index ASC
                    """,
                    (station_sn,),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT station_sn, slot_index, active_flag, pending_pairlist_flag,
                           peer_ipv4, camera_mac, camera_sn, last_session_id, created_at, updated_at
                    FROM pairing_slots
                    ORDER BY updated_at DESC, station_sn ASC, slot_index ASC
                    """
                ).fetchall()
            return [self._pairing_slot_payload_from_row(row) for row in rows]

    def resolve_pairing_context(
        self,
        *,
        camera_sn: str,
        station_sn: str = "",
        channel: int | None = None,
    ) -> dict[str, Any] | None:
        self = self.store
        camera_sn = str(camera_sn or "").strip()
        station_sn = str(station_sn or "").strip()
        if not camera_sn:
            return None
        if channel is not None:
            try:
                channel = int(channel)
            except (TypeError, ValueError):
                channel = None
        with self._connect() as conn:
            session_clauses = ["camera_sn = ?"]
            session_params: list[Any] = [camera_sn]
            if station_sn:
                session_clauses.append("station_sn = ?")
                session_params.append(station_sn)
            if channel is not None:
                session_clauses.append("channel = ?")
                session_params.append(int(channel))
            session_row = conn.execute(
                f"""
                SELECT session_id, station_sn, camera_sn, channel, stage, status, updated_at
                FROM pairing_sessions
                WHERE {' AND '.join(session_clauses)}
                ORDER BY updated_at DESC, session_id DESC
                LIMIT 1
                """,
                tuple(session_params),
            ).fetchone()

            slot_clauses = ["camera_sn = ?"]
            slot_params: list[Any] = [camera_sn]
            if station_sn:
                slot_clauses.append("station_sn = ?")
                slot_params.append(station_sn)
            if channel is not None:
                slot_clauses.append("slot_index = ?")
                slot_params.append(int(channel))
            slot_row = conn.execute(
                f"""
                SELECT station_sn, slot_index, active_flag, pending_pairlist_flag,
                       peer_ipv4, camera_mac, camera_sn, last_session_id, updated_at
                FROM pairing_slots
                WHERE {' AND '.join(slot_clauses)}
                ORDER BY updated_at DESC, station_sn ASC, slot_index ASC
                LIMIT 1
                """,
                tuple(slot_params),
            ).fetchone()

        resolved_station_sn = station_sn or str(
            (session_row["station_sn"] if session_row is not None else "")
            or (slot_row["station_sn"] if slot_row is not None else "")
            or ""
        ).strip()
        resolved_channel = channel
        if resolved_channel is None and session_row is not None:
            resolved_channel = int(session_row["channel"] or 0)
        if resolved_channel is None and slot_row is not None:
            resolved_channel = int(slot_row["slot_index"] or 0)
        if not resolved_station_sn or resolved_channel is None:
            return None
        return {
            "stationSn": resolved_station_sn,
            "channel": int(resolved_channel),
            "sessionId": str(
                (session_row["session_id"] if session_row is not None else "")
                or (slot_row["last_session_id"] if slot_row is not None else "")
                or f"{resolved_station_sn}:{camera_sn}:{int(resolved_channel)}"
            ),
            "stage": str((session_row["stage"] if session_row is not None else "") or ""),
            "status": str((session_row["status"] if session_row is not None else "") or ""),
            "cameraMac": str((slot_row["camera_mac"] if slot_row is not None else "") or ""),
            "peerIpv4": str((slot_row["peer_ipv4"] if slot_row is not None else "") or ""),
        }

    def reserve_pairing_slot(
        self,
        *,
        station_sn: str,
        camera_sn: str,
        preferred_channel: int | None = None,
        camera_mac: str = "",
        peer_ipv4: str = "",
        session_id: str = "",
    ) -> tuple[dict[str, Any] | None, str | None]:
        self = self.store
        station_sn = str(station_sn or "").strip()
        camera_sn = str(camera_sn or "").strip()
        camera_mac = str(camera_mac or "").strip()
        peer_ipv4 = str(peer_ipv4 or "").strip()
        session_id = str(session_id or "").strip()
        if not station_sn:
            return None, "stationSn required"
        if not camera_sn:
            return None, "cameraSn required"
        if preferred_channel is not None:
            try:
                preferred_channel = int(preferred_channel)
            except (TypeError, ValueError):
                preferred_channel = None
        if preferred_channel is not None and not (0 <= preferred_channel < 4):
            preferred_channel = None
        with self.lock, self._connect() as conn:
            state = self._load_state_unlocked(conn)
            self._ensure_station_row_unlocked(conn, state, station_sn)
            rows = conn.execute(
                """
                SELECT station_sn, slot_index, active_flag, pending_pairlist_flag,
                       peer_ipv4, camera_mac, camera_sn, last_session_id, created_at, updated_at
                FROM pairing_slots
                WHERE station_sn = ?
                ORDER BY slot_index ASC
                """,
                (station_sn,),
            ).fetchall()
            by_slot = {int(row["slot_index"] or 0): row for row in rows}
            matched_row = None
            for row in rows:
                current_camera_sn = str(row["camera_sn"] or "").strip()
                current_camera_mac = str(row["camera_mac"] or "").strip()
                if current_camera_sn and current_camera_sn == camera_sn:
                    matched_row = row
                    break
                if camera_mac and current_camera_mac and current_camera_mac == camera_mac:
                    matched_row = row
                    break
            slot_index: int | None = None
            created_at = iso_now()
            if matched_row is not None:
                slot_index = int(matched_row["slot_index"] or 0)
                created_at = str(matched_row["created_at"] or created_at)
            else:
                if preferred_channel is not None:
                    candidate = by_slot.get(preferred_channel)
                    if candidate is None or (
                        int(candidate["active_flag"] or 0) == 0
                        and int(candidate["pending_pairlist_flag"] or 0) == 0
                    ):
                        slot_index = preferred_channel
                if slot_index is None:
                    for candidate_index in range(4):
                        candidate = by_slot.get(candidate_index)
                        if candidate is None or (
                            int(candidate["active_flag"] or 0) == 0
                            and int(candidate["pending_pairlist_flag"] or 0) == 0
                        ):
                            slot_index = candidate_index
                            break
            if slot_index is None:
                return None, "no free pairing slot"
            now = iso_now()
            existing_peer_ipv4 = str((matched_row["peer_ipv4"] if matched_row is not None else "") or "").strip()
            existing_camera_mac = str((matched_row["camera_mac"] if matched_row is not None else "") or "").strip()
            existing_session_id = str((matched_row["last_session_id"] if matched_row is not None else "") or "").strip()
            conn.execute(
                """
                INSERT INTO pairing_slots (
                    station_sn, slot_index, active_flag, pending_pairlist_flag,
                    peer_ipv4, camera_mac, camera_sn, last_session_id, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(station_sn, slot_index) DO UPDATE SET
                    pending_pairlist_flag = excluded.pending_pairlist_flag,
                    peer_ipv4 = excluded.peer_ipv4,
                    camera_mac = excluded.camera_mac,
                    camera_sn = excluded.camera_sn,
                    last_session_id = excluded.last_session_id,
                    updated_at = excluded.updated_at
                """,
                (
                    station_sn,
                    int(slot_index),
                    int((matched_row["active_flag"] if matched_row is not None else 0) or 0),
                    1,
                    peer_ipv4 or existing_peer_ipv4,
                    camera_mac or existing_camera_mac,
                    camera_sn,
                    session_id or existing_session_id,
                    created_at,
                    now,
                ),
            )
            conn.commit()
            row = conn.execute(
                """
                SELECT station_sn, slot_index, active_flag, pending_pairlist_flag,
                       peer_ipv4, camera_mac, camera_sn, last_session_id, created_at, updated_at
                FROM pairing_slots
                WHERE station_sn = ? AND slot_index = ?
                """,
                (station_sn, int(slot_index)),
            ).fetchone()
            return self._pairing_slot_payload_from_row(row), None

    def activate_pairing_slot(
        self,
        *,
        station_sn: str,
        camera_sn: str,
        channel: int,
        camera_mac: str = "",
        peer_ipv4: str = "",
        session_id: str = "",
    ) -> dict[str, Any] | None:
        self = self.store
        station_sn = str(station_sn or "").strip()
        camera_sn = str(camera_sn or "").strip()
        if not station_sn or not camera_sn:
            return None
        camera_mac = str(camera_mac or "").strip()
        peer_ipv4 = str(peer_ipv4 or "").strip()
        session_id = str(session_id or "").strip()
        try:
            channel = int(channel)
        except (TypeError, ValueError):
            return None
        with self.lock, self._connect() as conn:
            state = self._load_state_unlocked(conn)
            self._ensure_station_row_unlocked(conn, state, station_sn)
            now = iso_now()
            existing = conn.execute(
                """
                SELECT created_at, peer_ipv4, camera_mac, last_session_id
                FROM pairing_slots
                WHERE station_sn = ? AND slot_index = ?
                """,
                (station_sn, channel),
            ).fetchone()
            created_at = str((existing["created_at"] if existing else now) or now)
            conn.execute(
                """
                INSERT INTO pairing_slots (
                    station_sn, slot_index, active_flag, pending_pairlist_flag,
                    peer_ipv4, camera_mac, camera_sn, last_session_id, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(station_sn, slot_index) DO UPDATE SET
                    active_flag = excluded.active_flag,
                    pending_pairlist_flag = excluded.pending_pairlist_flag,
                    peer_ipv4 = excluded.peer_ipv4,
                    camera_mac = excluded.camera_mac,
                    camera_sn = excluded.camera_sn,
                    last_session_id = excluded.last_session_id,
                    updated_at = excluded.updated_at
                """,
                (
                    station_sn,
                    channel,
                    1,
                    0,
                    peer_ipv4 or str((existing["peer_ipv4"] if existing else "") or ""),
                    camera_mac or str((existing["camera_mac"] if existing else "") or ""),
                    camera_sn,
                    session_id or str((existing["last_session_id"] if existing else "") or ""),
                    created_at,
                    now,
                ),
            )
            conn.commit()
            row = conn.execute(
                """
                SELECT station_sn, slot_index, active_flag, pending_pairlist_flag,
                       peer_ipv4, camera_mac, camera_sn, last_session_id, created_at, updated_at
                FROM pairing_slots
                WHERE station_sn = ? AND slot_index = ?
                """,
                (station_sn, channel),
            ).fetchone()
            return self._pairing_slot_payload_from_row(row)

    def release_pairing_slot(
        self,
        *,
        station_sn: str = "",
        camera_sn: str = "",
    ) -> int:
        self = self.store
        station_sn = str(station_sn or "").strip()
        camera_sn = str(camera_sn or "").strip()
        if not station_sn and not camera_sn:
            return 0
        with self.lock, self._connect() as conn:
            clauses: list[str] = []
            params: list[Any] = []
            if station_sn:
                clauses.append("station_sn = ?")
                params.append(station_sn)
            if camera_sn:
                clauses.append("camera_sn = ?")
                params.append(camera_sn)
            where_sql = " AND ".join(clauses)
            cur = conn.execute(f"DELETE FROM pairing_slots WHERE {where_sql}", tuple(params))
            conn.commit()
            return int(cur.rowcount or 0)
