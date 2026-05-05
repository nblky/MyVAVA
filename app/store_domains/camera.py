from __future__ import annotations

from typing import Any

from ..store_shared import DEFAULT_CAMERA_SN, DEFAULT_CHANNEL, DEFAULT_STATION_SN, deep_get, iso_now, now_ts
from .base import BaseDomainService


class CameraDomainService(BaseDomainService):
    def _build_camera(
        self,
        camera_sn: str,
        camera_name: str,
        station_sn: str,
        channel: int,
    ) -> dict[str, Any]:
        self = self.store
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
            "cloudStorageBound": 1,
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

    def _normalize_camera_hardver(self, hardver: str) -> str:
        self = self.store
        mapping = {
            "VA-CM002": "VA-HS02",
            "VA-CM003": "VA-HS03",
            "VA-CM004": "VA-HS04",
            "VA-HS002": "VA-HS02",
            "VA-HS003": "VA-HS03",
            "VA-HS004": "VA-HS04",
        }
        return mapping.get(hardver, hardver or "VA-HS03")

    def _normalize_camera_record(self, camera_sn: str, camera: dict[str, Any]) -> bool:
        self = self.store
        changed = False
        defaults = {
            "cameraSn": camera_sn,
            "cameraName": camera_sn,
            "bindId": f"bind-camera-{camera_sn}",
            "stationSn": DEFAULT_STATION_SN,
            "channel": 0,
            "addTime": iso_now(),
            "shareFlag": 0,
            "shareTime": "",
            "sharerName": "",
            "stationOnline": "1",
            "pending": False,
            "cloudStorageBound": 1,
        }
        for key, value in defaults.items():
            if key not in camera:
                camera[key] = value
                changed = True
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
        hardver = self._normalize_camera_hardver(attr.get("hardver", ""))
        if attr.get("hardver") != hardver:
            attr["hardver"] = hardver
            changed = True
        for key, value in {
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
        }.items():
            if attr.get(key) in (None, ""):
                attr[key] = value
                changed = True
        settings = camera.setdefault("settingsObject", {})
        for key, value in {"mailSwitch": 1, "pushSwitch": 1, "voiceSwitch": 1}.items():
            if key not in settings:
                settings[key] = value
                changed = True
            coerced = self._coerce_switch_value(settings.get(key))
            if settings.get(key) != coerced:
                settings[key] = coerced
                changed = True
        return changed

    def _camera_battery_low(self, status: dict[str, Any] | None) -> bool:
        self = self.store
        status = status if isinstance(status, dict) else {}
        try:
            level = int(deep_get(status, "lever", -1) or -1)
        except (TypeError, ValueError):
            level = -1
        if 0 <= level <= 20:
            return True
        try:
            voltage = int(deep_get(status, "voltage", 0) or 0)
        except (TypeError, ValueError):
            voltage = 0
        return 0 < voltage <= 3600

    def _camera_version_marker(self, attr: dict[str, Any] | None) -> str:
        self = self.store
        attr = attr if isinstance(attr, dict) else {}
        parts = [
            self._normalize_notice_text(deep_get(attr, "softver", "")),
            self._normalize_notice_text(deep_get(attr, "f_appversionout", "")),
            self._normalize_notice_text(deep_get(attr, "f_appversionin", "")),
            self._normalize_notice_text(deep_get(attr, "h_appversionout", "")),
            self._normalize_notice_text(deep_get(attr, "h_appversionin", "")),
        ]
        return "|".join(part for part in parts if part)

    def _camera_tombstone_key(self, camera_sn: str = "") -> str:
        self = self.store
        return str(camera_sn or "").strip()

    def _camera_is_removed_unlocked(self, state: dict[str, Any], camera_sn: str) -> bool:
        self = self.store
        key = self._camera_tombstone_key(camera_sn)
        if not key:
            return False
        return key in {
            str(item or "").strip()
            for item in state.get("cameraTombstones", [])
            if str(item or "").strip()
        }

    def _remember_removed_camera_unlocked(self, state: dict[str, Any], camera_sn: str) -> bool:
        self = self.store
        key = self._camera_tombstone_key(camera_sn)
        if not key:
            return False
        tombstones = state.setdefault("cameraTombstones", [])
        if key in tombstones:
            return False
        tombstones.append(key)
        if len(tombstones) > 1000:
            del tombstones[:-1000]
        return True

    def _forget_removed_camera_unlocked(self, state: dict[str, Any], camera_sn: str) -> bool:
        self = self.store
        key = self._camera_tombstone_key(camera_sn)
        if not key:
            return False
        tombstones = state.setdefault("cameraTombstones", [])
        filtered = [item for item in tombstones if self._camera_tombstone_key(item) != key]
        if filtered == tombstones:
            return False
        state["cameraTombstones"] = filtered
        return True

    def camera_bind_check_payload(
        self,
        *,
        camera_sn: str,
        station_sn: str = "",
        access_token: str = "",
        identifier: str = "",
        user_id: str = "",
    ) -> dict[str, Any]:
        self = self.store
        camera_sn = str(camera_sn or "").strip()
        station_sn = str(station_sn or "").strip()
        if not camera_sn:
            return {"stateCode": 301001, "stateMsg": "Camera SN does not exist", "data": {}}
        requested_scope = bool(str(access_token or "").strip() or str(identifier or "").strip() or str(user_id or "").strip())
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT c.camera_sn, c.station_sn, c.channel,
                       s.owner_user_id, s.owner_username
                FROM cameras c
                LEFT JOIN stations s ON s.station_sn = c.station_sn
                WHERE c.camera_sn = ?
                LIMIT 1
                """,
                (camera_sn,),
            ).fetchone()
            if not row:
                return {
                    "stateCode": 200,
                    "stateMsg": "OK",
                    "data": {
                        "cameraSn": camera_sn,
                        "stationSn": station_sn,
                        "bound": 0,
                    },
                }
            resolved_user = self._resolve_user_for_station_binding_unlocked(
                conn,
                access_token=access_token,
                identifier=identifier,
                user_id=user_id,
                allow_default_fallback=not requested_scope,
            )
            target_user_id = str((resolved_user["user_id"] if resolved_user else "") or "").strip()
            current_station_sn = str(row["station_sn"] or "") or station_sn
            owner_user_id = str(row["owner_user_id"] or "")
            owner_username = str(row["owner_username"] or "")
            payload = {
                "cameraSn": camera_sn,
                "stationSn": current_station_sn or station_sn,
                "channel": int(row["channel"] or 0),
                "bound": 1,
                "ownerUserId": owner_user_id,
                "ownerUsername": owner_username,
            }
            if target_user_id and owner_user_id and owner_user_id == target_user_id:
                return {
                    "stateCode": 216003,
                    "stateMsg": "Add completed! But this device has been added to your account already",
                    "data": payload,
                }
            if owner_user_id:
                return {
                    "stateCode": 216004,
                    "stateMsg": "Camera has been added by someone",
                    "data": payload,
                }
            return {"stateCode": 200, "stateMsg": "OK", "data": payload}

    def ensure_camera(
        self,
        camera_sn: str,
        station_sn: str,
        channel: int,
        camera_name: str | None = None,
        pending: bool = False,
    ) -> dict[str, Any]:
        self = self.store
        with self.lock, self._connect() as conn:
            state = self._load_state_unlocked(conn)
            self._forget_removed_camera_unlocked(state, camera_sn)
            cameras = state.setdefault("cameras", {})
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
            station = state.setdefault("stations", {}).setdefault(
                station_sn,
                self._build_station(station_sn, station_sn),
            )
            self._normalize_station_record(station_sn, station)
            station["cameraTotal"] = len(
                [c for c in cameras.values() if c.get("stationSn") == station_sn]
            )
            self._save_state_unlocked(conn, state)
            return camera

    def mark_camera_bound(self, camera_sn: str, camera_name: str | None = None):
        self = self.store
        with self.lock, self._connect() as conn:
            state = self._load_state_unlocked(conn)
            self._forget_removed_camera_unlocked(state, camera_sn)
            camera = state.setdefault("cameras", {}).get(camera_sn)
            if not camera:
                return None
            if camera_name:
                camera["cameraName"] = camera_name
            camera["pending"] = False
            camera["statusObject"]["timestamp"] = now_ts()
            camera["statusObject"]["time"] = iso_now()
            self._save_state_unlocked(conn, state)
            return camera

    def remove_station(self, station_sn: str):
        self = self.store
        with self.lock, self._connect() as conn:
            state = self._load_state_unlocked(conn)
            stations = state.setdefault("stations", {})
            station = stations.pop(station_sn, None)
            if not station:
                return None
            cameras = state.setdefault("cameras", {})
            removed_cameras = []
            for camera_sn in list(cameras.keys()):
                camera = cameras.get(camera_sn, {})
                if camera.get("stationSn") != station_sn:
                    continue
                removed_cameras.append(camera_sn)
                self._remember_removed_camera_unlocked(state, camera_sn)
                cameras.pop(camera_sn, None)
            conn.execute("DELETE FROM pairing_sessions WHERE station_sn = ?", (str(station_sn or "").strip(),))
            conn.execute("DELETE FROM pairing_slots WHERE station_sn = ?", (str(station_sn or "").strip(),))
            self._save_state_unlocked(conn, state)
            return {"station": station, "removedCameras": removed_cameras}

    def remove_camera(self, camera_sn: str):
        self = self.store
        with self.lock, self._connect() as conn:
            state = self._load_state_unlocked(conn)
            cameras = state.setdefault("cameras", {})
            camera = cameras.pop(camera_sn, None)
            if not camera:
                return None
            self._remember_removed_camera_unlocked(state, camera_sn)
            station_sn = camera.get("stationSn", "")
            if station_sn:
                station = state.setdefault("stations", {}).get(station_sn)
                if station:
                    station["cameraTotal"] = len(
                        [c for c in cameras.values() if c.get("stationSn") == station_sn]
                    )
                    station["statusObject"]["timestamp"] = now_ts()
                    station["statusObject"]["time"] = iso_now()
            conn.execute("DELETE FROM pairing_sessions WHERE camera_sn = ?", (str(camera_sn or "").strip(),))
            conn.execute("DELETE FROM pairing_slots WHERE camera_sn = ?", (str(camera_sn or "").strip(),))
            self._save_state_unlocked(conn, state)
            return camera

    def update_camera_settings(self, camera_sn: str, body: dict[str, Any]):
        self = self.store
        with self.lock, self._connect() as conn:
            state = self._load_state_unlocked(conn)
            camera = state.setdefault("cameras", {}).get(camera_sn)
            if not camera:
                return None
            settings = camera.setdefault("settingsObject", {})
            nested_settings = deep_get(body, "settingsObject", {}) or {}
            for dst, aliases in {
                "mailSwitch": (
                    "mailSwitch",
                    "emailNotification",
                    "emailSwitch",
                    "mailNotifySwitch",
                ),
                "pushSwitch": (
                    "pushSwitch",
                    "msgSwitch",
                    "mobilePhoneNotification",
                    "mobileNotification",
                    "notificationSwitch",
                    "systemNotification",
                ),
                "voiceSwitch": (
                    "voiceSwitch",
                    "soundSwitch",
                    "alarmSoundSwitch",
                ),
            }.items():
                value = None
                for src in aliases:
                    value = deep_get(body, src)
                    if value is None:
                        value = deep_get(nested_settings, src)
                    if value is not None:
                        break
                if value is not None:
                    settings[dst] = self._coerce_switch_value(value)
            if deep_get(body, "shareFlag") is not None:
                camera["shareFlag"] = int(deep_get(body, "shareFlag", 0) or 0)
            name = (
                deep_get(body, "cameraName")
                or deep_get(body, "deviceName")
                or deep_get(body, "name")
            )
            if name:
                camera["cameraName"] = name
            camera["statusObject"]["timestamp"] = now_ts()
            camera["statusObject"]["time"] = iso_now()
            self._normalize_camera_record(camera_sn, camera)
            self._save_state_unlocked(conn, state)
            return camera

    def camera_index_payload(
        self,
        *,
        access_token: str = "",
        identifier: str = "",
        user_id: str = "",
    ) -> dict[str, Any]:
        self = self.store
        with self._connect() as conn:
            state = self._load_state_unlocked(conn)
            allowed_station_sns = self._bound_station_sns_for_user_unlocked(
                conn,
                access_token=access_token,
                identifier=identifier,
                user_id=user_id,
            )
            stations = []
            for station_sn, station in sorted(state.get("stations", {}).items()):
                if allowed_station_sns is not None and station_sn not in allowed_station_sns:
                    continue
                stations.append(
                    self._export_share_metadata_unlocked(
                        {
                            "addTime": station.get("addTime", iso_now()),
                            "attrObject": station.get("attrObject", {}),
                            "bindId": station.get("bindId", f"bind-station-{station_sn}"),
                            "deviceName": station.get("deviceName", station.get("stationName", station_sn)),
                            "did": self.station_did_for_index(station_sn),
                            "permissionObject": station.get("permissionObject", {}),
                            "shareFlag": station.get("shareFlag", 0),
                            "shareTime": station.get("shareTime", ""),
                            "sharerName": station.get("sharerName", ""),
                            "stationName": station.get("stationName", station_sn),
                            "stationOnline": station.get("stationOnline", "1"),
                            "stationSn": station_sn,
                            "ownerUserId": station.get("ownerUserId", ""),
                            "ownerUsername": station.get("ownerUsername", ""),
                            "bindingCount": int(station.get("bindingCount", 0) or 0),
                            "statusObject": station.get("statusObject", {}),
                        }
                    )
                )
            cameras = []
            for camera_sn, camera in sorted(state.get("cameras", {}).items()):
                station_sn = str(camera.get("stationSn", "") or "")
                if allowed_station_sns is not None and station_sn not in allowed_station_sns:
                    continue
                latest_message = self._latest_message_for_camera_unlocked(state, camera_sn)
                latest_ext = latest_message.get("extObject", {}) or {}
                status_object = dict(camera.get("statusObject", {}) or {})
                if int(status_object.get("online", 0) or 0) == 1:
                    status_object["video"] = 1
                camera_payload = {
                    "addTime": camera.get("addTime", iso_now()),
                    "attrObject": camera.get("attrObject", {}),
                    "bindId": camera.get("bindId", f"bind-camera-{camera_sn}"),
                    "cameraName": camera.get("cameraName", camera_sn),
                    "cameraSn": camera_sn,
                    "channel": camera.get("channel", 0),
                    "connectType": 0,
                    "deviceName": camera.get("cameraName", camera_sn),
                    "settingsObject": camera.get("settingsObject", {}),
                    "shareFlag": camera.get("shareFlag", 0),
                    "shareTime": camera.get("shareTime", ""),
                    "sharerName": camera.get("sharerName", ""),
                    "stationOnline": camera.get("stationOnline", "1"),
                    "stationSn": camera.get("stationSn", ""),
                    "statusObject": status_object,
                    "yunFlag": int(camera.get("cloudStorageBound", 1) or 0),
                }
                camera_payload.update(
                    self._thumbnail_fields(
                        station_sn=str(camera.get("stationSn", "") or DEFAULT_STATION_SN),
                        camera_sn=camera_sn,
                        file_date=str(deep_get(latest_ext, "fileDate", "") or ""),
                        file_name=str(deep_get(latest_ext, "fileName", "") or ""),
                        stream_code=self._stream_code(
                            device_sn=camera_sn,
                            file_date=str(deep_get(latest_ext, "fileDate", "") or ""),
                            file_name=str(deep_get(latest_ext, "fileName", "") or ""),
                        ),
                    )
                )
                cameras.append(
                    self._export_share_metadata_unlocked(camera_payload)
                )
            return {
                "cameraList": cameras,
                "intervalSeconds": 10,
                "stationList": stations,
            }

    def my_devices_payload(
        self,
        *,
        access_token: str = "",
        identifier: str = "",
        user_id: str = "",
    ) -> dict[str, Any]:
        self = self.store
        index_payload = self.camera_index_payload(
            access_token=access_token,
            identifier=identifier,
            user_id=user_id,
        )
        station_list = []
        for station in index_payload["stationList"]:
            station_sn = station.get("stationSn", "")
            station_cameras = [
                camera
                for camera in index_payload["cameraList"]
                if camera.get("stationSn") == station_sn
            ]
            station_cameras.sort(key=lambda item: int(item.get("channel", 0)))
            entry = dict(station)
            entry["cameraList"] = station_cameras
            station_list.append(entry)
        return {"stationList": station_list}

    def station_camera_list_payload(
        self,
        station_sn: str,
        *,
        access_token: str = "",
        identifier: str = "",
        user_id: str = "",
    ) -> dict[str, Any]:
        self = self.store
        with self._connect() as conn:
            state = self._load_state_unlocked(conn)
            allowed_station_sns = self._bound_station_sns_for_user_unlocked(
                conn,
                access_token=access_token,
                identifier=identifier,
                user_id=user_id,
            )
            if allowed_station_sns is not None and station_sn not in allowed_station_sns:
                return {
                    "cameraList": [],
                    "intervalSeconds": 10,
                    "slowIntervalSeconds": 60,
                }
            cameras = []
            for camera_sn, camera in sorted(state.get("cameras", {}).items()):
                if camera.get("stationSn") != station_sn:
                    continue
                cameras.append(
                    {
                        "cameraSn": camera_sn,
                        "cameraName": camera.get("cameraName", camera_sn),
                        "channel": int(camera.get("channel", 0)),
                        "yunFlag": int(camera.get("cloudStorageBound", 1) or 0),
                    }
                )
            cameras.sort(key=lambda item: int(item.get("channel", 0)))
            return {
                "cameraList": cameras,
                "intervalSeconds": 10,
                "slowIntervalSeconds": 60,
            }

    def camera_status_payload(
        self,
        requested_sn_csv: str = "",
        *,
        access_token: str = "",
        identifier: str = "",
        user_id: str = "",
    ) -> dict[str, Any]:
        self = self.store
        requested_set = {item.strip() for item in str(requested_sn_csv or "").split(",") if item.strip()}
        payload = []
        for camera in self.camera_index_payload(
            access_token=access_token,
            identifier=identifier,
            user_id=user_id,
        )["cameraList"]:
            if requested_set and camera["cameraSn"] not in requested_set:
                continue
            payload.append(
                {
                    "deviceSn": camera["cameraSn"],
                    "statusObject": camera["statusObject"],
                }
            )
        return {"snList": payload}
