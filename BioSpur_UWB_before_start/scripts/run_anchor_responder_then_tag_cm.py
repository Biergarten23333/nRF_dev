#!/usr/bin/env python3
import argparse
import re
import time
from pathlib import Path

import serial
from serial import SerialException

from run_autopos_sweep_loop import UUIDS, open_port, reopen_port, write_cmd, collect_for_text
from run_autopos_sweep_loop import send_cmd_collect_text, send_cmd_collect, wait_for_patterns, quarantine_tag_for_sweep


def ensure_ota_target_name(
    ser: serial.Serial,
    logf,
    port: str,
    target_name: str,
    live_output: bool,
    verbose: int,
    retries: int = 4,
) -> tuple[serial.Serial, bool]:
    """
    Firmware may clear ota_target back to name='-' after kind/mode switches.
    Re-apply and verify effective filter before using oneshot paths (MCAL).
    """
    want_l = target_name.lower()
    for attempt in range(1, retries + 1):
        ser, txt = send_cmd_collect_text(
            ser,
            logf,
            port,
            f"ota_target name {target_name}",
            1.0,
            live_output,
            verbose,
        )
        ack_ok = (f"ota_target name rc=0 value={want_l}" in txt) or (f"ota_target name rc=0 value={target_name}" in txt)

        # Ask for show so we can verify even if the ack line was lost in serial backlog.
        ser, show = send_cmd_collect_text(
            ser,
            logf,
            port,
            "ota_target show",
            0.9,
            live_output,
            verbose,
        )
        show_ok = (f"name={want_l}" in show) or (f"name={target_name}" in show)
        if ack_ok or show_ok:
            return ser, True

        logf.write(f"OTA_TARGET_RETRY attempt={attempt}/{retries} want={target_name}\n")
        time.sleep(0.25)

    return ser, False


def ensure_tag_stream_on_for_cm(
    ser: serial.Serial,
    logf,
    port: str,
    target_name: str,
    live_output: bool,
    verbose: int,
    retries: int = 3,
) -> tuple[serial.Serial, bool]:
    """
    Best-effort restore of Tag live streaming before CM capture.

    Sweep/OTA preflight may quarantine Tag with STREAM OFF. For CM capture we
    explicitly try to re-enable streaming so MCAL output is not silently muted.
    """
    stream_on_cmds = [
        "cmd STREAM ON",
        "cmd STREAMON 1",
        "cmd STREAM 1",
    ]
    unsupported_seen = 0
    total_tries = 0
    for attempt in range(1, retries + 1):
        for stream_cmd in stream_on_cmds:
            total_tries += 1
            ser, txt = send_cmd_collect_text(
                ser,
                logf,
                port,
                stream_cmd,
                0.9,
                live_output,
                verbose,
            )
            ok = ("STREAM_OK ON" in txt) or ("STREAM_OK ON LIVE=1" in txt) or ("STREAM=ON" in txt)
            if not ok:
                ser, ok, more = wait_for_patterns(
                    ser,
                    logf,
                    port,
                    ["STREAM_OK ON", "STREAM_OK ON LIVE=1", "STREAM=ON"],
                    3.0,
                    live_output,
                    verbose,
                )
                txt += more
            if ok:
                logf.write(f"PRECHECK PASS: tag {target_name} stream enabled for CM via '{stream_cmd}'\n")
                return ser, True

            if "UNKNOWN_CMD" in txt or "cmd rc=-128" in txt:
                unsupported_seen += 1
                logf.write(
                    f"PRECHECK WARN: tag {target_name} rejected '{stream_cmd}' "
                    f"(attempt={attempt}/{retries})\n"
                )
            else:
                logf.write(
                    f"PRECHECK INFO: tag {target_name} no stream-on ack for '{stream_cmd}' "
                    f"(attempt={attempt}/{retries})\n"
                )
            time.sleep(0.1)

        # If every known STREAM ON variant is rejected, stop early.
        if unsupported_seen >= total_tries and attempt == 1:
            break

    logf.write(f"PRECHECK WARN: tag {target_name} stream-on not confirmed; continue with MCAL\n")
    return ser, False


def ensure_tag_mode_cal(
    ser: serial.Serial,
    logf,
    port: str,
    target_name: str,
    live_output: bool,
    verbose: int,
    retries: int = 3,
) -> tuple[serial.Serial, bool]:
    """
    Force calibration mode and verify with MODE? or CM output.
    """
    cal_cmds = ["cmd MODE CAL", "cmd MCAL", "oneshot MCAL"]
    for attempt in range(1, retries + 1):
        for cal_cmd in cal_cmds:
            ser, txt = send_cmd_collect_text(
                ser,
                logf,
                port,
                cal_cmd,
                0.9,
                live_output,
                verbose,
            )
            if "target mismatch" in txt or "rc=-128" in txt:
                continue
            if "MODE_OK MODE=CAL" in txt:
                logf.write(f"PRECHECK PASS: mode CAL via '{cal_cmd}'\n")
                return ser, True

            ser, qtxt = send_cmd_collect_text(
                ser,
                logf,
                port,
                "cmd MODE?",
                0.8,
                live_output,
                verbose,
            )
            merged = txt + qtxt
            if "MODE=CAL" in merged or "MODE_OK MODE=CAL" in merged:
                logf.write(f"PRECHECK PASS: mode CAL confirmed after '{cal_cmd}'\n")
                return ser, True

            ser, probe = wait_for_any(
                ser,
                logf,
                port,
                3.0,
                [f"{target_name} notify: CM;"],
                live_output,
                verbose,
            )
            if f"{target_name} notify: CM;" in probe:
                logf.write(f"PRECHECK PASS: CM observed after '{cal_cmd}'\n")
                return ser, True

        logf.write(f"PRECHECK RETRY: mode CAL not confirmed (attempt={attempt}/{retries})\n")
        time.sleep(0.2)

    return ser, False


def ensure_tag_link_ready_in_recv(
    ser: serial.Serial,
    logf,
    port: str,
    target_name: str,
    live_output: bool,
    verbose: int,
    retries: int = 4,
) -> tuple[serial.Serial, bool]:
    """
    In RECV mode, aggressively drive scan/conn until the NUS path is visibly ready.
    This avoids cmd/oneshot rc=-128 when no matching live peer is connected yet.
    """
    target_l = target_name.lower()
    ready_patterns = [
        "Connected[0]:",
        "BLE[0] link ready",
        "DISC complete[0]: link=nus",
        f"CFG assigned[0]: bs={target_name}",
        f"{target_name} notify:",
    ]
    last_bs = ""
    for attempt in range(1, retries + 1):
        ser = send_cmd_collect(ser, logf, port, "scan", 0.8, live_output, verbose)
        ser, conn_txt = send_cmd_collect_text(ser, logf, port, "conn", 1.2, live_output, verbose)
        ser, wait_txt = wait_for_any(
            ser,
            logf,
            port,
            8.0,
            ready_patterns,
            live_output,
            verbose,
        )
        merged = (conn_txt + wait_txt).lower()
        m = re.search(r"cfg assigned\[0\]: bs=([A-Za-z0-9]+)", conn_txt + wait_txt)
        if m:
            last_bs = m.group(1)
        if (
            "connected[0]:" in merged
            or "ble[0] link ready" in merged
            or "disc complete[0]: link=nus" in merged
            or f"cfg assigned[0]: bs={target_l}" in merged
            or f"{target_l} notify:" in merged
        ):
            logf.write(f"PRECHECK PASS: tag link ready in RECV (attempt={attempt}/{retries})\n")
            return ser, True

        if last_bs:
            logf.write(
                f"PRECHECK RETRY: tag link not ready yet (attempt={attempt}/{retries}) "
                f"last_bs={last_bs}\n"
            )
        else:
            logf.write(f"PRECHECK RETRY: tag link not ready yet (attempt={attempt}/{retries})\n")
        time.sleep(0.25)

    # Fallback: if target name matching became stale, briefly allow any BS* and retry.
    logf.write("PRECHECK FALLBACK: exact target not ready, retry with name='-' prefix='BS'\n")
    ser, _ = send_cmd_collect_text(ser, logf, port, "ota_target name -", 0.8, live_output, verbose)
    ser, _ = send_cmd_collect_text(ser, logf, port, "ota_target prefix BS", 0.8, live_output, verbose)
    ser = send_cmd_collect(ser, logf, port, "scan", 0.8, live_output, verbose)
    ser, conn_txt = send_cmd_collect_text(ser, logf, port, "conn", 1.2, live_output, verbose)
    ser, wait_txt = wait_for_any(
        ser,
        logf,
        port,
        8.0,
        ready_patterns,
        live_output,
        verbose,
    )
    merged = (conn_txt + wait_txt).lower()
    m = re.search(r"cfg assigned\[0\]: bs=([A-Za-z0-9]+)", conn_txt + wait_txt)
    if m:
        last_bs = m.group(1)
    if "connected[0]:" in merged or "ble[0] link ready" in merged or "disc complete[0]: link=nus" in merged:
        if last_bs:
            logf.write(f"PRECHECK PASS: fallback tag link ready, connected bs={last_bs}\n")
        else:
            logf.write("PRECHECK PASS: fallback tag link ready\n")
        # Restore intended target filter for subsequent commands.
        ser, _ = ensure_ota_target_name(ser, logf, port, target_name, live_output, verbose, retries=2)
        ser, _ = send_cmd_collect_text(ser, logf, port, "ota_target prefix BS", 0.8, live_output, verbose)
        return ser, True

    return ser, False


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
            # Converting anchors to responder is still useful even if we couldn't
            # quarantine the Tag. Don't abort the whole run here.
            logf.write(f"PRECHECK WARN: tag quarantine not reached for {quiet_tag_name}; continuing responder conversion\n")

    # Enter AUTOPOS and map anchors.
    ser = send_cmd_collect(ser, logf, port, "mode autopos", 1.0, True, 1)
    ser = send_cmd_collect(ser, logf, port, "device kind anchor", 0.6, True, 1)
    # Mapping a full A-H table is a hard prerequisite for "anchor role all ...".
    # With a short serial timeout it's easy to miss the confirmation line and
    # immediately proceed, which causes unmapped anchors to be skipped later.
    for label, uuid in UUIDS.items():
        want = f"AUTOPOS map set: {label}={uuid}"
        ok = False
        for attempt in range(1, 4):
            ser, txt = send_cmd_collect_text(
                ser,
                logf,
                port,
                f"autopos map {label} {uuid}",
                0.9,
                True,
                1,
            )
            if want in txt:
                ok = True
                break
            logf.write(f"AUTOPOS_MAP_RETRY label={label} attempt={attempt}/3\n")
            time.sleep(0.15)
        if not ok:
            logf.write(f"PRECHECK FAIL: autopos map did not confirm label={label} uuid={uuid}\n")
            return ser, False

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
    fastpath_only: bool = False,
) -> tuple[serial.Serial, bool]:
    def capture_cm_loop(initial_text: str = "") -> tuple[serial.Serial, bool]:
        nonlocal ser
        deadline = time.time() + capture_timeout_s
        count = 0
        first = ""
        no_cm_grace_s = 8.0
        next_retrigger_at = time.time() + no_cm_grace_s
        retrigger_count = 0

        def absorb_text(text: str) -> tuple[int, str]:
            local_count = 0
            local_first = ""
            for line in text.splitlines():
                if f"{target_name} notify: CM;" not in line:
                    continue
                local_count += 1
                if not local_first:
                    local_first = line.strip()
            return local_count, local_first

        if initial_text:
            c0, f0 = absorb_text(initial_text)
            count += c0
            if f0:
                first = f0
            if count >= cm_lines:
                logf.write(f"\nCM_CAPTURE_DONE count={count} first={first}\n")
                return ser, True

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
            c1, f1 = absorb_text(text)
            count += c1
            if f1 and not first:
                first = f1
            if count >= cm_lines:
                logf.write(f"\nCM_CAPTURE_DONE count={count} first={first}\n")
                return ser, True

            # If CM is still missing, re-trigger MCAL without link churn.
            if count == 0 and time.time() > next_retrigger_at:
                retrigger_count += 1
                logf.write(
                    f"\nCM_CAPTURE_NO_DATA after {no_cm_grace_s:.0f}s; retrigger MCAL "
                    f"attempt={retrigger_count}\n"
                )
                ser, _ = ensure_ota_target_name(ser, logf, port, target_name, True, 1, retries=2)
                ser = send_cmd_collect(ser, logf, port, "cmd MCAL", 0.8, True, 1)
                ser = send_cmd_collect(ser, logf, port, "oneshot MCAL", 0.8, True, 1)
                next_retrigger_at = time.time() + no_cm_grace_s

        logf.write(f"\nCM_CAPTURE_TIMEOUT count={count} need={cm_lines}\n")
        return ser, False

    # Fast path: if Tag is already connected and CM can start immediately, skip mode reboot.
    ser = send_cmd_collect(ser, logf, port, "device kind tag", 0.6, True, 1)
    ser, ok = ensure_ota_target_name(ser, logf, port, target_name, True, 1, retries=3)
    if ok:
        ser, ready = ensure_tag_link_ready_in_recv(ser, logf, port, target_name, True, 1, retries=2)
        if ready:
            ser, _ = ensure_tag_stream_on_for_cm(ser, logf, port, target_name, True, 1, retries=2)
            # Do not hard-gate on MODE_OK in fast path: some builds output CM without it.
            ser = send_cmd_collect(ser, logf, port, "cmd MCAL", 0.8, True, 1)
            ser = send_cmd_collect(ser, logf, port, "oneshot MCAL", 0.8, True, 1)
            ser, probe_txt = wait_for_any(
                ser,
                logf,
                port,
                6.0,
                [f"{target_name} notify: CM;"],
                True,
                1,
            )
            if f"{target_name} notify: CM;" in probe_txt:
                logf.write("FASTPATH PASS: tag already connected; CM seen without mode reboot\n")
                ser, ok_fast = capture_cm_loop(initial_text=probe_txt)
                if ok_fast:
                    return ser, True
                logf.write("FASTPATH FALLBACK: CM capture not completed; switching to full path\n")
            else:
                logf.write("FASTPATH INFO: no CM immediately; fallback to full path\n")
    if fastpath_only:
        logf.write("FASTPATH ONLY: direct CM path not ready\n")
        return ser, False

    # Switch to RECV and ensure Tag is connected/ready.
    ser = send_cmd_collect(ser, logf, port, "device kind tag", 0.6, True, 1)
    ser, _ = ensure_ota_target_name(ser, logf, port, target_name, True, 1, retries=3)
    ser = send_cmd_collect(ser, logf, port, "mode recv", 2.8, True, 1, resend_after_reopen=False)

    # After mode recv, firmware reboots; drain some time to let it come back.
    ser, _, _ = collect_for_text(ser, logf, 6.0, port, True, 1)

    # Re-assert known-good sequence.
    ser = send_cmd_collect(ser, logf, port, "device kind tag", 0.8, True, 1)
    ser, ok = ensure_ota_target_name(ser, logf, port, target_name, True, 1, retries=6)
    if not ok:
        logf.write(f"PRECHECK FAIL: could not lock ota_target name={target_name} after reboot\n")
        return ser, False
    ser, ready = ensure_tag_link_ready_in_recv(ser, logf, port, target_name, True, 1, retries=5)
    if not ready:
        logf.write(f"PRECHECK FAIL: tag {target_name} not connected/ready in RECV after mode switch\n")
        return ser, False

    # Trigger MCAL and capture CM lines.
    #
    # Do NOT hard-block on seeing "DISC complete[0]" + "CFG_OK" here: depending on timing,
    # those markers may have already been emitted earlier during the reconnect, and may not
    # re-emit on subsequent "conn" calls. A strict wait can therefore produce false failures.
    #
    # Instead:
    # - ensure ota_target is stable
    # - force MODE CAL and verify
    # - if no CM arrives for a while, re-issue conn + oneshot once as a recovery action
    ser, ok = ensure_ota_target_name(ser, logf, port, target_name, True, 1, retries=3)
    if not ok:
        logf.write(f"PRECHECK FAIL: ota_target name={target_name} not stable before MCAL\n")
        return ser, False
    ser, _ = ensure_tag_stream_on_for_cm(ser, logf, port, target_name, True, 1, retries=3)
    # Keep MCAL triggering best-effort and rely on CM lines as the success criterion.
    ser = send_cmd_collect(ser, logf, port, "cmd MCAL", 0.8, True, 1)
    ser = send_cmd_collect(ser, logf, port, "oneshot MCAL", 0.8, True, 1)

    return capture_cm_loop()


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
    ap.add_argument(
        "--autopos-fallback",
        action="store_true",
        help="If set, fallback to AUTOPOS anchor role conversion when direct RECV/CM path fails.",
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

            # Fast path first: if current runtime is already Tag-ready and CM can be
            # started directly, skip the full anchor role conversion loop.
            ser, ok2 = run_tag_cm_100(
                ser,
                logf,
                args.port,
                args.target_name,
                args.cm_lines,
                args.cm_timeout_s,
                fastpath_only=True,
            )
            if ok2:
                ok1 = True
                logf.write("\nANCHOR_RESPONDER_DONE ok=1 (fastpath reuse)\n")
                logf.write("\nTAG_CM_DONE ok=1\n")
                logf.write(f"END={time.strftime('%Y-%m-%d %H:%M:%S')}\n")
                return 0

            # Second try: stay in direct RECV/Tag flow (no AUTOPOS anchor reconfigure yet).
            # This prevents premature mode hopping when CM is already close to ready.
            logf.write("\nDIRECT_RECV_RETRY begin (no anchor reconfigure)\n")
            ser, ok2 = run_tag_cm_100(
                ser,
                logf,
                args.port,
                args.target_name,
                args.cm_lines,
                args.cm_timeout_s,
                fastpath_only=False,
            )
            if ok2:
                ok1 = True
                logf.write("\nANCHOR_RESPONDER_DONE ok=1 (direct recv retry)\n")
                logf.write("\nTAG_CM_DONE ok=1\n")
                logf.write(f"END={time.strftime('%Y-%m-%d %H:%M:%S')}\n")
                return 0

            # Last resort only: optional AUTOPOS fallback.
            if not args.autopos_fallback:
                logf.write("\nDIRECT_RECV_RETRY failed; AUTOPOS fallback disabled\n")
                logf.write("\nANCHOR_RESPONDER_DONE ok=0 (disabled)\n")
                logf.write("\nTAG_CM_DONE ok=0\n")
                logf.write(f"END={time.strftime('%Y-%m-%d %H:%M:%S')}\n")
                return 1

            logf.write("\nDIRECT_RECV_RETRY failed; fallback to AUTOPOS anchor role flow\n")
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
                    fastpath_only=False,
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
