from __future__ import annotations

import importlib.util
from functools import lru_cache
from pathlib import Path
from typing import Any

from .config import Settings
from .store_shared import DEFAULT_VISIBLE_NOTICE_TYPE, now_ts


@lru_cache(maxsize=1)
def load_station_helper():
    helper_path = Path(__file__).resolve().parents[1] / "legacy" / "vava_station_ctl.py"
    spec = importlib.util.spec_from_file_location("vava_station_ctl_helper", helper_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def collect_recording_notices(
    settings: Settings,
    state: dict[str, Any],
    station_sn: str,
) -> list[dict[str, Any]]:
    helper = load_station_helper()
    host = str(settings.station_sync_host or "vava-eth").strip() or "vava-eth"
    record_root = f"/mnt/sd0/{station_sn}"
    root_idx = helper.parse_vava_idx_bytes(
        helper.read_ssh_bytes(host, f"{record_root}/vava.idx")
    )
    file_dates = sorted(
        {
            entry.get("date", "")
            for entry in root_idx.get("entries", [])
            if len(str(entry.get("date", ""))) == 8
        },
        reverse=True,
    )
    cameras = [
        (camera_sn, camera)
        for camera_sn, camera in state.get("cameras", {}).items()
        if camera.get("stationSn") == station_sn
    ]
    notices: list[dict[str, Any]] = []
    for file_date in file_dates:
        for camera_sn, camera in cameras:
            idx_path = f"{record_root}/{file_date}/{camera_sn}/vava.idx"
            try:
                record_idx = helper.parse_vava_idx_bytes(
                    helper.read_ssh_bytes(host, idx_path)
                )
            except BaseException:
                continue
            for entry in record_idx.get("entries", []):
                file_name = str(entry.get("filename", "") or "")
                if len(file_name) < 6:
                    continue
                hhmmss = file_name.split("_", 1)[0]
                notices.append(
                    {
                        "deviceSn": camera_sn,
                        "fileDate": file_date,
                        "fileName": file_name,
                        "duration": int(entry.get("duration", 10) or 10),
                        "channel": int(
                            entry.get("channel", camera.get("channel", 0)) or 0
                        ),
                        "deviceTime": f"{file_date}{hhmmss}",
                        "timestamp": str(now_ts()),
                        "fileType": 5 if "_U_" in file_name else DEFAULT_VISIBLE_NOTICE_TYPE,
                    }
                )
    notices.sort(key=lambda item: (item["fileDate"], item["fileName"]), reverse=True)
    return notices
