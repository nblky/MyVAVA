#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any


ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.config import get_settings  # noqa: E402
from app.station_did import normalize_station_did_storage, render_station_did_payload  # noqa: E402
from app.store import get_store  # noqa: E402


def _looks_like_debug_prefix(line: str) -> bool:
    text = str(line or "").strip()
    return text.startswith("[D][") or text.startswith("[W][") or text.startswith("[E][") or text.startswith("[I][")


def _looks_valid_p2p_tuple(did: dict[str, str]) -> bool:
    did_code = str(did.get("didCode") or "").strip()
    sy_did = str(did.get("syDid") or "").strip()
    init_value = str(did.get("initString") or did.get("initCode") or "").strip()
    crc_key = str(did.get("crcKey") or "").strip()
    if not did_code or not sy_did or not init_value or not crc_key:
        return False
    if not did_code.startswith("PPCS-") or not sy_did.startswith("PPCS-"):
        return False
    if len(init_value) < 80 or len(init_value) > 200:
        return False
    if not init_value.isalnum():
        return False
    if len(crc_key) > 64 or any(ch.isspace() for ch in crc_key):
        return False
    return True


def _run_debug_strings(host: str, tail_lines: int) -> list[str]:
    remote_cmd = f"strings -n 8 /dev/DebugData | tail -n {int(tail_lines)}"
    cmd = [
        "ssh",
        "-o",
        "BatchMode=yes",
        "-o",
        "ConnectTimeout=5",
        host,
        remote_cmd,
    ]
    proc = subprocess.run(
        cmd,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="ignore",
    )
    if proc.returncode != 0:
        message = (proc.stderr or proc.stdout or "").strip() or "ssh command failed"
        raise RuntimeError(message)
    return proc.stdout.splitlines()


def _try_parse_json(fragment: str) -> dict[str, Any] | None:
    text = str(fragment or "").strip()
    if not text.startswith("{"):
        return None
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        return None
    if not isinstance(value, dict):
        return None
    return value


def _extract_sn_to_did(
    lines: list[str],
    *,
    station_sn_filter: str = "",
) -> dict[str, dict[str, str]]:
    station_filter = str(station_sn_filter or "").strip()
    latest_sn = ""
    pending_callback = ""
    results: dict[str, dict[str, str]] = {}

    def _consume_callback_payload(payload: dict[str, Any]) -> None:
        nonlocal latest_sn
        data = payload.get("data", {})
        if not isinstance(data, dict):
            return
        did = normalize_station_did_storage(data)
        if not did or not _looks_valid_p2p_tuple(did):
            return
        resolved_sn = str(
            data.get("sn")
            or data.get("stationSn")
            or data.get("deviceSn")
            or latest_sn
            or ""
        ).strip()
        if not resolved_sn:
            return
        if station_filter and resolved_sn != station_filter:
            return
        results[resolved_sn] = did

    for raw_line in lines:
        line = str(raw_line or "").strip()
        if not line:
            continue

        if pending_callback:
            if _looks_like_debug_prefix(line):
                pending_callback = ""
            else:
                pending_callback += line
                parsed = _try_parse_json(pending_callback)
                if parsed is not None:
                    _consume_callback_payload(parsed)
                    pending_callback = ""
                elif len(pending_callback) > 5000:
                    pending_callback = ""

        if "VAVASERVER_GetToken" in line and "{" in line:
            payload = _try_parse_json(line[line.find("{") :])
            if isinstance(payload, dict):
                candidate_sn = str(payload.get("sn") or "").strip()
                if candidate_sn:
                    latest_sn = candidate_sn
            continue

        if "token_callback" not in line:
            continue

        if "{" not in line:
            continue
        pending_callback = line[line.find("{") :].strip()
        parsed = _try_parse_json(pending_callback)
        if parsed is not None:
            _consume_callback_payload(parsed)
            pending_callback = ""

    return results


def main() -> int:
    settings = get_settings()
    parser = argparse.ArgumentParser(
        description=(
            "Capture full station P2P DID tuple from device-side /dev/DebugData "
            "and optionally persist as fixed SN mapping."
        )
    )
    parser.add_argument(
        "--host",
        default=str(settings.station_sync_host or "vava-eth").strip() or "vava-eth",
        help="Device SSH host (default: VAVA_STATION_SYNC_HOST)",
    )
    parser.add_argument(
        "--tail-lines",
        type=int,
        default=1200,
        help="How many recent printable log lines to inspect",
    )
    parser.add_argument(
        "--station-sn",
        default="",
        help="Only keep one station SN",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Persist extracted DID tuple(s) into store stationDidMappings",
    )
    args = parser.parse_args()

    lines = _run_debug_strings(args.host, args.tail_lines)
    mappings = _extract_sn_to_did(lines, station_sn_filter=args.station_sn)
    rendered = {
        sn: render_station_did_payload(did)
        for sn, did in sorted(mappings.items(), key=lambda item: item[0])
    }
    output: dict[str, Any] = {
        "host": args.host,
        "tailLines": int(args.tail_lines),
        "count": len(rendered),
        "items": [
            {"stationSn": sn, "did": payload}
            for sn, payload in rendered.items()
        ],
    }

    if args.apply:
        store = get_store()
        applied: list[dict[str, Any]] = []
        errors: list[dict[str, str]] = []
        for station_sn, did_payload in rendered.items():
            result, error = store.upsert_station_did_mapping(
                station_sn=station_sn,
                did=did_payload,
                source=f"device:{args.host}:/dev/DebugData",
            )
            if error:
                errors.append({"stationSn": station_sn, "error": error})
                continue
            applied.append(result or {"stationSn": station_sn})
        output["appliedCount"] = len(applied)
        output["applied"] = applied
        output["errors"] = errors

    print(json.dumps(output, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
