#!/usr/bin/env python3
import argparse
import time
from pathlib import Path

import serial
from serial import SerialException

from run_autopos_sweep_loop import UUIDS, open_port, reopen_port, write_cmd, collect_for_text
from run_autopos_sweep_loop import send_cmd_collect_text, send_cmd_collect, wait_for_patterns, quarantine_tag_for_sweep


def wait_for_any(
    ser: serial.Serial,
    logf,
    port: str,
    timeout_s: float,
    patterns: list[str],
    live_output: bool,
    verbose: int,
) -> tuple[serial.Serial, str]:
    deadline = time.time() + timeout_s
    chunks: list[str] = []
    while time.time() < deadline:
        try:
            data = ser.read(4096)
        except (SerialException, OSError):
            logf.write("--- SERIAL DISCONNECTED, REOPEN ---\n")
            try:
                ser.close()
            except Exception:
                pass
            ser = reopen_port(port)
            logf.write("--- SERIAL REOPENED ---\n")
            continue
        if not data:
            time.sleep(0.05)
            continue
        text = data.decode("utf-8", "ignore")
        chunks.append(text)
        logf.write(text)
        merged = "".join(chunks)
        if any(p in merged for p in patterns):
            return ser, merged
    return ser, "".join(chunks)


def run_anchor_responder(
    ser: serial.Serial,
    logf,
    port: str,
    quiet_tag_name: str | None,
    timeout_s: float,
) -> tuple[serial.Serial, bool]:
    # Optional: quarantine Tag so it stays powered-on but does not range.
    if quiet_tag_name:
        ser, ok = quarantine_tag_for_sweep(ser, logf, port, quiet_tag_name, True, 1)
        if not ok:
            return ser, False

    # Enter AUTOPOS and map anchors.
    ser = send_cmd_collect(ser, logf, port, "mode autopos", 1.0, True, 1)
    ser = send_cmd_collect(ser, logf, port, "device kind anchor", 0.6, True, 1)
    for label, uuid in UUIDS.items():
        ser = send_cmd_collect(ser, logf, port, f"autopos map {label} {uuid}", 0.25, True, 1)

    ser = send_cmd_collect(ser, logf, port, "anchor role all responder", 0.6, True, 1)

    # Wait for final success marker. The firmware also prints per-anchor rc lines,
    # but the 'target=all' line is the clean pass condition.
    ser, ok, _ = wait_for_patterns(
        ser,
        logf,
        port,
        ["anchor role rc=0 target=all role=responder"],
        timeout_s,
        True,
        1,
    )
    return ser, ok


def run_tag_cm_100(
    ser: serial.Serial,
    logf,
    port: str,
    target_name: str,
    cm_lines: int,
    capture_timeout_s: float,
) -> tuple[serial.Serial, bool]:
    # Switch to RECV and ensure Tag is connected/ready.
    ser = send_cmd_collect(ser, logf, port, "device kind tag", 0.6, True, 1)
    ser, _ = send_cmd_collect_text(ser, logf, port, f"ota_target name {target_name}", 0.8, True, 1)
    ser = send_cmd_collect(ser, logf, port, "mode recv", 2.8, True, 1, resend_after_reopen=False)

    # After mode recv, firmware reboots; drain some time to let it come back.
    ser, _, _ = collect_for_text(ser, logf, 6.0, port, True, 1)

    # Re-assert known-good sequence.
    ser = send_cmd_collect(ser, logf, port, "device kind tag", 0.8, True, 1)
    ser, _ = send_cmd_collect_text(ser, logf, port, f"ota_target name {target_name}", 0.8, True, 1)
    ser = send_cmd_collect(ser, logf, port, "conn", 1.0, True, 1)

    ser, ready_ok, _ = wait_for_patterns(
        ser,
        logf,
        port,
        ["DISC complete[0]", f"{target_name} notify: CFG_OK"],
        30.0,
        True,
        1,
    )
    if not ready_ok:
        return ser, False

    # Trigger MCAL and capture CM lines. Do not require MODE_OK MODE=CAL, because
    # deployed Tag builds are not consistent about emitting it.
    ser = send_cmd_collect(ser, logf, port, "oneshot MCAL", 0.5, True, 1)

    deadline = time.time() + capture_timeout_s
    count = 0
    first = ""
    while time.time() < deadline:
        try:
            data = ser.read(4096)
        except (SerialException, OSError):
            logf.write("--- SERIAL DISCONNECTED, REOPEN ---\n")
            try:
                ser.close()
            except Exception:
                pass
            ser = reopen_port(port)
            logf.write("--- SERIAL REOPENED ---\n")
            continue
        if not data:
            time.sleep(0.05)
            continue
        text = data.decode("utf-8", "ignore")
        logf.write(text)
        for line in text.splitlines():
            if f"{target_name} notify: CM;" not in line:
                continue
            count += 1
            if not first:
                first = line.strip()
            if count >= cm_lines:
                logf.write(f"\nCM_CAPTURE_DONE count={count} first={first}\n")
                return ser, True

    logf.write(f"\nCM_CAPTURE_TIMEOUT count={count} need={cm_lines}\n")
    return ser, False


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Assume sweep already done, convert anchors A-H to responder, then connect Tag115 and capture CM lines."
    )
    ap.add_argument("--port", required=True, help="52840 CDC port")
    ap.add_argument("--target-name", default="BSF66F", help="Tag BLE target name (Tag115)")
    ap.add_argument("--cm-lines", type=int, default=100, help="Required aggregated CM lines")
    ap.add_argument("--cm-timeout-s", type=float, default=900.0, help="CM capture deadline")
    ap.add_argument("--anchor-timeout-s", type=float, default=900.0, help="Responder conversion deadline")
    ap.add_argument(
        "--quiet-tag-name",
        default="BSF66F",
        help="Keep this Tag online but quarantine it into MODE AOTA before responder conversion. Use '-' to disable.",
    )
    ap.add_argument("--out-dir", required=True, help="Output directory")
    args = ap.parse_args()

    quiet = args.quiet_tag_name
    if quiet == "-":
        quiet = None

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    log_path = out_dir / "run.log"

    ser = open_port(args.port, 20.0)
    try:
        with open(log_path, "w", buffering=1) as logf:
            logf.write(f"PORT={args.port}\n")
            logf.write(f"START={time.strftime('%Y-%m-%d %H:%M:%S')}\n")
            logf.write(f"TARGET={args.target_name}\n")
            logf.write(f"CM_LINES={args.cm_lines}\n")

            ok1 = False
            ok2 = False
            ser, ok1 = run_anchor_responder(ser, logf, args.port, quiet, args.anchor_timeout_s)
            logf.write(f"\nANCHOR_RESPONDER_DONE ok={int(ok1)}\n")
            if ok1:
                ser, ok2 = run_tag_cm_100(
                    ser,
                    logf,
                    args.port,
                    args.target_name,
                    args.cm_lines,
                    args.cm_timeout_s,
                )
            logf.write(f"\nTAG_CM_DONE ok={int(ok2)}\n")
            logf.write(f"END={time.strftime('%Y-%m-%d %H:%M:%S')}\n")
            return 0 if (ok1 and ok2) else 1
    finally:
        try:
            ser.close()
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(main())

