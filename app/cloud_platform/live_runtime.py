from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Body, HTTPException
from fastapi.responses import FileResponse, Response

from ..config import get_settings, resolve_live_hls_root
from ..store import DEFAULT_CAMERA_SN, DEFAULT_STATION_SN, get_store, iso_now
from .live_backend import live_url_for_camera


router = APIRouter(tags=["cloud-platform-live"])
settings = get_settings()

LIVE_START_SCRIPT = settings.root_dir / "scripts" / "run_live_hls.sh"
LIVE_STOP_SCRIPT = settings.root_dir / "scripts" / "stop_live_hls.sh"
PPCS_BRIDGE_SCRIPT = settings.root_dir / "scripts" / "run_ppcs_bridge.sh"
LIVE_QUALITY_PRESETS = {
    "auto": 3,
    "high": 10,
    "medium": 1,
    "low": 2,
}
LIVE_WAKE_FATAL_ERRNOS = {58}
LIVE_WAKE_RETRY_COUNT = 7
LIVE_WAKE_RETRY_DELAY_SECONDS = 4.0


def _ok(data=None):
    payload = data if data is not None else {}
    return {
        "stateCode": 200,
        "stateMsg": "OK",
        "code": 200,
        "msg": "OK",
        "data": payload,
    }


def _payload(payload: dict[str, Any] | None) -> dict[str, Any]:
    return payload or {}


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value or "").strip().lower()
    return text in {"1", "true", "yes", "on"}


def _live_hls_dir(camera_sn: str) -> Path:
    safe_camera_sn = str(camera_sn or DEFAULT_CAMERA_SN).strip() or DEFAULT_CAMERA_SN
    return resolve_live_hls_root() / safe_camera_sn / "hls"


def _live_work_dir(camera_sn: str) -> Path:
    safe_camera_sn = str(camera_sn or DEFAULT_CAMERA_SN).strip() or DEFAULT_CAMERA_SN
    return resolve_live_hls_root() / safe_camera_sn


def _live_control_path(camera_sn: str) -> Path:
    safe_camera_sn = str(camera_sn or DEFAULT_CAMERA_SN).strip() or DEFAULT_CAMERA_SN
    return resolve_live_hls_root() / safe_camera_sn / "control.json"


def _load_live_control(camera_sn: str) -> dict[str, Any]:
    control_path = _live_control_path(camera_sn)
    try:
        payload = json.loads(control_path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _save_live_control(camera_sn: str, payload: dict[str, Any]) -> None:
    control_path = _live_control_path(camera_sn)
    control_path.parent.mkdir(parents=True, exist_ok=True)
    control_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def _normalize_live_quality(value: Any) -> tuple[str, int]:
    text = str(value or "").strip().lower()
    if not text:
        text = "auto"
    if text.isdigit():
        numeric = int(text)
        for label, preset in LIVE_QUALITY_PRESETS.items():
            if preset == numeric:
                return label, preset
        raise ValueError("unsupported quality value")
    if text not in LIVE_QUALITY_PRESETS:
        raise ValueError("unsupported quality label")
    return text, LIVE_QUALITY_PRESETS[text]


def _camera_channel(camera_sn: str, station_sn: str) -> int:
    target_camera = str(camera_sn or "").strip()
    target_station = str(station_sn or "").strip()
    store = get_store()
    payload = store.camera_index_payload()
    for camera in payload.get("cameraList", []) or []:
        if not isinstance(camera, dict):
            continue
        if target_camera and str(camera.get("cameraSn", "") or "").strip() != target_camera:
            continue
        if target_station and str(camera.get("stationSn", "") or "").strip() != target_station:
            continue
        try:
            return int(camera.get("channel", 0) or 0)
        except (TypeError, ValueError):
            return 0
    return 0


def _command_errno(command_result: dict[str, Any]) -> int | None:
    response = command_result.get("responseJson")
    if not isinstance(response, dict):
        return None
    try:
        return int(response.get("errno"))
    except (TypeError, ValueError):
        return None


def _ensure_camera_live_ready(
    *,
    camera_sn: str,
    station_sn: str,
    quality_label: str,
    quality_value: int,
) -> dict[str, Any]:
    channel = _camera_channel(camera_sn, station_sn)
    attempts: list[dict[str, Any]] = []
    for attempt in range(1, LIVE_WAKE_RETRY_COUNT + 1):
        wake_result = _run_station_p2p_command(
            station_sn=station_sn,
            cmd_code=1,
            cmd_json=json.dumps({"channel": channel}, separators=(",", ":")),
        )
        open_result = _run_station_p2p_command(
            station_sn=station_sn,
            cmd_code=3,
            cmd_json=json.dumps({"channel": channel}, separators=(",", ":")),
        )
        quality_result = None
        if quality_label != "auto":
            quality_result = _run_station_p2p_command(
                station_sn=station_sn,
                cmd_code=202,
                cmd_json=json.dumps({"channel": channel, "quality": quality_value}, separators=(",", ":")),
            )

        attempt_payload = {
            "attempt": attempt,
            "channel": channel,
            "wake": wake_result,
            "openVideo": open_result,
        }
        if quality_result is not None:
            attempt_payload["setQuality"] = quality_result
        attempts.append(attempt_payload)

        if wake_result.get("ok") and open_result.get("ok"):
            return {
                "ready": True,
                "channel": channel,
                "attempts": attempts,
            }

        errnos = {
            errno
            for errno in (
                _command_errno(wake_result),
                _command_errno(open_result),
                _command_errno(quality_result or {}),
            )
            if errno is not None
        }
        if errnos and errnos.intersection(LIVE_WAKE_FATAL_ERRNOS):
            break
        if attempt < LIVE_WAKE_RETRY_COUNT:
            time.sleep(LIVE_WAKE_RETRY_DELAY_SECONDS)

    return {
        "ready": False,
        "channel": channel,
        "attempts": attempts,
    }


def _read_pid(pid_path: Path) -> int | None:
    try:
        return int(str(pid_path.read_text(encoding="utf-8")).strip() or "0")
    except Exception:
        return None


def _pid_alive(pid: int | None) -> bool:
    if not pid or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _live_pipeline_running(camera_sn: str) -> bool:
    work_dir = _live_work_dir(camera_sn)
    bridge_pid = _read_pid(work_dir / "bridge.pid")
    ffmpeg_pid = _read_pid(work_dir / "ffmpeg.pid")
    return _pid_alive(bridge_pid) and _pid_alive(ffmpeg_pid)


def _spawn_live_pipeline(
    *,
    camera_sn: str,
    station_sn: str,
    quality_label: str,
    quality_value: int,
    keep_alive: bool | None = None,
    reason: str = "manual",
) -> dict[str, Any]:
    existing = _load_live_control(camera_sn)
    existing_quality = str(existing.get("quality", "auto") or "auto").strip().lower() or "auto"
    existing_station = str(existing.get("stationSn", "") or "").strip()
    if (
        _as_bool(existing.get("active", False))
        and _live_pipeline_running(camera_sn)
        and existing_quality == quality_label
        and (not existing_station or existing_station == station_sn)
    ):
        payload = {
            **existing,
            "cameraSn": camera_sn,
            "stationSn": station_sn,
            "quality": quality_label,
            "qualityValue": quality_value,
            "audioEnabled": False,
            "active": True,
            "keepAlive": _as_bool(existing.get("keepAlive", False)) if keep_alive is None else keep_alive,
            "startReason": str(reason or existing.get("startReason", "manual") or "manual"),
            "reused": True,
            "updatedAt": iso_now(),
        }
        _save_live_control(camera_sn, payload)
        return payload

    subprocess.run(
        [str(LIVE_STOP_SCRIPT)],
        cwd=str(settings.root_dir),
        env={**os.environ.copy(), "CAMERA_SN": camera_sn},
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    env = os.environ.copy()
    env["CAMERA_SN"] = camera_sn
    env["STATION_SN"] = station_sn
    env["QUALITY"] = "" if quality_label == "auto" else str(quality_value)
    env["ENABLE_AUDIO"] = "0"
    env["SKIP_P2P_SETUP"] = "1"
    subprocess.Popen(
        [str(LIVE_START_SCRIPT)],
        cwd=str(settings.root_dir),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    payload = {
        "cameraSn": camera_sn,
        "stationSn": station_sn,
        "quality": quality_label,
        "qualityValue": quality_value,
        "audioEnabled": False,
        "active": True,
        "keepAlive": False if keep_alive is None else keep_alive,
        "startReason": str(reason or "manual"),
        "reused": False,
        "updatedAt": iso_now(),
    }
    _save_live_control(camera_sn, payload)
    return payload


def _stop_live_pipeline(camera_sn: str) -> dict[str, Any]:
    env = os.environ.copy()
    env["CAMERA_SN"] = camera_sn
    subprocess.run(
        [str(LIVE_STOP_SCRIPT)],
        cwd=str(settings.root_dir),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    payload = _load_live_control(camera_sn)
    payload.update(
        {
            "cameraSn": camera_sn,
            "active": False,
            "updatedAt": iso_now(),
        }
    )
    if "quality" not in payload:
        payload["quality"] = "auto"
        payload["qualityValue"] = LIVE_QUALITY_PRESETS["auto"]
    _save_live_control(camera_sn, payload)
    return payload


def _station_ppcs_context(station_sn: str) -> tuple[str, str, str]:
    store = get_store()
    did = store.station_did_payload(station_sn)
    session_payload = store.session_key_payload(station_sn)
    session_key = str((session_payload[0].get("sessionKey", "") if session_payload else "") or "")
    return (
        str(did.get("didCode", "") or ""),
        str(did.get("initCode", did.get("initString", "")) or ""),
        session_key,
    )


def _run_station_p2p_command(
    *,
    station_sn: str,
    cmd_code: int,
    cmd_json: str,
    read_cmd_count: int = 1,
    read_timeout_ms: int = 1800,
) -> dict[str, Any]:
    did_code, init_code, session_key = _station_ppcs_context(station_sn)
    command = [
        str(PPCS_BRIDGE_SCRIPT),
        "--target-id",
        did_code,
        "--init-string",
        init_code,
        "--auth-session-key",
        session_key,
        "--cmd",
        f"{cmd_code}:{cmd_json}",
        "--read-cmd-count",
        str(read_cmd_count),
        "--read-timeout-ms",
        str(read_timeout_ms),
    ]
    timed_out = False
    try:
        result = subprocess.run(
            command,
            cwd=str(settings.root_dir),
            capture_output=True,
            text=True,
            timeout=12,
            check=False,
        )
        stdout_text = result.stdout or ""
        stderr_text = result.stderr or ""
        return_code = result.returncode
    except subprocess.TimeoutExpired as exc:
        timed_out = True
        stdout_raw = exc.stdout or ""
        stderr_raw = exc.stderr or ""
        stdout_text = (
            stdout_raw.decode("utf-8", errors="ignore")
            if isinstance(stdout_raw, bytes)
            else str(stdout_raw)
        )
        stderr_text = (
            stderr_raw.decode("utf-8", errors="ignore")
            if isinstance(stderr_raw, bytes)
            else str(stderr_raw)
        )
        return_code = -124

    parsed_lines: list[dict[str, Any]] = []
    send_event: dict[str, Any] | None = None
    auth_event: dict[str, Any] | None = None
    response_event: dict[str, Any] | None = None
    for raw_line in stdout_text.splitlines():
        line = raw_line.strip()
        if not line or not line.startswith("{"):
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict):
            continue
        parsed_lines.append(payload)
        phase = str(payload.get("phase", "") or "")
        if phase == "send_cmd":
            send_event = payload
        elif phase == "auth_resp":
            auth_event = payload
        elif phase == "read_cmd" and str(payload.get("ret", "")) == "0" and response_event is None:
            response_event = payload

    response_json_raw = str((response_event or {}).get("json", "") or "").strip()
    response_json: dict[str, Any] | None = None
    if response_json_raw:
        try:
            loaded = json.loads(response_json_raw)
        except json.JSONDecodeError:
            loaded = None
        if isinstance(loaded, dict):
            response_json = loaded

    send_ret = int(str((send_event or {}).get("ret", "-999") or "-999"))
    auth_ret = int(str((auth_event or {}).get("ret", "-999") or "-999"))
    response_result = str((response_json or {}).get("result", "") or "").strip().lower()
    response_accepted = response_event is not None and response_result in {"ok", "success", "0"}
    ok = auth_ret == 0 and (send_ret == 0 or response_accepted) and (return_code == 0 or response_accepted)
    return {
        "ok": ok,
        "returnCode": return_code,
        "sendRet": send_ret,
        "authRet": auth_ret,
        "responseCmd": int(str((response_event or {}).get("cmd", "0") or "0")),
        "responseJson": response_json,
        "responseAccepted": response_accepted,
        "timedOut": timed_out,
        "stdoutTail": parsed_lines[-8:],
        "stderr": stderr_text.strip()[-1000:],
    }


@router.api_route("/monitor/live/{camera_sn}/{asset_name}", methods=["GET", "HEAD"])
@router.api_route("/debug/live/{camera_sn}/{asset_name}", methods=["GET", "HEAD"])
def debug_live_hls_asset(camera_sn: str, asset_name: str):
    if Path(asset_name).name != asset_name:
        return Response(status_code=400)

    asset_path = _live_hls_dir(camera_sn) / asset_name
    if not asset_path.is_file():
        return Response(status_code=404)

    if asset_name.endswith(".m3u8"):
        media_type = "application/vnd.apple.mpegurl"
    elif asset_name.endswith(".ts"):
        media_type = "video/mp2t"
    elif asset_name.endswith(".mp4"):
        media_type = "video/mp4"
    else:
        media_type = "application/octet-stream"

    return FileResponse(
        asset_path,
        media_type=media_type,
        headers={
            "Cache-Control": "no-store",
            "X-VAVA-Camera-Sn": camera_sn,
        },
    )


@router.post("/monitor/live/control/start")
@router.post("/debug/live/control/start")
def debug_live_control_start(payload: dict[str, Any] | None = Body(default=None)):
    body = _payload(payload)
    camera_sn = str(body.get("cameraSn") or body.get("deviceSn") or DEFAULT_CAMERA_SN).strip()
    station_sn = str(body.get("stationSn") or body.get("parentDeviceSn") or DEFAULT_STATION_SN).strip()
    if not camera_sn:
        camera_sn = DEFAULT_CAMERA_SN
    if not station_sn:
        station_sn = DEFAULT_STATION_SN

    try:
        quality_label, quality_value = _normalize_live_quality(body.get("quality", "auto"))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    keep_alive_raw = body.get("keepAlive") if "keepAlive" in body else body.get("prewarm")
    keep_alive = _as_bool(keep_alive_raw) if keep_alive_raw is not None else None
    reason = str(body.get("reason", "manual") or "manual").strip() or "manual"
    readiness = _ensure_camera_live_ready(
        camera_sn=camera_sn,
        station_sn=station_sn,
        quality_label=quality_label,
        quality_value=quality_value,
    )
    if not readiness["ready"]:
        raise HTTPException(
            status_code=504,
            detail={
                "cameraSn": camera_sn,
                "stationSn": station_sn,
                "quality": quality_label,
                "readiness": readiness,
            },
        )
    response_payload = _spawn_live_pipeline(
        camera_sn=camera_sn,
        station_sn=station_sn,
        quality_label=quality_label,
        quality_value=quality_value,
        keep_alive=keep_alive,
        reason=reason,
    )
    response_payload["channel"] = int(readiness.get("channel", 0) or 0)
    response_payload["wakeAttempts"] = len(readiness.get("attempts", []) or [])
    response_payload["liveUrl"] = live_url_for_camera(camera_sn)
    return _ok(response_payload)


@router.post("/monitor/live/control/stop")
@router.post("/debug/live/control/stop")
def debug_live_control_stop(payload: dict[str, Any] | None = Body(default=None)):
    body = _payload(payload)
    camera_sn = str(body.get("cameraSn") or body.get("deviceSn") or DEFAULT_CAMERA_SN).strip()
    if not camera_sn:
        camera_sn = DEFAULT_CAMERA_SN
    response_payload = _stop_live_pipeline(camera_sn)
    response_payload["liveUrl"] = live_url_for_camera(camera_sn)
    return _ok(response_payload)


@router.post("/monitor/live/control/buzzer")
@router.post("/debug/live/control/buzzer")
def debug_live_control_buzzer(payload: dict[str, Any] | None = Body(default=None)):
    body = _payload(payload)
    station_sn = str(
        body.get("stationSn")
        or body.get("deviceSn")
        or body.get("parentDeviceSn")
        or DEFAULT_STATION_SN
    ).strip()
    if not station_sn:
        station_sn = DEFAULT_STATION_SN

    action = str(body.get("action", "") or "").strip().lower()
    if action in {"on", "open", "start"}:
        enabled = True
    elif action in {"off", "close", "stop"}:
        enabled = False
    elif "enabled" in body:
        enabled = bool(body.get("enabled"))
    elif "enable" in body:
        enabled = bool(body.get("enable"))
    else:
        enabled = True

    command_result = _run_station_p2p_command(
        station_sn=station_sn,
        cmd_code=13 if enabled else 14,
        cmd_json='{"type":1}' if enabled else "",
    )
    if not command_result["ok"]:
        raise HTTPException(
            status_code=502,
            detail={
                "stationSn": station_sn,
                "enabled": enabled,
                "command": command_result,
            },
        )

    get_store().update_station_status(
        station_sn,
        {"stationStatusObject": {"buzzer": 1 if enabled else 0}},
    )
    return _ok(
        {
            "stationSn": station_sn,
            "enabled": enabled,
            "cmdCode": 13 if enabled else 14,
            "response": command_result["responseJson"] or {},
            "updatedAt": iso_now(),
        }
    )
