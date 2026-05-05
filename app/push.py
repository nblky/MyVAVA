from __future__ import annotations

import base64
import json
import subprocess
import time
import urllib.parse
import urllib.error
import urllib.request
from functools import lru_cache
from pathlib import Path
from typing import Any

from .config import Settings, get_settings


_HEX = set("0123456789abcdef")


def token_suffix(token: str) -> str:
    token = str(token or "").strip()
    return token[-12:] if len(token) > 12 else token


def classify_push_token(push_token: str) -> str:
    token = str(push_token or "").strip()
    if not token:
        return "unknown"
    lowered = token.lower()
    if ":" in token or token.startswith("APA91") or token.startswith("AAAA"):
        return "fcm"
    if len(lowered) >= 32 and all(ch in _HEX for ch in lowered):
        return "apns"
    if len(token) >= 48 and not all(ch in _HEX for ch in lowered):
        return "fcm"
    return "unknown"


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


class PushDispatcher:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._apns_token = ""
        self._apns_token_expire_at = 0

    def dispatch_notice(self, store: Any, notice: dict[str, Any]) -> dict[str, Any]:
        envelope = self._notice_envelope(notice)
        registrations = self._unique_registrations(store.push_registrations())
        result = {
            "enabled": bool(self.settings.push_enabled),
            "noticeId": int(notice.get("noticeId", 0) or 0),
            "title": envelope["title"],
            "body": envelope["body"],
            "targetCount": len(registrations),
            "sideChannelCount": 0,
            "targets": [],
        }
        delivered = 0
        if self.settings.push_enabled:
            for registration in registrations:
                attempt = self._dispatch_target(registration, envelope)
                if attempt.get("status") == "sent":
                    delivered += 1
                result["targets"].append(attempt)
        side_attempts = self._dispatch_side_channels(envelope)
        result["sideChannelCount"] = len(side_attempts)
        for attempt in side_attempts:
            if attempt.get("status") == "sent":
                delivered += 1
            result["targets"].append(attempt)
        result["delivered"] = delivered
        if delivered:
            result["status"] = "sent"
        elif not self.settings.push_enabled and not side_attempts:
            result["status"] = "disabled"
        elif not registrations and not side_attempts:
            result["status"] = "no_targets"
        else:
            result["status"] = "completed"
        return result

    def _unique_registrations(self, registrations: list[dict[str, Any]]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        seen: set[str] = set()
        for registration in registrations:
            token = str(registration.get("token", "") or "").strip()
            if not token or token in seen:
                continue
            seen.add(token)
            out.append(registration)
        return out

    def _notice_envelope(self, notice: dict[str, Any]) -> dict[str, Any]:
        ext = notice.get("extObject", {}) if isinstance(notice.get("extObject"), dict) else {}
        title = str(notice.get("title", "") or "").strip() or "VAVA Home"
        device_name = str(
            notice.get("deviceName", "")
            or ext.get("deviceName", "")
            or notice.get("deviceSn", "")
            or ""
        ).strip()
        content = str(notice.get("content", "") or "").strip()
        body = content or device_name
        if body == title:
            body = device_name
        payload = {
            "type": "1",
            "noticeId": str(int(notice.get("noticeId", 0) or 0)),
            "noticeType": str(int(notice.get("noticeType", 0) or 0)),
            "visibleNoticeType": str(int(notice.get("visibleNoticeType", 0) or 0)),
            "deviceSn": str(notice.get("deviceSn", "") or ""),
            "parentDeviceSn": str(notice.get("parentDeviceSn", "") or ""),
            "deviceName": device_name,
            "fileDate": str(ext.get("fileDate", "") or ""),
            "fileName": str(ext.get("fileName", "") or ""),
            "deviceTime": str(notice.get("deviceTime", "") or ext.get("deviceTime", "") or ""),
            "gcm.notification.title": title,
        }
        if body:
            payload["gcm.notification.body"] = body
        return {"title": title, "body": body, "payload": payload}

    def _dispatch_target(self, registration: dict[str, Any], envelope: dict[str, Any]) -> dict[str, Any]:
        token = str(registration.get("token", "") or "").strip()
        platform = str(registration.get("platform", "") or classify_push_token(token))
        attempt = {
            "platform": platform,
            "tokenSuffix": token_suffix(token),
            "remote": str(registration.get("remote", "") or ""),
        }
        if not token:
            attempt["status"] = "skipped_empty_token"
            return attempt
        if platform == "fcm":
            attempt.update(self._send_fcm(token, envelope))
            return attempt
        if platform == "apns":
            attempt.update(self._send_apns(token, envelope))
            return attempt
        attempt["status"] = "skipped_unknown_platform"
        return attempt

    def _dispatch_side_channels(self, envelope: dict[str, Any]) -> list[dict[str, Any]]:
        attempts: list[dict[str, Any]] = []
        if self.settings.notify_webhook_url:
            attempts.append(self._send_webhook(envelope))
        if self.settings.ntfy_topic_url:
            attempts.append(self._send_ntfy(envelope))
        return attempts

    def _send_webhook(self, envelope: dict[str, Any]) -> dict[str, Any]:
        payload = {
            "title": envelope["title"],
            "body": envelope["body"],
            "payload": envelope["payload"],
            "source": "vava-local",
        }
        headers = {"Content-Type": "application/json"}
        auth = str(self.settings.notify_webhook_auth or "").strip()
        if auth:
            headers["Authorization"] = auth
        return self._http_json_request(
            url=self.settings.notify_webhook_url,
            payload=payload,
            headers=headers,
            platform="webhook",
        )

    def _send_ntfy(self, envelope: dict[str, Any]) -> dict[str, Any]:
        body = envelope["body"] or envelope["title"]
        headers = {
            "Title": self._ntfy_title(envelope["title"]),
            "Tags": self._ntfy_tags(envelope["payload"].get("visibleNoticeType", "")),
            "Priority": "default",
            "Content-Type": "text/plain; charset=utf-8",
        }
        auth = str(self.settings.ntfy_auth or "").strip()
        if auth:
            headers["Authorization"] = auth
        request = urllib.request.Request(
            self.settings.ntfy_topic_url,
            data=body.encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(
                request,
                timeout=max(float(self.settings.push_timeout_seconds), 1.0),
            ) as response:
                response_body = response.read().decode("utf-8", "replace")
                return {
                    "platform": "ntfy",
                    "target": self._mask_url(self.settings.ntfy_topic_url),
                    "status": "sent" if 200 <= response.status < 300 else "error",
                    "httpStatus": int(response.status),
                    "responseBody": response_body[:400],
                }
        except urllib.error.HTTPError as exc:
            response_body = exc.read().decode("utf-8", "replace")
            return {
                "platform": "ntfy",
                "target": self._mask_url(self.settings.ntfy_topic_url),
                "status": "error",
                "httpStatus": int(getattr(exc, "code", 0) or 0),
                "responseBody": response_body[:400],
            }
        except BaseException as exc:
            return {
                "platform": "ntfy",
                "target": self._mask_url(self.settings.ntfy_topic_url),
                "status": "error",
                "error": str(exc),
            }

    def _http_json_request(
        self,
        *,
        url: str,
        payload: dict[str, Any],
        headers: dict[str, str],
        platform: str,
    ) -> dict[str, Any]:
        request = urllib.request.Request(
            url,
            data=json.dumps(payload, separators=(",", ":")).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(
                request,
                timeout=max(float(self.settings.push_timeout_seconds), 1.0),
            ) as response:
                response_body = response.read().decode("utf-8", "replace")
                return {
                    "platform": platform,
                    "target": self._mask_url(url),
                    "status": "sent" if 200 <= response.status < 300 else "error",
                    "httpStatus": int(response.status),
                    "responseBody": response_body[:400],
                }
        except urllib.error.HTTPError as exc:
            response_body = exc.read().decode("utf-8", "replace")
            return {
                "platform": platform,
                "target": self._mask_url(url),
                "status": "error",
                "httpStatus": int(getattr(exc, "code", 0) or 0),
                "responseBody": response_body[:400],
            }
        except BaseException as exc:
            return {
                "platform": platform,
                "target": self._mask_url(url),
                "status": "error",
                "error": str(exc),
            }

    def _mask_url(self, raw_url: str) -> str:
        parsed = urllib.parse.urlsplit(str(raw_url or "").strip())
        if not parsed.scheme or not parsed.netloc:
            return str(raw_url or "").strip()
        path = parsed.path or "/"
        return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))

    def _ntfy_title(self, title: str) -> str:
        prefix = str(self.settings.ntfy_title_prefix or "").strip()
        return f"{prefix}: {title}" if prefix else title

    def _ntfy_tags(self, visible_notice_type: Any) -> str:
        mapping = {
            "5": "warning,camera",
            "6": "rotating_light,person",
            "7": "eyes,camera",
            "8": "rotating_light,face",
            "2": "skull,camera",
        }
        return mapping.get(str(visible_notice_type or ""), "camera")

    def _send_fcm(self, token: str, envelope: dict[str, Any]) -> dict[str, Any]:
        if not self.settings.fcm_server_key:
            return {"status": "skipped_missing_fcm_server_key"}
        payload = {
            "to": token,
            "priority": "high",
            "notification": {
                "title": envelope["title"],
                "body": envelope["body"] or envelope["title"],
                "sound": "default",
            },
            "data": envelope["payload"],
        }
        request = urllib.request.Request(
            self.settings.fcm_endpoint,
            data=json.dumps(payload, separators=(",", ":")).encode("utf-8"),
            headers={
                "Authorization": f"key={self.settings.fcm_server_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(
                request,
                timeout=max(float(self.settings.push_timeout_seconds), 1.0),
            ) as response:
                body = response.read().decode("utf-8", "replace")
                return {
                    "status": "sent" if 200 <= response.status < 300 else "error",
                    "httpStatus": int(response.status),
                    "responseBody": body[:400],
                }
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", "replace")
            return {
                "status": "error",
                "httpStatus": int(getattr(exc, "code", 0) or 0),
                "responseBody": body[:400],
            }
        except BaseException as exc:
            return {"status": "error", "error": str(exc)}

    def _send_apns(self, token: str, envelope: dict[str, Any]) -> dict[str, Any]:
        auth_mode = self._apns_auth_mode()
        if auth_mode == "none":
            return {"status": "skipped_missing_apns_credentials"}
        payload = {
            "aps": {
                "alert": {
                    "title": envelope["title"],
                    "body": envelope["body"] or envelope["title"],
                },
                "sound": "default",
            },
            **envelope["payload"],
        }
        headers = {
            "apns-topic": self.settings.apns_topic,
            "apns-push-type": "alert",
            "apns-priority": "10",
            "content-type": "application/json",
        }
        if auth_mode == "token":
            headers["authorization"] = f"bearer {self._apns_jwt()}"
        url = f"{self._apns_base_url()}/3/device/{token}"
        cmd = [
            "curl",
            "--silent",
            "--show-error",
            "--http2",
            "-o",
            "-",
            "-w",
            "\n__HTTP_STATUS__:%{http_code}",
        ]
        if auth_mode == "cert":
            cmd.extend(["--cert", self.settings.apns_cert_path])
            if self.settings.apns_key_path:
                cmd.extend(["--key", self.settings.apns_key_path])
        for key, value in headers.items():
            cmd.extend(["-H", f"{key}: {value}"])
        cmd.extend(["-d", json.dumps(payload, separators=(",", ":")), url])
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=max(float(self.settings.push_timeout_seconds), 1.0) + 2.0,
                check=False,
            )
        except BaseException as exc:
            return {"status": "error", "error": str(exc)}
        if proc.returncode != 0:
            return {"status": "error", "error": (proc.stderr or proc.stdout or "").strip()[:400]}
        body = proc.stdout or ""
        marker = "\n__HTTP_STATUS__:"
        http_status = 0
        if marker in body:
            body, status_text = body.rsplit(marker, 1)
            try:
                http_status = int(status_text.strip() or 0)
            except ValueError:
                http_status = 0
        return {
            "status": "sent" if http_status == 200 else "error",
            "httpStatus": http_status,
            "responseBody": body.strip()[:400],
        }

    def _apns_auth_mode(self) -> str:
        if self.settings.apns_cert_path:
            return "cert"
        if (
            self.settings.apns_auth_key_path
            and self.settings.apns_key_id
            and self.settings.apns_team_id
        ):
            return "token"
        return "none"

    def _apns_base_url(self) -> str:
        if self.settings.apns_env == "sandbox":
            return "https://api.sandbox.push.apple.com"
        return "https://api.push.apple.com"

    def _apns_jwt(self) -> str:
        now = int(time.time())
        if self._apns_token and now < self._apns_token_expire_at:
            return self._apns_token
        key_path = Path(self.settings.apns_auth_key_path)
        if not key_path.is_file():
            raise RuntimeError(f"APNs auth key not found: {key_path}")
        header = {"alg": "ES256", "kid": self.settings.apns_key_id}
        claims = {"iss": self.settings.apns_team_id, "iat": now}
        signing_input = ".".join(
            (
                _b64url(json.dumps(header, separators=(",", ":")).encode("utf-8")),
                _b64url(json.dumps(claims, separators=(",", ":")).encode("utf-8")),
            )
        )
        proc = subprocess.run(
            ["openssl", "dgst", "-binary", "-sha256", "-sign", str(key_path)],
            input=signing_input.encode("utf-8"),
            capture_output=True,
            check=False,
        )
        if proc.returncode != 0:
            raise RuntimeError((proc.stderr or b"").decode("utf-8", "replace").strip() or "openssl sign failed")
        token = f"{signing_input}.{_b64url(proc.stdout)}"
        self._apns_token = token
        self._apns_token_expire_at = now + 50 * 60
        return token


@lru_cache(maxsize=1)
def get_push_dispatcher() -> PushDispatcher:
    return PushDispatcher(get_settings())
