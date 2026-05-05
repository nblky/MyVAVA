#!/usr/bin/env python3
import argparse
import hashlib
import json
import shlex
import socket
import struct
import subprocess
import sys
import time
from pathlib import Path


DEFAULT_HOST = "vava-eth"
DEFAULT_REMOTE_RECORD_ROOT = "/mnt/sd0/64XI7DE3Q2115F3BBF02F9A80"
REMOTE_PAIR_PID_FILE = "/tmp/force_pair.pid"
REMOTE_PAIR_LOG = "/tmp/force_pair.log"
PAIR_FLAG_ADDR = 0x73FC8E
PAIR_FLAG_WRITE_ADDR = 0x73FC8F
SSH_FALLBACK_HOSTS = {
    "vava-eth": ["vava-eth-199"],
    "vava-eth-199": ["vava-eth"],
}
NATIVE_RECORD_SYMBOLS = {
    "vava_hs003_read_packet": 0x3CA9C,
    "vava_hs003_seek_record": 0x3D5BC,
    "vava_hs003_open_record": 0x3D7EC,
    "vava_hs004_read_packet": 0x3C7C8,
    "vava_hs004_seek_record": 0x3D348,
    "vava_hs004_open_record": 0x3DA58,
    "vava_hs_open_record": 0x3E434,
    "vava_hs_read_packet": 0x3E69C,
    "vava_hs_parser_sequence_hdr": 0x3E95C,
    "vava_hs_close_record": 0x3ED38,
    "vava_hs_create_mux_record": 0x3F054,
    "vava_hs_write_mux_record": 0x3F4DC,
}


def run_local(cmd, *, check=True, capture=False):
    return subprocess.run(
        cmd,
        check=check,
        text=True,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.STDOUT if capture else None,
    )


def run_local_bytes(cmd, *, check=True):
    return subprocess.run(
        cmd,
        check=check,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def pick_local_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def iter_ssh_hosts(host):
    seen = set()
    for candidate in [host] + SSH_FALLBACK_HOSTS.get(host, []):
        if candidate in seen:
            continue
        seen.add(candidate)
        yield candidate


def run_ssh(host, remote_cmd, *, capture=False, check=True):
    last_result = None
    for candidate in iter_ssh_hosts(host):
        result = run_local(
            ["ssh", candidate, remote_cmd],
            capture=capture,
            check=False,
        )
        if result.returncode == 0:
            return result
        last_result = result
    if last_result is not None and check:
        raise subprocess.CalledProcessError(
            last_result.returncode,
            last_result.args,
            output=last_result.stdout,
        )
    if last_result is not None:
        return last_result
    raise RuntimeError("No SSH host candidates available")


def read_ssh_bytes(host, remote_path):
    quoted = shlex.quote(remote_path)
    last_proc = None
    for candidate in iter_ssh_hosts(host):
        proc = run_local_bytes(["ssh", candidate, f"cat {quoted}"], check=False)
        if proc.returncode == 0:
            return proc.stdout
        last_proc = proc
    if last_proc is not None:
        sys.stderr.write(last_proc.stderr.decode("utf-8", "replace"))
        raise SystemExit(last_proc.returncode)
    raise SystemExit("No SSH host candidates available")


def start_ssh_forward(host, remote_port):
    local_port = pick_local_port()
    last_error = None
    for candidate in iter_ssh_hosts(host):
        proc = subprocess.Popen(
            [
                "ssh",
                "-o",
                "BatchMode=yes",
                "-o",
                "ExitOnForwardFailure=yes",
                "-L",
                f"{local_port}:172.26.168.1:{int(remote_port)}",
                candidate,
                "-N",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )
        deadline = time.time() + 8
        while time.time() < deadline:
            if proc.poll() is not None:
                last_error = (candidate, proc.stderr.read())
                break
            try:
                with socket.create_connection(("127.0.0.1", local_port), timeout=0.3):
                    return candidate, local_port, proc
            except OSError:
                time.sleep(0.2)
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
        if last_error is None:
            last_error = (candidate, "forward did not become ready")
    if last_error is None:
        raise RuntimeError("No SSH host candidates available")
    candidate, message = last_error
    raise RuntimeError(f"SSH forward via {candidate} failed: {message}")


def stop_ssh_forward(proc):
    if proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=3)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()


def shell_join(lines):
    return "set -e; " + "; ".join(lines)


def shell_script(lines):
    return "set -e\n" + "\n".join(lines)


def run_ssh_detached(host, remote_script, *, remote_log):
    remote_cmd = (
        "trap '' HUP INT TERM; "
        f"/bin/sh -c {shlex.quote(remote_script)} "
        f">{shlex.quote(remote_log)} 2>&1 </dev/null &"
    )
    return run_ssh(host, remote_cmd, check=False)


def status_snapshot(record_root):
    return shell_join(
        [
            "echo '--- pair ap ---'",
            "echo SSID:$(uci get wireless.ra1.ssid 2>/dev/null || echo unknown)",
            "echo PASS:$(uci get wireless.ra1.key 2>/dev/null || echo unknown)",
            "echo FLAG:$(cat /etc_ro/ssidflag 2>/dev/null || echo none)",
            "echo '--- ps ---'",
            "ps | grep -E 'Ppcs_vava|Ppcs_tfupdate|Vava_NetCheck|vava-softdog' || true",
            "echo '--- wan ---'",
            "ifconfig eth0.1 2>/dev/null | sed -n '1,5p' || true",
            "echo '--- recordings ---'",
            f"find {shlex.quote(record_root)} -type f ! -name vava.idx 2>/dev/null | tail -n 20 || true",
            "echo '--- log ---'",
            "tail -n 80 /tmp/ppcs_baseline.log 2>/dev/null || "
            "tail -n 80 /tmp/ppcs_pair.log 2>/dev/null || "
            "tail -n 80 /tmp/ppcs_local.log 2>/dev/null || "
            "tail -n 80 /tmp/ppcs_test.log 2>/dev/null || true",
        ]
    )


def cmd_status(args):
    remote = shell_join(
        [
            "echo '--- sd ---'",
            "df -Th /mnt/sd0 2>/dev/null || true",
            status_snapshot(args.record_root),
        ]
    )
    run_ssh(args.host, remote)


def cmd_start_local(args):
    steps = [
        "killall Ppcs_vava 2>/dev/null || true",
        "killall Ppcs_tfupdate 2>/dev/null || true",
        "killall Vava_NetCheck 2>/dev/null || true",
        "/usr/sbin/vava-dogctrl -e 0 >/dev/null 2>&1 || true",
    ]
    if args.pulse_netcheck:
        steps.extend(
            [
                "/usr/sbin/Vava_NetCheck >/tmp/vava_netcheck_pulse.log 2>&1 &",
                "NETCHECK_PID=$!",
                "sleep 1",
                "kill $NETCHECK_PID 2>/dev/null || true",
            ]
        )
    steps.extend(["/usr/sbin/Ppcs_vava >/tmp/ppcs_local.log 2>&1 &"])
    if args.with_tfupdate:
        steps.extend(
            [
                "/usr/sbin/Ppcs_tfupdate >/tmp/ppcs_tfupdate.log 2>&1 &",
            ]
        )
    run_ssh_detached(
        args.host,
        shell_script(steps),
        remote_log="/tmp/vava_start_local_bootstrap.log",
    )
    time.sleep(6)
    run_ssh(args.host, status_snapshot(args.record_root), check=False)


def cmd_start_baseline(args):
    steps = [
        f"kill $(cat {shlex.quote(REMOTE_PAIR_PID_FILE)} 2>/dev/null) 2>/dev/null || true",
        f"rm -f {shlex.quote(REMOTE_PAIR_PID_FILE)}",
        "killall Ppcs_vava 2>/dev/null || true",
        "killall Ppcs_tfupdate 2>/dev/null || true",
        "killall Vava_NetCheck 2>/dev/null || true",
        "killall Ppcs_factory 2>/dev/null || true",
        "killall vava-softdog 2>/dev/null || true",
        "/usr/sbin/vava-dogctrl -e 0 >/dev/null 2>&1 || true",
        "rm -f /tmp/ssidbuild.log /tmp/ppcs_pair.log /tmp/ppcs_local.log "
        "/tmp/ppcs_baseline.log /tmp/vava_netcheck_baseline.log /tmp/vava_netcheck_pair.log",
        "/usr/sbin/Ppcs_ssidbuild >/tmp/ssidbuild.log 2>&1 || true",
        "ifconfig ra0 up 2>/dev/null || true",
        "ifconfig ra1 up 2>/dev/null || true",
        "ifconfig wds1 up 2>/dev/null || true",
        "sleep 2",
        "/usr/sbin/Vava_NetCheck >/tmp/vava_netcheck_baseline.log 2>&1 &",
        "sleep 5",
        "/usr/sbin/Ppcs_vava >/tmp/ppcs_baseline.log 2>&1 &",
    ]
    if args.with_tfupdate:
        steps.extend(
            [
                "sleep 1",
                "/usr/sbin/Ppcs_tfupdate >/tmp/ppcs_tfupdate.log 2>&1 &",
            ]
        )
    run_ssh_detached(
        args.host,
        shell_script(steps),
        remote_log="/tmp/vava_start_baseline_bootstrap.log",
    )
    time.sleep(6)
    run_ssh(args.host, status_snapshot(args.record_root), check=False)


def cmd_stop(args):
    remote = shell_join(
        [
            "killall Ppcs_vava 2>/dev/null || true",
            "killall Ppcs_tfupdate 2>/dev/null || true",
            "killall Vava_NetCheck 2>/dev/null || true",
            "/usr/sbin/vava-dogctrl -e 0 >/dev/null 2>&1 || true",
            "echo stopped",
            "ps | grep -E 'Ppcs_vava|Ppcs_tfupdate|Vava_NetCheck|vava-softdog' || true",
        ]
    )
    run_ssh(args.host, remote)


def cmd_pair_ap(args):
    remote = shell_script(
        [
            "rm -f /tmp/ssidbuild.log",
            "/usr/sbin/vava-dogctrl -e 0 >/dev/null 2>&1 || true",
            "/usr/sbin/Ppcs_ssidbuild >/tmp/ssidbuild.log 2>&1 || true",
            "ifconfig ra1 up 2>/dev/null || true",
            "ifconfig wds1 up 2>/dev/null || true",
            "sleep 2",
            "echo '--- pair ap ---'",
            "echo SSID:$(uci get wireless.ra1.ssid 2>/dev/null || echo unknown)",
            "echo PASS:$(uci get wireless.ra1.key 2>/dev/null || echo unknown)",
            "echo FLAG:$(cat /etc_ro/ssidflag 2>/dev/null || echo none)",
            "echo '--- log ---'",
            "cat /tmp/ssidbuild.log 2>/dev/null || true",
        ]
    )
    run_ssh(args.host, remote)


def cmd_pair_window(args):
    steps = [
        "killall Ppcs_vava 2>/dev/null || true",
        "killall Ppcs_tfupdate 2>/dev/null || true",
        "killall Vava_NetCheck 2>/dev/null || true",
        "/usr/sbin/vava-dogctrl -e 0 >/dev/null 2>&1 || true",
        "rm -f /tmp/ssidbuild.log /tmp/ppcs_pair.log /tmp/vava_netcheck_pair.log /tmp/ppcs_tfupdate.log",
        "/usr/sbin/Ppcs_ssidbuild >/tmp/ssidbuild.log 2>&1 || true",
        "ifconfig ra1 up 2>/dev/null || true",
        "ifconfig wds1 up 2>/dev/null || true",
        "/usr/sbin/Vava_NetCheck >/tmp/vava_netcheck_pair.log 2>&1 &",
        "sleep 1",
    ]
    if args.with_tfupdate:
        steps.extend(
            [
                "/usr/sbin/Ppcs_tfupdate >/tmp/ppcs_tfupdate.log 2>&1 &",
                "sleep 1",
            ]
        )
    steps.extend(["/usr/sbin/Ppcs_vava >/tmp/ppcs_pair.log 2>&1 &"])
    run_ssh_detached(
        args.host,
        shell_script(steps),
        remote_log="/tmp/vava_pair_bootstrap.log",
    )
    time.sleep(8)
    run_ssh(args.host, status_snapshot(args.record_root), check=False)


def pair_force_status_snapshot():
    return shell_join(
        [
            "set -- $(pidof Ppcs_vava 2>/dev/null || true)",
            "PID=$1",
            "echo '--- pair pid ---'",
            "echo PID:${PID:-none}",
            "echo '--- pair bytes ---'",
            "if [ -n \"$PID\" ]; then dd if=/proc/$PID/mem bs=1 skip=$((0x73fc8e)) count=8 2>/dev/null | hexdump -Cv; else echo no-ppcs; fi",
            "echo '--- pair state ---'",
            "grep -n 'pair = .*bsadd' /tmp/ppcs_pair.log 2>/dev/null | tail -n 10 || true",
            "echo '--- force loop ---'",
            f"cat {shlex.quote(REMOTE_PAIR_PID_FILE)} 2>/dev/null || echo none",
            f"tail -n 40 {shlex.quote(REMOTE_PAIR_LOG)} 2>/dev/null || true",
            "echo '--- pair ap ---'",
            "ifconfig ra1 2>/dev/null | sed -n '1,6p' || true",
            "iwconfig ra1 2>/dev/null || true",
        ]
    )


def cmd_pair_force_status(args):
    run_ssh(args.host, pair_force_status_snapshot(), check=False)


def cmd_pair_force_start(args):
    remote_script = shell_script(
        [
            f"kill $(cat {shlex.quote(REMOTE_PAIR_PID_FILE)} 2>/dev/null) 2>/dev/null || true",
            f"rm -f {shlex.quote(REMOTE_PAIR_PID_FILE)}",
            "(",
            "i=0",
            "ifconfig ra1 up 2>/dev/null || true",
            "ifconfig wds1 up 2>/dev/null || true",
            f"while [ $i -lt {int(args.seconds)} ]; do",
            "  if ! ifconfig ra1 2>/dev/null | grep -q ' UP '; then",
            "    ifconfig ra1 up 2>/dev/null || true",
            "  fi",
            "  if ! ifconfig wds1 2>/dev/null | grep -q ' UP '; then",
            "    ifconfig wds1 up 2>/dev/null || true",
            "  fi",
            "  set -- $(pidof Ppcs_vava 2>/dev/null || true)",
            "  PID=$1",
            "  if [ -z \"$PID\" ]; then",
            "    /usr/sbin/Ppcs_vava >>/tmp/ppcs_pair.log 2>&1 &",
            "    sleep 1",
            "    set -- $(pidof Ppcs_vava 2>/dev/null || true)",
            "    PID=$1",
            "  fi",
            "  if [ -n \"$PID\" ]; then",
            f"    printf '\\x01\\x05\\x01' | dd of=/proc/$PID/mem bs=1 seek=$(({PAIR_FLAG_WRITE_ADDR:#x})) conv=notrunc 2>/dev/null",
            "  fi",
            "  i=$((i+1))",
            "  sleep 1",
            "done",
            ") &",
            f"echo $! > {shlex.quote(REMOTE_PAIR_PID_FILE)}",
            f"wait $(cat {shlex.quote(REMOTE_PAIR_PID_FILE)})",
        ]
    )
    run_ssh_detached(args.host, remote_script, remote_log=REMOTE_PAIR_LOG)
    time.sleep(2)
    run_ssh(
        args.host,
        shell_join(
            [
                f"echo started:$(cat {shlex.quote(REMOTE_PAIR_PID_FILE)} 2>/dev/null || echo none)",
                "grep -n 'pair = .*bsadd' /tmp/ppcs_pair.log 2>/dev/null | tail -n 3 || true",
            ]
        ),
        check=False,
    )


def cmd_pair_force_stop(args):
    remote = shell_join(
        [
            f"kill $(cat {shlex.quote(REMOTE_PAIR_PID_FILE)} 2>/dev/null) 2>/dev/null || true",
            f"rm -f {shlex.quote(REMOTE_PAIR_PID_FILE)}",
            "echo stopped",
            "grep -n 'pair = .*bsadd' /tmp/ppcs_pair.log 2>/dev/null | tail -n 3 || true",
        ]
    )
    run_ssh(args.host, remote, check=False)


def cmd_logs(args):
    log_name = shlex.quote(args.remote_log)
    remote = f"tail -n {int(args.lines)} {log_name} 2>/dev/null || true"
    run_ssh(args.host, remote)


def cmd_list_recordings(args):
    remote = (
        f"find {shlex.quote(args.record_root)} -type f ! -name vava.idx 2>/dev/null | sort"
    )
    run_ssh(args.host, remote)


def find_latest_recording_path(host, record_root, camera_sn=None, day=None):
    clauses = [f"find {shlex.quote(record_root)} -type f ! -name vava.idx"]
    if camera_sn:
        clauses.append(f"| grep {shlex.quote('/' + camera_sn + '/')}")
    if day:
        clauses.append(f"| grep {shlex.quote('/' + day + '/')}")
    clauses.append("| sort | tail -n 1")
    result = run_ssh(host, " ".join(clauses), capture=True)
    latest = (result.stdout or "").strip()
    if not latest:
        filters = []
        if camera_sn:
            filters.append(f"camera={camera_sn}")
        if day:
            filters.append(f"day={day}")
        suffix = f" ({', '.join(filters)})" if filters else ""
        raise SystemExit(f"No recording found under {record_root}{suffix}")
    return latest


def cmd_latest_recording(args):
    print(
        find_latest_recording_path(
            args.host,
            args.record_root,
            camera_sn=args.camera_sn,
            day=args.day,
        )
    )


def cmd_pull(args):
    remote_path = args.remote_path
    local_path = Path(args.local_path).expanduser().resolve()
    local_path.parent.mkdir(parents=True, exist_ok=True)
    quoted = shlex.quote(remote_path)
    last_proc = None
    for candidate in iter_ssh_hosts(args.host):
        with local_path.open("wb") as fh:
            proc = subprocess.run(
                ["ssh", candidate, f"cat {quoted}"],
                stdout=fh,
                stderr=subprocess.PIPE,
            )
        if proc.returncode == 0:
            print(local_path)
            return
        last_proc = proc
    if last_proc is not None:
        sys.stderr.write(last_proc.stderr.decode("utf-8", "replace"))
        raise SystemExit(last_proc.returncode)
    raise SystemExit("No SSH host candidates available")


def cmd_pull_latest(args):
    remote_path = find_latest_recording_path(
        args.host,
        args.record_root,
        camera_sn=args.camera_sn,
        day=args.day,
    )
    local_path = Path(args.local_path).expanduser().resolve()
    local_path.parent.mkdir(parents=True, exist_ok=True)
    pull_args = argparse.Namespace(
        host=args.host,
        remote_path=remote_path,
        local_path=str(local_path),
    )
    cmd_pull(pull_args)
    print(f"remote_source={remote_path}")


def cmd_led(args):
    mode_map = {
        "white-solid": ["echo 0 > /dev/power_led", "echo 1 > /dev/wlink_led"],
        "red-solid": ["echo 1 > /dev/power_led", "echo 0 > /dev/wlink_led"],
        "off": ["echo 0 > /dev/power_led", "echo 0 > /dev/wlink_led"],
    }
    remote = shell_join(mode_map[args.mode] + ["echo led:" + shlex.quote(args.mode)])
    run_ssh(args.host, remote)


def _decode_xor_name(block, key):
    try:
        return bytes(b ^ key for b in block).decode("ascii")
    except UnicodeDecodeError:
        return None


def parse_vava_idx_bytes(data):
    if len(data) < 12:
        raise ValueError("idx file too small")
    magic = data[:4]
    count = int.from_bytes(data[4:8], "little")
    reserved = int.from_bytes(data[8:12], "little")
    body = data[12:]
    result = {
        "magic_hex": magic.hex(),
        "count": count,
        "reserved": reserved,
        "entry_size": None,
        "type": "unknown",
        "entries": [],
    }
    if count == 0:
        result["entry_size"] = 0
        return result
    if len(body) % count != 0:
        raise ValueError(f"unexpected idx payload length {len(body)} for count {count}")
    entry_size = len(body) // count
    result["entry_size"] = entry_size
    if entry_size == 10:
        result["type"] = "date_index"
        for index in range(count):
            entry = body[index * entry_size : (index + 1) * entry_size]
            key = (entry[0] - 26) & 0xFF
            value = _decode_xor_name(entry[1:9], key)
            result["entries"].append(
                {
                    "index": index,
                    "key": key,
                    "date": value,
                    "raw_hex": entry.hex(),
                }
            )
        return result
    if entry_size == 16:
        result["type"] = "record_index"
        for index in range(count):
            entry = body[index * entry_size : (index + 1) * entry_size]
            key = (entry[0] - 26) & 0xFF
            filename = _decode_xor_name(entry[3:13], key)
            result["entries"].append(
                {
                    "index": index,
                    "key": key,
                    "meta0": entry[1],
                    "channel": entry[2],
                    "filename": filename,
                    "marker": entry[13],
                    "duration": int.from_bytes(entry[14:16], "little"),
                    "raw_hex": entry.hex(),
                }
            )
        return result
    for index in range(count):
        entry = body[index * entry_size : (index + 1) * entry_size]
        result["entries"].append({"index": index, "raw_hex": entry.hex()})
    return result


def parse_vava_record_bytes(data):
    if len(data) < 16:
        raise ValueError("record file too small")
    file_flag0, file_flag1 = struct.unpack_from("<HH", data, 0)
    outer0, outer1, duration = struct.unpack_from("<III", data, 4)
    native_header = {
        "byte0": data[0],
        "video_codec_id": data[1],
        "audio_codec_id_raw": data[2],
        "audio_codec_id_normalized": data[2] or 3,
        "byte3": data[3],
        "mode_byte": data[5],
        "u16_6_7": int.from_bytes(data[6:8], "little"),
        "u32_8_11": int.from_bytes(data[8:12], "little"),
        "u32_12_15": int.from_bytes(data[12:16], "little"),
        "sequence_parser_path": "mode_nonzero" if data[5] else "mode_zero",
    }
    offset = 16
    records = []
    while offset + 24 <= len(data):
        tag, size, field0, field1, ts0, ts1 = struct.unpack_from("<IIIIII", data, offset)
        if tag != 0xEB0000AA:
            raise ValueError(f"unexpected record tag {tag:#x} at offset {offset}")
        payload_offset = offset + 24
        payload_end = payload_offset + size
        if payload_end > len(data):
            raise ValueError(
                f"record payload overruns file at offset {offset}: {size} > {len(data) - payload_offset}"
            )
        payload = data[payload_offset:payload_end]
        if field0 == 0 and payload.startswith(b"\x00\x00\x00\x01"):
            chunk_type = "video_nalu"
        elif field0 == 8 and field1 == 8:
            chunk_type = "audio_chunk"
        elif field0 == 1:
            chunk_type = "video_key_blob"
        else:
            chunk_type = "unknown"
        records.append(
            {
                "offset": offset,
                "tag_hex": f"{tag:08x}",
                "size": size,
                "field0": field0,
                "field1": field1,
                "ts0": ts0,
                "ts1": ts1,
                "pts_ms": (ts0 * 1000) + ts1,
                "payload_prefix_hex": payload[:16].hex(),
                "payload_starts_with_annexb": payload.startswith(b"\x00\x00\x00\x01"),
                "chunk_type": chunk_type,
            }
        )
        offset = payload_end
    return {
        "file_flag0": file_flag0,
        "file_flag1": file_flag1,
        "outer0": outer0,
        "outer1": outer1,
        "duration": duration,
        "filesize": len(data),
        "native_header": native_header,
        "native_symbols": dict(NATIVE_RECORD_SYMBOLS),
        "record_count": len(records),
        "records": records,
    }


def format_vava_idx_summary(parsed, source):
    lines = [
        f"source: {source}",
        f"type: {parsed['type']}",
        f"magic: {parsed['magic_hex']}",
        f"count: {parsed['count']}",
        f"entry_size: {parsed['entry_size']}",
    ]
    if parsed["type"] == "date_index":
        for entry in parsed["entries"]:
            lines.append(f"date[{entry['index']}]: {entry['date']}")
        return "\n".join(lines)
    if parsed["type"] == "record_index":
        channels = sorted({entry["channel"] for entry in parsed["entries"]})
        durations = sorted({entry["duration"] for entry in parsed["entries"]})
        lines.append(f"channels: {','.join(str(item) for item in channels)}")
        lines.append(f"durations: {','.join(str(item) for item in durations)}")
        for entry in parsed["entries"]:
            lines.append(
                f"record[{entry['index']}]: ch={entry['channel']} dur={entry['duration']} file={entry['filename']}"
            )
        return "\n".join(lines)
    for entry in parsed["entries"]:
        lines.append(f"entry[{entry['index']}]: {entry['raw_hex']}")
    return "\n".join(lines)


def format_vava_record_summary(parsed, source):
    type_counts = {}
    for record in parsed["records"]:
        type_counts[record["chunk_type"]] = type_counts.get(record["chunk_type"], 0) + 1
    native_header = parsed["native_header"]
    lines = [
        f"source: {source}",
        f"file_flag0: {parsed['file_flag0']}",
        f"file_flag1: {parsed['file_flag1']}",
        f"outer0: {parsed['outer0']}",
        f"outer1: {parsed['outer1']}",
        f"duration: {parsed['duration']}",
        f"filesize: {parsed['filesize']}",
        (
            "native_header: byte0={byte0} vcodec={video_codec_id} "
            "acodec_raw={audio_codec_id_raw} acodec={audio_codec_id_normalized} "
            "byte3={byte3} mode_byte={mode_byte} u16_6_7={u16_6_7} "
            "u32_8_11={u32_8_11} u32_12_15={u32_12_15} path={sequence_parser_path}"
        ).format(**native_header),
        f"record_count: {parsed['record_count']}",
        "chunk_types: "
        + ", ".join(f"{key}={value}" for key, value in sorted(type_counts.items())),
    ]
    for index, record in enumerate(parsed["records"][:20]):
        lines.append(
            "record[{idx}]: off={off} type={chunk_type} size={size} "
            "field0={field0} field1={field1} ts0={ts0} ts1={ts1} pts_ms={pts_ms} prefix={prefix}".format(
                idx=index,
                off=record["offset"],
                chunk_type=record["chunk_type"],
                size=record["size"],
                field0=record["field0"],
                field1=record["field1"],
                ts0=record["ts0"],
                ts1=record["ts1"],
                pts_ms=record["pts_ms"],
                prefix=record["payload_prefix_hex"],
            )
        )
    if len(parsed["records"]) > 20:
        lines.append(f"... truncated {len(parsed['records']) - 20} more records")
    return "\n".join(lines)


def load_record_source(host, remote_path=None, local_path=None):
    if remote_path:
        return read_ssh_bytes(host, remote_path), remote_path
    resolved = Path(local_path).expanduser().resolve()
    return resolved.read_bytes(), str(resolved)


def iter_vava_record_chunks(data):
    parsed = parse_vava_record_bytes(data)
    offset = 16
    for index, record in enumerate(parsed["records"]):
        payload_offset = offset + 24
        payload_end = payload_offset + record["size"]
        payload = data[payload_offset:payload_end]
        yield index, record, payload
        offset = payload_end


def default_export_dir(source, explicit_output_dir=None):
    if explicit_output_dir:
        path = Path(explicit_output_dir).expanduser().resolve()
        path.mkdir(parents=True, exist_ok=True)
        return path
    source_name = Path(source).name
    path = Path.cwd() / f"{source_name}.export"
    path.mkdir(parents=True, exist_ok=True)
    return path


def cmd_export_record(args):
    data, source = load_record_source(args.host, args.remote_path, args.local_path)
    parsed = parse_vava_record_bytes(data)
    export_dir = default_export_dir(source, args.output_dir)

    clear_video = bytearray()
    audio_concat = bytearray()
    manifest_records = []
    key_blob_common_prefix = None
    key_blob_prefix_len = None

    for index, record, payload in iter_vava_record_chunks(data):
        entry = dict(record)
        entry["index"] = index
        entry["payload_size"] = len(payload)

        if record["chunk_type"] == "video_nalu":
            entry["output_stream"] = "video_clear.h264"
            entry["output_offset"] = len(clear_video)
            clear_video.extend(payload)
        elif record["chunk_type"] == "audio_chunk":
            entry["output_stream"] = "audio_chunks.bin"
            entry["output_offset"] = len(audio_concat)
            audio_concat.extend(payload)
        elif record["chunk_type"] == "video_key_blob":
            key_blob_name = f"keyblob_{index:03d}_pts_{record['pts_ms']:06d}.bin"
            (export_dir / key_blob_name).write_bytes(payload)
            entry["output_stream"] = key_blob_name
            entry["output_offset"] = 0
            if key_blob_common_prefix is None:
                key_blob_common_prefix = payload[:32]
                key_blob_prefix_len = len(key_blob_common_prefix)
            else:
                common = 0
                for lhs, rhs in zip(key_blob_common_prefix, payload):
                    if lhs != rhs:
                        break
                    common += 1
                key_blob_prefix_len = min(key_blob_prefix_len, common)
        manifest_records.append(entry)

    if clear_video:
        (export_dir / "video_clear.h264").write_bytes(clear_video)
    if audio_concat:
        (export_dir / "audio_chunks.bin").write_bytes(audio_concat)

    summary = {
        "source": source,
        "file_flag0": parsed["file_flag0"],
        "file_flag1": parsed["file_flag1"],
        "outer0": parsed["outer0"],
        "outer1": parsed["outer1"],
        "duration": parsed["duration"],
        "filesize": parsed["filesize"],
        "native_header": parsed["native_header"],
        "native_symbols": parsed["native_symbols"],
        "record_count": parsed["record_count"],
        "key_blob_common_prefix_len": key_blob_prefix_len or 0,
        "key_blob_common_prefix_hex": (
            key_blob_common_prefix[: key_blob_prefix_len or 0].hex()
            if key_blob_common_prefix is not None
            else ""
        ),
        "records": manifest_records,
    }
    (export_dir / "manifest.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    print(export_dir)
    print(export_dir / "manifest.json")
    if clear_video:
        print(export_dir / "video_clear.h264")
    if audio_concat:
        print(export_dir / "audio_chunks.bin")


def cmd_export_latest_record(args):
    remote_path = find_latest_recording_path(
        args.host,
        args.record_root,
        camera_sn=args.camera_sn,
        day=args.day,
    )
    export_args = argparse.Namespace(
        host=args.host,
        remote_path=remote_path,
        local_path=None,
        output_dir=args.output_dir,
    )
    cmd_export_record(export_args)
    print(f"remote_source={remote_path}")


def cmd_inspect_idx(args):
    data, source = load_record_source(args.host, args.remote_path, args.local_path)
    parsed = parse_vava_idx_bytes(data)
    if args.json:
        print(json.dumps(parsed, ensure_ascii=False, indent=2))
        return
    print(format_vava_idx_summary(parsed, source))


def cmd_list_recordings_idx(args):
    result = run_ssh(
        args.host,
        f"find {shlex.quote(args.record_root)} -name vava.idx -type f | sort",
        capture=True,
    )
    paths = [line.strip() for line in (result.stdout or "").splitlines() if line.strip()]
    for idx_path in paths:
        parsed = parse_vava_idx_bytes(read_ssh_bytes(args.host, idx_path))
        print(format_vava_idx_summary(parsed, idx_path))
        print()


def cmd_inspect_record(args):
    data, source = load_record_source(args.host, args.remote_path, args.local_path)
    parsed = parse_vava_record_bytes(data)
    if args.json:
        print(json.dumps(parsed, ensure_ascii=False, indent=2))
        return
    print(format_vava_record_summary(parsed, source))


def build_auth_payload(session_key, random_value):
    auth = hashlib.md5(f"vava:{random_value}:2017".encode("utf-8")).hexdigest()
    return json.dumps(
        {
            "random": random_value,
            "auth": auth,
            "key": session_key,
        },
        separators=(",", ":"),
        ensure_ascii=False,
    )


def recv_exact(sock, size):
    chunks = []
    remaining = size
    while remaining > 0:
        chunk = sock.recv(remaining)
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def cmd_probe_local_port(args):
    if args.auth_session_key is not None and args.json_payload is not None:
        raise SystemExit("use either --json-payload or --auth-session-key, not both")
    if args.auth_session_key is not None:
        json_payload = build_auth_payload(args.auth_session_key, args.auth_random)
    else:
        json_payload = args.json_payload or ""
    payload = json_payload.encode("utf-8")
    packet = struct.pack("<III", 0xEB000003, args.cmd_code, len(payload)) + payload
    candidate = None
    local_port = None
    proc = None
    try:
        candidate, local_port, proc = start_ssh_forward(args.host, args.remote_port)
        with socket.create_connection(("127.0.0.1", local_port), timeout=3) as sock:
            sock.settimeout(args.recv_timeout)
            if args.wait_before_send > 0:
                time.sleep(args.wait_before_send)
            if not args.no_send:
                sock.sendall(packet)
            response = {
                "ssh_host": candidate,
                "remote_port": args.remote_port,
                "local_port": local_port,
                "cmd_code": args.cmd_code,
                "sent_hex": packet.hex() if not args.no_send else "",
            }
            if args.no_send:
                greeting = sock.recv(args.greeting_bytes)
                response["greeting_hex"] = greeting.hex()
                response["greeting_text"] = greeting.decode("utf-8", "replace")
                print(json.dumps(response, ensure_ascii=False, indent=2))
                return
            header = recv_exact(sock, 12)
            response["response_header_len"] = len(header)
            response["response_header_hex"] = header.hex()
            if len(header) == 12:
                sync_code, cmd_code, body_size = struct.unpack("<III", header)
                body = recv_exact(sock, body_size)
                response["response_sync_code"] = f"{sync_code:#x}"
                response["response_cmd_code"] = cmd_code
                response["response_body_size"] = body_size
                response["response_body_hex"] = body.hex()
                response["response_body_text"] = body.decode("utf-8", "replace")
            print(json.dumps(response, ensure_ascii=False, indent=2))
    except ConnectionResetError as exc:
        print(
            json.dumps(
                {
                    "ssh_host": candidate,
                    "remote_port": args.remote_port,
                    "local_port": local_port,
                    "cmd_code": args.cmd_code,
                    "sent_hex": packet.hex() if not args.no_send else "",
                    "error": "connection_reset",
                    "detail": str(exc),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    finally:
        if proc is not None:
            stop_ssh_forward(proc)


def build_parser():
    parser = argparse.ArgumentParser(description="Manage the recovered VAVA station over SSH")
    parser.add_argument("--host", default=DEFAULT_HOST, help="SSH host alias")
    parser.add_argument(
        "--record-root",
        default=DEFAULT_REMOTE_RECORD_ROOT,
        help="Remote station recording root",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    status = sub.add_parser("status", help="Show process, storage, recordings, and recent logs")
    status.set_defaults(func=cmd_status)

    start_local = sub.add_parser(
        "start-local",
        help="Start local camera intake without watchdog",
    )
    start_local.add_argument(
        "--pulse-netcheck",
        action="store_true",
        help="Briefly start Vava_NetCheck first to seed the netchange queue",
    )
    start_local.add_argument(
        "--with-tfupdate",
        action="store_true",
        help="Also start Ppcs_tfupdate",
    )
    start_local.set_defaults(func=cmd_start_local)

    start_baseline = sub.add_parser(
        "start-baseline",
        help="Start the clean app-facing station baseline without pairing helpers",
    )
    start_baseline.add_argument(
        "--with-tfupdate",
        action="store_true",
        help="Also start Ppcs_tfupdate",
    )
    start_baseline.set_defaults(func=cmd_start_baseline)

    stop = sub.add_parser("stop", help="Stop recovered VAVA daemons")
    stop.set_defaults(func=cmd_stop)

    pair_ap = sub.add_parser("pair-ap", help="Build the VAPair SSID only")
    pair_ap.set_defaults(func=cmd_pair_ap)

    pair_window = sub.add_parser(
        "pair-window",
        help="Build the VAPair SSID and start the local pairing daemons",
    )
    pair_window.add_argument(
        "--with-tfupdate",
        action="store_true",
        help="Also start Ppcs_tfupdate",
    )
    pair_window.set_defaults(func=cmd_pair_window)

    pair_force = sub.add_parser(
        "pair-force",
        help="Temporarily hold the station in real camera pair mode by refreshing the runtime flag",
    )
    pair_force_sub = pair_force.add_subparsers(dest="pair_force_cmd", required=True)

    pair_force_start = pair_force_sub.add_parser("start", help="Start the runtime pair-mode hold loop")
    pair_force_start.add_argument("--seconds", type=int, default=600)
    pair_force_start.set_defaults(func=cmd_pair_force_start)

    pair_force_stop = pair_force_sub.add_parser("stop", help="Stop the runtime pair-mode hold loop")
    pair_force_stop.set_defaults(func=cmd_pair_force_stop)

    pair_force_status = pair_force_sub.add_parser("status", help="Show current runtime pair-mode state")
    pair_force_status.set_defaults(func=cmd_pair_force_status)

    logs = sub.add_parser("logs", help="Tail a remote log")
    logs.add_argument("--remote-log", default="/tmp/ppcs_local.log")
    logs.add_argument("--lines", type=int, default=120)
    logs.set_defaults(func=cmd_logs)

    list_recordings = sub.add_parser("list-recordings", help="List remote recording files")
    list_recordings.set_defaults(func=cmd_list_recordings)

    latest_recording = sub.add_parser(
        "latest-recording",
        help="Print the newest remote recording path",
    )
    latest_recording.add_argument("--camera-sn", help="Filter by camera SN")
    latest_recording.add_argument("--day", help="Filter by day in YYYYMMDD")
    latest_recording.set_defaults(func=cmd_latest_recording)

    pull = sub.add_parser("pull", help="Copy a remote file to the local machine")
    pull.add_argument("remote_path")
    pull.add_argument("local_path")
    pull.set_defaults(func=cmd_pull)

    pull_latest = sub.add_parser(
        "pull-latest",
        help="Copy the newest remote recording file to the local machine",
    )
    pull_latest.add_argument("local_path")
    pull_latest.add_argument("--camera-sn", help="Filter by camera SN")
    pull_latest.add_argument("--day", help="Filter by day in YYYYMMDD")
    pull_latest.set_defaults(func=cmd_pull_latest)

    led = sub.add_parser("led", help="Set the station front LED")
    led.add_argument("mode", choices=["white-solid", "red-solid", "off"])
    led.set_defaults(func=cmd_led)

    inspect_idx = sub.add_parser("inspect-idx", help="Parse a local or remote vava.idx file")
    inspect_idx_group = inspect_idx.add_mutually_exclusive_group(required=True)
    inspect_idx_group.add_argument("--remote-path")
    inspect_idx_group.add_argument("--local-path")
    inspect_idx.add_argument("--json", action="store_true")
    inspect_idx.set_defaults(func=cmd_inspect_idx)

    inspect_record = sub.add_parser(
        "inspect-record",
        help="Parse a local or remote _U_ recording file",
    )
    inspect_record_group = inspect_record.add_mutually_exclusive_group(required=True)
    inspect_record_group.add_argument("--remote-path")
    inspect_record_group.add_argument("--local-path")
    inspect_record.add_argument("--json", action="store_true")
    inspect_record.set_defaults(func=cmd_inspect_record)

    export_record = sub.add_parser(
        "export-record",
        help="Export clear video NALUs, key blobs, audio chunks, and a manifest from a local or remote _U_ file",
    )
    export_record_group = export_record.add_mutually_exclusive_group(required=True)
    export_record_group.add_argument("--remote-path")
    export_record_group.add_argument("--local-path")
    export_record.add_argument(
        "--output-dir",
        help="Directory to write the exported files into (defaults to ./<source>.export)",
    )
    export_record.set_defaults(func=cmd_export_record)

    export_latest_record = sub.add_parser(
        "export-latest-record",
        help="Export the newest remote _U_ recording file",
    )
    export_latest_record.add_argument(
        "--output-dir",
        help="Directory to write the exported files into (defaults to ./<source>.export)",
    )
    export_latest_record.add_argument("--camera-sn", help="Filter by camera SN")
    export_latest_record.add_argument("--day", help="Filter by day in YYYYMMDD")
    export_latest_record.set_defaults(func=cmd_export_latest_record)

    list_recordings_idx = sub.add_parser(
        "list-recordings-idx",
        help="Parse every vava.idx under the recording root",
    )
    list_recordings_idx.set_defaults(func=cmd_list_recordings_idx)

    probe_local_port = sub.add_parser(
        "probe-local-port",
        help="Probe a station local TCP port through SSH forwarding",
    )
    probe_local_port.add_argument("--remote-port", type=int, default=16154)
    probe_local_port.add_argument("--cmd-code", type=int, default=100)
    probe_local_port.add_argument("--json-payload")
    probe_local_port.add_argument("--auth-session-key")
    probe_local_port.add_argument("--auth-random", type=int, default=123456)
    probe_local_port.add_argument("--recv-timeout", type=float, default=3.0)
    probe_local_port.add_argument("--wait-before-send", type=float, default=0.0)
    probe_local_port.add_argument("--no-send", action="store_true")
    probe_local_port.add_argument("--greeting-bytes", type=int, default=64)
    probe_local_port.set_defaults(func=cmd_probe_local_port)

    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
