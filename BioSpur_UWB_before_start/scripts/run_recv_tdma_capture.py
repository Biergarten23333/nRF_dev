#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import serial
from serial import SerialException

from master_control_port import assert_not_jlink_when_biospur_available


TAG_NOTIFY_PREFIX_RE = r"(?:BLE(?:\[(?P<conn>\d+)(?::[^\]]*)?\])?|BS[0-9A-F]{4}|NUS)"

TAG_SUMMARY_RE_FULL = re.compile(
    rf"{TAG_NOTIFY_PREFIX_RE} notify: TagSummary sweep=(?P<sweep>\d+) plan=(?P<plan>\w+) "
    r"xyz=\((?P<x>-?\d+),(?P<y>-?\d+),(?P<z>-?\d+)\) "
    r"rms=(?P<rms>\d+) max=(?P<max>\d+)"
    r"(?: anchors=\[(?P<anchors>[A-Z,]*)\])?"
    r"(?: motion_dt=(?P<motion_dt>\d+))?"
    r"(?: disp=(?P<disp>\d+) speed=(?P<speed>\d+))?"
)

TAG_SUMMARY_RE_COMPACT = re.compile(
    rf"{TAG_NOTIFY_PREFIX_RE} notify: TagSummary s=(?P<sweep>\d+) p=(?P<plan>\w+) "
    r"xyz=\((?P<x>-?\d+),(?P<y>-?\d+),(?P<z>-?\d+)\) "
    r"r=(?P<rms>\d+) m=(?P<max>\d+)"
    r"(?: a=\[(?P<anchors>[A-Z,]*)\])?"
    r"(?: dt=(?P<motion_dt>\d+)| motion=na)?"
)

TAG_SUMMARY_RE_BUNDLE = re.compile(
    rf"{TAG_NOTIFY_PREFIX_RE} notify: (?:TS|TagSummary) s=(?P<sweep>\d+) p=(?P<plan>\w+) "
    r"xyz=(?:(?P<x>-?\d+),(?P<y>-?\d+),(?P<z>-?\d+)|\((?P<x2>-?\d+),(?P<y2>-?\d+),(?P<z2>-?\d+)\)) "
    r"r=(?P<rms>\d+) m=(?P<max>\d+)"
    r"(?: a=(?P<anchors>[A-Z0-9,\[\]]*))?"
    r"(?: (?:d|dt)=(?P<motion_dt>\d+)| motion=na)?"
)

TAG_SUMMARY_RE_SEMI = re.compile(
    rf"{TAG_NOTIFY_PREFIX_RE} notify: TS;"
    r"(?P<ver>\d+);"
    r"(?P<sweep>\d+);"
    r"(?P<plan>[tfrx]);"
    r"(?P<x>-?\d+);(?P<y>-?\d+);(?P<z>-?\d+);"
    r"(?P<rms>\d+);(?P<max>\d+);"
    r"(?P<anchors>[A-Z0-9]*);"
    r"(?P<slot_idx>\d+);(?P<slot_cnt>\d+);"
    r"(?P<src>[MSB]);"
    r"(?P<cut>[01]);"
    r"(?P<reason>[SPRCN]);"
    r"(?P<motion_dt>\d+)"
)

CM_RE = re.compile(
    rf"{TAG_NOTIFY_PREFIX_RE} notify: CM;"
    r"(?P<ver>\d+);"
    r"(?P<sweep>\d+);"
    r"(?P<anchor>\d+);"
    r"(?P<status>[a-z_]+);"
    r"(?P<raw>-?\d+);"
    r"(?P<filt>\d+);"
    r"(?P<q>\d+);"
    r"(?P<ok>\d+);"
    r"(?P<fail>\d+)"
)

CONNECTED_RE = re.compile(
    r"Connected\[(?P<conn>\d+)\]:.*?(?:name=(?P<name>[^\s]+))?.*?(?:bs=(?P<bs>BS[0-9A-F]{4}))?.*?tag_id=(?P<tag_id>-?\d+)"
)

CFG_ASSIGNED_RE = re.compile(
    r"CFG assigned\[(?P<conn>\d+)\]: bs=(?P<bs>BS[0-9A-F]{4}) tag=(?P<tag_id>\d+)"
)


def parse_tag_summary(text: str):
    for regex in (
        TAG_SUMMARY_RE_FULL,
        TAG_SUMMARY_RE_COMPACT,
        TAG_SUMMARY_RE_BUNDLE,
        TAG_SUMMARY_RE_SEMI,
    ):
        match = regex.search(text)
        if match:
            return match
    return None


def extract_bs_name(text: str) -> str:
    match = re.search(r"\b(BS[0-9A-F]{4})\b", text)
    return match.group(1) if match else ""


def iter_tag_summary_matches(text: str):
    prefix = None
    if "notify:" in text:
        prefix = text.split("notify:", 1)[0] + "notify: "

    for idx, fragment in enumerate(text.split("|")):
        fragment = fragment.strip()
        if not fragment:
            continue
        if idx > 0 and "notify:" not in fragment and fragment.startswith(("TagSummary", "TS", "TS;")):
            fragment = (prefix or "BLE notify: ") + fragment

        match = parse_tag_summary(fragment)
        if match:
            yield match


def iter_cm_matches(text: str):
    prefix = None
    if "notify:" in text:
        prefix = text.split("notify:", 1)[0] + "notify: "

    for idx, fragment in enumerate(text.split("|")):
        fragment = fragment.strip()
        if not fragment:
            continue
        if idx > 0 and "notify:" not in fragment and fragment.startswith("CM;"):
            fragment = (prefix or "NUS notify: ") + fragment

        match = CM_RE.search(fragment)
        if match:
            yield match


def normalize_target(name: str) -> str:
    value = name.strip().upper()
    if not value.startswith("BS"):
        raise ValueError(f"Invalid target name: {name}")
    return value


def open_serial_with_retry(port: str, baud: int, timeout_s: float = 0.2, retries: int = 40) -> serial.Serial:
    last_exc = None
    for _ in range(retries):
        try:
            return serial.Serial(port, baud, timeout=timeout_s, write_timeout=2)
        except (SerialException, OSError) as exc:
            last_exc = exc
            time.sleep(0.25)
    raise last_exc if last_exc is not None else RuntimeError("serial open failed")


def drain_serial_until(ser: serial.Serial, logf, wait_s: float) -> None:
    deadline = time.time() + wait_s
    while time.time() < deadline:
        try:
            data = ser.read(ser.in_waiting or 1)
        except (SerialException, OSError):
            raise
        if not data:
            continue
        text = data.decode("utf-8", errors="replace")
        logf.write(text)
        logf.flush()


def send_cmd(ser: serial.Serial, logf, cmd: str, wait_s: float) -> serial.Serial:
    logf.write(f"[HOST_CMD {time.monotonic():.3f}] {cmd}\n")
    logf.flush()
    ser.write((cmd + "\n").encode("utf-8"))
    ser.flush()
    try:
        drain_serial_until(ser, logf, wait_s)
        return ser
    except (SerialException, OSError):
        if cmd.strip().lower() != "mode recv":
            raise
        try:
            ser.close()
        except Exception:
            pass
        last_exc = None
        for _ in range(12):
            try:
                time.sleep(0.75)
                ser = open_serial_with_retry(ser.port, ser.baudrate)
                drain_serial_until(ser, logf, max(wait_s, 2.5))
                return ser
            except (SerialException, OSError) as exc:
                last_exc = exc
                try:
                    ser.close()
                except Exception:
                    pass
        if last_exc is not None:
            raise last_exc
        raise RuntimeError("mode recv reopen failed")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Prepare master RECV tag session, configure TDMA, and capture multi-tag logs."
    )
    parser.add_argument(
        "--port",
        default="/dev/serial/by-id/usb-BioSpur_BioSpur_BLE_Control_87EA2F4A526C5A02-if00",
        help="Master control serial port",
    )
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument("--duration", type=float, default=120.0, help="Capture duration in seconds")
    parser.add_argument(
        "--targets",
        default="BSF66F,BS2DCE,BSDC91",
        help="Comma-separated BS names to collect",
    )
    parser.add_argument(
        "--profiles",
        default="BSF66F:static,BS2DCE:roto,BSDC91:roto",
        help="Comma-separated TDMA profile map: BSxxxx:static|roto|motion",
    )
    parser.add_argument("--static-hz", type=int, default=5)
    parser.add_argument("--roto-hz", type=int, default=10)
    parser.add_argument("--motion-hz", type=int, default=5)
    parser.add_argument(
        "--out-dir",
        default="logs/recv_tdma_capture",
        help="Base output directory",
    )
    return parser


def parse_profiles(text: str) -> list[tuple[str, str]]:
    items: list[tuple[str, str]] = []
    for token in text.split(","):
        token = token.strip()
        if not token:
            continue
        if ":" not in token:
            raise ValueError(f"Invalid profile token: {token}")
        name, profile = token.split(":", 1)
        items.append((normalize_target(name), profile.strip().lower()))
    return items


def write_rows(path: Path, fieldnames: list[str], rows: list[dict]) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def print_capture_status(start_wall: float,
                         end_time: float,
                         positions: list[dict],
                         cm_rows: list[dict],
                         targets: list[str],
                         positions_by_target: dict[str, int],
                         cm_by_target: dict[str, int]) -> None:
    elapsed = max(0.0, time.time() - start_wall)
    remaining = max(0.0, end_time - time.time())
    parts = [
        f"elapsed={elapsed:.0f}s",
        f"remain={remaining:.0f}s",
        f"pos={len(positions)}",
        f"cm={len(cm_rows)}",
    ]
    for target in targets:
        parts.append(
            f"{target}:TS={positions_by_target.get(target, 0)} CM={cm_by_target.get(target, 0)}"
        )
    print("[CAPTURE] " + " ".join(parts), flush=True)


def main() -> int:
    args = build_parser().parse_args()
    assert_not_jlink_when_biospur_available(args.port)

    targets = [normalize_target(x) for x in args.targets.split(",") if x.strip()]
    profile_items = parse_profiles(args.profiles)
    target_set = set(targets)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    base_out = Path(args.out_dir)
    session_dir = base_out.parent / f"{base_out.name}_{ts}"
    session_dir.mkdir(parents=True, exist_ok=True)

    raw_log_path = session_dir / "raw.log"
    summary_json_path = session_dir / "summary.json"
    commands_json_path = session_dir / "commands.json"

    cmd_plan = {
        "mode": "recv",
        "device_kind": "tag",
        "targets": targets,
        "profiles": [{"name": name, "profile": profile} for name, profile in profile_items],
        "freq_hz": {
            "static": args.static_hz,
            "roto": args.roto_hz,
            "motion": args.motion_hz,
        },
        "duration_s": args.duration,
    }
    commands_json_path.write_text(json.dumps(cmd_plan, indent=2), encoding="utf-8")

    conn_meta: dict[str, dict] = {}
    positions: list[dict] = []
    cm_rows: list[dict] = []
    start_wall = time.time()
    interrupted = False
    startup_failed = False
    startup_fail_targets: list[str] = []

    with raw_log_path.open("w", encoding="utf-8") as logf:
        with open_serial_with_retry(args.port, args.baud) as ser:
            time.sleep(0.8)

            # Initial drain
            drain_serial_until(ser, logf, 1.0)

            ser = send_cmd(ser, logf, "mode recv", 3.0)
            ser = send_cmd(ser, logf, "tdma clear", 0.8)
            ser = send_cmd(ser, logf, "ota_target token -1", 0.5)
            ser = send_cmd(ser, logf, "ota_target name -", 0.5)
            ser = send_cmd(ser, logf, "ota_target prefix -", 0.5)
            ser = send_cmd(ser, logf, "ota_target uuid -", 0.5)
            for name, profile in profile_items:
                ser = send_cmd(ser, logf, f"tdma profile {name} {profile}", 0.8)
            ser = send_cmd(ser, logf, f"tdma freq static {args.static_hz}", 0.5)
            ser = send_cmd(ser, logf, f"tdma freq roto {args.roto_hz}", 0.5)
            ser = send_cmd(ser, logf, f"tdma freq motion {args.motion_hz}", 0.5)
            ser = send_cmd(ser, logf, "device kind tag", 2.0)
            ser = send_cmd(ser, logf, "tdma rebalance", 0.8)
            ser = send_cmd(ser, logf, "tdma show", 1.0)
            ser = send_cmd(ser, logf, "status", 0.8)
            ser = send_cmd(ser, logf, "device show", 0.8)

            pending = ""
            end_time = time.time() + args.duration
            last_status_at = 0.0
            positions_seen: dict[str, int] = defaultdict(int)
            cm_seen: dict[str, int] = defaultdict(int)
            cm_ok_seen: dict[str, int] = defaultdict(int)
            startup_strikes: dict[str, int] = defaultdict(int)
            try:
                while time.time() < end_time:
                    chunk = ser.read(ser.in_waiting or 1)
                    if not chunk:
                        if time.time() - last_status_at >= 1.0:
                            print_capture_status(
                                start_wall,
                                end_time,
                                positions,
                                cm_rows,
                                targets,
                                positions_seen,
                                cm_seen,
                            )
                            elapsed = time.time() - start_wall
                            if elapsed >= 5.0:
                                bad = []
                                for target in targets:
                                    if cm_ok_seen.get(target, 0) == 0:
                                        startup_strikes[target] += 1
                                        if startup_strikes[target] >= 3:
                                            bad.append(target)
                                    else:
                                        startup_strikes[target] = 0
                                if bad:
                                    startup_failed = True
                                    startup_fail_targets = bad
                                    print(
                                        "[CAPTURE] startup failed after 3 checks with no CM ok: "
                                        + ",".join(bad),
                                        file=sys.stderr,
                                        flush=True,
                                    )
                                    break
                            last_status_at = time.time()
                        continue

                    text = chunk.decode("utf-8", errors="replace")
                    logf.write(text)
                    logf.flush()
                    pending += text

                    while "\n" in pending:
                        line, pending = pending.split("\n", 1)
                        line = line.rstrip("\r")
                        if not line:
                            continue

                        match = CONNECTED_RE.search(line)
                        if match:
                            conn_id = match.group("conn")
                            meta = conn_meta.setdefault(conn_id, {})
                            if match.group("name"):
                                meta["peer_name"] = match.group("name")
                            if match.group("bs"):
                                meta["peer_name"] = match.group("bs")
                            meta["tag_id"] = int(match.group("tag_id"))

                        match = CFG_ASSIGNED_RE.search(line)
                        if match:
                            conn_id = match.group("conn")
                            meta = conn_meta.setdefault(conn_id, {})
                            meta["peer_name"] = match.group("bs")
                            meta["tag_id"] = int(match.group("tag_id"))

                        for m in iter_tag_summary_matches(line):
                            conn_id = m.groupdict().get("conn") or "0"
                            meta = conn_meta.get(conn_id, {})
                            peer_name = extract_bs_name(line) or meta.get("peer_name", "")
                            if peer_name and peer_name not in target_set:
                                continue
                            x = m.group("x") or m.group("x2")
                            y = m.group("y") or m.group("y2")
                            z = m.group("z") or m.group("z2")
                            positions.append(
                                {
                                    "conn_id": conn_id,
                                    "peer_name": peer_name,
                                    "tag_id": meta.get("tag_id", ""),
                                    "sweep": int(m.group("sweep")),
                                    "plan": m.group("plan"),
                                    "x_mm": int(x),
                                    "y_mm": int(y),
                                    "z_mm": int(z),
                                    "rms_mm": int(m.group("rms")),
                                    "max_mm": int(m.group("max")),
                                    "anchors": m.group("anchors") or "",
                                    "motion_dt_ms": int(m.group("motion_dt") or 0),
                                    "disp_mm": int(m.groupdict().get("disp") or 0),
                                    "speed_mm_s": int(m.groupdict().get("speed") or 0),
                                }
                            )
                            if peer_name:
                                positions_seen[peer_name] += 1

                        for m in iter_cm_matches(line):
                            conn_id = m.groupdict().get("conn") or "0"
                            meta = conn_meta.get(conn_id, {})
                            peer_name = extract_bs_name(line) or meta.get("peer_name", "")
                            if peer_name and peer_name not in target_set:
                                continue
                            cm_rows.append(
                                {
                                    "conn_id": conn_id,
                                    "peer_name": peer_name,
                                    "tag_id": meta.get("tag_id", ""),
                                    "sweep": int(m.group("sweep")),
                                    "anchor_id": int(m.group("anchor")),
                                    "status": m.group("status"),
                                    "raw_mm": int(m.group("raw")),
                                    "filt_mm": int(m.group("filt")),
                                    "quality_percent": int(m.group("q")),
                                    "ok_count": int(m.group("ok")),
                                    "fail_count": int(m.group("fail")),
                                }
                            )
                            if peer_name:
                                cm_seen[peer_name] += 1
                                if m.group("status") == "ok":
                                    cm_ok_seen[peer_name] += 1

                    if time.time() - last_status_at >= 1.0:
                        print_capture_status(
                            start_wall,
                            end_time,
                            positions,
                            cm_rows,
                            targets,
                            positions_seen,
                            cm_seen,
                        )
                        elapsed = time.time() - start_wall
                        if elapsed >= 5.0:
                            bad = []
                            for target in targets:
                                if cm_ok_seen.get(target, 0) == 0:
                                    startup_strikes[target] += 1
                                    if startup_strikes[target] >= 3:
                                        bad.append(target)
                                else:
                                    startup_strikes[target] = 0
                            if bad:
                                startup_failed = True
                                startup_fail_targets = bad
                                print(
                                    "[CAPTURE] startup failed after 3 checks with no CM ok: "
                                    + ",".join(bad),
                                    file=sys.stderr,
                                    flush=True,
                                )
                                break
                        last_status_at = time.time()
            except KeyboardInterrupt:
                interrupted = True
                print("\n[CAPTURE] interrupted by user; writing partial outputs...", file=sys.stderr, flush=True)

            if pending.strip():
                logf.write(pending.rstrip("\r") + "\n")

    positions_by_target: dict[str, list[dict]] = defaultdict(list)
    cm_by_target: dict[str, list[dict]] = defaultdict(list)

    for row in positions:
        key = row["peer_name"] or f"tag{row['tag_id']}"
        positions_by_target[key].append(row)
    for row in cm_rows:
        key = row["peer_name"] or f"tag{row['tag_id']}"
        cm_by_target[key].append(row)

    position_fields = [
        "sweep",
        "conn_id",
        "peer_name",
        "tag_id",
        "plan",
        "x_mm",
        "y_mm",
        "z_mm",
        "rms_mm",
        "max_mm",
        "anchors",
        "motion_dt_ms",
        "disp_mm",
        "speed_mm_s",
    ]
    cm_fields = [
        "sweep",
        "conn_id",
        "peer_name",
        "tag_id",
        "anchor_id",
        "status",
        "raw_mm",
        "filt_mm",
        "quality_percent",
        "ok_count",
        "fail_count",
    ]

    write_rows(session_dir / "positions_all.csv", position_fields, positions)
    write_rows(session_dir / "cm_all.csv", cm_fields, cm_rows)

    per_tag_summary: dict[str, dict] = {}
    for target in targets:
        tag_dir = session_dir / target
        tag_dir.mkdir(parents=True, exist_ok=True)
        pos_rows = positions_by_target.get(target, [])
        cm_target_rows = cm_by_target.get(target, [])
        write_rows(tag_dir / "positions.csv", position_fields, pos_rows)
        write_rows(tag_dir / "cm.csv", cm_fields, cm_target_rows)

        per_tag_summary[target] = {
            "position_rows": len(pos_rows),
            "cm_rows": len(cm_target_rows),
            "latest_position": pos_rows[-1] if pos_rows else None,
            "anchors_seen": sorted({row["anchor_id"] for row in cm_target_rows}) if cm_target_rows else [],
            "status_counts": {
                status: sum(1 for row in cm_target_rows if row["status"] == status)
                for status in sorted({row["status"] for row in cm_target_rows})
            },
        }

    summary = {
        "success": (not interrupted) and (not startup_failed),
        "interrupted": interrupted,
        "startup_failed": startup_failed,
        "startup_fail_targets": startup_fail_targets,
        "port": args.port,
        "duration_s": args.duration,
        "elapsed_s": time.time() - start_wall,
        "session_dir": str(session_dir),
        "targets": targets,
        "profiles": {name: profile for name, profile in profile_items},
        "freq_hz": {
            "static": args.static_hz,
            "roto": args.roto_hz,
            "motion": args.motion_hz,
        },
        "positions_all": len(positions),
        "cm_all": len(cm_rows),
        "connections": conn_meta,
        "per_tag": per_tag_summary,
        "raw_log": str(raw_log_path),
        "positions_all_csv": str(session_dir / "positions_all.csv"),
        "cm_all_csv": str(session_dir / "cm_all.csv"),
    }
    summary_json_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
