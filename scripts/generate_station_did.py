#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import shlex
import sys
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.station_did import derive_station_did_storage, render_station_did_payload  # noqa: E402
from app.store import get_store  # noqa: E402
from app.store_shared import DEFAULT_CRC, DEFAULT_DID_TOKEN, DEFAULT_INIT  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate or backfill station DIDs for fake cloud bindings."
    )
    parser.add_argument("--station-sn", help="Target station serial number")
    parser.add_argument("--user-id", default="", help="Bound user id used as part of DID issuance")
    parser.add_argument("--seed", default="", help="Deterministic seed used for DID derivation")
    parser.add_argument("--prefix", default="VAHS", help="Four-character DID prefix")
    parser.add_argument(
        "--token",
        default=DEFAULT_DID_TOKEN,
        help="Token appended to didCode when rendering API payload",
    )
    parser.add_argument(
        "--init-code",
        default=DEFAULT_INIT,
        help="initCode/initString to store with the generated DID",
    )
    parser.add_argument(
        "--crc-key",
        default=DEFAULT_CRC,
        help="crcKey to store with the generated DID",
    )
    parser.add_argument(
        "--format",
        choices=("json", "env"),
        default="json",
        help="Output raw JSON or shell-friendly env suggestions",
    )
    parser.add_argument(
        "--env-style",
        choices=("static", "derived"),
        default="static",
        help="When --format env is used, emit either a fixed JSON override or derived env vars",
    )
    parser.add_argument(
        "--as-map",
        action="store_true",
        help="Wrap the payload as {stationSn: didPayload} for VAVA_DEFAULT_STATION_DID_JSON",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Persist the DID into the fake cloud store for the given station/user binding",
    )
    parser.add_argument(
        "--backfill",
        action="store_true",
        help="Backfill all currently bound stations in the fake cloud store",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Force regenerate DID even if one is already assigned",
    )
    args = parser.parse_args()

    if args.backfill:
        result = get_store().backfill_station_dids(force=args.force)
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return 0

    if not args.station_sn:
        parser.error("--station-sn is required unless --backfill is used")

    if args.apply:
        if not args.user_id:
            parser.error("--user-id is required with --apply")
        result, error = get_store().ensure_station_did(
            station_sn=args.station_sn,
            user_id=args.user_id,
            force=args.force,
        )
        if error:
            print(error, file=sys.stderr)
            return 1
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return 0

    seed_root = "|".join(
        part
        for part in (
            args.seed,
            args.user_id,
            args.station_sn,
        )
        if str(part or "").strip()
    )
    stored = derive_station_did_storage(
        args.station_sn,
        prefix=args.prefix,
        seed=seed_root,
        init_code=args.init_code,
        crc_key=args.crc_key,
    )
    payload = render_station_did_payload(stored, token=args.token)
    json_payload = {args.station_sn: payload} if args.as_map else payload

    if args.format == "json":
        print(json.dumps(json_payload, ensure_ascii=False, indent=2, sort_keys=True))
        return 0

    if args.env_style == "derived":
        print("VAVA_DEFAULT_STATION_DID_MODE=derived")
        print(f"VAVA_DEFAULT_STATION_DID_SEED={shlex.quote(args.seed)}")
        print(f"VAVA_DEFAULT_STATION_DID_PREFIX={shlex.quote(args.prefix)}")
        print(f"VAVA_DEFAULT_STATION_DID_TOKEN={shlex.quote(args.token)}")
        print(f"VAVA_DEFAULT_STATION_INIT_CODE={shlex.quote(args.init_code)}")
        print(f"VAVA_DEFAULT_STATION_CRC_KEY={shlex.quote(args.crc_key)}")
        return 0

    compact = json.dumps(json_payload, ensure_ascii=False, separators=(",", ":"))
    print("VAVA_DEFAULT_STATION_DID_MODE=static")
    print(f"VAVA_DEFAULT_STATION_DID_JSON={shlex.quote(compact)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
