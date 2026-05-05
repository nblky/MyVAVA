from __future__ import annotations

import json
from typing import Any

from ..store_shared import DEFAULT_CAMERA_SN, DEFAULT_STATION_SN, deep_get, iso_now
from .base import BaseDomainService


class PlaybackDomainService(BaseDomainService):
    def storage_video_days(
        self,
        *,
        device_sn: str = "",
        access_token: str = "",
        identifier: str = "",
        user_id: str = "",
    ) -> list[str]:
        self = self.store
        with self._connect() as conn:
            state = self._load_state_unlocked(conn)
            allowed_station_sns = self._bound_station_sns_for_user_unlocked(
                conn,
                access_token=access_token,
                identifier=identifier,
                user_id=user_id,
            )
            allowed_camera_sns: set[str] | None = None
            if allowed_station_sns is not None:
                cameras = state.get("cameras", {}) if isinstance(state.get("cameras"), dict) else {}
                allowed_camera_sns = {
                    str(camera_sn or "").strip()
                    for camera_sn, camera in cameras.items()
                    if isinstance(camera, dict)
                    and str(camera.get("stationSn", "") or "").strip() in allowed_station_sns
                }
            requested_device_sn = str(device_sn or "").strip()
            if allowed_camera_sns is not None and requested_device_sn and requested_device_sn not in allowed_camera_sns:
                return []
            return sorted(
                {
                    self._normalize_date_digits(item.get("fileDate", "") or "")
                    for item in state.get("cloudMedia", [])
                    if allowed_camera_sns is None
                    or str(item.get("deviceSn", "") or "").strip() in allowed_camera_sns
                    if self._normalize_date_digits(item.get("fileDate", "") or "")
                    and (
                        not requested_device_sn
                        or str(item.get("deviceSn", "") or "").strip() == requested_device_sn
                    )
                },
                reverse=True,
            )

    def storage_video_list_payload(
        self,
        *,
        date: str = "",
        device_sn: str = "",
        page: int = 1,
        size: int = 20,
        media_record_start_time: str = "",
        access_token: str = "",
        identifier: str = "",
        user_id: str = "",
    ) -> list[dict[str, Any]]:
        self = self.store
        with self._connect() as conn:
            state = self._load_state_unlocked(conn)
            changed = False
            allowed_station_sns = self._bound_station_sns_for_user_unlocked(
                conn,
                access_token=access_token,
                identifier=identifier,
                user_id=user_id,
            )
            allowed_camera_sns: set[str] | None = None
            if allowed_station_sns is not None:
                cameras = state.get("cameras", {}) if isinstance(state.get("cameras"), dict) else {}
                allowed_camera_sns = {
                    str(camera_sn or "").strip()
                    for camera_sn, camera in cameras.items()
                    if isinstance(camera, dict)
                    and str(camera.get("stationSn", "") or "").strip() in allowed_station_sns
                }
            requested_date = self._normalize_date_digits(date or "")
            requested_device_sn = str(device_sn or "").strip()
            if allowed_camera_sns is not None and requested_device_sn and requested_device_sn not in allowed_camera_sns:
                return []
            candidates: list[tuple[str, int, dict[str, Any], dict[str, Any] | None]] = []
            for cloud_media in state.get("cloudMedia", []):
                changed = self._infer_cloud_media_asset_paths_unlocked(cloud_media) or changed
                camera_sn = str(cloud_media.get("deviceSn", "") or "").strip()
                if allowed_camera_sns is not None and camera_sn not in allowed_camera_sns:
                    continue
                file_date = self._normalize_date_digits(cloud_media.get("fileDate", "") or "")
                file_name = str(cloud_media.get("fileName", "") or "").strip()
                stream_code = str(cloud_media.get("streamCode", "") or "").strip()
                if requested_device_sn and camera_sn != requested_device_sn:
                    continue
                if requested_date:
                    if len(requested_date) == 6 and not file_date.startswith(requested_date):
                        continue
                    if len(requested_date) == 8 and file_date != requested_date:
                        continue
                if not ((camera_sn and file_date and file_name) or stream_code):
                    continue
                message = self._find_message_for_cloud_media_unlocked(
                    state,
                    notice_id=cloud_media.get("noticeId", 0),
                    device_sn=camera_sn,
                    file_date=file_date,
                    file_name=file_name,
                )
                device_time = self._format_notice_device_time(
                    str(
                        cloud_media.get("deviceTime", "")
                        or (self._message_device_time(message) if message else "")
                        or cloud_media.get("startedAt", "")
                        or cloud_media.get("createdAt", "")
                        or ""
                    )
                )
                started_at_ms = int(cloud_media.get("startedAtMs", 0) or 0)
                candidates.append((device_time, started_at_ms, cloud_media, message))
            candidates.sort(
                key=lambda item: (
                    item[0],
                    item[1],
                    int(item[2].get("noticeId", 0) or 0),
                    str(item[2].get("fileName", "") or ""),
                ),
                reverse=True,
            )
            cursor_time = str(media_record_start_time or "").strip()
            if cursor_time:
                candidates = [
                    item
                    for item in candidates
                    if item[0] < cursor_time
                ]
                start = 0
            else:
                page = max(int(page or 1), 1)
                start = (page - 1) * max(int(size or 20), 1)
            limit = max(int(size or 20), 1)
            payload: list[dict[str, Any]] = []
            for _, _, cloud_media, message in candidates[start : start + limit]:
                ext = message.get("extObject", {}) if message else {}
                station_sn = str(
                    (
                        message.get("parentDeviceSn", "")
                        if message
                        else cloud_media.get("stationSn", "")
                    )
                    or DEFAULT_STATION_SN
                )
                camera_sn = str(cloud_media.get("deviceSn", "") or DEFAULT_CAMERA_SN)
                cameras = state.get("cameras", {}) if isinstance(state.get("cameras"), dict) else {}
                camera_state = cameras.get(camera_sn, {}) if isinstance(cameras.get(camera_sn), dict) else {}
                camera_name = str(
                    deep_get(ext, "cameraName")
                    or camera_state.get("cameraName")
                    or camera_sn
                )
                file_date = str(cloud_media.get("fileDate", "") or deep_get(ext, "fileDate", "") or "")
                file_name = str(cloud_media.get("fileName", "") or deep_get(ext, "fileName", "") or "")
                device_time = self._format_notice_device_time(
                    str(
                        cloud_media.get("deviceTime", "")
                        or (self._message_device_time(message) if message else "")
                        or cloud_media.get("startedAt", "")
                        or cloud_media.get("createdAt", "")
                        or ""
                    )
                )
                notice_fields = self._recording_notice_fields_unlocked(
                    conn,
                    device_sn=camera_sn,
                    file_date=file_date,
                    file_name=file_name,
                    message=message,
                    cloud_media=cloud_media,
                )
                if not message:
                    ext = {
                        "fileType": int(
                            notice_fields.get(
                                "fileType",
                                self._default_recording_file_type(file_name),
                            )
                            or self._default_recording_file_type(file_name)
                        )
                    }
                stream_code = str(cloud_media.get("streamCode", "") or self._stream_code(
                    device_sn=camera_sn, file_date=file_date, file_name=file_name
                ))
                thumb_version = self._cloud_media_version_token_unlocked(cloud_media)
                thumb_url = self._thumbnail_url(
                    station_sn=station_sn,
                    camera_sn=camera_sn,
                    file_date=file_date,
                    file_name=file_name,
                    stream_code=stream_code,
                    version=thumb_version,
                )
                notice_type = int(notice_fields.get("visibleNoticeType", 0) or 0)
                app_trigger_type = self._cloud_trigger_type_for_app(notice_type)
                for key in ("sourceNoticeType", "visibleNoticeType", "fileType", "title"):
                    new_value = notice_fields.get(key)
                    if cloud_media.get(key) != new_value:
                        cloud_media[key] = new_value
                        changed = True
                item = {
                    "streamCode": stream_code,
                    "mediaRecordStartTime": device_time,
                    "fileExpireTime": "",
                    "timeZone": str(deep_get(ext, "timezone", "8") or "8"),
                    "coverImageSignedUrl": thumb_url,
                    "duration": float(cloud_media.get("duration", 0) or deep_get(ext, "duration", 10) or 10),
                    "deviceSN": camera_sn,
                    "deviceName": camera_name,
                    "createTime": device_time,
                    "modifyTime": device_time,
                    "id": str(cloud_media.get("streamCode", "") or stream_code),
                    "mp4FileSize": int((cloud_media or {}).get("mp4FileSize", 0) or 0),
                    "stationSN": station_sn,
                    "stationDeviceCode": station_sn,
                    "cameraDeviceCode": camera_sn,
                    "cameraName": camera_name,
                    "date": file_date,
                    "file": file_name,
                    "tiggerType": app_trigger_type,
                    "visibleNoticeType": notice_type,
                }
                item.update(
                    self._thumbnail_fields(
                        station_sn=station_sn,
                        camera_sn=camera_sn,
                        file_date=file_date,
                        file_name=file_name,
                        stream_code=stream_code,
                        version=thumb_version,
                    )
                )
                payload.append(item)
            if changed:
                self._save_state_unlocked(conn, state)
            return payload

    def remove_cloud_media(
        self,
        *,
        stream_codes: Any = None,
        device_sn: str = "",
        file_date: str = "",
        file_names: Any = None,
        access_token: str = "",
        identifier: str = "",
        user_id: str = "",
    ) -> int:
        self = self.store
        requested_stream_codes = set(self._coerce_str_list(stream_codes))
        requested_file_names = set(self._coerce_str_list(file_names))
        requested_device_sn = str(device_sn or "").strip()
        requested_file_date = self._normalize_date_digits(file_date or "")
        requested_keys: set[tuple[str, str, str]] = set()
        for stream_code in requested_stream_codes:
            stream_device_sn, stream_file_date, stream_file_name = self._parse_stream_code(
                stream_code
            )
            if stream_device_sn and stream_file_date and stream_file_name:
                requested_keys.add(
                    (
                        stream_device_sn,
                        self._normalize_date_digits(stream_file_date),
                        stream_file_name,
                    )
                )
            elif stream_file_name:
                requested_file_names.add(stream_file_name)

        def should_remove(item: dict[str, Any]) -> bool:
            item_stream_code = str(item.get("streamCode", "") or "").strip()
            item_key = (
                str(item.get("deviceSn", "") or "").strip(),
                self._normalize_date_digits(item.get("fileDate", "") or ""),
                str(item.get("fileName", "") or "").strip(),
            )
            if requested_stream_codes and item_stream_code in requested_stream_codes:
                return True
            if requested_keys and item_key in requested_keys:
                return True
            if requested_file_names and item_key[2] in requested_file_names:
                if requested_device_sn and item_key[0] != requested_device_sn:
                    return False
                if requested_file_date and item_key[1] != requested_file_date:
                    return False
                return True
            return False

        with self.lock, self._connect() as conn:
            state = self._load_state_unlocked(conn)
            allowed_station_sns = self._bound_station_sns_for_user_unlocked(
                conn,
                access_token=access_token,
                identifier=identifier,
                user_id=user_id,
            )
            allowed_camera_sns: set[str] | None = None
            if allowed_station_sns is not None:
                cameras = state.get("cameras", {}) if isinstance(state.get("cameras"), dict) else {}
                allowed_camera_sns = {
                    str(camera_sn or "").strip()
                    for camera_sn, camera in cameras.items()
                    if isinstance(camera, dict)
                    and str(camera.get("stationSn", "") or "").strip() in allowed_station_sns
                }
            if allowed_camera_sns is not None and requested_device_sn and requested_device_sn not in allowed_camera_sns:
                return 0
            cloud_media = state.get("cloudMedia", [])
            kept: list[dict[str, Any]] = []
            removed_items: list[dict[str, Any]] = []
            removed_paths: list[str] = []
            for item in cloud_media:
                item_camera_sn = str(item.get("deviceSn", "") or "").strip()
                if allowed_camera_sns is not None and item_camera_sn not in allowed_camera_sns:
                    kept.append(item)
                    continue
                if should_remove(item):
                    item_copy = json.loads(json.dumps(item))
                    self._infer_cloud_media_asset_paths_unlocked(item_copy)
                    removed_items.append(item_copy)
                    removed_paths.extend(self._remove_cloud_media_files_unlocked(item_copy))
                else:
                    kept.append(item)
            removed = len(removed_items)
            if removed:
                state["cloudMedia"] = kept
                events = state.setdefault("events", [])
                events.append(
                    {
                        "ts": iso_now(),
                        "message": "cloud.media.delete",
                        "payload": {
                            "removed": removed,
                            "removedFiles": len(removed_paths),
                            "streamCodes": sorted(requested_stream_codes),
                            "fileNames": sorted(requested_file_names),
                        },
                    }
                )
                del events[:-200]
                self._save_state_unlocked(conn, state)
            return removed
