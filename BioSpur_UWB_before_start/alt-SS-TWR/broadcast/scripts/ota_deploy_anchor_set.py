#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import subprocess
import time
from pathlib import Path

import serial
from serial import SerialException


UUIDS = {
    "A": "4DC6B8187E33803AE8601FB0D7992B96",
    "B": "B9179575C776C98F1CB132DD6EDC6223",
    "C": "CEE5A7EFCB35F8A56B430047629F5309",
    "D": "AB14CCA262A092E70EB26B0ACB0A394B",
    "E": "A892AF05DD59CF0D0D3408AD74F364A1",
    "F": "840C68591E90019821AACFF1B73AAA34",
    "G": "B3087BC3D87CCCD316AEDC6B71D6677F",
    "H": "1EABFBEC28B8053FBB0D5C448112AE93",
}

UPLOAD_PROGRESS_RE = re.compile(r"OTA upload progress:\s*(\d+)%")
ANCHOR_VERSION_RE = re.compile(
    r"ANCHOR_VERSION query=(?P<query>\S+)\s+uuid=(?P<uuid>[0-9A-F]+)\s+fw=(?P<fw>\S+)\s+label=(?P<label>\S+)\s+role=(?P<role>\S+)"
)
ANCHOR_FW_NOTIFY_RE = re.compile(
    r"ANCHOR_CTRL\[\d+\]\s+notify:\s+ANCHOR_FW\s+fw=(?P<fw>\S+)\s+bs=(?P<bs>\S+)\s+uuid=(?P<uuid>[0-9A-F]+)\s+label=(?P<label>[A-H])(?:\s+role=(?P<role>\S+)|\s+r)?"
)


def detect_stage(log_path: Path) -> tuple[str, int | None]:
    try:
        text = log_path.read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return "starting", None

    if "ota_success_observed" in text or "OTA command sequence sent" in text:
        return "success-seen", 100
    if "OTA pending-state recovery reset request" in text:
        return "recovery-reset", None
    if "OTA reset request" in text:
        return "reset", None
    if "OTA pending/test request" in text:
        return "pending-test", None
    if "OTA upload complete" in text:
        return "upload-complete", 100

    matches = UPLOAD_PROGRESS_RE.findall(text)
    if matches:
        return "upload", int(matches[-1])

    if "OTA upload starting" in text:
        return "upload", 0
    if "DFU SMP service ready" in text:
        return "dfu-ready", None
    if "Connected: " in text:
        return "connected", None
    if "mode ota (already ota)" in text:
        return "already-ota", None
    if "Control mode loaded: OTA" in text:
        return "ota-mode", None
    if "phase=A anchor_ready_uuid_write" in text or "ota_target uuid rc=0" in text:
        return "targeting", None
    return "starting", None


def format_eta(seconds: float | None) -> str:
    if seconds is None or seconds < 0:
        return "--"
    total = int(round(seconds))
    mins, secs = divmod(total, 60)
    if mins >= 60:
        hours, mins = divmod(mins, 60)
        return f"{hours}h{mins:02d}m"
    return f"{mins}m{secs:02d}s"


def estimate_eta(stage: str, percent: int | None, started_at: float) -> str:
    if stage != "upload" or percent is None or percent <= 0:
        return "--"
    elapsed = time.monotonic() - started_at
    if elapsed <= 0:
        return "--"
    total_est = elapsed / (percent / 100.0)
    remaining = total_est - elapsed
    return format_eta(remaining)


def render_progress(label: str, attempt: int, max_attempts: int, stage: str, percent: int | None, started_at: float) -> str:
    elapsed = int(time.monotonic() - started_at)
    if percent is None:
        bar = "." * 20
        pct = "--"
    else:
        filled = max(0, min(20, percent // 5))
        bar = "#" * filled + "." * (20 - filled)
        pct = f"{percent:3d}"
    eta = estimate_eta(stage, percent, started_at)
    return f"\r[{label}] attempt {attempt}/{max_attempts} [{bar}] {pct}% stage={stage:<14} elapsed={elapsed:4d}s eta={eta:<6}"


def run_with_progress(
    cmd: list[str],
    log_path: Path,
    *,
    label: str,
    attempt: int,
    max_attempts: int,
    starting_timeout_s: float,
    stalled_timeout_s: float,
) -> subprocess.CompletedProcess[str]:
    started_at = time.monotonic()
    last_progress_at = started_at
    last_stage = "starting"
    last_percent: int | None = None
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    captured: list[str] = []
    last_line = ""
    forced_rc: int | None = None
    while True:
        rc = proc.poll()
        stage, percent = detect_stage(log_path)
        now = time.monotonic()
        if stage != last_stage or percent != last_percent:
            last_stage = stage
            last_percent = percent
            last_progress_at = now

        if rc is None:
            if stage == "starting" and (now - started_at) > starting_timeout_s:
                print(f"\n[{label}] attempt {attempt}/{max_attempts} forced-timeout: stayed in starting > {int(starting_timeout_s)}s", flush=True)
                proc.terminate()
                try:
                    proc.wait(timeout=3.0)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    try:
                        proc.wait(timeout=2.0)
                    except subprocess.TimeoutExpired:
                        forced_rc = -9
                        break
                rc = proc.returncode if proc.returncode is not None else -9
                forced_rc = rc
                if forced_rc == -9:
                    break
            elif (now - last_progress_at) > stalled_timeout_s:
                print(f"\n[{label}] attempt {attempt}/{max_attempts} forced-timeout: no stage/progress change > {int(stalled_timeout_s)}s", flush=True)
                proc.terminate()
                try:
                    proc.wait(timeout=3.0)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    try:
                        proc.wait(timeout=2.0)
                    except subprocess.TimeoutExpired:
                        forced_rc = -9
                        break
                rc = proc.returncode if proc.returncode is not None else -9
                forced_rc = rc
                if forced_rc == -9:
                    break

        display_stage = stage
        display_percent = percent
        if rc is None and stage == "success-seen":
            display_stage = "finalizing"
            display_percent = 100
        elif rc is not None:
            if rc == 0:
                display_stage = "done"
                display_percent = 100
            elif stage == "success-seen":
                display_stage = "finalizing"
                display_percent = 100
        line = render_progress(label, attempt, max_attempts, display_stage, display_percent, started_at)
        if line != last_line:
            print(line, end="", flush=True)
            last_line = line
        if rc is not None:
            break
        time.sleep(0.5)

    if proc.stdout is not None:
        captured_text = proc.stdout.read()
        if captured_text:
            captured.append(captured_text)
    print()
    stdout_text = "".join(captured)
    if stdout_text.strip():
        print(stdout_text.strip())
    final_rc = forced_rc
    if final_rc is None:
        final_rc = proc.poll()
    if final_rc is None:
        proc.kill()
        try:
            proc.wait(timeout=1.0)
        except subprocess.TimeoutExpired:
            pass
        final_rc = proc.poll()
    if final_rc is None:
        final_rc = -9
    return subprocess.CompletedProcess(cmd, final_rc, stdout=stdout_text, stderr=None)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Deploy current embedded Anchor OTA payload to A-H via strict BLE UUID OTA.")
    p.add_argument("--port", required=True, help="52840 CDC control port")
    p.add_argument("--out-dir", required=True, help="Root artifact directory")
    p.add_argument("--timeout-s", type=int, default=900, help="Per-anchor OTA timeout")
    p.add_argument(
        "--starting-timeout-s",
        type=float,
        default=90.0,
        help="Abort one OTA attempt if it stays in stage=starting longer than this.",
    )
    p.add_argument(
        "--stalled-timeout-s",
        type=float,
        default=180.0,
        help="Abort one OTA attempt if stage/progress does not advance longer than this.",
    )
    p.add_argument("--order", default="ABCDEFGH", help="Anchor deployment order, e.g. ABCDEFGH")
    p.add_argument(
        "--force-kill-port-owner",
        action="store_true",
        help=(
            "If the master_control CDC port is exclusively locked by another process, "
            "try to terminate the owning PID(s) and retry. DANGEROUS."
        ),
    )
    p.add_argument(
        "--skip-post-verify",
        action="store_true",
        help="Skip post-OTA all-responder runtime verification/recovery.",
    )
    p.add_argument(
        "--expected-fw-marker",
        default="",
        help="Expected anchor fw marker. If omitted, auto-detect from generated/build manifest.",
    )
    return p.parse_args()


def load_expected_fw_marker(repo_root: Path, explicit_value: str) -> tuple[str | None, dict[str, object]]:
    if explicit_value:
        return explicit_value, {"source": "arg", "path": None}

    generated_manifest = repo_root / "apps" / "master_ota" / "generated" / "anchor_ota_manifest.json"
    candidates = []
    if generated_manifest.exists():
        candidates.append(generated_manifest)
    candidates.extend(
        sorted(
            repo_root.glob("build-anchor-unified-ota*/build_manifest.json"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
    )
    for path in candidates:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        fw_marker = str(data.get("fw_marker") or "").strip()
        if fw_marker:
            source = "generated_manifest" if path == generated_manifest else "build_manifest"
            return fw_marker, {"source": source, "path": str(path)}
    return None, {"source": "none", "path": None}


def open_control_serial(port: str, timeout: float = 0.2, retries: int = 20, retry_delay_s: float = 0.5) -> serial.Serial:
    last_exc: Exception | None = None
    for _ in range(retries):
        try:
            return serial.Serial(port, 115200, timeout=timeout, write_timeout=2.0)
        except Exception as exc:
            last_exc = exc
            time.sleep(retry_delay_s)
    assert last_exc is not None
    raise last_exc


def drain_serial(ser: serial.Serial, duration_s: float) -> str:
    end = time.time() + duration_s
    chunks: list[str] = []
    while time.time() < end:
        data = ser.read(4096)
        if data:
            chunks.append(data.decode("utf-8", "ignore"))
    return "".join(chunks)


def send_serial_command(ser: serial.Serial, cmd: str, wait_s: float) -> str:
    ser.write((cmd + "\n").encode("utf-8"))
    ser.flush()
    return drain_serial(ser, wait_s)


def wait_for_serial_port(port: str, timeout_s: float = 30.0) -> bool:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if Path(port).exists():
            return True
        time.sleep(0.25)
    return Path(port).exists()


def wait_for_control_ready(port: str, timeout_s: float = 45.0) -> tuple[serial.Serial, str]:
    deadline = time.time() + timeout_s
    transcript: list[str] = []
    last_exc: Exception | None = None
    while time.time() < deadline:
        try:
            ser = serial.Serial(port, 115200, timeout=0.2, write_timeout=2.0)
            time.sleep(0.5)
            transcript.append(drain_serial(ser, 0.8))
            transcript.append(">>> status\n")
            transcript.append(send_serial_command(ser, "status", 1.2))
            text = "".join(transcript)
            if "Control status:" in text or "UART control ready" in text or "BioSpur BLE master control ready" in text:
                return ser, text
            ser.close()
        except Exception as exc:
            last_exc = exc
        time.sleep(0.5)
    if last_exc is not None:
        raise last_exc
    raise TimeoutError(f"control port did not become ready: {port}")


def prepare_control_plane_after_ota(args: argparse.Namespace, out_root: Path) -> dict[str, object]:
    """Return the B120 master from OTA mode to AUTOPOS/anchor before post-verify.

    The firmware intentionally reboots when switching OTA -> RECV.  The deploy
    script must treat the CDC disconnect as expected and reopen the port before
    running responder verification.
    """
    log_path = out_root / "post_verify_mode_handoff.log"
    transcript: list[str] = []
    summary: dict[str, object] = {
        "port": args.port,
        "log_path": str(log_path),
        "mode_recv_sent": False,
        "reboot_observed": False,
        "autopos_ready": False,
        "device_kind_anchor_sent": False,
    }

    def append(text: str) -> None:
        transcript.append(text)
        log_path.write_text("".join(transcript), encoding="utf-8")

    print("=== POST-VERIFY mode handoff: OTA -> RECV -> AUTOPOS ===", flush=True)
    try:
        ser = open_control_serial(args.port, retries=30, retry_delay_s=0.5)
        try:
            time.sleep(0.5)
            append(drain_serial(ser, 0.5))
            append(">>> status\n")
            status_text = send_serial_command(ser, "status", 1.5)
            append(status_text)
            in_ota = "mode=OTA" in status_text or "[OTA]" in status_text
            summary["initial_status"] = status_text.strip()
            if in_ota:
                append(">>> mode recv\n")
                summary["mode_recv_sent"] = True
                try:
                    append(send_serial_command(ser, "mode recv", 8.0))
                except SerialException as exc:
                    summary["reboot_observed"] = True
                    append(f"\n[SCRIPT] expected CDC disconnect during OTA->RECV reboot: {exc}\n")
                except OSError as exc:
                    summary["reboot_observed"] = True
                    append(f"\n[SCRIPT] expected CDC disconnect during OTA->RECV reboot: {exc}\n")
            else:
                append("[SCRIPT] master is not in OTA; no mode recv reboot required\n")
        finally:
            try:
                ser.close()
            except Exception:
                pass

        if bool(summary["mode_recv_sent"]):
            wait_for_serial_port(args.port, timeout_s=45.0)
            time.sleep(2.0)

        ser, ready_text = wait_for_control_ready(args.port, timeout_s=60.0)
        append(ready_text)
        try:
            append(">>> device kind anchor\n")
            append(send_serial_command(ser, "device kind anchor", 2.5))
            summary["device_kind_anchor_sent"] = True
            append(">>> mode autopos\n")
            append(send_serial_command(ser, "mode autopos", 8.0))
        except SerialException as exc:
            summary["autopos_reboot_observed"] = True
            append(f"\n[SCRIPT] CDC disconnect during RECV->AUTOPOS switch: {exc}\n")
        finally:
            try:
                ser.close()
            except Exception:
                pass

        if bool(summary.get("autopos_reboot_observed")):
            wait_for_serial_port(args.port, timeout_s=45.0)
            time.sleep(2.0)

        ser, ready_text = wait_for_control_ready(args.port, timeout_s=60.0)
        append(ready_text)
        try:
            append(">>> status\n")
            final_status = send_serial_command(ser, "status", 1.5)
            append(final_status)
            summary["final_status"] = final_status.strip()
            summary["autopos_ready"] = "mode=AUTOPOS" in final_status or "[AUTOPOS]" in final_status
            if not summary["autopos_ready"]:
                append(">>> mode autopos\n")
                append(send_serial_command(ser, "mode autopos", 8.0))
                append(">>> status\n")
                final_status = send_serial_command(ser, "status", 1.5)
                append(final_status)
                summary["final_status"] = final_status.strip()
                summary["autopos_ready"] = "mode=AUTOPOS" in final_status or "[AUTOPOS]" in final_status
        finally:
            ser.close()
    except Exception as exc:
        summary["error"] = repr(exc)
        append(f"\n[SCRIPT] post-verify mode handoff failed: {exc!r}\n")

    return summary


def run_anchor_version_verify(args: argparse.Namespace, out_root: Path, expected_fw_marker: str) -> tuple[bool, dict[str, object]]:
    raw_log_path = out_root / "post_anchor_version_verify.log"
    summary: dict[str, object] = {
        "expected_fw_marker": expected_fw_marker,
        "port": args.port,
        "raw_log": str(raw_log_path),
        "anchors": {},
        "strict_ok": False,
    }
    transcript: list[str] = []
    try:
        ser = open_control_serial(args.port)
        try:
            time.sleep(1.0)
            transcript.append(drain_serial(ser, 0.8))
            transcript.append(">>> mode recv\n")
            transcript.append(send_serial_command(ser, "mode recv", 4.0))
            transcript.append(">>> device kind anchor\n")
            transcript.append(send_serial_command(ser, "device kind anchor", 4.0))
            transcript.append(">>> anchor version all\n")
            transcript.append(send_serial_command(ser, "anchor version all", 32.0))
        finally:
            ser.close()
    except Exception as exc:
        summary["error"] = repr(exc)
        raw_log_path.write_text("".join(transcript), encoding="utf-8")
        return False, summary

    raw_text = "".join(transcript)
    raw_log_path.write_text(raw_text, encoding="utf-8")

    anchors: dict[str, dict[str, object]] = {}
    for match in ANCHOR_VERSION_RE.finditer(raw_text):
        query = match.group("query")
        if len(query) != 1 or query not in UUIDS:
            continue
        fw = match.group("fw")
        role = match.group("role") or "-"
        label = match.group("label")
        entry = {
            "query": query,
            "uuid": match.group("uuid"),
            "fw": fw,
            "label": label,
            "role": role,
            "fw_match": fw == expected_fw_marker,
        }
        anchors[query] = entry
    for match in ANCHOR_FW_NOTIFY_RE.finditer(raw_text):
        label = match.group("label")
        if label not in UUIDS:
            continue
        fw = match.group("fw")
        role = match.group("role")
        entry = {
            "query": label,
            "uuid": match.group("uuid"),
            "fw": fw,
            "label": label,
            "role": role,
            "fw_match": fw == expected_fw_marker,
            "source": "anchor_fw_notify",
        }
        anchors[label] = entry

    missing = [label for label in UUIDS if label not in anchors]
    summary["anchors"] = anchors
    summary["missing_labels"] = missing
    summary["found_count"] = len(anchors)
    summary["strict_ok"] = len(anchors) == len(UUIDS) and not missing and all(
        bool(anchor.get("fw_match")) for anchor in anchors.values()
    )
    return bool(summary["strict_ok"]), summary


def print_anchor_version_banner(label: str, current_fw: str, target_fw: str, actual_fw: str | None = None) -> None:
    if actual_fw is None:
        print(f"=== {label} VERSION pre current={current_fw} target={target_fw} ===", flush=True)
    else:
        print(f"=== {label} VERSION post target={target_fw} actual={actual_fw} match={actual_fw == target_fw} ===", flush=True)


def run_post_verify(args: argparse.Namespace, out_root: Path) -> tuple[int, dict[str, object]]:
    verify_out_dir = out_root / "post_verify_all_responder"
    verify_parent = verify_out_dir.parent
    verify_prefix = verify_out_dir.name
    cmd = [
        "python3",
        "scripts/verify_all_anchor_responder_runtime.py",
        "--port",
        args.port,
        "--retry-count",
        "4",
        "--scan-timeout-s",
        "8",
        "--command-timeout-s",
        "30",
        "--out-dir",
        str(verify_out_dir),
    ]
    print("=== POST-VERIFY all responder runtime ===", flush=True)
    cp = subprocess.run(cmd, text=True, capture_output=True)
    if cp.stdout.strip():
        print(cp.stdout.strip())
    if cp.stderr.strip():
        print(cp.stderr.strip())

    summary: dict[str, object] = {
        "returncode": cp.returncode,
    }
    try:
        candidates = sorted(
            (
                p
                for p in verify_parent.glob(f"{verify_prefix}*")
                if p.is_dir() and (p / "summary.json").exists()
            ),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        if not candidates:
            raise FileNotFoundError(f"no summary.json found under {verify_parent}/{verify_prefix}*")
        actual_verify_out_dir = candidates[0]
        summary["summary_json"] = str(actual_verify_out_dir / "summary.json")
        with open(actual_verify_out_dir / "summary.json", "r", encoding="utf-8") as f:
            verify_summary = json.load(f)
        summary["summary"] = verify_summary
        final_ack = verify_summary.get("final_ack") or {}
        final_role_counts = verify_summary.get("final_role_counts") or {}
        ack_ok = bool(verify_summary.get("success"))
        role_total = sum(int(final_role_counts.get(role, 0)) for role in ("matrix", "responder", "master", "other"))
        # When Master_Anchor keeps all anchors connected, they may stop
        # advertising, so BLE scan role counts can legitimately be 0/0.  In
        # that resident-control case the runtime NUS ack is authoritative.
        responder_ok = role_total == 0 or int(final_role_counts.get("responder", 0)) >= len(UUIDS)
        ready_ok = (
            int(final_ack.get("ready_count", 0)) >= len(UUIDS)
            and int(final_ack.get("ready_target", 0)) >= len(UUIDS)
        )
        summary["role_total"] = role_total
        summary["ack_ok"] = ack_ok
        summary["ready_ok"] = ready_ok
        summary["responder_ok"] = responder_ok
        summary["strict_ok"] = ack_ok and ready_ok and responder_ok
    except Exception as exc:
        summary["summary_read_error"] = repr(exc)
        summary["strict_ok"] = False
    return cp.returncode, summary


def main() -> int:
    args = parse_args()
    out_root = Path(args.out_dir)
    out_root.mkdir(parents=True, exist_ok=True)
    repo_root = Path(__file__).resolve().parent.parent
    guard = subprocess.run(
        [
            "python3",
            "scripts/verify_ota_payload_kind.py",
            "--expected",
            "anchor",
            "--repo-root",
            str(repo_root),
        ],
        text=True,
        capture_output=True,
    )
    (out_root / "payload_guard_anchor.json").write_text(
        (guard.stdout or "") + (guard.stderr or ""),
        encoding="utf-8",
    )
    if guard.returncode != 0:
        print((guard.stdout or "") + (guard.stderr or ""), flush=True)
        raise SystemExit("refusing Anchor OTA: embedded payload is not an Anchor image")
    expected_fw_marker, expected_fw_meta = load_expected_fw_marker(repo_root, args.expected_fw_marker)
    deploy_summary: dict[str, object] = {
        "port": args.port,
        "timeout_s": args.timeout_s,
        "order": list(args.order),
        "started_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "rounds": {},
        "post_verify": {},
        "post_version_verify": {},
        "expected_fw_marker": expected_fw_marker,
        "expected_fw_marker_meta": expected_fw_meta,
    }

    if expected_fw_marker:
        _, pre_version_summary = run_anchor_version_verify(args, out_root, expected_fw_marker)
        deploy_summary["pre_version_verify"] = pre_version_summary
        for label in args.order:
            current_fw = str((pre_version_summary.get("anchors") or {}).get(label, {}).get("fw", "-"))
            print_anchor_version_banner(label, current_fw, expected_fw_marker)
        with open(out_root / "deploy_summary.json", "w", encoding="utf-8") as f:
            json.dump(deploy_summary, f, indent=2)

    for label in args.order:
        uuid = UUIDS[label]
        round_dir = out_root / f"anchor_{label}"
        round_dir.mkdir(parents=True, exist_ok=True)
        entry: dict[str, object] = {"uuid": uuid, "attempts": []}
        max_attempts = 2
        for attempt in range(1, max_attempts + 1):
            stage_dir = round_dir / f"stage{attempt}"
            cmd = [
                "python3",
                "scripts/ota_single_shot_stable.py",
                "--timeout-s",
                str(args.timeout_s),
                "--port",
                args.port,
                "--target-uuid",
                uuid,
                "--out-dir",
                str(stage_dir),
            ]
            if args.force_kill_port_owner:
                cmd.append("--force-kill-port-owner")
            print(f"=== OTA {label} {uuid} attempt={attempt}/{max_attempts} ===", flush=True)
            cp = run_with_progress(
                cmd,
                stage_dir / "single_shot.log",
                label=label,
                attempt=attempt,
                max_attempts=max_attempts,
                starting_timeout_s=args.starting_timeout_s,
                stalled_timeout_s=args.stalled_timeout_s,
            )
            attempt_entry: dict[str, object] = {
                "attempt": attempt,
                "returncode": cp.returncode,
                "summary_json": str(stage_dir / "summary.json"),
            }
            run_summary = None
            try:
                with open(stage_dir / "summary.json", "r", encoding="utf-8") as f:
                    run_summary = json.load(f)
                    attempt_entry["summary"] = run_summary
            except Exception as exc:
                attempt_entry["summary_read_error"] = repr(exc)
            retry_pending_state = False
            try:
                log_text = (stage_dir / "single_shot.log").read_text(encoding="utf-8", errors="replace")
                retry_pending_state = (
                    "OTA pending-state recovery reset request" in log_text or
                    "OTA erase failed: 6" in log_text or
                    "OTA upload-gate recovery reset request" in log_text
                )
            except Exception:
                pass
            attempt_entry["retry_pending_state"] = retry_pending_state
            recovery_only = isinstance(run_summary, dict) and (
                run_summary.get("reason") == "ota_pending_state_recovery_reset" or
                bool(run_summary.get("ota_pending_recovery_reset_seen"))
            )
            attempt_entry["recovery_only"] = recovery_only
            cast_attempts = entry["attempts"]
            assert isinstance(cast_attempts, list)
            cast_attempts.append(attempt_entry)

            if recovery_only and attempt < max_attempts:
                print(f"--- retrying {label} after pending-state recovery reset completion ---", flush=True)
                time.sleep(8.0)
                continue

            if cp.returncode == 0:
                entry["returncode"] = 0
                entry["summary_json"] = str(stage_dir / "summary.json")
                entry["summary"] = run_summary
                break
            if retry_pending_state and attempt < max_attempts:
                print(f"--- retrying {label} after pending-state recovery reset ---", flush=True)
                time.sleep(8.0)
                continue
            entry["returncode"] = cp.returncode
            entry["summary_json"] = str(stage_dir / "summary.json")
            if run_summary is not None:
                entry["summary"] = run_summary
            break
        deploy_summary["rounds"][label] = entry
        with open(out_root / "deploy_summary.json", "w", encoding="utf-8") as f:
            json.dump(deploy_summary, f, indent=2)
        final_rc = int(entry.get("returncode", 1))
        if final_rc != 0:
            return final_rc

    if not args.skip_post_verify:
        handoff_summary = prepare_control_plane_after_ota(args, out_root)
        deploy_summary["post_verify_mode_handoff"] = handoff_summary
        with open(out_root / "deploy_summary.json", "w", encoding="utf-8") as f:
            json.dump(deploy_summary, f, indent=2)
        if not bool(handoff_summary.get("autopos_ready")):
            deploy_summary["post_verify"] = {
                "strict_ok": False,
                "error": "post_verify_mode_handoff_failed",
                "handoff": handoff_summary,
            }
            with open(out_root / "deploy_summary.json", "w", encoding="utf-8") as f:
                json.dump(deploy_summary, f, indent=2)
            return 2

        verify_rc, verify_summary = run_post_verify(args, out_root)
        deploy_summary["post_verify"] = verify_summary
        with open(out_root / "deploy_summary.json", "w", encoding="utf-8") as f:
            json.dump(deploy_summary, f, indent=2)
        if verify_rc != 0 or not bool(verify_summary.get("strict_ok")):
            return 2

    if expected_fw_marker:
        version_ok, version_summary = run_anchor_version_verify(args, out_root, expected_fw_marker)
        deploy_summary["post_version_verify"] = version_summary
        for label in args.order:
            actual_fw = str((version_summary.get("anchors") or {}).get(label, {}).get("fw", "-"))
            print_anchor_version_banner(label, expected_fw_marker, expected_fw_marker, actual_fw)
        with open(out_root / "deploy_summary.json", "w", encoding="utf-8") as f:
            json.dump(deploy_summary, f, indent=2)
        if not version_ok:
            return 3
    else:
        deploy_summary["post_version_verify"] = {
            "strict_ok": False,
            "skipped": True,
            "reason": "expected_fw_marker_not_found",
        }
        with open(out_root / "deploy_summary.json", "w", encoding="utf-8") as f:
            json.dump(deploy_summary, f, indent=2)
        return 4

    deploy_summary["finished_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    with open(out_root / "deploy_summary.json", "w", encoding="utf-8") as f:
        json.dump(deploy_summary, f, indent=2)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
