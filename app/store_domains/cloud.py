from __future__ import annotations

import json
import os
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlencode

from ..store_shared import (
    DEFAULT_CAMERA_SN,
    DEFAULT_CHANNEL,
    DEFAULT_STATION_SN,
    DEFAULT_VISIBLE_NOTICE_TYPE,
    deep_get,
    iso_now,
)
from .base import BaseDomainService


class CloudDomainService(BaseDomainService):
    def _coerce_str_list(self, value: Any) -> list[str]:
        self = self.store
        if value is None:
            return []
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return [str(item).strip() for item in (value or []) if str(item).strip()]

    def _normalize_date_digits(self, value: Any) -> str:
        self = self.store
        return "".join(ch for ch in str(value or "").strip() if ch.isdigit())

    def _stream_code(self, *, device_sn: str, file_date: str, file_name: str) -> str:
        self = self.store
        return "|".join(
            [
                str(device_sn or "").strip(),
                str(file_date or "").strip(),
                str(file_name or "").strip(),
            ]
        )

    def _parse_stream_code(self, value: Any) -> tuple[str, str, str]:
        self = self.store
        raw = str(value or "").strip()
        if not raw:
            return ("", "", "")
        parts = raw.split("|", 2)
        if len(parts) == 3:
            return tuple(parts)
        return ("", "", raw)

    def _cloud_capture_datetime_unlocked(
        self,
        *,
        started_at_ms: Any = 0,
        started_at: Any = "",
        device_time: Any = "",
        created_at: Any = "",
    ) -> datetime:
        self = self.store
        try:
            capture_ms = int(started_at_ms or 0)
        except (TypeError, ValueError):
            capture_ms = 0
        if capture_ms > 0:
            try:
                return datetime.fromtimestamp(capture_ms / 1000.0, tz=timezone.utc)
            except (OverflowError, OSError, ValueError):
                pass
        for candidate in (started_at, device_time, created_at):
            parsed = self._parse_state_datetime(candidate)
            if parsed is not None:
                return parsed
        return datetime.now(timezone.utc)

    def _cloud_media_identity_unlocked(
        self,
        *,
        device_sn: Any = "",
        started_at_ms: Any = 0,
        started_at: Any = "",
        device_time: Any = "",
        created_at: Any = "",
        file_date: Any = "",
        file_name: Any = "",
        stream_code: Any = "",
        channel: Any = DEFAULT_CHANNEL,
    ) -> tuple[str, str, str, str]:
        self = self.store
        stream_device_sn, stream_file_date, stream_file_name = self._parse_stream_code(stream_code)
        normalized_device_sn = str(device_sn or stream_device_sn or "").strip()
        normalized_file_date = self._normalize_date_digits(file_date or stream_file_date or "")
        normalized_file_name = str(file_name or stream_file_name or "").strip()
        capture_dt = self._cloud_capture_datetime_unlocked(
            started_at_ms=started_at_ms,
            started_at=started_at,
            device_time=device_time,
            created_at=created_at,
        )
        if not normalized_file_date:
            normalized_file_date = capture_dt.strftime("%Y%m%d")
        if not normalized_file_name:
            try:
                channel_value = int(channel or DEFAULT_CHANNEL)
            except (TypeError, ValueError):
                channel_value = DEFAULT_CHANNEL
            normalized_file_name = (
                f"{capture_dt.strftime('%H%M%S')}_{int(capture_dt.microsecond / 1000):03d}_U_{channel_value}"
            )
        normalized_stream_code = str(stream_code or "").strip()
        if (
            not normalized_stream_code
            and normalized_device_sn
            and normalized_file_date
            and normalized_file_name
        ):
            normalized_stream_code = self._stream_code(
                device_sn=normalized_device_sn,
                file_date=normalized_file_date,
                file_name=normalized_file_name,
            )
        return (
            normalized_device_sn,
            normalized_file_date,
            normalized_file_name,
            normalized_stream_code,
        )

    def _normalize_cloud_media_record_unlocked(self, item: dict[str, Any]) -> bool:
        self = self.store
        changed = False
        device_sn, file_date, file_name, stream_code = self._cloud_media_identity_unlocked(
            device_sn=item.get("deviceSn", ""),
            started_at_ms=item.get("startedAtMs", 0),
            started_at=item.get("startedAt", ""),
            device_time=item.get("deviceTime", ""),
            created_at=item.get("createdAt", ""),
            file_date=item.get("fileDate", ""),
            file_name=item.get("fileName", ""),
            stream_code=item.get("streamCode", ""),
            channel=item.get("channel", DEFAULT_CHANNEL),
        )
        try:
            channel_value = int(item.get("channel", DEFAULT_CHANNEL) or DEFAULT_CHANNEL)
        except (TypeError, ValueError):
            channel_value = DEFAULT_CHANNEL
        try:
            started_at_ms = int(item.get("startedAtMs", 0) or 0)
        except (TypeError, ValueError):
            started_at_ms = 0
        if started_at_ms <= 0:
            started_at_ms = int(
                self._cloud_capture_datetime_unlocked(
                    started_at_ms=item.get("startedAtMs", 0),
                    started_at=item.get("startedAt", ""),
                    device_time=item.get("deviceTime", ""),
                    created_at=item.get("createdAt", ""),
                ).timestamp()
                * 1000
            )
            if int(item.get("startedAtMs", 0) or 0) != started_at_ms:
                item["startedAtMs"] = started_at_ms
                changed = True
        device_time = self._format_notice_device_time(
            str(
                item.get("deviceTime", "")
                or item.get("startedAt", "")
                or item.get("createdAt", "")
                or self._format_state_datetime(
                    self._cloud_capture_datetime_unlocked(
                        started_at_ms=started_at_ms,
                        started_at=item.get("startedAt", ""),
                        device_time=item.get("deviceTime", ""),
                        created_at=item.get("createdAt", ""),
                    )
                )
            )
        )
        defaults = {
            "streamCode": stream_code,
            "deviceSn": device_sn,
            "fileDate": file_date,
            "fileName": file_name,
            "stationSn": str(item.get("stationSn", "") or DEFAULT_STATION_SN),
            "channel": channel_value,
            "noticeId": int(item.get("noticeId", 0) or 0),
            "deviceTime": device_time,
            "createdAt": str(item.get("createdAt", iso_now()) or iso_now()),
            "updatedAt": str(item.get("updatedAt", item.get("createdAt", iso_now())) or iso_now()),
            "startedAt": str(item.get("startedAt", "") or ""),
            "endedAt": str(item.get("endedAt", "") or ""),
            "startedAtMs": started_at_ms,
            "duration": float(item.get("duration", 0) or 0),
            "status": str(item.get("status", "captured") or "captured"),
            "token": str(item.get("token", "") or ""),
            "app": str(item.get("app", "") or ""),
            "tcUrl": str(item.get("tcUrl", "") or ""),
            "publishingName": str(item.get("publishingName", "") or ""),
            "publishingType": str(item.get("publishingType", "") or ""),
            "flvPath": str(item.get("flvPath", "") or ""),
            "mp4Path": str(item.get("mp4Path", "") or ""),
            "thumbnailPath": str(item.get("thumbnailPath", "") or ""),
            "flvFileSize": int(item.get("flvFileSize", 0) or 0),
            "mp4FileSize": int(item.get("mp4FileSize", 0) or 0),
            "thumbnailSize": int(item.get("thumbnailSize", 0) or 0),
            "metadata": item.get("metadata", {}) if isinstance(item.get("metadata", {}), (dict, list)) else {},
        }
        for key, value in defaults.items():
            if item.get(key) != value:
                item[key] = value
                changed = True
        if any(key in item for key in ("sourceNoticeType", "visibleNoticeType", "fileType", "title")):
            default_file_type = self._default_recording_file_type(file_name)
            try:
                source_notice_type = int(item.get("sourceNoticeType", item.get("noticeType", 1)) or 1)
            except (TypeError, ValueError):
                source_notice_type = 1
            try:
                file_type = int(item.get("fileType", default_file_type) or default_file_type)
            except (TypeError, ValueError):
                file_type = default_file_type
            visible_notice_type = self._notice_type_from_ext(
                source_notice_type,
                {"fileType": file_type},
            )
            notice_defaults = {
                "sourceNoticeType": source_notice_type,
                "visibleNoticeType": int(
                    item.get("visibleNoticeType", visible_notice_type) or visible_notice_type
                ),
                "fileType": file_type,
                "title": str(
                    item.get("title", "")
                    or self._notice_title(source_notice_type, {"fileType": file_type})
                ),
            }
            for key, value in notice_defaults.items():
                if item.get(key) != value:
                    item[key] = value
                    changed = True
        return changed

    def _find_cloud_media_unlocked(
        self,
        state: dict[str, Any],
        *,
        stream_code: str = "",
        device_sn: str = "",
        file_date: str = "",
        file_name: str = "",
    ) -> dict[str, Any] | None:
        self = self.store
        stream_device_sn, stream_file_date, stream_file_name = self._parse_stream_code(stream_code)
        device_sn = str(device_sn or stream_device_sn or "").strip()
        file_date = self._normalize_date_digits(file_date or stream_file_date or "")
        file_name = str(file_name or stream_file_name or "").strip()
        for item in state.get("cloudMedia", []):
            if stream_code and str(item.get("streamCode", "") or "") == stream_code:
                return item
        if device_sn and file_date and file_name:
            for item in state.get("cloudMedia", []):
                if (
                    str(item.get("deviceSn", "") or "") == device_sn
                    and self._normalize_date_digits(item.get("fileDate", "") or "") == file_date
                    and str(item.get("fileName", "") or "") == file_name
                ):
                    return item
        return None

    def _find_message_for_cloud_media_unlocked(
        self,
        state: dict[str, Any],
        *,
        notice_id: Any = None,
        device_sn: str = "",
        file_date: str = "",
        file_name: str = "",
    ) -> dict[str, Any] | None:
        self = self.store
        requested_notice_id = 0
        try:
            requested_notice_id = int(notice_id or 0)
        except (TypeError, ValueError):
            requested_notice_id = 0
        if requested_notice_id:
            for message in state.get("messages", []):
                if int(message.get("noticeId", 0) or 0) == requested_notice_id:
                    return message
        if not (device_sn and file_date and file_name):
            return None
        normalized_file_date = self._normalize_date_digits(file_date)
        best_message: dict[str, Any] | None = None
        best_notice_id = -1
        for message in state.get("messages", []):
            message_key = self._message_key(message)
            if (
                message_key[0] != device_sn
                or self._normalize_date_digits(message_key[1]) != normalized_file_date
                or message_key[2] != file_name
            ):
                continue
            current_notice_id = int(message.get("noticeId", 0) or 0)
            if current_notice_id >= best_notice_id:
                best_notice_id = current_notice_id
                best_message = message
        return best_message

    def _default_recording_file_type(self, file_name: Any) -> int:
        self = self.store
        return 5 if "_U_" in str(file_name or "") else DEFAULT_VISIBLE_NOTICE_TYPE

    def _notice_add_payload_for_recording_unlocked(
        self,
        conn: sqlite3.Connection,
        *,
        device_sn: str,
        file_date: str,
        file_name: str,
    ) -> tuple[int, dict[str, Any]] | None:
        self = self.store
        device_sn = str(device_sn or "").strip()
        file_date = self._normalize_date_digits(file_date or "")
        file_name = str(file_name or "").strip()
        if not (device_sn and file_date and file_name):
            return None
        rows = conn.execute(
            """
            SELECT body_json
            FROM request_logs
            WHERE path = '/ipc/msg/notice/add'
              AND body_json LIKE ?
              AND body_json LIKE ?
              AND body_json LIKE ?
            ORDER BY id DESC
            LIMIT 10
            """,
            (
                f"%{device_sn}%",
                f"%{file_date}%",
                f"%{file_name}%",
            ),
        ).fetchall()
        for row in rows:
            try:
                body = json.loads(row["body_json"] or "{}")
            except json.JSONDecodeError:
                continue
            ext = deep_get(body, "extJson", {}) or {}
            if not isinstance(ext, dict):
                continue
            if str(body.get("deviceSn", "") or "").strip() != device_sn:
                continue
            if self._normalize_date_digits(deep_get(ext, "fileDate", "") or "") != file_date:
                continue
            if str(deep_get(ext, "fileName", "") or "").strip() != file_name:
                continue
            try:
                notice_type = int(deep_get(body, "noticeType", 1) or 1)
            except (TypeError, ValueError):
                notice_type = 1
            return notice_type, dict(ext)
        return None

    def _recording_notice_fields_unlocked(
        self,
        conn: sqlite3.Connection,
        *,
        device_sn: str,
        file_date: str,
        file_name: str,
        message: dict[str, Any] | None = None,
        cloud_media: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self = self.store
        default_file_type = self._default_recording_file_type(file_name)
        if message:
            ext = message.get("extObject", {}) or {}
            try:
                source_notice_type = int(
                    message.get("sourceNoticeType", message.get("noticeType", 1)) or 1
                )
            except (TypeError, ValueError):
                source_notice_type = 1
            try:
                file_type = int(deep_get(ext, "fileType", default_file_type) or default_file_type)
            except (TypeError, ValueError):
                file_type = default_file_type
            try:
                visible_notice_type = int(
                    message.get(
                        "visibleNoticeType",
                        self._notice_type_from_ext(source_notice_type, ext),
                    )
                    or 0
                )
            except (TypeError, ValueError):
                visible_notice_type = self._notice_type_from_ext(source_notice_type, ext)
            return {
                "sourceNoticeType": source_notice_type,
                "visibleNoticeType": visible_notice_type,
                "fileType": file_type,
                "title": str(message.get("title", "") or self._notice_title(source_notice_type, ext)),
            }

        logged_notice = self._notice_add_payload_for_recording_unlocked(
            conn,
            device_sn=device_sn,
            file_date=file_date,
            file_name=file_name,
        )
        if logged_notice:
            source_notice_type, ext = logged_notice
            try:
                file_type = int(deep_get(ext, "fileType", default_file_type) or default_file_type)
            except (TypeError, ValueError):
                file_type = default_file_type
            visible_notice_type = self._notice_type_from_ext(source_notice_type, ext)
            return {
                "sourceNoticeType": source_notice_type,
                "visibleNoticeType": visible_notice_type,
                "fileType": file_type,
                "title": self._notice_title(source_notice_type, ext),
            }

        cloud_media = cloud_media if isinstance(cloud_media, dict) else {}
        if any(
            cloud_media.get(key) not in (None, "")
            for key in ("sourceNoticeType", "visibleNoticeType", "fileType")
        ):
            try:
                source_notice_type = int(
                    cloud_media.get("sourceNoticeType", cloud_media.get("noticeType", 1)) or 1
                )
            except (TypeError, ValueError):
                source_notice_type = 1
            try:
                file_type = int(cloud_media.get("fileType", default_file_type) or default_file_type)
            except (TypeError, ValueError):
                file_type = default_file_type
            ext = {"fileType": file_type}
            try:
                visible_notice_type = int(
                    cloud_media.get(
                        "visibleNoticeType",
                        self._notice_type_from_ext(source_notice_type, ext),
                    )
                    or 0
                )
            except (TypeError, ValueError):
                visible_notice_type = self._notice_type_from_ext(source_notice_type, ext)
            return {
                "sourceNoticeType": source_notice_type,
                "visibleNoticeType": visible_notice_type,
                "fileType": file_type,
                "title": str(
                    cloud_media.get("title", "") or self._notice_title(source_notice_type, ext)
                ),
            }

        ext = {"fileType": default_file_type}
        return {
            "sourceNoticeType": 1,
            "visibleNoticeType": self._notice_type_from_ext(1, ext),
            "fileType": default_file_type,
            "title": self._notice_title(1, ext),
        }

    def _message_device_time(self, message: dict[str, Any]) -> str:
        self = self.store
        ext = message.get("extObject", {}) or {}
        return self._format_notice_device_time(
            str(
                message.get("deviceTime")
                or deep_get(ext, "deviceTime", "")
                or ""
            )
        )

    def _public_base_url(self) -> str:
        self = self.store
        return str(self.settings.public_base_url or "https://mi-api-pro.sunvalleycloud.com").rstrip("/")

    def _cloud_cover_url(self, *, plan_id: str = "") -> str:
        self = self.store
        params = {}
        if str(plan_id or "").strip():
            params["planId"] = str(plan_id).strip()
        query = f"?{urlencode(params)}" if params else ""
        return f"{self._public_base_url()}/debug/cloud-cover{query}"

    def _thumbnail_url(
        self,
        *,
        station_sn: str,
        camera_sn: str,
        file_date: str = "",
        file_name: str = "",
        stream_code: str = "",
        version: str = "",
    ) -> str:
        self = self.store
        params = {
            "stationSn": str(station_sn or DEFAULT_STATION_SN),
            "cameraSn": str(camera_sn or DEFAULT_CAMERA_SN),
            "fileDate": str(file_date or ""),
            "fileName": str(file_name or ""),
        }
        if str(stream_code or "").strip():
            params["streamCode"] = str(stream_code).strip()
        if str(version or "").strip():
            params["v"] = str(version).strip()
        query = "&".join(f"{key}={quote(value, safe='')}" for key, value in params.items())
        return f"{self._public_base_url()}/debug/thumbnail.png?{query}"

    def _thumbnail_fields(
        self,
        *,
        station_sn: str,
        camera_sn: str,
        file_date: str = "",
        file_name: str = "",
        stream_code: str = "",
        version: str = "",
    ) -> dict[str, str]:
        self = self.store
        thumb_url = self._thumbnail_url(
            station_sn=station_sn,
            camera_sn=camera_sn,
            file_date=file_date,
            file_name=file_name,
            stream_code=stream_code,
            version=version,
        )
        return {
            "coverImagePath": thumb_url,
            "coverImageSignedUrl": thumb_url,
            "coverImageUrl": thumb_url,
            "coverUrl": thumb_url,
            "imageUrl": thumb_url,
            "imgUrl": thumb_url,
            "snapshotUrl": thumb_url,
            "thumbPath": thumb_url,
            "thumbUrl": thumb_url,
            "thumbnailPath": thumb_url,
            "thumbnailUrl": thumb_url,
        }

    def _parse_state_datetime(self, value: Any) -> datetime | None:
        self = self.store
        raw = str(value or "").strip()
        if not raw:
            return None
        try:
            parsed = datetime.fromisoformat(raw)
        except ValueError:
            for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
                try:
                    parsed = datetime.strptime(raw, fmt)
                    break
                except ValueError:
                    continue
            else:
                return None
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

    def _format_state_datetime(self, value: datetime) -> str:
        self = self.store
        return value.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

    def _storage_plan_id(self, camera_count: int, storage_period: int) -> str:
        self = self.store
        return f"local-storage-plan-{camera_count}cam-{storage_period}day"

    def _storage_plan_catalog_unlocked(
        self,
        state: dict[str, Any],
    ) -> list[dict[str, Any]]:
        self = self.store
        active_service = state.get("storageService", {}) if isinstance(state.get("storageService"), dict) else {}
        active_plan_id = str(active_service.get("planId") or active_service.get("serviceId") or "").strip()
        active_camera_count = max(len(state.get("cameras", {})), 1)
        plans: list[dict[str, Any]] = []
        for camera_count in range(1, 5):
            for storage_period in (7, 30):
                price_value = round((camera_count * storage_period) / 10, 2)
                plan_id = self._storage_plan_id(camera_count, storage_period)
                recommended = int(
                    plan_id == active_plan_id
                    or (not active_plan_id and camera_count == active_camera_count and storage_period == 30)
                )
                plans.append(
                    {
                        "appId": "local.vava.fastapi",
                        "autoRenew": 0,
                        "cameraCount": camera_count,
                        "description": (
                            f"Local test cloud plan: {camera_count} camera"
                            f"{'' if camera_count == 1 else 's'}, {storage_period}-day history."
                        ),
                        "effectiveMonths": 1,
                        "id": plan_id,
                        "isFree": 1,
                        "isLimitedTime": 0,
                        "isNew": recommended,
                        "isOff": 0,
                        "label": recommended,
                        "off": 0,
                        "planId": plan_id,
                        "price": f"{price_value:.2f}",
                        "productionIdentifier": f"local.test.storage.{camera_count}cam.{storage_period}day",
                        "serviceId": plan_id,
                        "sku": f"LOCAL-{camera_count}CAM-{storage_period}DAY",
                        "stockNum": 9999,
                        "storagePeriod": storage_period,
                        "storageServiceName": f"Local Test Cloud {camera_count} Cam / {storage_period} Day",
                        "storageServiceStatus": 1,
                        "storageServiceType": 1,
                        "storageServiceUrl": self._cloud_cover_url(plan_id=plan_id),
                        "unitFree": 1,
                        "unitPrice": int(round(price_value * 100)),
                    }
                )
        return plans

    def _paginate_items(
        self,
        items: list[dict[str, Any]],
        *,
        current_page: int = 1,
        page_size: int = 20,
    ) -> list[dict[str, Any]]:
        self = self.store
        current_page = max(int(current_page or 1), 1)
        page_size = max(int(page_size or 20), 1)
        start = (current_page - 1) * page_size
        return items[start : start + page_size]

    def _resolve_storage_plan_unlocked(
        self,
        state: dict[str, Any],
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self = self.store
        payload = payload if isinstance(payload, dict) else {}
        plans = self._storage_plan_catalog_unlocked(state)
        if not plans:
            return {}

        plan_map: dict[str, dict[str, Any]] = {}
        for plan in plans:
            for key in (
                plan.get("id"),
                plan.get("planId"),
                plan.get("serviceId"),
                plan.get("productionIdentifier"),
                plan.get("sku"),
            ):
                value = str(key or "").strip()
                if value:
                    plan_map[value] = plan

        current_service = state.get("storageService", {}) if isinstance(state.get("storageService"), dict) else {}
        default_camera_count = int(
            current_service.get("cameraCount", len(state.get("cameras", {})) or 1) or 1
        )
        default_camera_count = min(max(default_camera_count, 1), 4)
        default_period = int(current_service.get("storagePeriod", 30) or 30)
        if default_period not in (7, 30):
            default_period = 30

        legacy_aliases = {
            "10001": self._storage_plan_id(default_camera_count, default_period),
            "local-storage-plan-monthly": self._storage_plan_id(default_camera_count, 30),
            "local-storage-plan-weekly": self._storage_plan_id(default_camera_count, 7),
        }
        requested_ids = [
            str(
                payload.get("serviceId")
                or payload.get("planId")
                or payload.get("id")
                or payload.get("productionIdentifier")
                or payload.get("sku")
                or ""
            ).strip()
        ]
        for candidate in requested_ids:
            if not candidate:
                continue
            if candidate in plan_map:
                return dict(plan_map[candidate])
            alias = legacy_aliases.get(candidate)
            if alias and alias in plan_map:
                return dict(plan_map[alias])

        try:
            camera_count = int(payload.get("cameraCount", default_camera_count) or default_camera_count)
        except (TypeError, ValueError):
            camera_count = default_camera_count
        camera_count = min(max(camera_count, 1), 4)

        try:
            storage_period = int(payload.get("storagePeriod", default_period) or default_period)
        except (TypeError, ValueError):
            storage_period = default_period
        if storage_period not in (7, 30):
            storage_period = default_period

        target_plan_id = self._storage_plan_id(camera_count, storage_period)
        return dict(plan_map.get(target_plan_id, plans[0]))

    def _default_storage_service_unlocked(self, state: dict[str, Any]) -> dict[str, Any]:
        self = self.store
        plan = self._resolve_storage_plan_unlocked(
            state,
            {
                "cameraCount": max(len(state.get("cameras", {})), 1),
                "storagePeriod": 30,
            },
        )
        expire_at = datetime.now(timezone.utc) + timedelta(days=30)
        return {
            "autoRenew": 0,
            "cameraCount": int(plan.get("cameraCount", 1) or 1),
            "effectiveMonths": int(plan.get("effectiveMonths", 1) or 1),
            "expireTime": self._format_state_datetime(expire_at),
            "isAlert": False,
            "lastOrderNo": "",
            "lastPaymentId": "",
            "planId": str(plan.get("id", "") or ""),
            "productionIdentifier": str(plan.get("productionIdentifier", "") or ""),
            "serviceId": 10001,
            "serviceStatus": 1,
            "sku": str(plan.get("sku", "") or ""),
            "storagePeriod": int(plan.get("storagePeriod", 30) or 30),
            "storageServiceName": str(plan.get("storageServiceName", "Local Test Cloud") or "Local Test Cloud"),
            "storageServiceType": int(plan.get("storageServiceType", 1) or 1),
            "storageServiceUrl": str(
                plan.get(
                    "storageServiceUrl",
                    self._cloud_cover_url(plan_id=str(plan.get("id", "") or "")),
                )
                or self._cloud_cover_url(plan_id=str(plan.get("id", "") or ""))
            ),
            "trial": 0,
        }

    def _normalize_storage_state_unlocked(self, state: dict[str, Any]) -> bool:
        self = self.store
        changed = False
        service = state.get("storageService")
        if not isinstance(service, dict) or not service:
            state["storageService"] = self._default_storage_service_unlocked(state)
            service = state["storageService"]
            changed = True

        plan = self._resolve_storage_plan_unlocked(
            state,
            {
                "serviceId": service.get("planId") or service.get("serviceId"),
                "cameraCount": service.get("cameraCount"),
                "storagePeriod": service.get("storagePeriod"),
                "productionIdentifier": service.get("productionIdentifier"),
                "sku": service.get("sku"),
            },
        )
        default_service = self._default_storage_service_unlocked(state)
        expire_at = self._parse_state_datetime(service.get("expireTime"))
        if expire_at is None:
            expire_at = self._parse_state_datetime(default_service.get("expireTime")) or (
                datetime.now(timezone.utc) + timedelta(days=30)
            )
        desired_service = {
            "autoRenew": int(service.get("autoRenew", default_service["autoRenew"]) or 0),
            "cameraCount": int(plan.get("cameraCount", service.get("cameraCount", default_service["cameraCount"])) or default_service["cameraCount"]),
            "effectiveMonths": int(plan.get("effectiveMonths", service.get("effectiveMonths", default_service["effectiveMonths"])) or default_service["effectiveMonths"]),
            "expireTime": self._format_state_datetime(expire_at),
            "isAlert": bool(service.get("isAlert", default_service["isAlert"])),
            "lastOrderNo": str(service.get("lastOrderNo", "") or ""),
            "lastPaymentId": str(service.get("lastPaymentId", "") or ""),
            "planId": str(plan.get("id", default_service["planId"]) or default_service["planId"]),
            "productionIdentifier": str(plan.get("productionIdentifier", default_service["productionIdentifier"]) or default_service["productionIdentifier"]),
            "serviceId": int(service.get("serviceId", default_service["serviceId"]) or default_service["serviceId"]),
            "serviceStatus": int(service.get("serviceStatus", default_service["serviceStatus"]) or default_service["serviceStatus"]),
            "sku": str(plan.get("sku", default_service["sku"]) or default_service["sku"]),
            "storagePeriod": int(plan.get("storagePeriod", service.get("storagePeriod", default_service["storagePeriod"])) or default_service["storagePeriod"]),
            "storageServiceName": str(plan.get("storageServiceName", default_service["storageServiceName"]) or default_service["storageServiceName"]),
            "storageServiceType": int(plan.get("storageServiceType", default_service["storageServiceType"]) or default_service["storageServiceType"]),
            "storageServiceUrl": str(plan.get("storageServiceUrl", default_service["storageServiceUrl"]) or default_service["storageServiceUrl"]),
            "trial": int(service.get("trial", default_service["trial"]) or default_service["trial"]),
        }
        if state.get("storageService") != desired_service:
            state["storageService"] = desired_service
            changed = True

        normalized_orders: list[dict[str, Any]] = []
        for order in state.get("storageOrders", []):
            if not isinstance(order, dict):
                changed = True
                continue
            order_plan = self._resolve_storage_plan_unlocked(
                state,
                {
                    "serviceId": order.get("planId") or order.get("serviceId"),
                    "cameraCount": order.get("cameraCount"),
                    "storagePeriod": order.get("storagePeriod"),
                    "productionIdentifier": order.get("productionIdentifier"),
                    "sku": order.get("sku"),
                },
            )
            created_at = str(order.get("createdAt", iso_now()) or iso_now())
            updated_at = str(order.get("updatedAt", created_at) or created_at)
            normalized_orders.append(
                {
                    "autoRenew": int(order.get("autoRenew", 0) or 0),
                    "cameraCount": int(order_plan.get("cameraCount", 1) or 1),
                    "createdAt": created_at,
                    "effectiveMonths": int(order_plan.get("effectiveMonths", 1) or 1),
                    "expireTime": str(order.get("expireTime", "") or ""),
                    "orderNo": str(order.get("orderNo", "") or ""),
                    "orderStatus": int(order.get("orderStatus", 0) or 0),
                    "orderType": str(order.get("orderType", "purchase") or "purchase"),
                    "paidAt": str(order.get("paidAt", "") or ""),
                    "paymentId": str(order.get("paymentId", "") or ""),
                    "paymentStatus": int(order.get("paymentStatus", 0) or 0),
                    "planId": str(order_plan.get("id", "") or ""),
                    "price": str(order_plan.get("price", "0.00") or "0.00"),
                    "productionIdentifier": str(order_plan.get("productionIdentifier", "") or ""),
                    "serviceId": str(order_plan.get("serviceId", "") or ""),
                    "serviceStatus": int(order.get("serviceStatus", 0) or 0),
                    "sku": str(order_plan.get("sku", "") or ""),
                    "source": int(order.get("source", 0) or 0),
                    "storagePeriod": int(order_plan.get("storagePeriod", 30) or 30),
                    "storageServiceId": int(order.get("storageServiceId", 10001) or 10001),
                    "storageServiceName": str(order_plan.get("storageServiceName", "Local Test Cloud") or "Local Test Cloud"),
                    "updatedAt": updated_at,
                }
            )
        normalized_orders.sort(
            key=lambda item: (item.get("createdAt", ""), item.get("orderNo", "")),
            reverse=True,
        )
        if state.get("storageOrders") != normalized_orders:
            state["storageOrders"] = normalized_orders[:100]
            changed = True
        return changed

    def _storage_days_remaining(self, expire_time: Any) -> int:
        self = self.store
        expire_at = self._parse_state_datetime(expire_time)
        if not expire_at:
            return 0
        today = datetime.now(timezone.utc).date()
        return max((expire_at.date() - today).days, 0)

    def _storage_success_url(self, order_no: str, payment_id: str) -> str:
        self = self.store
        return (
            f"{self._public_base_url()}/h5/payment-result?"
            + urlencode({"orderNo": order_no, "paymentId": payment_id})
        )

    def _datetime_to_epoch_ms_str(self, value: Any) -> str:
        self = self.store
        dt = self._parse_state_datetime(value)
        if not dt:
            return ""
        return str(int(dt.timestamp() * 1000))

    def _existing_path(self, value: Any) -> str:
        self = self.store
        candidate = Path(str(value or "").strip())
        if candidate.is_file():
            return str(candidate)
        return ""

    def _cloud_media_root_unlocked(self) -> Path:
        self = self.store
        return self.settings.data_dir / "cloud_media"

    def _cloud_media_owned_paths_unlocked(self, item: dict[str, Any]) -> list[Path]:
        self = self.store
        roots = [self._cloud_media_root_unlocked().resolve(strict=False)]
        candidates: list[Path] = []
        seen: set[str] = set()

        def add_candidate(path_value: Any) -> None:
            raw = str(path_value or "").strip()
            if not raw:
                return
            resolved = Path(raw).resolve(strict=False)
            try:
                allowed = any(resolved.is_relative_to(root) for root in roots)
            except AttributeError:
                allowed = any(
                    str(resolved).startswith(f"{root}{os.sep}") or resolved == root
                    for root in roots
                )
            if not allowed:
                return
            key = str(resolved)
            if key in seen:
                return
            seen.add(key)
            candidates.append(resolved)

        for field in ("flvPath", "mp4Path", "thumbnailPath"):
            add_candidate(item.get(field, ""))

        flv_path = str(item.get("flvPath", "") or "").strip()
        if flv_path:
            clean_path = self._cloud_media_root_unlocked() / "decode" / "movies" / f"{Path(flv_path).stem}.clean.flv"
            add_candidate(clean_path)
        return candidates

    def _remove_cloud_media_files_unlocked(self, item: dict[str, Any]) -> list[str]:
        self = self.store
        removed_paths: list[str] = []
        for candidate in self._cloud_media_owned_paths_unlocked(item):
            if not candidate.is_file():
                continue
            try:
                candidate.unlink()
            except OSError:
                continue
            removed_paths.append(str(candidate))
        return removed_paths

    def _cloud_media_decode_path_unlocked(
        self,
        *,
        flv_path: str = "",
        current_path: str = "",
        subdir: str,
        suffix: str,
    ) -> str:
        self = self.store
        stem_source = self._existing_path(flv_path) or self._existing_path(current_path)
        if not stem_source:
            return ""
        candidate = self.settings.data_dir / "cloud_media" / "decode" / subdir / f"{Path(stem_source).stem}{suffix}"
        if candidate.is_file():
            return str(candidate)
        return ""

    def _infer_cloud_media_asset_paths_unlocked(self, item: dict[str, Any]) -> bool:
        self = self.store
        changed = False
        flv_path = self._existing_path(item.get("flvPath", "") or "")
        mp4_path = self._cloud_media_decode_path_unlocked(
            flv_path=flv_path,
            current_path=str(item.get("mp4Path", "") or ""),
            subdir="movies",
            suffix=".mp4",
        )
        if not mp4_path:
            mp4_path = self._existing_path(item.get("mp4Path", "") or "")
        if not mp4_path and flv_path:
            sibling_mp4 = Path(flv_path).with_suffix(".mp4")
            if sibling_mp4.is_file():
                mp4_path = str(sibling_mp4)
        if mp4_path:
            mp4_size = Path(mp4_path).stat().st_size
            if str(item.get("mp4Path", "") or "") != mp4_path:
                item["mp4Path"] = mp4_path
                changed = True
            if int(item.get("mp4FileSize", 0) or 0) != mp4_size:
                item["mp4FileSize"] = mp4_size
                changed = True

        thumb_path = self._cloud_media_decode_path_unlocked(
            flv_path=flv_path,
            current_path=str(item.get("thumbnailPath", "") or ""),
            subdir="imgs",
            suffix=".png",
        )
        if not thumb_path:
            thumb_path = self._existing_path(item.get("thumbnailPath", "") or "")
        if not thumb_path:
            for source_path in (mp4_path, item.get("flvPath", "") or ""):
                candidate_path = self._existing_path(source_path)
                if not candidate_path:
                    continue
                sibling_png = Path(candidate_path).with_suffix(".png")
                if sibling_png.is_file():
                    thumb_path = str(sibling_png)
                    break
        if thumb_path:
            thumb_size = Path(thumb_path).stat().st_size
            if str(item.get("thumbnailPath", "") or "") != thumb_path:
                item["thumbnailPath"] = thumb_path
                changed = True
            if int(item.get("thumbnailSize", 0) or 0) != thumb_size:
                item["thumbnailSize"] = thumb_size
                changed = True
        return changed

    def _cloud_media_version_token_unlocked(self, item: dict[str, Any]) -> str:
        self = self.store
        for key in ("thumbnailPath", "mp4Path"):
            asset_path = self._existing_path(item.get(key, "") or "")
            if not asset_path:
                continue
            stat = Path(asset_path).stat()
            return f"{int(stat.st_mtime_ns)}-{stat.st_size}"
        for key in ("thumbnailSize", "mp4FileSize", "noticeId"):
            value = str(item.get(key, "") or "").strip()
            if value:
                return value
        return ""

    def _allowed_camera_sns_for_scope_unlocked(
        self,
        conn: sqlite3.Connection,
        state: dict[str, Any],
        *,
        access_token: str = "",
        identifier: str = "",
        user_id: str = "",
    ) -> set[str] | None:
        self = self.store
        allowed_station_sns = self._bound_station_sns_for_user_unlocked(
            conn,
            access_token=access_token,
            identifier=identifier,
            user_id=user_id,
        )
        if allowed_station_sns is None:
            return None
        cameras = state.get("cameras", {}) if isinstance(state.get("cameras"), dict) else {}
        return {
            str(camera_sn or "").strip()
            for camera_sn, camera in cameras.items()
            if isinstance(camera, dict)
            and str(camera.get("stationSn", "") or "").strip() in allowed_station_sns
        }

    def _scoped_camera_items_unlocked(
        self,
        state: dict[str, Any],
        *,
        allowed_camera_sns: set[str] | None = None,
    ) -> list[tuple[str, dict[str, Any]]]:
        self = self.store
        cameras = state.get("cameras", {}) if isinstance(state.get("cameras"), dict) else {}
        items = [
            (str(camera_sn or "").strip(), dict(camera))
            for camera_sn, camera in sorted(cameras.items())
            if isinstance(camera, dict)
            if str(camera_sn or "").strip()
        ]
        if allowed_camera_sns is None:
            return items
        return [item for item in items if item[0] in allowed_camera_sns]

    def _effective_cloud_bound_camera_sns_unlocked(
        self,
        state: dict[str, Any],
        *,
        allowed_camera_sns: set[str] | None = None,
        camera_capacity: int = 0,
        enforce_capacity: bool = True,
    ) -> list[str]:
        self = self.store
        bound_sns = [
            camera_sn
            for camera_sn, camera in self._scoped_camera_items_unlocked(
                state,
                allowed_camera_sns=allowed_camera_sns,
            )
            if int(camera.get("cloudStorageBound", 1) or 0) == 1
        ]
        if not enforce_capacity:
            return bound_sns
        capacity = max(int(camera_capacity or 0), 0)
        if capacity <= 0:
            return []
        return bound_sns[:capacity]

    def _cloud_storage_camera_info_payload_unlocked(
        self,
        state: dict[str, Any],
        *,
        allowed_camera_sns: set[str] | None = None,
        enforce_capacity: bool = True,
    ) -> dict[str, Any]:
        self = self.store
        service = state.get("storageService", {}) if isinstance(state.get("storageService"), dict) else {}
        configured_capacity = max(int(service.get("cameraCount", 0) or 0), 1)
        effective_bound_sns = self._effective_cloud_bound_camera_sns_unlocked(
            state,
            allowed_camera_sns=allowed_camera_sns,
            camera_capacity=configured_capacity,
            enforce_capacity=enforce_capacity and allowed_camera_sns is not None,
        )
        scoped_cameras = dict(
            self._scoped_camera_items_unlocked(
                state,
                allowed_camera_sns=allowed_camera_sns,
            )
        )
        device_list = [
            {
                "deviceName": scoped_cameras.get(camera_sn, {}).get("cameraName", camera_sn),
                "deviceSn": camera_sn,
                "select": 0,
            }
            for camera_sn in effective_bound_sns
        ]
        camera_capacity = configured_capacity
        if allowed_camera_sns is None:
            camera_capacity = max(camera_capacity, len(device_list), 1)
        expire_time = str(service.get("expireTime", "") or "")
        plan_id = str(service.get("planId", "") or "")
        storage_service_url = str(
            service.get("storageServiceUrl", self._cloud_cover_url(plan_id=plan_id))
            or self._cloud_cover_url(plan_id=plan_id)
        )
        return {
            "availableCount": max(camera_capacity - len(device_list), 0),
            "cameraCount": camera_capacity,
            "coverImageSignedUrl": storage_service_url,
            "coverImageUrl": storage_service_url,
            "daysRemaining": self._storage_days_remaining(expire_time),
            "deviceVoList": device_list,
            "effectiveMonths": int(service.get("effectiveMonths", 1) or 1),
            "expireTime": expire_time,
            "isAlert": bool(service.get("isAlert", False)),
            "planId": str(service.get("planId", "") or ""),
            "productionIdentifier": str(service.get("productionIdentifier", "") or ""),
            "serviceId": int(service.get("serviceId", 10001) or 10001),
            "serviceStatus": int(service.get("serviceStatus", 1) or 1),
            "storagePeriod": int(service.get("storagePeriod", 7) or 7),
            "storageServiceName": str(service.get("storageServiceName", "") or ""),
            "storageServiceType": int(service.get("storageServiceType", 1) or 1),
            "storageServiceUrl": storage_service_url,
            "trial": int(service.get("trial", 0) or 0),
        }

    def cloud_media_asset_paths(
        self,
        *,
        stream_code: str = "",
        device_sn: str = "",
        file_date: str = "",
        file_name: str = "",
    ) -> dict[str, Any]:
        self = self.store
        with self.lock, self._connect() as conn:
            state = self._load_state_unlocked(conn)
            item = self._find_cloud_media_unlocked(
                state,
                stream_code=stream_code,
                device_sn=device_sn,
                file_date=file_date,
                file_name=file_name,
            )
            if not item:
                return {}
            changed = self._infer_cloud_media_asset_paths_unlocked(item)
            if changed:
                self._save_state_unlocked(conn, state)
            return json.loads(json.dumps(item))

    def register_cloud_media_capture(
        self,
        *,
        device_sn: str,
        started_at_ms: int,
        started_at: str = "",
        ended_at: str = "",
        duration: float = 0,
        token: str = "",
        app: str = "",
        tc_url: str = "",
        publishing_name: str = "",
        publishing_type: str = "",
        flv_path: str = "",
        mp4_path: str = "",
        thumbnail_path: str = "",
        metadata: Any = None,
    ) -> dict[str, Any]:
        self = self.store
        started_at_ms = int(started_at_ms or 0)
        with self.lock, self._connect() as conn:
            state = self._load_state_unlocked(conn)
            cameras = state.get("cameras", {}) if isinstance(state.get("cameras"), dict) else {}
            camera_state = cameras.get(str(device_sn or "").strip(), {}) if isinstance(cameras.get(str(device_sn or "").strip()), dict) else {}
            station_sn = str(camera_state.get("stationSn", "") or DEFAULT_STATION_SN)
            try:
                channel = int(camera_state.get("channel", DEFAULT_CHANNEL) or DEFAULT_CHANNEL)
            except (TypeError, ValueError):
                channel = DEFAULT_CHANNEL
            best_message: dict[str, Any] | None = None
            candidates: list[tuple[int, int, dict[str, Any]]] = []
            for message in state.get("messages", []):
                if str(message.get("deviceSn", "") or "") != str(device_sn or ""):
                    continue
                ext = message.get("extObject", {}) or {}
                msg_file_date = str(deep_get(ext, "fileDate", "") or "")
                msg_file_name = str(deep_get(ext, "fileName", "") or "")
                if not msg_file_date or not msg_file_name:
                    continue
                delta = abs(int(message.get("addTimestamp", 0) or 0) - started_at_ms)
                if delta > 10 * 60 * 1000:
                    continue
                candidates.append((delta, -int(message.get("noticeId", 0) or 0), message))
            if candidates:
                candidates.sort(key=lambda item: (item[0], item[1]))
                best_message = candidates[0][2]

            ext = best_message.get("extObject", {}) if best_message else {}
            device_sn, file_date, file_name, stream_code = self._cloud_media_identity_unlocked(
                device_sn=str(device_sn or ""),
                started_at_ms=started_at_ms,
                started_at=str(started_at or ""),
                device_time=str(deep_get(ext, "deviceTime", "") or ""),
                created_at=iso_now(),
                file_date=str(deep_get(ext, "fileDate", "") or ""),
                file_name=str(deep_get(ext, "fileName", "") or ""),
                stream_code="",
                channel=channel,
            )
            now = iso_now()
            flv_path = str(flv_path or "")
            mp4_path = str(mp4_path or "")
            thumbnail_path = str(thumbnail_path or "")
            flv_size = Path(flv_path).stat().st_size if flv_path and Path(flv_path).is_file() else 0
            mp4_size = Path(mp4_path).stat().st_size if mp4_path and Path(mp4_path).is_file() else 0
            thumbnail_size = (
                Path(thumbnail_path).stat().st_size
                if thumbnail_path and Path(thumbnail_path).is_file()
                else 0
            )
            cloud_media = state.setdefault("cloudMedia", [])
            existing_index = None
            if stream_code:
                for index, item in enumerate(cloud_media):
                    if str(item.get("streamCode", "") or "") == stream_code:
                        existing_index = index
                        break
            if existing_index is None and flv_path:
                for index, item in enumerate(cloud_media):
                    if str(item.get("flvPath", "") or "") == flv_path:
                        existing_index = index
                        break
            if existing_index is None and mp4_path:
                for index, item in enumerate(cloud_media):
                    if str(item.get("mp4Path", "") or "") == mp4_path:
                        existing_index = index
                        break
            if existing_index is None and device_sn and started_at_ms:
                for index, item in enumerate(cloud_media):
                    if (
                        str(item.get("deviceSn", "") or "") == device_sn
                        and int(item.get("startedAtMs", 0) or 0) == started_at_ms
                    ):
                        existing_index = index
                        break
            existing = cloud_media[existing_index] if existing_index is not None else {}
            notice_fields = self._recording_notice_fields_unlocked(
                conn,
                device_sn=device_sn,
                file_date=file_date,
                file_name=file_name,
                message=best_message,
                cloud_media=existing,
            )
            record = {
                "streamCode": stream_code,
                "deviceSn": str(device_sn or ""),
                "fileDate": file_date,
                "fileName": file_name,
                "stationSn": station_sn,
                "channel": channel,
                "noticeId": int((best_message or existing or {}).get("noticeId", 0) or 0),
                "sourceNoticeType": int(notice_fields.get("sourceNoticeType", 1) or 1),
                "visibleNoticeType": int(notice_fields.get("visibleNoticeType", 0) or 0),
                "fileType": int(notice_fields.get("fileType", self._default_recording_file_type(file_name)) or self._default_recording_file_type(file_name)),
                "title": str(notice_fields.get("title", "") or ""),
                "deviceTime": str(
                    deep_get(ext, "deviceTime", "")
                    or existing.get("deviceTime", "")
                    or started_at
                    or now
                ),
                "createdAt": str(existing.get("createdAt", now) or now),
                "updatedAt": now,
                "startedAt": str(started_at or existing.get("startedAt", "") or ""),
                "endedAt": str(ended_at or existing.get("endedAt", "") or ""),
                "startedAtMs": started_at_ms,
                "duration": float(duration or deep_get(ext, "duration", 0) or 0),
                "status": "ready" if mp4_size else "captured",
                "token": str(token or ""),
                "app": str(app or ""),
                "tcUrl": str(tc_url or ""),
                "publishingName": str(publishing_name or ""),
                "publishingType": str(publishing_type or ""),
                "flvPath": flv_path,
                "mp4Path": mp4_path,
                "thumbnailPath": thumbnail_path,
                "flvFileSize": flv_size,
                "mp4FileSize": mp4_size,
                "thumbnailSize": thumbnail_size,
                "metadata": metadata if isinstance(metadata, (dict, list)) else {},
            }
            self._normalize_cloud_media_record_unlocked(record)
            if existing_index is not None:
                cloud_media[existing_index] = record
            else:
                cloud_media.insert(0, record)
            del cloud_media[200:]
            events = state.setdefault("events", [])
            events.append({"ts": now, "message": "cloud.media.capture", "payload": record})
            del events[:-200]
            self._save_state_unlocked(conn, state)
            return json.loads(json.dumps(record))

    def storage_camera_management_payload(
        self,
        *,
        access_token: str = "",
        identifier: str = "",
        user_id: str = "",
    ) -> dict[str, Any]:
        self = self.store
        with self._connect() as conn:
            state = self._load_state_unlocked(conn)
            allowed_camera_sns = self._allowed_camera_sns_for_scope_unlocked(
                conn,
                state,
                access_token=access_token,
                identifier=identifier,
                user_id=user_id,
            )
            scope_requested = bool(
                str(access_token or "").strip()
                or str(identifier or "").strip()
                or str(user_id or "").strip()
            )
            return self._cloud_storage_camera_info_payload_unlocked(
                state,
                allowed_camera_sns=allowed_camera_sns,
                enforce_capacity=scope_requested,
            )

    def storage_purchase_plan_payloads(self) -> list[dict[str, Any]]:
        self = self.store
        with self._connect() as conn:
            state = self._load_state_unlocked(conn)
            return self._storage_plan_catalog_unlocked(state)

    def storage_purchase_plan_page_payload(
        self,
        *,
        current_page: int = 1,
        page_size: int = 20,
    ) -> list[dict[str, Any]]:
        self = self.store
        with self._connect() as conn:
            state = self._load_state_unlocked(conn)
            plans = self._storage_plan_catalog_unlocked(state)
            return self._paginate_items(
                plans,
                current_page=current_page,
                page_size=page_size,
            )

    def storage_renew_plan_page_payload(
        self,
        *,
        current_page: int = 1,
        page_size: int = 20,
        service_id: str = "",
    ) -> list[dict[str, Any]]:
        self = self.store
        with self._connect() as conn:
            state = self._load_state_unlocked(conn)
            plans = self._storage_plan_catalog_unlocked(state)
            service = state.get("storageService", {}) if isinstance(state.get("storageService"), dict) else {}
            active_period = int(service.get("storagePeriod", 30) or 30)
            filtered = [
                plan
                for plan in plans
                if int(plan.get("storagePeriod", active_period) or active_period) == active_period
            ]
            return self._paginate_items(
                filtered,
                current_page=current_page,
                page_size=page_size,
            )

    def storage_service_info_payload(
        self,
        *,
        access_token: str = "",
        identifier: str = "",
        user_id: str = "",
    ) -> dict[str, Any]:
        self = self.store
        with self._connect() as conn:
            state = self._load_state_unlocked(conn)
            allowed_camera_sns = self._allowed_camera_sns_for_scope_unlocked(
                conn,
                state,
                access_token=access_token,
                identifier=identifier,
                user_id=user_id,
            )
            scope_requested = bool(
                str(access_token or "").strip()
                or str(identifier or "").strip()
                or str(user_id or "").strip()
            )
            cloud_info = self._cloud_storage_camera_info_payload_unlocked(
                state,
                allowed_camera_sns=allowed_camera_sns,
                enforce_capacity=scope_requested,
            )
            return {
                "cameraCount": int(cloud_info.get("cameraCount", 0) or 0),
                "coverImageSignedUrl": str(cloud_info.get("coverImageSignedUrl", "") or ""),
                "coverImageUrl": str(cloud_info.get("coverImageUrl", "") or ""),
                "expireTime": str(cloud_info.get("expireTime", "") or ""),
                "isAlert": 1 if bool(cloud_info.get("isAlert", False)) else 0,
                "planId": str(cloud_info.get("planId", "") or ""),
                "serviceId": int(cloud_info.get("serviceId", 10001) or 10001),
                "serviceStatus": int(cloud_info.get("serviceStatus", 1) or 1),
                "storagePeriod": int(cloud_info.get("storagePeriod", 7) or 7),
                "storageServiceName": str(cloud_info.get("storageServiceName", "") or ""),
                "storageServiceUrl": str(cloud_info.get("storageServiceUrl", "") or ""),
            }

    def storage_camera_unbind_list_payload(
        self,
        *,
        access_token: str = "",
        identifier: str = "",
        user_id: str = "",
    ) -> list[dict[str, Any]]:
        self = self.store
        with self._connect() as conn:
            state = self._load_state_unlocked(conn)
            allowed_camera_sns = self._allowed_camera_sns_for_scope_unlocked(
                conn,
                state,
                access_token=access_token,
                identifier=identifier,
                user_id=user_id,
            )
            service = state.get("storageService", {}) if isinstance(state.get("storageService"), dict) else {}
            configured_capacity = max(int(service.get("cameraCount", 0) or 0), 1)
            scope_requested = bool(
                str(access_token or "").strip()
                or str(identifier or "").strip()
                or str(user_id or "").strip()
            )
            effective_bound = set(
                self._effective_cloud_bound_camera_sns_unlocked(
                    state,
                    allowed_camera_sns=allowed_camera_sns,
                    camera_capacity=configured_capacity,
                    enforce_capacity=scope_requested and allowed_camera_sns is not None,
                )
            )
            return [
                {
                    "deviceName": camera.get("cameraName", camera_sn),
                    "deviceSn": camera_sn,
                    "select": 0,
                }
                for camera_sn, camera in self._scoped_camera_items_unlocked(
                    state,
                    allowed_camera_sns=allowed_camera_sns,
                )
                if camera_sn not in effective_bound
            ]

    def set_cloud_storage_bound(
        self,
        device_sns: list[str],
        *,
        bound: bool,
        access_token: str = "",
        identifier: str = "",
        user_id: str = "",
    ) -> int:
        self = self.store
        requested = [str(item or "").strip() for item in device_sns if str(item or "").strip()]
        requested_set = set(requested)
        if not requested_set:
            return 0
        changed = 0
        with self.lock, self._connect() as conn:
            state = self._load_state_unlocked(conn)
            allowed_camera_sns = self._allowed_camera_sns_for_scope_unlocked(
                conn,
                state,
                access_token=access_token,
                identifier=identifier,
                user_id=user_id,
            )
            scope_requested = bool(
                str(access_token or "").strip()
                or str(identifier or "").strip()
                or str(user_id or "").strip()
            )
            if allowed_camera_sns is not None:
                requested_set = {camera_sn for camera_sn in requested_set if camera_sn in allowed_camera_sns}
                requested = [camera_sn for camera_sn in requested if camera_sn in requested_set]
                if not requested_set:
                    return 0
            cameras = state.setdefault("cameras", {})
            for camera_sn in sorted(requested_set):
                camera = cameras.get(camera_sn)
                if not camera:
                    continue
                desired = 1 if bound else 0
                if int(camera.get("cloudStorageBound", 1)) == desired:
                    continue
                camera["cloudStorageBound"] = desired
                self._normalize_camera_record(camera_sn, camera)
                changed += 1
            if bound and scope_requested and allowed_camera_sns is not None:
                service = state.get("storageService", {}) if isinstance(state.get("storageService"), dict) else {}
                configured_capacity = max(int(service.get("cameraCount", 0) or 0), 1)
                effective_bound = self._effective_cloud_bound_camera_sns_unlocked(
                    state,
                    allowed_camera_sns=allowed_camera_sns,
                    camera_capacity=configured_capacity,
                    enforce_capacity=False,
                )
                preferred_keep: list[str] = []
                for camera_sn in requested:
                    if camera_sn in effective_bound and camera_sn not in preferred_keep:
                        preferred_keep.append(camera_sn)
                for camera_sn in effective_bound:
                    if camera_sn not in preferred_keep:
                        preferred_keep.append(camera_sn)
                keep_set = set(preferred_keep[:configured_capacity])
                for camera_sn in effective_bound:
                    camera = cameras.get(camera_sn)
                    if not camera:
                        continue
                    desired = 1 if camera_sn in keep_set else 0
                    if int(camera.get("cloudStorageBound", 1)) == desired:
                        continue
                    camera["cloudStorageBound"] = desired
                    self._normalize_camera_record(camera_sn, camera)
                    changed += 1
            if changed:
                self._save_state_unlocked(conn, state)
        return changed

    def create_storage_order(
        self,
        *,
        kind: str,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self = self.store
        body = payload if isinstance(payload, dict) else {}
        with self.lock, self._connect() as conn:
            state = self._load_state_unlocked(conn)
            plan = self._resolve_storage_plan_unlocked(state, body)
            order_no = str(
                deep_get(body, "orderNo")
                or deep_get(body, "order_no")
                or f"LOCAL-{kind.upper()}-{uuid.uuid4().hex[:12].upper()}"
            )
            payment_id = str(
                deep_get(body, "paymentId")
                or deep_get(body, "payment_id")
                or f"PAY-{uuid.uuid4().hex[:12].upper()}"
            )
            now = iso_now()
            orders = state.setdefault("storageOrders", [])
            record = next(
                (item for item in orders if str(item.get("orderNo", "") or "") == order_no),
                None,
            )
            if record is None:
                record = {}
            record.update(
                {
                    "autoRenew": int(deep_get(body, "autoRenew", record.get("autoRenew", 0)) or 0),
                    "cameraCount": int(plan.get("cameraCount", 1) or 1),
                    "createdAt": str(record.get("createdAt", now) or now),
                    "effectiveMonths": int(plan.get("effectiveMonths", 1) or 1),
                    "expireTime": str(record.get("expireTime", "") or ""),
                    "orderNo": order_no,
                    "orderStatus": int(record.get("orderStatus", 0) or 0),
                    "orderType": str(kind or "purchase"),
                    "paidAt": str(record.get("paidAt", "") or ""),
                    "paymentId": payment_id,
                    "paymentStatus": int(record.get("paymentStatus", 0) or 0),
                    "planId": str(plan.get("id", "") or ""),
                    "price": str(plan.get("price", "0.00") or "0.00"),
                    "productionIdentifier": str(plan.get("productionIdentifier", "") or ""),
                    "serviceId": str(plan.get("serviceId", "") or ""),
                    "serviceStatus": int(record.get("serviceStatus", 0) or 0),
                    "sku": str(plan.get("sku", "") or ""),
                    "source": int(deep_get(body, "source", record.get("source", 0)) or 0),
                    "storagePeriod": int(plan.get("storagePeriod", 30) or 30),
                    "storageServiceId": 10001,
                    "storageServiceName": str(plan.get("storageServiceName", "Local Test Cloud") or "Local Test Cloud"),
                    "updatedAt": now,
                }
            )
            state["storageOrders"] = [
                item for item in orders if str(item.get("orderNo", "") or "") != order_no
            ]
            state["storageOrders"].insert(0, record)
            del state["storageOrders"][100:]
            self._save_state_unlocked(conn, state)
            return {
                "cameraCount": int(plan.get("cameraCount", 1) or 1),
                "orderNo": order_no,
                "paymentId": payment_id,
                "productionIdentifier": str(plan.get("productionIdentifier", "") or ""),
                "serviceId": str(plan.get("serviceId", "") or ""),
                "storagePeriod": int(plan.get("storagePeriod", 30) or 30),
                "successUrl": self._storage_success_url(order_no, payment_id),
            }

    def finalize_storage_order(
        self,
        *,
        order_no: str = "",
        payment_id: str = "",
    ) -> dict[str, Any]:
        self = self.store
        with self.lock, self._connect() as conn:
            state = self._load_state_unlocked(conn)
            orders = state.setdefault("storageOrders", [])
            record = None
            for item in orders:
                if order_no and str(item.get("orderNo", "") or "") == order_no:
                    record = item
                    break
                if payment_id and str(item.get("paymentId", "") or "") == payment_id:
                    record = item
                    break

            if record is None:
                record = self.create_storage_order(kind="purchase")
                state = self._load_state_unlocked(conn)
                orders = state.setdefault("storageOrders", [])
                record = next(
                    (
                        item
                        for item in orders
                        if str(item.get("orderNo", "") or "") == str(record.get("orderNo", "") or "")
                    ),
                    {},
                )

            plan = self._resolve_storage_plan_unlocked(
                state,
                {
                    "serviceId": record.get("planId") or record.get("serviceId"),
                    "cameraCount": record.get("cameraCount"),
                    "storagePeriod": record.get("storagePeriod"),
                    "productionIdentifier": record.get("productionIdentifier"),
                    "sku": record.get("sku"),
                },
            )
            now_dt = datetime.now(timezone.utc)
            current_service = state.get("storageService", {})
            effective_start = now_dt
            if str(record.get("orderType", "") or "") == "renew":
                current_expire = self._parse_state_datetime(current_service.get("expireTime"))
                if current_expire and current_expire > now_dt:
                    effective_start = current_expire
            expire_dt = effective_start + timedelta(
                days=30 * int(plan.get("effectiveMonths", 1) or 1)
            )
            now = self._format_state_datetime(now_dt)
            order_no = str(order_no or record.get("orderNo", "") or f"LOCAL-PAY-{uuid.uuid4().hex[:12].upper()}")
            payment_id = str(payment_id or record.get("paymentId", "") or f"PAY-{uuid.uuid4().hex[:12].upper()}")
            record.update(
                {
                    "cameraCount": int(plan.get("cameraCount", 1) or 1),
                    "effectiveMonths": int(plan.get("effectiveMonths", 1) or 1),
                    "expireTime": self._format_state_datetime(expire_dt),
                    "orderNo": order_no,
                    "orderStatus": 1,
                    "paidAt": now,
                    "paymentId": payment_id,
                    "paymentStatus": 1,
                    "planId": str(plan.get("id", "") or ""),
                    "price": str(plan.get("price", "0.00") or "0.00"),
                    "productionIdentifier": str(plan.get("productionIdentifier", "") or ""),
                    "serviceId": str(plan.get("serviceId", "") or ""),
                    "serviceStatus": 1,
                    "sku": str(plan.get("sku", "") or ""),
                    "storagePeriod": int(plan.get("storagePeriod", 30) or 30),
                    "storageServiceName": str(plan.get("storageServiceName", "Local Test Cloud") or "Local Test Cloud"),
                    "updatedAt": now,
                }
            )
            service = state.setdefault("storageService", self._default_storage_service_unlocked(state))
            service.update(
                {
                    "cameraCount": int(plan.get("cameraCount", 1) or 1),
                    "effectiveMonths": int(plan.get("effectiveMonths", 1) or 1),
                    "expireTime": self._format_state_datetime(expire_dt),
                    "lastOrderNo": order_no,
                    "lastPaymentId": payment_id,
                    "planId": str(plan.get("id", "") or ""),
                    "productionIdentifier": str(plan.get("productionIdentifier", "") or ""),
                    "serviceId": int(service.get("serviceId", 10001) or 10001),
                    "serviceStatus": 1,
                    "sku": str(plan.get("sku", "") or ""),
                    "storagePeriod": int(plan.get("storagePeriod", 30) or 30),
                    "storageServiceName": str(plan.get("storageServiceName", "Local Test Cloud") or "Local Test Cloud"),
                    "storageServiceType": int(plan.get("storageServiceType", 1) or 1),
                    "storageServiceUrl": str(
                        plan.get(
                            "storageServiceUrl",
                            self._cloud_cover_url(plan_id=str(plan.get("id", "") or "")),
                        )
                        or self._cloud_cover_url(plan_id=str(plan.get("id", "") or ""))
                    ),
                    "trial": 0,
                }
            )
            self._save_state_unlocked(conn, state)
            return {
                "cameraCount": int(plan.get("cameraCount", 1) or 1),
                "orderNo": order_no,
                "paymentId": payment_id,
                "paymentStatus": 1,
                "productionIdentifier": str(plan.get("productionIdentifier", "") or ""),
                "serviceId": str(plan.get("serviceId", "") or ""),
                "storagePeriod": int(plan.get("storagePeriod", 30) or 30),
                "successUrl": self._storage_success_url(order_no, payment_id),
            }

    def storage_payment_page_payload(
        self,
        *,
        current_page: int = 1,
        page_size: int = 20,
    ) -> list[dict[str, Any]]:
        self = self.store
        with self._connect() as conn:
            state = self._load_state_unlocked(conn)
            orders = list(state.get("storageOrders", []))
            orders.sort(
                key=lambda item: (item.get("createdAt", ""), item.get("orderNo", "")),
                reverse=True,
            )
            current_page = max(int(current_page or 1), 1)
            page_size = max(int(page_size or 20), 1)
            start = (current_page - 1) * page_size
            payload: list[dict[str, Any]] = []
            for order in orders[start : start + page_size]:
                payload.append(
                    {
                        "autoRenew": int(order.get("autoRenew", 0) or 0),
                        "cameraCount": int(order.get("cameraCount", 1) or 1),
                        "createTime": str(order.get("createdAt", "") or ""),
                        "effectiveMonths": int(order.get("effectiveMonths", 1) or 1),
                        "expireTime": str(order.get("expireTime", "") or ""),
                        "orderNo": str(order.get("orderNo", "") or ""),
                        "orderStatus": int(order.get("orderStatus", 0) or 0),
                        "orderType": str(order.get("orderType", "") or ""),
                        "payTime": self._datetime_to_epoch_ms_str(order.get("paidAt", "")),
                        "paymentId": str(order.get("paymentId", "") or ""),
                        "paymentStatus": int(order.get("paymentStatus", 0) or 0),
                        "planId": str(order.get("planId", "") or ""),
                        "price": str(order.get("price", "0.00") or "0.00"),
                        "productionIdentifier": str(order.get("productionIdentifier", "") or ""),
                        "serviceId": str(order.get("serviceId", "") or ""),
                        "serviceStatus": int(order.get("serviceStatus", 0) or 0),
                        "sku": str(order.get("sku", "") or ""),
                        "storagePeriod": int(order.get("storagePeriod", 30) or 30),
                        "storageServiceId": int(order.get("storageServiceId", 10001) or 10001),
                        "storageServiceName": str(order.get("storageServiceName", "Local Test Cloud") or "Local Test Cloud"),
                        "updateTime": str(order.get("updatedAt", "") or ""),
                    }
                )
            return payload

    def storage_payment_status_payload(
        self,
        *,
        order_no: str = "",
        payment_id: str = "",
    ) -> dict[str, Any]:
        self = self.store
        with self._connect() as conn:
            state = self._load_state_unlocked(conn)
            record = None
            for item in state.get("storageOrders", []):
                if order_no and str(item.get("orderNo", "") or "") == str(order_no):
                    record = item
                    break
                if payment_id and str(item.get("paymentId", "") or "") == str(payment_id):
                    record = item
                    break
            return {
                "isPaid": bool(record and int(record.get("paymentStatus", 0) or 0) == 1),
                "orderNo": str(order_no or (record or {}).get("orderNo", "") or ""),
                "paymentId": str(payment_id or (record or {}).get("paymentId", "") or ""),
                "paymentStatus": int((record or {}).get("paymentStatus", 0) or 0),
                "serviceId": str((record or {}).get("serviceId", "") or ""),
            }
