from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from typing import Any

from ..push import classify_push_token
from ..station_sync import collect_recording_notices
from ..store_shared import (
    DEFAULT_CAMERA_SN,
    DEFAULT_STATION_SN,
    DEFAULT_USER_ID,
    DEFAULT_VISIBLE_NOTICE_TYPE,
    VISIBLE_NOTICE_TYPES,
    deep_get,
    iso_now,
    now_ts,
)
from .base import BaseDomainService


class MessageDomainService(BaseDomainService):
    def _message_station_sn_unlocked(
        self,
        state: dict[str, Any],
        message: dict[str, Any],
    ) -> str:
        self = self.store
        station_sn = str(message.get("parentDeviceSn", "") or "").strip()
        if station_sn:
            return station_sn
        camera_sn = str(message.get("deviceSn", "") or "").strip()
        if not camera_sn:
            return ""
        camera = deep_get(state.get("cameras", {}), camera_sn, {}) or {}
        return str(camera.get("stationSn", "") or "").strip()

    def _message_visible_for_scope_unlocked(
        self,
        state: dict[str, Any],
        message: dict[str, Any],
        *,
        allowed_station_sns: set[str] | None,
    ) -> bool:
        self = self.store
        if allowed_station_sns is None:
            return True
        message_station_sn = self._message_station_sn_unlocked(state, message)
        return bool(message_station_sn and message_station_sn in allowed_station_sns)

    def _message_key(self, message):
        self = self.store
        ext = message.get("extObject", {}) or {}
        return (
            str(message.get("deviceSn", "") or ""),
            str(deep_get(ext, "fileDate", "") or ""),
            str(deep_get(ext, "fileName", "") or ""),
        )

    def _message_tombstone_key(
        self,
        *,
        device_sn: Any = "",
        file_date: Any = "",
        file_name: Any = "",
        stream_code: Any = "",
    ) -> str:
        self = self.store
        stream_device_sn, stream_file_date, stream_file_name = self._parse_stream_code(stream_code)
        normalized_device_sn = str(device_sn or stream_device_sn or "").strip()
        normalized_file_date = self._normalize_date_digits(file_date or stream_file_date or "")
        normalized_file_name = str(file_name or stream_file_name or "").strip()
        if not (normalized_device_sn and normalized_file_date and normalized_file_name):
            return ""
        return self._stream_code(
            device_sn=normalized_device_sn,
            file_date=normalized_file_date,
            file_name=normalized_file_name,
        )

    def _message_tombstone_key_for_message(self, message: dict[str, Any]) -> str:
        self = self.store
        device_sn, file_date, file_name = self._message_key(message)
        return self._message_tombstone_key(
            device_sn=device_sn,
            file_date=file_date,
            file_name=file_name,
        )

    def _collect_recording_notices(
        self,
        state: dict[str, Any],
        station_sn: str,
    ) -> list[dict[str, Any]]:
        self = self.store
        return collect_recording_notices(self.settings, state, station_sn)

    def _notice_type_from_ext(self, source_notice_type, ext):
        self = self.store
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

    def _cloud_trigger_type_for_app(self, visible_notice_type: Any) -> int:
        self = self.store
        try:
            visible_notice_type = int(visible_notice_type or 0)
        except (TypeError, ValueError):
            visible_notice_type = 0
        return {
            5: 4,
            6: 0,
            7: 2,
            8: 3,
            2: 4,
        }.get(visible_notice_type, visible_notice_type)

    def _normalize_notice_text(self, value: Any) -> str:
        self = self.store
        return " ".join(str(value or "").strip().split())

    def _should_refresh_notice_title(self, title: str) -> bool:
        self = self.store
        normalized = self._normalize_notice_text(title)
        return (not normalized) or normalized.startswith("Notice ") or normalized in {
            "Motion detected",
            "Movement detected",
            "Human detected",
            "Face detected",
            "Human face detected",
            "Camera has low battery, please charge",
            "Firmware successfully updated",
            "The camera is offline",
        }

    def _notice_title(
        self,
        notice_type,
        ext: dict[str, Any] | None = None,
        *,
        content: str = "",
        explicit_title: str = "",
    ):
        self = self.store
        explicit = self._normalize_notice_text(explicit_title)
        if explicit:
            return explicit

        ext = ext if isinstance(ext, dict) else {}
        try:
            source_notice_type = int(notice_type or 0)
        except (TypeError, ValueError):
            source_notice_type = 0
        try:
            file_type = int(deep_get(ext, "fileType", 0) or 0)
        except (TypeError, ValueError):
            file_type = 0

        if source_notice_type == 1:
            return {
                5: "Movement detected",
                6: "Human detected",
                7: "Face detected",
                8: "Human face detected",
            }.get(file_type, "Movement detected")

        if source_notice_type == 7:
            return "Firmware successfully updated"

        raw_hint = deep_get(ext, "title", "") or deep_get(ext, "msg", "") or content
        hint = self._normalize_notice_text(raw_hint).lower()
        if hint:
            if "battery" in hint and ("low" in hint or "charge" in hint):
                return "Camera has low battery, please charge"
            if "offline" in hint or "disconnect" in hint:
                return "The camera is offline"
            return self._normalize_notice_text(raw_hint)

        if source_notice_type == 2:
            return "The camera is offline"
        return f"Notice {notice_type}"

    def _camera_is_offline(self, status: dict[str, Any] | None) -> bool:
        self = self.store
        status = status if isinstance(status, dict) else {}
        try:
            return int(deep_get(status, "online", 1) or 0) == 0
        except (TypeError, ValueError):
            return False

    def _massage_notice_payload_unlocked(
        self,
        state: dict[str, Any],
        camera_sn: str,
        notice_type: int,
        ext: dict[str, Any] | None = None,
        *,
        content: str = "",
        title: str = "",
    ) -> tuple[int, dict[str, Any], str]:
        self = self.store
        ext = dict(ext or {})
        explicit_title = self._normalize_notice_text(title)
        camera = state.get("cameras", {}).get(str(camera_sn or DEFAULT_CAMERA_SN), {}) or {}
        status = camera.get("statusObject", {}) or {}
        file_name = str(deep_get(ext, "fileName", "") or "").strip()
        file_date = str(deep_get(ext, "fileDate", "") or "").strip()
        try:
            file_type = int(deep_get(ext, "fileType", 0) or 0)
        except (TypeError, ValueError):
            file_type = 0

        if int(notice_type or 0) == 5 and not file_name and not file_date and file_type == 0:
            if self._camera_is_offline(status):
                ext["msg"] = "offline"
                return 2, ext, "The camera is offline"
            if self._camera_battery_low(status):
                ext["msg"] = "low battery"
                return 2, ext, "Camera has low battery, please charge"
        return int(notice_type or 1), ext, explicit_title

    def _format_notice_device_time(self, raw: str) -> str:
        self = self.store
        if not raw:
            return datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        raw = str(raw)
        if len(raw) == 14 and raw.isdigit():
            return (
                f"{raw[0:4]}-{raw[4:6]}-{raw[6:8]} "
                f"{raw[8:10]}:{raw[10:12]}:{raw[12:14]}"
            )
        return raw

    def _parse_notice_add_timestamp_ms(self, device_time, raw_timestamp):
        self = self.store
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
                dt = datetime.strptime(device_time, "%Y-%m-%d %H:%M:%S").replace(
                    tzinfo=timezone.utc
                )
                return int(dt.timestamp() * 1000)
            except ValueError:
                pass
        return now_ts() * 1000

    def _remember_removed_messages_unlocked(
        self,
        state: dict[str, Any],
        removed_items: list[dict[str, Any]],
    ) -> bool:
        self = self.store
        tombstones = state.setdefault("messageTombstones", [])
        existing = {str(item or "").strip() for item in tombstones if str(item or "").strip()}
        changed = False
        for message in removed_items:
            key = self._message_tombstone_key_for_message(message)
            if not key or key in existing:
                continue
            tombstones.append(key)
            existing.add(key)
            changed = True
        if len(tombstones) > 1000:
            del tombstones[:-1000]
            changed = True
        return changed

    def _classify_push_token(self, push_token: str) -> str:
        return classify_push_token(push_token)

    def _is_viable_push_token(self, push_token: str) -> bool:
        self = self.store
        token = str(push_token or "").strip()
        if len(token) < 32:
            return False
        return self._classify_push_token(token) in {"apns", "fcm"}

    def _filtered_messages(
        self,
        state: dict[str, Any],
        date: str = "",
        device_sn: str = "",
        type_list: list[Any] | None = None,
        *,
        allowed_station_sns: set[str] | None = None,
    ) -> list[dict[str, Any]]:
        self = self.store
        normalized_date = "".join(ch for ch in str(date or "").strip() if ch.isdigit())
        type_set = None
        if type_list:
            type_set = {int(item) for item in type_list}
        out = []
        for message in state.get("messages", []):
            if not self._message_visible_for_scope_unlocked(
                state,
                message,
                allowed_station_sns=allowed_station_sns,
            ):
                continue
            source_type = int(message.get("sourceNoticeType", message.get("noticeType", 1)) or 1)
            visible_type = int(
                message.get(
                    "visibleNoticeType",
                    self._notice_type_from_ext(source_type, message.get("extObject", {}) or {}),
                )
                or 0
            )
            stored_type = int(message.get("noticeType", source_type) or source_type)
            if type_set and not ({visible_type, source_type, stored_type} & type_set):
                continue
            if device_sn and message.get("deviceSn") != device_sn:
                continue
            msg_date = deep_get(message.get("extObject", {}), "fileDate", "") or ""
            if normalized_date:
                if len(normalized_date) == 6 and not msg_date.startswith(normalized_date):
                    continue
                if len(normalized_date) == 8 and msg_date != normalized_date:
                    continue
            out.append(message)
        return out

    def _new_notice_id_unlocked(self, state: dict[str, Any]) -> int:
        self = self.store
        notice_id = int(state.get("messageCounter", 1) or 1)
        state["messageCounter"] = notice_id + 1
        return notice_id

    def _append_notice_unlocked(self, state: dict[str, Any], message: dict[str, Any]) -> None:
        self = self.store
        messages = state.setdefault("messages", [])
        messages.insert(0, message)
        del messages[500:]

    def _append_system_notice_unlocked(
        self,
        state: dict[str, Any],
        *,
        camera_sn: str,
        notice_type: int,
        title: str,
        content: str = "",
        ext: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self = self.store
        message = self._build_notice_message_unlocked(
            state,
            camera_sn,
            notice_type,
            ext=ext or {},
            content=content,
            title=title,
        )
        self._append_notice_unlocked(state, message)
        return message

    def _build_notice_message_unlocked(
        self,
        state: dict[str, Any],
        camera_sn: str,
        source_notice_type: int,
        ext: dict[str, Any] | None = None,
        content: str = "",
        title: str = "",
    ) -> dict[str, Any]:
        self = self.store
        ext = ext or {}
        camera_sn = str(camera_sn or DEFAULT_CAMERA_SN)
        camera = state.setdefault("cameras", {}).get(camera_sn, {})
        device_name = camera.get("cameraName", camera_sn)
        device_time = self._format_notice_device_time(deep_get(ext, "deviceTime", ""))
        visible_notice_type = self._notice_type_from_ext(source_notice_type, ext)
        message = {
            "noticeId": self._new_notice_id_unlocked(state),
            "noticeStatus": 0,
            "noticeType": int(source_notice_type or 1),
            "sourceNoticeType": int(source_notice_type or 1),
            "visibleNoticeType": visible_notice_type,
            "shareFlag": 0,
            "deviceSn": camera_sn,
            "deviceName": device_name,
            "parentDeviceSn": camera.get("stationSn", DEFAULT_STATION_SN),
            "title": self._notice_title(
                source_notice_type,
                ext,
                content=content,
                explicit_title=title,
            ),
            "content": content or "",
            "addTimestamp": self._parse_notice_add_timestamp_ms(
                device_time,
                deep_get(ext, "timestamp", now_ts()),
            ),
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
        self._normalize_message_record_unlocked(message, state)
        return message

    def _latest_message_for_camera_unlocked(
        self,
        state: dict[str, Any],
        camera_sn: str,
    ) -> dict[str, Any]:
        self = self.store
        latest: dict[str, Any] | None = None
        for message in state.get("messages", []):
            if str(message.get("deviceSn", "") or "") != camera_sn:
                continue
            if latest is None or int(message.get("noticeId", 0) or 0) > int(
                latest.get("noticeId", 0) or 0
            ):
                latest = message
        return latest or {}

    def _normalize_message_record_unlocked(
        self,
        message: dict[str, Any],
        state: dict[str, Any],
    ) -> bool:
        self = self.store
        changed = False
        ext = message.setdefault("extObject", {})
        camera_sn = str(message.get("deviceSn", "") or "")
        camera = state.get("cameras", {}).get(camera_sn, {})
        remapped_notice_type, remapped_ext, remapped_title = self._massage_notice_payload_unlocked(
            state,
            camera_sn,
            int(message.get("sourceNoticeType", message.get("noticeType", 1)) or 1),
            ext,
            content=str(message.get("content", "") or ""),
            title=str(message.get("title", "") or ""),
        )
        if remapped_ext != ext:
            message["extObject"] = remapped_ext
            ext = message["extObject"]
            changed = True
        source_notice_type = int(
            remapped_notice_type
            or message.get("sourceNoticeType", message.get("noticeType", 1))
            or 1
        )
        visible_notice_type = self._notice_type_from_ext(source_notice_type, ext)
        if message.get("sourceNoticeType") != source_notice_type:
            message["sourceNoticeType"] = source_notice_type
            changed = True
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
        normalized_title = self._notice_title(
            source_notice_type,
            ext,
            content=str(message.get("content", "") or ""),
            explicit_title=remapped_title,
        )
        title = str(message.get("title", "") or "")
        if self._should_refresh_notice_title(title):
            if title != normalized_title:
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

    def _export_share_metadata_unlocked(self, payload: dict[str, Any]) -> dict[str, Any]:
        self = self.store
        share_flag = int(payload.get("shareFlag", 0) or 0)
        payload["shareFlag"] = share_flag
        if share_flag == 0:
            if not str(payload.get("sharerName", "") or "").strip():
                payload.pop("sharerName", None)
            if not str(payload.get("shareTime", "") or "").strip():
                payload.pop("shareTime", None)
        return payload

    def add_notice(self, body: dict[str, Any]) -> dict[str, Any]:
        self = self.store
        with self.lock, self._connect() as conn:
            state = self._load_state_unlocked(conn)
            camera_sn = deep_get(body, "deviceSn", DEFAULT_CAMERA_SN)
            notice_type = int(deep_get(body, "noticeType", 1) or 1)
            ext = deep_get(body, "extJson", {}) or {}
            if isinstance(ext, str):
                try:
                    ext = json.loads(ext)
                except json.JSONDecodeError:
                    ext = {}
            notice_type, ext, explicit_title = self._massage_notice_payload_unlocked(
                state,
                camera_sn,
                notice_type,
                ext,
                content=deep_get(body, "content", "") or "",
                title=deep_get(body, "title", "") or deep_get(ext, "title", "") or "",
            )
            message = self._build_notice_message_unlocked(
                state,
                camera_sn,
                notice_type,
                ext=ext,
                content=deep_get(body, "content", "") or "",
                title=explicit_title,
            )
            self._append_notice_unlocked(state, message)
            self._save_state_unlocked(conn, state)
            return message

    def refresh_messages_from_station_if_needed(self, force: bool = False) -> int:
        self = self.store
        if not self.settings.station_sync_enabled:
            return 0
        with self.lock, self._connect() as conn:
            if not force and time.time() - self.last_message_sync_at < 15:
                return 0
            self.last_message_sync_at = time.time()
            state = self._load_state_unlocked(conn)
            station_sns = list(state.get("stations", {}).keys()) or [DEFAULT_STATION_SN]
            state_snapshot = json.loads(json.dumps(state))
        try:
            candidates: list[dict[str, Any]] = []
            for station_sn in station_sns:
                candidates.extend(self._collect_recording_notices(state_snapshot, station_sn))
        except BaseException as exc:
            self.last_message_sync_error = str(exc)
            return 0

        added = 0
        removed = 0
        with self.lock, self._connect() as conn:
            state = self._load_state_unlocked(conn)
            messages = state.setdefault("messages", [])
            changed = False
            existing = {self._message_key(message) for message in messages}
            tombstones = {
                self._message_tombstone_key(stream_code=item)
                for item in state.get("messageTombstones", [])
            }
            for candidate in candidates:
                key = (candidate["deviceSn"], candidate["fileDate"], candidate["fileName"])
                if key in existing:
                    continue
                tombstone_key = self._message_tombstone_key(
                    device_sn=candidate["deviceSn"],
                    file_date=candidate["fileDate"],
                    file_name=candidate["fileName"],
                )
                if tombstone_key and tombstone_key in tombstones:
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
                        state,
                        candidate["deviceSn"],
                        1,
                        ext=ext,
                    ),
                )
                existing.add(key)
                added += 1
                changed = True
            if changed:
                messages.sort(
                    key=lambda item: (
                        str(deep_get(item.get("extObject", {}), "fileDate", "") or ""),
                        str(deep_get(item.get("extObject", {}), "fileName", "") or ""),
                        int(item.get("noticeId", 0) or 0),
                    ),
                    reverse=True,
                )
                del messages[500:]
                self._save_state_unlocked(conn, state)
        self.last_message_sync_error = ""
        return added + removed

    def set_push_token(self, push_token: str, remote: str = "") -> bool:
        self = self.store
        push_token = str(push_token or "").strip()
        if not self._is_viable_push_token(push_token):
            return False
        with self.lock, self._connect() as conn:
            state = self._load_state_unlocked(conn)
            changed = False
            if state.get("pushToken") != push_token:
                state["pushToken"] = push_token
                changed = True
            tokens = state.setdefault("pushTokens", [])
            if push_token not in tokens:
                tokens.append(push_token)
                del tokens[:-10]
                changed = True
            registrations = state.setdefault("pushRegistrations", [])
            platform = self._classify_push_token(push_token)
            registration = None
            for item in registrations:
                if str(item.get("token", "") or "").strip() == push_token:
                    registration = item
                    break
            desired = {
                "token": push_token,
                "platform": platform,
                "remote": str(remote or ""),
                "tokenSuffix": push_token[-12:] if len(push_token) > 12 else push_token,
                "updatedAt": iso_now(),
            }
            if registration is None:
                registrations.append(desired)
                changed = True
            else:
                for key, value in desired.items():
                    if registration.get(key) != value:
                        registration[key] = value
                        changed = True
            registrations.sort(
                key=lambda item: (
                    str(item.get("updatedAt", "") or ""),
                    str(item.get("token", "") or ""),
                )
            )
            if len(registrations) > 20:
                del registrations[:-20]
                changed = True
            meta = state.setdefault("meta", {})
            if remote and meta.get("lastPushTokenRemote") != remote:
                meta["lastPushTokenRemote"] = remote
                changed = True
            if changed:
                self._save_state_unlocked(conn, state)
            return changed

    def push_registrations(self) -> list[dict[str, Any]]:
        self = self.store
        with self._connect() as conn:
            state = self._load_state_unlocked(conn)
            registrations: list[dict[str, Any]] = []
            seen: set[str] = set()
            for item in state.get("pushRegistrations", []):
                if not isinstance(item, dict):
                    continue
                token = str(item.get("token", "") or "").strip()
                if not self._is_viable_push_token(token) or token in seen:
                    continue
                registrations.append(json.loads(json.dumps(item)))
                seen.add(token)
            for token in state.get("pushTokens", []):
                token = str(token or "").strip()
                if not self._is_viable_push_token(token) or token in seen:
                    continue
                registrations.append(
                    {
                        "token": token,
                        "platform": self._classify_push_token(token),
                        "remote": str(state.get("meta", {}).get("lastPushTokenRemote", "") or ""),
                        "tokenSuffix": token[-12:] if len(token) > 12 else token,
                        "updatedAt": iso_now(),
                    }
                )
                seen.add(token)
            registrations.sort(
                key=lambda item: (
                    str(item.get("updatedAt", "") or ""),
                    str(item.get("token", "") or ""),
                ),
                reverse=True,
            )
            return registrations

    def message_count_payload(
        self,
        type_list: list[Any] | None = None,
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
            messages = self._filtered_messages(
                state,
                type_list=type_list,
                allowed_station_sns=allowed_station_sns,
            )
            return {
                "unreadCount": len(messages),
                "unreadTypeCount": len(
                    {
                        int(
                            msg.get(
                                "visibleNoticeType",
                                self._notice_type_from_ext(
                                    int(
                                        msg.get(
                                            "sourceNoticeType",
                                            msg.get("noticeType", 1),
                                        )
                                        or 1
                                    ),
                                    msg.get("extObject", {}) or {},
                                ),
                            )
                            or 0
                        )
                        for msg in messages
                    }
                ),
            }

    def message_filter_payload(
        self,
        date: str = "",
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
            messages = self._filtered_messages(
                state,
                date=date,
                allowed_station_sns=allowed_station_sns,
            )
            date_set = sorted(
                {
                    deep_get(msg.get("extObject", {}), "fileDate", "")
                    for msg in messages
                    if deep_get(msg.get("extObject", {}), "fileDate", "")
                },
                reverse=True,
            )
            device_names = {}
            for camera_sn, camera in state.get("cameras", {}).items():
                if allowed_station_sns is not None and str(camera.get("stationSn", "") or "").strip() not in allowed_station_sns:
                    continue
                device_names[camera_sn] = camera.get("cameraName", camera_sn)
            for msg in messages:
                device_names.setdefault(msg.get("deviceSn", ""), msg.get("deviceName", ""))
            device_list = [
                {
                    "deviceSn": sn,
                    "deviceName": name,
                    "isSelect": False,
                    "isTempSelect": False,
                    "userId": state.get("user", {}).get("userid", DEFAULT_USER_ID),
                }
                for sn, name in sorted(device_names.items())
                if sn
            ]
            return {"dateSet": date_set, "deviceList": device_list}

    def message_list_payload(
        self,
        *,
        date: str = "",
        device_sn: str = "",
        last_notice_id: int = 0,
        page_size: int = 20,
        type_list: list[Any] | None = None,
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
            messages = self._filtered_messages(
                state,
                date=date,
                device_sn=device_sn,
                type_list=type_list,
                allowed_station_sns=allowed_station_sns,
            )
            if last_notice_id:
                messages = [
                    msg
                    for msg in messages
                    if int(msg.get("noticeId", 0)) < int(last_notice_id)
                ]
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
                if requested_types and 1 in requested_types and requested_types <= {1}:
                    item["noticeType"] = source_type
                elif requested_types:
                    item["noticeType"] = visible_type
                ext = item.setdefault("extObject", {})
                thumb_fields = self._thumbnail_fields(
                    station_sn=str(item.get("parentDeviceSn", "") or DEFAULT_STATION_SN),
                    camera_sn=str(item.get("deviceSn", "") or DEFAULT_CAMERA_SN),
                    file_date=str(deep_get(ext, "fileDate", "") or ""),
                    file_name=str(deep_get(ext, "fileName", "") or ""),
                    stream_code=self._stream_code(
                        device_sn=str(item.get("deviceSn", "") or DEFAULT_CAMERA_SN),
                        file_date=str(deep_get(ext, "fileDate", "") or ""),
                        file_name=str(deep_get(ext, "fileName", "") or ""),
                    ),
                )
                item.update(thumb_fields)
                for key, value in thumb_fields.items():
                    ext.setdefault(key, value)
                self._export_share_metadata_unlocked(item)
                exported.append(item)
            return {"totalCount": len(messages), "list": exported}

    def remove_messages(self, notice_ids) -> int:
        self = self.store
        with self.lock, self._connect() as conn:
            state = self._load_state_unlocked(conn)
            if isinstance(notice_ids, str):
                raw_ids = [item.strip() for item in notice_ids.split(",") if item.strip()]
            else:
                raw_ids = [
                    str(item).strip() for item in (notice_ids or []) if str(item).strip()
                ]
            id_set = {int(item) for item in raw_ids}
            before = len(state.get("messages", []))
            removed_items: list[dict[str, Any]] = []
            kept_messages: list[dict[str, Any]] = []
            for msg in state.get("messages", []):
                if int(msg.get("noticeId", 0)) in id_set:
                    removed_items.append(json.loads(json.dumps(msg)))
                else:
                    kept_messages.append(msg)
            state["messages"] = kept_messages
            removed = before - len(state["messages"])
            if removed:
                self._remember_removed_messages_unlocked(state, removed_items)
            self._save_state_unlocked(conn, state)
            return removed

    def remove_messages_matching(
        self,
        *,
        notice_ids: Any = None,
        device_sn: str = "",
        file_date: str = "",
        file_names: Any = None,
        stream_codes: Any = None,
        access_token: str = "",
        identifier: str = "",
        user_id: str = "",
    ) -> int:
        self = self.store
        with self.lock, self._connect() as conn:
            state = self._load_state_unlocked(conn)
            allowed_station_sns = self._bound_station_sns_for_user_unlocked(
                conn,
                access_token=access_token,
                identifier=identifier,
                user_id=user_id,
            )
            id_set: set[int] = set()
            for raw in self._coerce_str_list(notice_ids):
                try:
                    id_set.add(int(raw))
                except (TypeError, ValueError):
                    continue
            name_set = set(self._coerce_str_list(file_names))
            exact_keys: set[tuple[str, str, str]] = set()
            for stream_code in self._coerce_str_list(stream_codes):
                stream_device_sn, stream_file_date, stream_file_name = self._parse_stream_code(
                    stream_code
                )
                if stream_file_name and stream_device_sn and stream_file_date:
                    exact_keys.add((stream_device_sn, stream_file_date, stream_file_name))
                elif stream_file_name:
                    name_set.add(stream_file_name)

            requested_device_sn = str(device_sn or "").strip()
            requested_file_date = str(file_date or "").strip()
            removed_items: list[dict[str, Any]] = []

            def should_remove(message: dict[str, Any]) -> bool:
                if not self._message_visible_for_scope_unlocked(
                    state,
                    message,
                    allowed_station_sns=allowed_station_sns,
                ):
                    return False
                message_key = self._message_key(message)
                if id_set and int(message.get("noticeId", 0) or 0) in id_set:
                    return True
                if exact_keys and message_key in exact_keys:
                    return True
                if name_set and message_key[2] in name_set:
                    if requested_device_sn and message_key[0] != requested_device_sn:
                        return False
                    if requested_file_date and message_key[1] != requested_file_date:
                        return False
                    return True
                return False

            before = len(state.get("messages", []))
            kept_messages: list[dict[str, Any]] = []
            for message in state.get("messages", []):
                if should_remove(message):
                    removed_items.append(json.loads(json.dumps(message)))
                else:
                    kept_messages.append(message)
            state["messages"] = kept_messages
            removed = before - len(state["messages"])
            if removed:
                self._remember_removed_messages_unlocked(state, removed_items)
                events = state.setdefault("events", [])
                events.append(
                    {
                        "ts": iso_now(),
                        "message": "messages.remove",
                        "payload": {
                            "removed": removed,
                            "requestedNoticeIds": sorted(id_set),
                            "requestedDeviceSn": requested_device_sn,
                            "requestedFileDate": requested_file_date,
                            "requestedFileNames": sorted(name_set),
                            "removedNoticeIds": [
                                int(item.get("noticeId", 0) or 0) for item in removed_items
                            ],
                        },
                    }
                )
                del events[:-200]
                self._save_state_unlocked(conn, state)
            return removed
