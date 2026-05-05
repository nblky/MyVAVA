from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any

from .config import Settings
from .store_shared import DEFAULT_CRC, DEFAULT_DID, DEFAULT_DID_TOKEN, DEFAULT_INIT, DEFAULT_STATION_SN


STATION_DID_KEYS = ("didCode", "initCode", "crcKey", "syDid", "initString")
PLACEHOLDER_STATION_DID = {
    "didCode": {DEFAULT_DID, f"{DEFAULT_DID},{DEFAULT_DID_TOKEN}"},
    "initCode": {DEFAULT_INIT},
    "crcKey": {DEFAULT_CRC},
    "syDid": {DEFAULT_DID},
    "initString": {DEFAULT_INIT},
}
LEGACY_WORKING_STATIC_DID = {
    "didCode": "PPCS-016834-JGDBD,GIUQFR",
    "initCode": DEFAULT_INIT,
    "crcKey": "EasyView",
    "syDid": "PPCS-016834-JGDBD",
    "initString": DEFAULT_INIT,
}
LEGACY_TEST_STATION_DIDS = (LEGACY_WORKING_STATIC_DID,)


def default_station_did_storage(
    *,
    init_code: str = DEFAULT_INIT,
    crc_key: str = DEFAULT_CRC,
) -> dict[str, str]:
    init_value = str(init_code or DEFAULT_INIT).strip() or DEFAULT_INIT
    crc_value = str(crc_key or DEFAULT_CRC).strip() or DEFAULT_CRC
    return {
        "didCode": DEFAULT_DID,
        "initCode": init_value,
        "crcKey": crc_value,
        "syDid": DEFAULT_DID,
        "initString": init_value,
    }


def static_test_station_did_pool(
    *,
    init_code: str = DEFAULT_INIT,
    crc_key: str = DEFAULT_CRC,
) -> list[dict[str, str]]:
    init_value = str(init_code or DEFAULT_INIT).strip() or DEFAULT_INIT
    crc_override = str(crc_key or DEFAULT_CRC).strip() or DEFAULT_CRC
    use_legacy_crc = crc_override == DEFAULT_CRC
    pool: list[dict[str, str]] = []
    seen_sy_dids: set[str] = set()
    for raw in LEGACY_TEST_STATION_DIDS:
        normalized = normalize_station_did_storage(raw)
        if not normalized:
            continue
        candidate = dict(normalized)
        candidate["initCode"] = init_value
        candidate["initString"] = init_value
        if not use_legacy_crc:
            candidate["crcKey"] = crc_override
        sy_did = str(candidate.get("syDid") or candidate.get("didCode") or "").split(",", 1)[0].strip()
        if not sy_did or sy_did in seen_sy_dids:
            continue
        seen_sy_dids.add(sy_did)
        pool.append(candidate)
    return pool


def _normalize_prefix(prefix: str | None) -> str:
    value = "".join(ch for ch in str(prefix or "").upper() if ch.isalnum())
    if not value or not ("A" <= value[0] <= "Z"):
        value = "PPCS"
    return value[:8]


def _normalize_derived_token(token: str | None) -> str:
    value = "".join(ch for ch in str(token or "").upper() if ch.isalnum())
    if not value:
        return ""
    # Treat the old local placeholder token as "unset" so generated DIDs
    # default to production-like 6-char auth fragments.
    if value == str(DEFAULT_DID_TOKEN).upper():
        return ""
    return value[:16]


def _digest_hex(seed_text: str, length: int) -> str:
    out = ""
    counter = 0
    while len(out) < length:
        payload = f"{seed_text}:{counter}".encode("utf-8")
        out += hashlib.sha256(payload).hexdigest().upper()
        counter += 1
    return out[:length]


def _letters_from_hex(chunk: str, length: int) -> str:
    letters: list[str] = []
    for idx in range(length):
        start = idx * 2
        piece = chunk[start : start + 2]
        if len(piece) < 2:
            piece = (piece + "00")[:2]
        letters.append(chr(ord("A") + (int(piece, 16) % 26)))
    return "".join(letters)


def derive_station_did_storage(
    station_sn: str,
    *,
    prefix: str = "PPCS",
    seed: str = "",
    token: str = "",
    init_code: str = DEFAULT_INIT,
    crc_key: str = DEFAULT_CRC,
) -> dict[str, str]:
    station_key = str(station_sn or "").strip() or DEFAULT_DID.replace("-", "")
    digest = _digest_hex(f"{seed}|{station_key}", 40)
    prefix_value = _normalize_prefix(prefix)
    number = f"{int(digest[0:8], 16) % 1_000_000:06d}"
    suffix = _letters_from_hex(digest[8:18], 5)
    token_value = _normalize_derived_token(token) or _letters_from_hex(digest[18:30], 6)
    sy_did = f"{prefix_value}-{number}-{suffix}"
    did_code = f"{sy_did},{token_value}" if token_value else sy_did
    init_value = str(init_code or DEFAULT_INIT).strip() or DEFAULT_INIT
    crc_value = str(crc_key or DEFAULT_CRC).strip() or DEFAULT_CRC
    return {
        "didCode": did_code,
        "initCode": init_value,
        "crcKey": crc_value,
        "syDid": sy_did,
        "initString": init_value,
    }


def normalize_station_did_storage(raw: Any) -> dict[str, str]:
    candidates: list[Mapping[str, Any]] = []
    if isinstance(raw, Mapping):
        candidates.append(raw)
        for key in ("did", "didObject", "stationDid", "stationDidObject", "ppcsDid", "ppcsDidObject"):
            nested = raw.get(key)
            if isinstance(nested, Mapping):
                candidates.insert(0, nested)
    for candidate in candidates:
        did_code = str(candidate.get("didCode") or "").strip()
        sy_did = str(candidate.get("syDid") or "").strip()
        if did_code and not sy_did:
            sy_did = did_code.split(",", 1)[0].strip()
        if sy_did and not did_code:
            did_code = sy_did
        init_code = str(candidate.get("initCode") or candidate.get("initString") or "").strip()
        init_string = str(candidate.get("initString") or init_code or "").strip()
        crc_key = str(candidate.get("crcKey") or "").strip()
        if not any((did_code, sy_did, init_code, init_string, crc_key)):
            continue
        return {
            "didCode": did_code or sy_did or DEFAULT_DID,
            "initCode": init_code or DEFAULT_INIT,
            "crcKey": crc_key or DEFAULT_CRC,
            "syDid": sy_did or did_code.split(",", 1)[0].strip() or DEFAULT_DID,
            "initString": init_string or init_code or DEFAULT_INIT,
        }
    return {}


def render_station_did_payload(
    raw: Any,
    *,
    token: str = DEFAULT_DID_TOKEN,
) -> dict[str, str]:
    normalized = normalize_station_did_storage(raw) or default_station_did_storage()
    sy_did = str(normalized.get("syDid") or normalized.get("didCode") or DEFAULT_DID).split(",", 1)[0].strip()
    did_code = str(normalized.get("didCode") or sy_did or DEFAULT_DID).strip()
    token_value = str(token or DEFAULT_DID_TOKEN).strip()
    if did_code and "," not in did_code and token_value:
        did_code = f"{did_code},{token_value}"
    init_code = str(normalized.get("initCode") or normalized.get("initString") or DEFAULT_INIT).strip()
    return {
        "didCode": did_code,
        "initCode": init_code or DEFAULT_INIT,
        "crcKey": str(normalized.get("crcKey") or DEFAULT_CRC).strip() or DEFAULT_CRC,
        "syDid": sy_did or DEFAULT_DID,
        "initString": str(normalized.get("initString") or init_code or DEFAULT_INIT).strip() or DEFAULT_INIT,
    }


def station_did_json_override(
    settings: Settings,
    station_sn: str,
) -> dict[str, str]:
    raw = str(settings.default_station_did_json or "").strip()
    if not raw:
        return {}
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    if isinstance(payload, Mapping):
        if any(key in payload for key in STATION_DID_KEYS):
            return normalize_station_did_storage(payload)
        candidate = payload.get(str(station_sn or "").strip())
        if isinstance(candidate, Mapping):
            return normalize_station_did_storage(candidate)
    return {}


def configured_station_did(settings: Settings, station_sn: str) -> dict[str, str]:
    override = station_did_json_override(settings, station_sn)
    if override:
        return override
    mode = str(settings.default_station_did_mode or "static").strip().lower()
    init_code = str(settings.default_station_init_code or DEFAULT_INIT).strip() or DEFAULT_INIT
    crc_key = str(settings.default_station_crc_key or DEFAULT_CRC).strip() or DEFAULT_CRC
    if mode in {"derived", "deterministic", "generated"}:
        return derive_station_did_storage(
            station_sn,
            prefix=settings.default_station_did_prefix,
            seed=settings.default_station_did_seed,
            token=settings.default_station_did_token,
            init_code=init_code,
            crc_key=crc_key,
        )
    # Keep static mode compatible with the PPCS DID shape that existing app/base
    # clients have already verified in production captures. For additional
    # stations, issue deterministic per-station PPCS IDs to avoid DID
    # collisions when multiple base stations are online at the same time.
    station_value = str(station_sn or "").strip()
    if station_value and station_value != DEFAULT_STATION_SN:
        legacy_token = str(LEGACY_WORKING_STATIC_DID["didCode"]).split(",", 1)
        static_token = legacy_token[1].strip() if len(legacy_token) > 1 else ""
        static_crc = (
            LEGACY_WORKING_STATIC_DID["crcKey"]
            if crc_key == DEFAULT_CRC
            else crc_key
        )
        return derive_station_did_storage(
            station_value,
            prefix=settings.default_station_did_prefix,
            seed=f"static|{station_value}",
            token=static_token,
            init_code=init_code,
            crc_key=static_crc,
        )
    static_did = normalize_station_did_storage(LEGACY_WORKING_STATIC_DID)
    static_did["initCode"] = init_code
    static_did["initString"] = init_code
    static_did["crcKey"] = (
        LEGACY_WORKING_STATIC_DID["crcKey"]
        if crc_key == DEFAULT_CRC
        else crc_key
    )
    return static_did


def is_placeholder_station_did_value(key: str, value: Any) -> bool:
    raw = str(value or "").strip()
    if not raw:
        return True
    return raw in PLACEHOLDER_STATION_DID.get(key, set())
