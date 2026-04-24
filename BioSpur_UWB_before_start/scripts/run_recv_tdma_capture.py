#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import subprocess
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
    r"(?:pmode=(?P<pmode>\d+) )?"
    r"(?:qf=(?P<qf>\d+) )?"
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
    r"(?P<plan>[A-Za-z0-9_]+);"
    r"(?P<x>-?\d+);(?P<y>-?\d+);(?P<z>-?\d+);"
    r"(?P<rms>\d+);(?P<max>\d+);"
    r"(?P<anchors>[A-Z0-9]*);"
    r"(?P<slot_idx>\d+);(?P<slot_cnt>\d+);"
    r"(?P<src>[MSB]);"
    r"(?P<cut>[01]);"
    r"(?P<reason>[SPRCN]);"
    r"(?P<motion_dt>\d+)"
    r"(?:;(?P<pmode>\d+);(?P<plan_label>[A-Za-z0-9_]+);(?P<qf>\d+))?"
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

CS_RE = re.compile(
    rf"{TAG_NOTIFY_PREFIX_RE} notify: CS;"
    r"(?P<ver>\d+);"
    r"(?P<sweep>\d+);"
    r"(?P<plan>[A-Za-z0-9_]+);"
    r"(?P<pmode>\d+);"
    r"(?P<qf>\d+);"
    r"(?P<targets>[A-Z0-9,]*);"
    r"(?P<statuses>[a-z_,]*);"
    r"(?P<qualities>[0-9,]*)"
)

CR_RE = re.compile(
    rf"{TAG_NOTIFY_PREFIX_RE} notify: CR;"
    r"(?P<ver>\d+);"
    r"(?P<sweep>\d+);"
    r"(?P<plan>[A-Za-z0-9_]+);"
    r"(?P<pmode>\d+);"
    r"(?P<anchor>[A-Z\?]);"
    r"(?P<status>[a-z_]+);"
    r"(?P<reason>[a-z_]+);"
    r"(?P<raw>-?\d+);"
    r"(?P<filt>\d+);"
    r"(?P<pred>\d+);"
    r"(?P<resid>\d+);"
    r"(?P<tracker_q>\d+);"
    r"(?P<solve_q>\d+)"
)

CF_RE = re.compile(
    rf"{TAG_NOTIFY_PREFIX_RE} notify: CF;"
    r"(?P<ver>\d+);"
    r"(?P<sweep>\d+);"
    r"(?P<plan>[A-Za-z0-9_]+);"
    r"(?P<pmode>\d+);"
    r"(?P<solve_reason>[a-z_]+);"
    r"(?P<qf>\d+);"
    r"(?P<active>\d+);"
    r"(?P<valid>\d+);"
    r"(?P<rms>\d+);"
    r"(?P<max>\d+);"
    r"(?P<step>\d+)"
)

CONNECTED_RE = re.compile(
    r"Connected\[(?P<conn>\d+)\]:.*?(?:name=(?P<name>[^\s]+))?.*?(?:bs=(?P<bs>BS[0-9A-F]{4}))?.*?tag_id=(?P<tag_id>-?\d+)"
)

CFG_ASSIGNED_RE = re.compile(
    r"CFG assigned\[(?P<conn>\d+)\]: bs=(?P<bs>BS[0-9A-F]{4}) tag=(?P<tag_id>\d+)"
    r".*?pmode=(?P<pmode>\d+)"
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


def iter_cs_matches(text: str):
    prefix = None
    if "notify:" in text:
        prefix = text.split("notify:", 1)[0] + "notify: "

    for idx, fragment in enumerate(text.split("|")):
        fragment = fragment.strip()
        if not fragment:
            continue
        if idx > 0 and "notify:" not in fragment and fragment.startswith("CS;"):
            fragment = (prefix or "NUS notify: ") + fragment

        match = CS_RE.search(fragment)
        if match:
            yield match


def iter_cr_matches(text: str):
    prefix = None
    if "notify:" in text:
        prefix = text.split("notify:", 1)[0] + "notify: "

    for idx, fragment in enumerate(text.split("|")):
        fragment = fragment.strip()
        if not fragment:
            continue
        if idx > 0 and "notify:" not in fragment and fragment.startswith("CR;"):
            fragment = (prefix or "NUS notify: ") + fragment

        match = CR_RE.search(fragment)
        if match:
            yield match


def iter_cf_matches(text: str):
    prefix = None
    if "notify:" in text:
        prefix = text.split("notify:", 1)[0] + "notify: "

    for idx, fragment in enumerate(text.split("|")):
        fragment = fragment.strip()
        if not fragment:
            continue
        if idx > 0 and "notify:" not in fragment and fragment.startswith("CF;"):
            fragment = (prefix or "NUS notify: ") + fragment

        match = CF_RE.search(fragment)
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
    try:
        ser.write((cmd + "\n").encode("utf-8"))
        ser.flush()
    except (serial.SerialTimeoutException, SerialException, OSError) as exc:
        logf.write(
            f"[HOST_WARN {time.monotonic():.3f}] write failed for {cmd!r}: {exc}; reopen/retry\n"
        )
        logf.flush()
        port = ser.port
        baud = ser.baudrate
        try:
            ser.close()
        except Exception:
            pass
        ser = open_serial_with_retry(port, baud)
        time.sleep(1.0)
        drain_serial_until(ser, logf, 1.0)
        ser.write((cmd + "\n").encode("utf-8"))
        ser.flush()
    try:
        drain_serial_until(ser, logf, wait_s)
        return ser
    except (SerialException, OSError):
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
                logf.write(
                    f"[HOST_WARN {time.monotonic():.3f}] read failed after {cmd!r}; reopened serial and continued\n"
                )
                logf.flush()
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


def reset_controller_via_jlink(logf, snr: str) -> bool:
    snr = (snr or "").strip()
    if not snr:
        return False

    cmd_path = Path(f"/tmp/biospur_b120_reset_{snr}.jlink")
    cmd_path.write_text(
        "\n".join(
            [
                "Device NRF5340_XXAA_APP",
                "SI SWD",
                "Speed 4000",
                "r",
                "g",
                "q",
                "",
            ]
        ),
        encoding="utf-8",
    )
    logf.write(f"[HOST_RECOVERY {time.monotonic():.3f}] J-Link reset B120 snr={snr}\n")
    logf.flush()
    cp = subprocess.run(
        [
            "JLinkExe",
            "-NoGui",
            "1",
            "-SelectEmuBySN",
            snr,
            "-CommanderScript",
            str(cmd_path),
        ],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    logf.write(cp.stdout)
    logf.write(f"[HOST_RECOVERY {time.monotonic():.3f}] J-Link reset rc={cp.returncode}\n")
    logf.flush()
    return cp.returncode == 0


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
    parser.add_argument(
        "--controller-reset-snr",
        default=os.environ.get("B120_SNR", "960148546"),
        help="B120 J-Link SNR used for hard recovery when BLE central links get stuck. Use '-' to disable.",
    )
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
    parser.add_argument(
        "--skip-anchor-preflight",
        action="store_true",
        help="Skip 8/8 runtime responder verification before tag capture.",
    )
    parser.add_argument(
        "--anchor-preflight-timeout-s",
        type=float,
        default=30.0,
        help="Per-attempt anchor runtime responder command timeout.",
    )
    parser.add_argument(
        "--anchor-preflight-retries",
        type=int,
        default=3,
        help="Runtime responder verification attempts before aborting capture.",
    )
    parser.add_argument(
        "--allow-zero-positions",
        action="store_true",
        help="Do not fail the session when one or more targets produce no position rows.",
    )
    parser.add_argument(
        "--cm-probe-target",
        default="BSF66F",
        help="Fixed static tag used for startup CM preflight.",
    )
    parser.add_argument(
        "--cm-probe-timeout-s",
        type=float,
        default=20.0,
        help="Seconds to wait for the fixed startup tag to show 8/8 CM ok anchors.",
    )
    parser.add_argument(
        "--cm-probe-retries",
        type=int,
        default=2,
        help="CM probe attempts before aborting capture. Failed attempts trigger all-responder repair.",
    )
    parser.add_argument(
        "--skip-cm-probe",
        action="store_true",
        help="Skip startup BSF66F CM probe and go straight to capture configuration.",
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


def profile_expected_pmode(profile: str) -> int:
    if profile == "static":
        return 4
    if profile == "roto":
        return 5
    return 0


def profile_expects_positions(profile: str) -> bool:
    return profile_expected_pmode(profile) not in {4, 5}


def write_rows(path: Path, fieldnames: list[str], rows: list[dict]) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def run_anchor_responder_preflight(args, session_dir: Path) -> dict:
    preflight_base = session_dir / "anchor_responder_preflight"
    preflight_log = session_dir / "anchor_responder_preflight.console.log"
    cmd = [
        sys.executable,
        "scripts/verify_all_anchor_responder_runtime.py",
        "--port",
        args.port,
        "--command-timeout-s",
        str(args.anchor_preflight_timeout_s),
        "--retry-count",
        str(args.anchor_preflight_retries),
        "--out-dir",
        str(preflight_base),
    ]
    print("[CAPTURE] anchor preflight: require 8/8 runtime responder ack", flush=True)
    cp = subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False)
    preflight_log.write_text(cp.stdout, encoding="utf-8")
    print(cp.stdout, end="", flush=True)

    result = {
        "success": False,
        "returncode": cp.returncode,
        "console_log": str(preflight_log),
    }
    json_match = re.search(r"(\{\s*\"success\".*\})\s*$", cp.stdout, re.S)
    if json_match:
        try:
            parsed = json.loads(json_match.group(1))
            result.update(parsed)
        except json.JSONDecodeError as exc:
            result["error"] = f"preflight_json_parse_failed: {exc}"
    else:
        result["error"] = "preflight_json_not_found"
    return result


def ensure_target_links_ready(
    ser: serial.Serial,
    logf,
    targets: list[str],
    controller_reset_snr: str = "",
    wait_per_target_s: float = 30.0,
) -> serial.Serial:
    ready_targets: set[str] = set()
    hard_reset_used = False

    def mark_ready_from_text(text: str) -> None:
        text_u = text.upper()
        for item in targets:
            target_u = item.upper()
            if (
                f"BS={target_u}" in text_u
                or f"{target_u} NOTIFY:" in text_u
                or f"CFG_OK" in text_u and target_u in text_u
            ):
                ready_targets.add(target_u)

    for pass_idx in range(1, 6):
        ser = send_cmd(ser, logf, "ota_target token -1", 0.5)
        ser = send_cmd(ser, logf, "ota_target name -", 0.5)
        ser = send_cmd(ser, logf, "ota_target uuid -", 0.5)
        ser = send_cmd(ser, logf, "ota_target prefix BS", 0.5)
        ser = send_cmd(ser, logf, "ota_target show", 0.5)

        deadline = time.time() + wait_per_target_s
        while time.time() < deadline:
            ser = send_cmd(ser, logf, "scan", 0.8)
            ser = send_cmd(ser, logf, "conn", 1.6)
            burst = drain_serial_until_capture(ser, logf, 4.0)
            mark_ready_from_text(burst)
            if all(target.upper() in ready_targets for target in targets):
                break
            time.sleep(0.4)

        if all(target.upper() in ready_targets for target in targets):
            break

        missing = sorted(
            target for target in targets
            if target.upper() not in ready_targets
        )
        logf.write(
            f"[HOST_WARN {time.monotonic():.3f}] target links missing after pass {pass_idx}: {','.join(missing)}; reset recv discovery\n"
        )
        logf.flush()

        if pass_idx >= 3 and not hard_reset_used and controller_reset_snr != "-":
            port = ser.port
            baud = ser.baudrate
            try:
                ser.close()
            except Exception:
                pass
            if reset_controller_via_jlink(logf, controller_reset_snr):
                hard_reset_used = True
                ready_targets.clear()
                time.sleep(2.5)
                ser = open_serial_with_retry(port, baud, retries=60)
                drain_serial_until(ser, logf, 3.0)
                ser = send_cmd(ser, logf, "mode recv", 8.0)
                ser = send_cmd(ser, logf, "device kind tag", 2.0)
                continue
            ser = open_serial_with_retry(port, baud, retries=60)

        ser = send_cmd(ser, logf, "ota_target token -1", 0.5)
        ser = send_cmd(ser, logf, "ota_target name -", 0.5)
        ser = send_cmd(ser, logf, "ota_target prefix BS", 0.5)
        ser = send_cmd(ser, logf, "mode recv", 8.0)
        ser = send_cmd(ser, logf, "device kind tag", 2.0)

    missing = [
        target for target in targets
        if target.upper() not in ready_targets
    ]
    if missing:
        raise RuntimeError(f"target_link_not_ready:{','.join(missing)}")

    ser = send_cmd(ser, logf, "ota_target token -1", 0.5)
    ser = send_cmd(ser, logf, "ota_target name -", 0.5)
    ser = send_cmd(ser, logf, "ota_target prefix -", 0.5)
    ser = send_cmd(ser, logf, "ota_target uuid -", 0.5)
    return ser


def drain_serial_until_capture(ser: serial.Serial, logf, wait_s: float) -> str:
    deadline = time.time() + wait_s
    chunks: list[str] = []
    while time.time() < deadline:
        try:
            data = ser.read(ser.in_waiting or 1)
        except (SerialException, OSError):
            raise
        if not data:
            continue
        text = data.decode("utf-8", errors="replace")
        chunks.append(text)
        logf.write(text)
        logf.flush()
    return "".join(chunks)


def configure_recv_capture_session(
    ser: serial.Serial,
    logf,
    args,
    targets: list[str],
    profile_items: list[tuple[str, str]],
) -> serial.Serial:
    ser = send_cmd(ser, logf, "mode recv", 8.0)
    ser = send_cmd(ser, logf, "tdma clear", 0.8)
    ser = send_cmd(ser, logf, "device kind tag", 2.0)
    ser = ensure_target_links_ready(ser, logf, targets, args.controller_reset_snr)
    for name, profile in profile_items:
        ser = send_cmd(ser, logf, f"tdma profile {name} {profile}", 0.8)
    ser = send_cmd(ser, logf, f"tdma freq static {args.static_hz}", 0.5)
    ser = send_cmd(ser, logf, f"tdma freq roto {args.roto_hz}", 0.5)
    ser = send_cmd(ser, logf, f"tdma freq motion {args.motion_hz}", 0.5)
    ser = send_cmd(ser, logf, "tdma rebalance", 0.8)
    ser = send_cmd(ser, logf, "tdma show", 1.0)
    ser = send_cmd(ser, logf, "status", 0.8)
    ser = send_cmd(ser, logf, "device show", 0.8)
    return ser


def run_static_cm_probe(
    ser: serial.Serial,
    logf,
    probe_target: str,
    timeout_s: float,
) -> tuple[serial.Serial, dict]:
    deadline = time.time() + timeout_s
    ok_anchors: set[int] = set()
    seen_anchors: set[int] = set()
    pending = ""
    last_progress = 0.0

    while time.time() < deadline:
        try:
            chunk = ser.read(ser.in_waiting or 1)
        except (SerialException, OSError):
            try:
                ser.close()
            except Exception:
                pass
            ser = open_serial_with_retry(ser.port, ser.baudrate)
            time.sleep(0.8)
            drain_serial_until(ser, logf, 0.8)
            continue

        if not chunk:
            now = time.time()
            if now - last_progress >= 1.0:
                print(
                    f"[CAPTURE] preflight probe {probe_target}: ok={len(ok_anchors)}/8 seen={len(seen_anchors)}/8",
                    flush=True,
                )
                last_progress = now
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
            for m in iter_cm_matches(line):
                peer_name = extract_bs_name(line)
                if peer_name != probe_target:
                    continue
                anchor_id = int(m.group("anchor"))
                seen_anchors.add(anchor_id)
                if m.group("status") == "ok":
                    ok_anchors.add(anchor_id)
            if len(ok_anchors) == 8:
                return ser, {
                    "success": True,
                    "probe_target": probe_target,
                    "ok_anchors": sorted(ok_anchors),
                    "seen_anchors": sorted(seen_anchors),
                    "timeout_s": timeout_s,
                }

    return ser, {
        "success": False,
        "probe_target": probe_target,
        "ok_anchors": sorted(ok_anchors),
        "seen_anchors": sorted(seen_anchors),
        "timeout_s": timeout_s,
    }


def print_capture_status(capture_start_wall: float,
                         end_time: float,
                         positions: list[dict],
                         cm_rows: list[dict],
                         targets: list[str],
                         positions_by_target: dict[str, int],
                         cm_by_target: dict[str, int]) -> None:
    elapsed = max(0.0, time.time() - capture_start_wall)
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
    probe_target = normalize_target(args.cm_probe_target)

    targets = [normalize_target(x) for x in args.targets.split(",") if x.strip()]
    profile_items = parse_profiles(args.profiles)
    target_set = set(targets)
    expected_pmode_by_target = {
        name: profile_expected_pmode(profile) for name, profile in profile_items
    }

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
        "expected_pmode": expected_pmode_by_target,
        "freq_hz": {
            "static": args.static_hz,
            "roto": args.roto_hz,
            "motion": args.motion_hz,
        },
        "cm_probe": {
            "target": probe_target,
            "timeout_s": args.cm_probe_timeout_s,
            "retries": args.cm_probe_retries,
        },
        "duration_s": args.duration,
    }
    commands_json_path.write_text(json.dumps(cmd_plan, indent=2), encoding="utf-8")

    start_wall = time.time()
    anchor_preflight = {"skipped": True, "success": True}
    cm_probe = {
        "success": False,
        "probe_target": probe_target,
        "attempts": [],
    }
    if not args.skip_anchor_preflight:
        anchor_preflight = run_anchor_responder_preflight(args, session_dir)
        if not anchor_preflight.get("success"):
            summary = {
                "success": False,
                "anchor_preflight_failed": True,
                "anchor_preflight": anchor_preflight,
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
                "positions_all": 0,
                "cm_all": 0,
                "cs_all": 0,
                "cr_all": 0,
                "cf_all": 0,
                "cm_probe": cm_probe,
                "raw_log": str(raw_log_path),
                "positions_all_csv": str(session_dir / "positions_all.csv"),
                "cm_all_csv": str(session_dir / "cm_all.csv"),
                "cs_all_csv": str(session_dir / "cs_all.csv"),
                "cr_all_csv": str(session_dir / "cr_all.csv"),
                "cf_all_csv": str(session_dir / "cf_all.csv"),
            }
            write_rows(
                session_dir / "positions_all.csv",
                [
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
                ],
                [],
            )
            write_rows(
                session_dir / "cm_all.csv",
                [
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
                ],
                [],
            )
            write_rows(
                session_dir / "cs_all.csv",
                [
                    "sweep",
                    "conn_id",
                    "peer_name",
                    "tag_id",
                    "plan",
                    "pmode",
                    "quality_flag_percent",
                    "targets",
                    "statuses",
                    "qualities",
                ],
                [],
            )
            write_rows(
                session_dir / "cr_all.csv",
                [
                    "sweep",
                    "conn_id",
                    "peer_name",
                    "tag_id",
                    "plan",
                    "pmode",
                    "anchor_label",
                    "status",
                    "reason",
                    "raw_mm",
                    "filt_mm",
                    "pred_mm",
                    "resid_mm",
                    "tracker_quality_percent",
                    "solve_quality_percent",
                ],
                [],
            )
            write_rows(
                session_dir / "cf_all.csv",
                [
                    "sweep",
                    "conn_id",
                    "peer_name",
                    "tag_id",
                    "plan",
                    "pmode",
                    "solve_reason",
                    "quality_flag_percent",
                    "active_anchor_count",
                    "valid_anchor_count",
                    "rms_mm",
                    "max_mm",
                    "step_mm",
                ],
                [],
            )
            summary_json_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
            print(json.dumps(summary, indent=2))
            return 2

    conn_meta: dict[str, dict] = {}
    positions: list[dict] = []
    cm_rows: list[dict] = []
    cs_rows: list[dict] = []
    cr_rows: list[dict] = []
    cf_rows: list[dict] = []
    interrupted = False
    startup_failed = False
    startup_fail_targets: list[str] = []

    with raw_log_path.open("w", encoding="utf-8") as logf:
        with open_serial_with_retry(args.port, args.baud) as ser:
            time.sleep(0.8)

            # Initial drain
            drain_serial_until(ser, logf, 1.0)

            if args.skip_cm_probe:
                cm_probe["success"] = True
                print("[CAPTURE] startup CM probe skipped by flag", flush=True)
                ser = configure_recv_capture_session(ser, logf, args, targets, profile_items)
            else:
                for attempt in range(1, args.cm_probe_retries + 1):
                    print(
                        f"[CAPTURE] startup CM probe attempt {attempt}/{args.cm_probe_retries}: target={probe_target}",
                        flush=True,
                    )
                    ser = configure_recv_capture_session(ser, logf, args, targets, profile_items)
                    ser, probe_result = run_static_cm_probe(
                        ser, logf, probe_target, args.cm_probe_timeout_s
                    )
                    probe_result["attempt"] = attempt
                    cm_probe["attempts"].append(probe_result)
                    if probe_result.get("success"):
                        cm_probe["success"] = True
                        break

                    print(
                        f"[CAPTURE] startup CM probe failed: target={probe_target} ok={len(probe_result['ok_anchors'])}/8; forcing all anchors responder",
                        flush=True,
                    )
                    repair = run_anchor_responder_preflight(args, session_dir)
                    cm_probe["attempts"][-1]["responder_repair"] = repair
                    if not repair.get("success"):
                        break
                    try:
                        ser.close()
                    except Exception:
                        pass
                    ser = open_serial_with_retry(args.port, args.baud)
                    time.sleep(0.8)
                    drain_serial_until(ser, logf, 1.0)

            if not cm_probe.get("success"):
                summary = {
                    "success": False,
                    "startup_failed": True,
                    "startup_fail_targets": [probe_target],
                    "zero_position_failed": False,
                    "zero_position_targets": [],
                    "anchor_preflight": anchor_preflight,
                    "cm_probe": cm_probe,
                    "port": args.port,
                    "duration_s": args.duration,
                    "elapsed_s": time.time() - start_wall,
                    "session_dir": str(session_dir),
                    "targets": targets,
                    "profiles": {name: profile for name, profile in profile_items},
                    "expected_pmode": expected_pmode_by_target,
                    "freq_hz": {
                        "static": args.static_hz,
                        "roto": args.roto_hz,
                        "motion": args.motion_hz,
                    },
                    "positions_all": 0,
                    "cm_all": 0,
                    "cs_all": 0,
                    "cr_all": 0,
                    "cf_all": 0,
                    "connections": {},
                    "per_tag": {},
                    "raw_log": str(raw_log_path),
                    "positions_all_csv": str(session_dir / "positions_all.csv"),
                    "cm_all_csv": str(session_dir / "cm_all.csv"),
                    "cs_all_csv": str(session_dir / "cs_all.csv"),
                    "cr_all_csv": str(session_dir / "cr_all.csv"),
                    "cf_all_csv": str(session_dir / "cf_all.csv"),
                }
                write_rows(
                    session_dir / "positions_all.csv",
                    [
                        "sweep",
                        "conn_id",
                        "peer_name",
                        "tag_id",
                        "plan",
                        "pmode",
                        "plan_label",
                        "quality_flag_percent",
                        "x_mm",
                        "y_mm",
                        "z_mm",
                        "rms_mm",
                        "max_mm",
                        "anchors",
                        "motion_dt_ms",
                        "disp_mm",
                        "speed_mm_s",
                    ],
                    [],
                )
                write_rows(
                    session_dir / "cm_all.csv",
                    [
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
                    ],
                    [],
                )
                write_rows(
                    session_dir / "cs_all.csv",
                    [
                        "sweep",
                        "conn_id",
                        "peer_name",
                        "tag_id",
                        "plan",
                        "pmode",
                        "quality_flag_percent",
                        "targets",
                        "statuses",
                        "qualities",
                    ],
                    [],
                )
                write_rows(
                    session_dir / "cr_all.csv",
                    [
                        "sweep",
                        "conn_id",
                        "peer_name",
                        "tag_id",
                        "plan",
                        "pmode",
                        "anchor_label",
                        "status",
                        "reason",
                        "raw_mm",
                        "filt_mm",
                        "pred_mm",
                        "resid_mm",
                        "tracker_quality_percent",
                        "solve_quality_percent",
                    ],
                    [],
                )
                write_rows(
                    session_dir / "cf_all.csv",
                    [
                        "sweep",
                        "conn_id",
                        "peer_name",
                        "tag_id",
                        "plan",
                        "pmode",
                        "solve_reason",
                        "quality_flag_percent",
                        "active_anchor_count",
                        "valid_anchor_count",
                        "rms_mm",
                        "max_mm",
                        "step_mm",
                    ],
                    [],
                )
                summary_json_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
                print(json.dumps(summary, indent=2))
                return 2

            print(
                f"[CAPTURE] startup CM probe passed: target={probe_target} ok=8/8; start capture",
                flush=True,
            )

            pending = ""
            capture_start_wall = time.time()
            end_time = capture_start_wall + args.duration
            last_status_at = 0.0
            positions_seen: dict[str, int] = defaultdict(int)
            cm_seen: dict[str, int] = defaultdict(int)
            cm_ok_seen: dict[str, int] = defaultdict(int)
            startup_strikes: dict[str, int] = defaultdict(int)
            skipped_before_target_pmode = 0
            # The setup commands above drain serial output before the parser loop
            # starts. Seed the desired final pmode so CM rows, which do not carry
            # pmode in-band, are not discarded just because their CFG_ASSIGNED
            # line was consumed during command setup.
            pmode_by_peer: dict[str, int] = {
                name: pmode
                for name, pmode in expected_pmode_by_target.items()
                if pmode is not None
            }
            try:
                while time.time() < end_time:
                    try:
                        chunk = ser.read(ser.in_waiting or 1)
                    except (SerialException, OSError) as exc:
                        logf.write(
                            f"[HOST_WARN {time.monotonic():.3f}] capture read failed: {exc}; reopen\n"
                        )
                        logf.flush()
                        try:
                            ser.close()
                        except Exception:
                            pass
                        ser = open_serial_with_retry(args.port, args.baud)
                        time.sleep(0.8)
                        drain_serial_until(ser, logf, 0.8)
                        continue
                    if not chunk:
                        if time.time() - last_status_at >= 1.0:
                            print_capture_status(
                                capture_start_wall,
                                end_time,
                                positions,
                                cm_rows,
                                targets,
                                positions_seen,
                                cm_seen,
                            )
                            elapsed = time.time() - capture_start_wall
                            if elapsed >= 60.0:
                                bad = []
                                for target in targets:
                                    if cm_ok_seen.get(target, 0) == 0 and positions_seen.get(target, 0) == 0:
                                        startup_strikes[target] += 1
                                        if startup_strikes[target] >= 10:
                                            bad.append(target)
                                    else:
                                        startup_strikes[target] = 0
                                if bad:
                                    startup_failed = True
                                    startup_fail_targets = bad
                                    print(
                                        "[CAPTURE] startup failed after extended checks with no CM ok: "
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
                            meta["pmode"] = int(match.group("pmode"))
                            pmode_by_peer[match.group("bs")] = int(match.group("pmode"))

                        for m in iter_tag_summary_matches(line):
                            conn_id = m.groupdict().get("conn") or ""
                            meta = conn_meta.get(conn_id, {}) if conn_id else {}
                            peer_name = extract_bs_name(line) or meta.get("peer_name", "")
                            if peer_name and peer_name not in target_set:
                                continue
                            expected_pmode = expected_pmode_by_target.get(peer_name)
                            reported_pmode = m.groupdict().get("pmode")
                            active_pmode = (
                                int(reported_pmode) if reported_pmode
                                else pmode_by_peer.get(peer_name, meta.get("pmode"))
                            )
                            if expected_pmode is not None and active_pmode != expected_pmode:
                                skipped_before_target_pmode += 1
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
                                    "pmode": m.groupdict().get("pmode") or "",
                                    "plan_label": m.groupdict().get("plan_label") or "",
                                    "quality_flag_percent": m.groupdict().get("qf") or "",
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
                            conn_id = m.groupdict().get("conn") or ""
                            meta = conn_meta.get(conn_id, {}) if conn_id else {}
                            peer_name = extract_bs_name(line) or meta.get("peer_name", "")
                            if peer_name and peer_name not in target_set:
                                continue
                            expected_pmode = expected_pmode_by_target.get(peer_name)
                            active_pmode = pmode_by_peer.get(peer_name, meta.get("pmode"))
                            if expected_pmode is not None and active_pmode != expected_pmode:
                                skipped_before_target_pmode += 1
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

                        for m in iter_cs_matches(line):
                            conn_id = m.groupdict().get("conn") or ""
                            meta = conn_meta.get(conn_id, {}) if conn_id else {}
                            peer_name = extract_bs_name(line) or meta.get("peer_name", "")
                            if peer_name and peer_name not in target_set:
                                continue
                            expected_pmode = expected_pmode_by_target.get(peer_name)
                            active_pmode = int(m.group("pmode"))
                            if expected_pmode is not None and active_pmode != expected_pmode:
                                skipped_before_target_pmode += 1
                                continue
                            cs_rows.append(
                                {
                                    "conn_id": conn_id,
                                    "peer_name": peer_name,
                                    "tag_id": meta.get("tag_id", ""),
                                    "sweep": int(m.group("sweep")),
                                    "plan": m.group("plan"),
                                    "pmode": active_pmode,
                                    "quality_flag_percent": int(m.group("qf")),
                                    "targets": m.group("targets"),
                                    "statuses": m.group("statuses"),
                                    "qualities": m.group("qualities"),
                                }
                            )

                        for m in iter_cr_matches(line):
                            conn_id = m.groupdict().get("conn") or ""
                            meta = conn_meta.get(conn_id, {}) if conn_id else {}
                            peer_name = extract_bs_name(line) or meta.get("peer_name", "")
                            if peer_name and peer_name not in target_set:
                                continue
                            expected_pmode = expected_pmode_by_target.get(peer_name)
                            active_pmode = int(m.group("pmode"))
                            if expected_pmode is not None and active_pmode != expected_pmode:
                                skipped_before_target_pmode += 1
                                continue
                            cr_rows.append(
                                {
                                    "conn_id": conn_id,
                                    "peer_name": peer_name,
                                    "tag_id": meta.get("tag_id", ""),
                                    "sweep": int(m.group("sweep")),
                                    "plan": m.group("plan"),
                                    "pmode": active_pmode,
                                    "anchor_label": m.group("anchor"),
                                    "status": m.group("status"),
                                    "reason": m.group("reason"),
                                    "raw_mm": int(m.group("raw")),
                                    "filt_mm": int(m.group("filt")),
                                    "pred_mm": int(m.group("pred")),
                                    "resid_mm": int(m.group("resid")),
                                    "tracker_quality_percent": int(m.group("tracker_q")),
                                    "solve_quality_percent": int(m.group("solve_q")),
                                }
                            )

                        for m in iter_cf_matches(line):
                            conn_id = m.groupdict().get("conn") or ""
                            meta = conn_meta.get(conn_id, {}) if conn_id else {}
                            peer_name = extract_bs_name(line) or meta.get("peer_name", "")
                            if peer_name and peer_name not in target_set:
                                continue
                            expected_pmode = expected_pmode_by_target.get(peer_name)
                            active_pmode = int(m.group("pmode"))
                            if expected_pmode is not None and active_pmode != expected_pmode:
                                skipped_before_target_pmode += 1
                                continue
                            cf_rows.append(
                                {
                                    "conn_id": conn_id,
                                    "peer_name": peer_name,
                                    "tag_id": meta.get("tag_id", ""),
                                    "sweep": int(m.group("sweep")),
                                    "plan": m.group("plan"),
                                    "pmode": active_pmode,
                                    "solve_reason": m.group("solve_reason"),
                                    "quality_flag_percent": int(m.group("qf")),
                                    "active_anchor_count": int(m.group("active")),
                                    "valid_anchor_count": int(m.group("valid")),
                                    "rms_mm": int(m.group("rms")),
                                    "max_mm": int(m.group("max")),
                                    "step_mm": int(m.group("step")),
                                }
                            )

                    if time.time() - last_status_at >= 1.0:
                        print_capture_status(
                            capture_start_wall,
                            end_time,
                            positions,
                            cm_rows,
                            targets,
                            positions_seen,
                            cm_seen,
                        )
                        elapsed = time.time() - capture_start_wall
                        if elapsed >= 60.0:
                            bad = []
                            for target in targets:
                                if cm_ok_seen.get(target, 0) == 0 and positions_seen.get(target, 0) == 0:
                                    startup_strikes[target] += 1
                                    if startup_strikes[target] >= 10:
                                        bad.append(target)
                                else:
                                    startup_strikes[target] = 0
                            if bad:
                                startup_failed = True
                                startup_fail_targets = bad
                                print(
                                    "[CAPTURE] startup failed after extended checks with no CM ok: "
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
        "pmode",
        "plan_label",
        "quality_flag_percent",
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
    cs_fields = [
        "sweep",
        "conn_id",
        "peer_name",
        "tag_id",
        "plan",
        "pmode",
        "quality_flag_percent",
        "targets",
        "statuses",
        "qualities",
    ]
    cr_fields = [
        "sweep",
        "conn_id",
        "peer_name",
        "tag_id",
        "plan",
        "pmode",
        "anchor_label",
        "status",
        "reason",
        "raw_mm",
        "filt_mm",
        "pred_mm",
        "resid_mm",
        "tracker_quality_percent",
        "solve_quality_percent",
    ]
    cf_fields = [
        "sweep",
        "conn_id",
        "peer_name",
        "tag_id",
        "plan",
        "pmode",
        "solve_reason",
        "quality_flag_percent",
        "active_anchor_count",
        "valid_anchor_count",
        "rms_mm",
        "max_mm",
        "step_mm",
    ]

    write_rows(session_dir / "positions_all.csv", position_fields, positions)
    write_rows(session_dir / "cm_all.csv", cm_fields, cm_rows)
    write_rows(session_dir / "cs_all.csv", cs_fields, cs_rows)
    write_rows(session_dir / "cr_all.csv", cr_fields, cr_rows)
    write_rows(session_dir / "cf_all.csv", cf_fields, cf_rows)

    per_tag_summary: dict[str, dict] = {}
    zero_position_targets: list[str] = []
    cs_by_target: dict[str, list[dict]] = defaultdict(list)
    cr_by_target: dict[str, list[dict]] = defaultdict(list)
    cf_by_target: dict[str, list[dict]] = defaultdict(list)
    for row in cs_rows:
        key = row["peer_name"] or f"tag{row['tag_id']}"
        cs_by_target[key].append(row)
    for row in cr_rows:
        key = row["peer_name"] or f"tag{row['tag_id']}"
        cr_by_target[key].append(row)
    for row in cf_rows:
        key = row["peer_name"] or f"tag{row['tag_id']}"
        cf_by_target[key].append(row)
    for target in targets:
        tag_dir = session_dir / target
        tag_dir.mkdir(parents=True, exist_ok=True)
        pos_rows = positions_by_target.get(target, [])
        cm_target_rows = cm_by_target.get(target, [])
        cs_target_rows = cs_by_target.get(target, [])
        cr_target_rows = cr_by_target.get(target, [])
        cf_target_rows = cf_by_target.get(target, [])
        write_rows(tag_dir / "positions.csv", position_fields, pos_rows)
        write_rows(tag_dir / "cm.csv", cm_fields, cm_target_rows)
        write_rows(tag_dir / "cs.csv", cs_fields, cs_target_rows)
        write_rows(tag_dir / "cr.csv", cr_fields, cr_target_rows)
        write_rows(tag_dir / "cf.csv", cf_fields, cf_target_rows)

        reason_counts: dict[str, int] = {}
        anchor_reason_counts: dict[str, dict[str, int]] = {}
        for row in cr_target_rows:
            reason = row["reason"]
            reason_counts[reason] = reason_counts.get(reason, 0) + 1
            anchor_reason_counts.setdefault(row["anchor_label"], {})
            anchor_reason_counts[row["anchor_label"]][reason] = (
                anchor_reason_counts[row["anchor_label"]].get(reason, 0) + 1
            )

        per_tag_summary[target] = {
            "position_rows": len(pos_rows),
            "cm_rows": len(cm_target_rows),
            "cs_rows": len(cs_target_rows),
            "cr_rows": len(cr_target_rows),
            "cf_rows": len(cf_target_rows),
            "latest_position": pos_rows[-1] if pos_rows else None,
            "latest_calibration_summary": cs_target_rows[-1] if cs_target_rows else None,
            "latest_calibration_reject": cr_target_rows[-1] if cr_target_rows else None,
            "latest_calibration_frame": cf_target_rows[-1] if cf_target_rows else None,
            "anchors_seen": sorted({row["anchor_id"] for row in cm_target_rows}) if cm_target_rows else [],
            "status_counts": {
                status: sum(1 for row in cm_target_rows if row["status"] == status)
                for status in sorted({row["status"] for row in cm_target_rows})
            },
            "reject_reason_counts": reason_counts,
            "anchor_reject_reason_counts": anchor_reason_counts,
        }
        profile = dict(profile_items).get(target, "")
        if profile_expects_positions(profile) and not pos_rows:
            zero_position_targets.append(target)

    zero_position_failed = bool(zero_position_targets) and not args.allow_zero_positions
    summary = {
        "success": (not interrupted) and (not startup_failed) and (not zero_position_failed),
        "interrupted": interrupted,
        "startup_failed": startup_failed,
        "startup_fail_targets": startup_fail_targets,
        "zero_position_failed": zero_position_failed,
        "zero_position_targets": zero_position_targets,
        "anchor_preflight": anchor_preflight,
        "cm_probe": cm_probe,
        "port": args.port,
        "duration_s": args.duration,
        "elapsed_s": time.time() - start_wall,
        "session_dir": str(session_dir),
        "targets": targets,
        "profiles": {name: profile for name, profile in profile_items},
        "expected_pmode": expected_pmode_by_target,
        "skipped_before_target_pmode": skipped_before_target_pmode,
        "freq_hz": {
            "static": args.static_hz,
            "roto": args.roto_hz,
            "motion": args.motion_hz,
        },
        "positions_all": len(positions),
        "cm_all": len(cm_rows),
        "cs_all": len(cs_rows),
        "cr_all": len(cr_rows),
        "cf_all": len(cf_rows),
        "connections": conn_meta,
        "per_tag": per_tag_summary,
        "raw_log": str(raw_log_path),
        "positions_all_csv": str(session_dir / "positions_all.csv"),
        "cm_all_csv": str(session_dir / "cm_all.csv"),
        "cs_all_csv": str(session_dir / "cs_all.csv"),
        "cr_all_csv": str(session_dir / "cr_all.csv"),
        "cf_all_csv": str(session_dir / "cf_all.csv"),
    }
    summary_json_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
