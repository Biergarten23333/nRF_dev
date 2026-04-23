#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import subprocess
import time
from pathlib import Path

import serial

UPLOAD_PROGRESS_RE = re.compile(r"OTA upload progress:\s*(\d+)%")
TAG_VERSION_RE = re.compile(
    r"(?P<name>BS[0-9A-F]{4}) notify:\s+VERSION fw=(?P<fw>\S+)\s+bs=(?P<bs>BS[0-9A-F]{4})\s+tag=(?P<tag>\d+)\s+pmode=(?P<pmode>\d+)\s+amode=(?P<amode>\d+)",
    re.IGNORECASE,
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
    if "OTA upload-gate recovery reset request" in text:
        return "recovery-reset", None
    if "initiate_busy_auto_reset" in text:
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
    if "Connected[" in text or "Connected:" in text:
        return "connected", None
    if "Control mode loaded: OTA" in text:
        return "ota-mode", None
    if "phase=A name_ack_ok" in text or "ota_target name rc=0" in text:
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
    return format_eta(total_est - elapsed)


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


def run_with_progress(cmd: list[str], log_path: Path, *, label: str, attempt: int, max_attempts: int) -> subprocess.CompletedProcess[str]:
    started_at = time.monotonic()
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    captured: list[str] = []
    last_line = ""
    while True:
        rc = proc.poll()
        stage, percent = detect_stage(log_path)
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
        text = proc.stdout.read()
        if text:
            captured.append(text)
    print()
    stdout_text = "".join(captured)
    if stdout_text.strip():
        print(stdout_text.strip())
    return subprocess.CompletedProcess(cmd, proc.returncode, stdout=stdout_text, stderr=None)


def parse_targets(value: str) -> list[str]:
    targets: list[str] = []
    for item in re.split(r"[,\s]+", value.strip()):
        if item:
            targets.append(item)
    return targets


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Deploy current embedded Tag OTA payload to BioSpur Tags by BLE name.")
    p.add_argument("--port", required=True, help="B120/master_control CDC port")
    p.add_argument("--out-dir", required=True, help="Root artifact directory")
    p.add_argument("--timeout-s", type=int, default=420, help="Per-Tag OTA timeout")
    p.add_argument("--targets", default="BSF66F,BS2DCE,BSDC91", help="Comma/space separated Tag names")
    p.add_argument("--prefix", default="BS")
    p.add_argument("--max-attempts", type=int, default=2)
    p.add_argument("--force-kill-port-owner", action="store_true")
    p.add_argument("--expected-fw-marker", default="", help="Expected tag fw marker. If omitted, auto-detect from manifest/build cache.")
    return p.parse_args()


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


def load_expected_tag_fw_marker(repo_root: Path, explicit_value: str) -> tuple[str | None, dict[str, object]]:
    if explicit_value:
        return explicit_value, {"source": "arg", "path": None}

    generated_manifest = repo_root / "apps" / "master_ota" / "generated" / "tag_ota_manifest.json"
    if generated_manifest.exists():
        try:
            data = json.loads(generated_manifest.read_text(encoding="utf-8"))
            fw = str(data.get("fw_marker") or "").strip()
            if fw:
                return fw, {"source": "generated_manifest", "path": str(generated_manifest)}
        except Exception:
            pass

    caches = sorted(
        repo_root.glob("build-tag*/CMakeCache.txt"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    for path in caches:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        m = re.search(r"^APP_TAG_FW_MARKER:STRING=(.+)$", text, re.MULTILINE)
        if m:
            fw = m.group(1).strip()
            if fw:
                return fw, {"source": "cmake_cache", "path": str(path)}
    return None, {"source": "none", "path": None}


def query_tag_versions(port: str, targets: list[str], out_root: Path) -> dict[str, object]:
    raw_log_path = out_root / "tag_version_query.log"
    summary: dict[str, object] = {
        "raw_log": str(raw_log_path),
        "targets": targets,
        "versions": {},
    }
    transcript: list[str] = []
    try:
        ser = open_control_serial(port)
        try:
            time.sleep(1.0)
            transcript.append(drain_serial(ser, 0.8))
            transcript.append(">>> mode recv\n")
            transcript.append(send_serial_command(ser, "mode recv", 4.0))
            transcript.append(">>> device kind tag\n")
            transcript.append(send_serial_command(ser, "device kind tag", 6.0))
            transcript.append(">>> cmd VERSION\n")
            transcript.append(send_serial_command(ser, "cmd VERSION", 10.0))
        finally:
            ser.close()
    except Exception as exc:
        summary["error"] = repr(exc)
        raw_log_path.write_text("".join(transcript), encoding="utf-8")
        return summary

    raw_text = "".join(transcript)
    raw_log_path.write_text(raw_text, encoding="utf-8")
    versions: dict[str, dict[str, object]] = {}
    for match in TAG_VERSION_RE.finditer(raw_text):
        name = match.group("name").upper()
        versions[name] = {
            "name": name,
            "fw": match.group("fw"),
            "bs": match.group("bs"),
            "tag": int(match.group("tag")),
            "pmode": int(match.group("pmode")),
            "amode": int(match.group("amode")),
        }
    summary["versions"] = versions
    summary["missing_targets"] = [t for t in targets if t not in versions]
    return summary


def print_version_banner(label: str, current_fw: str, target_fw: str, actual_fw: str | None = None) -> None:
    if actual_fw is None:
        print(f"=== {label} VERSION pre current={current_fw} target={target_fw} ===", flush=True)
    else:
        print(f"=== {label} VERSION post target={target_fw} actual={actual_fw} match={actual_fw == target_fw} ===", flush=True)


def main() -> int:
    args = parse_args()
    targets = parse_targets(args.targets)
    if not targets:
        raise SystemExit("No targets specified")
    out_root = Path(args.out_dir)
    out_root.mkdir(parents=True, exist_ok=True)
    repo_root = Path(__file__).resolve().parent.parent
    expected_fw_marker, expected_fw_meta = load_expected_tag_fw_marker(repo_root, args.expected_fw_marker)
    deploy_summary: dict[str, object] = {
        "port": args.port,
        "timeout_s": args.timeout_s,
        "targets": targets,
        "prefix": args.prefix,
        "started_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "rounds": {},
        "expected_fw_marker": expected_fw_marker,
        "expected_fw_marker_meta": expected_fw_meta,
        "pre_version_query": {},
        "post_version_query": {},
    }

    pre_version_summary = query_tag_versions(args.port, targets, out_root)
    deploy_summary["pre_version_query"] = pre_version_summary
    (out_root / "deploy_summary.json").write_text(json.dumps(deploy_summary, indent=2) + "\n", encoding="utf-8")

    for target in targets:
        round_dir = out_root / target
        round_dir.mkdir(parents=True, exist_ok=True)
        entry: dict[str, object] = {"target_name": target, "attempts": []}
        current_fw = str((pre_version_summary.get("versions") or {}).get(target, {}).get("fw", "-"))
        target_fw = expected_fw_marker or "-"
        print_version_banner(target, current_fw, target_fw)
        for attempt in range(1, args.max_attempts + 1):
            stage_dir = round_dir / f"stage{attempt}"
            cmd = [
                "python3",
                "scripts/ota_single_tag_stable.py",
                "--timeout-s",
                str(args.timeout_s),
                "--port",
                args.port,
                "--target-name",
                target,
                "--target-prefix",
                args.prefix,
                "--out-dir",
                str(stage_dir),
            ]
            if args.force_kill_port_owner:
                cmd.append("--force-kill-port-owner")
            print(f"=== OTA TAG {target} attempt={attempt}/{args.max_attempts} ===", flush=True)
            cp = run_with_progress(cmd, stage_dir / "single_shot.log", label=target, attempt=attempt, max_attempts=args.max_attempts)
            attempt_entry: dict[str, object] = {
                "attempt": attempt,
                "returncode": cp.returncode,
                "summary_json": str(stage_dir / "summary.json"),
            }
            run_summary = None
            try:
                run_summary = json.loads((stage_dir / "summary.json").read_text(encoding="utf-8"))
                attempt_entry["summary"] = run_summary
            except Exception as exc:
                attempt_entry["summary_read_error"] = repr(exc)
            retry_pending_state = False
            try:
                log_text = (stage_dir / "single_shot.log").read_text(encoding="utf-8", errors="replace")
                retry_pending_state = (
                    "OTA pending-state recovery reset request" in log_text
                    or "OTA erase failed: 6" in log_text
                    or "OTA upload-gate recovery reset request" in log_text
                    or "initiate_busy_auto_reset" in log_text
                )
            except Exception:
                pass
            attempt_entry["retry_pending_state"] = retry_pending_state
            recovery_only = isinstance(run_summary, dict) and (
                run_summary.get("reason") in {
                    "ota_pending_state_recovery_reset",
                    "ota_pending_state_recovery_reset_completed",
                    "ota_upload_gate_recovery_reset",
                }
                or bool(run_summary.get("ota_pending_recovery_reset_seen"))
                or run_summary.get("initiate_rc") == -16
                or run_summary.get("reason") == "initiate_busy_auto_reset"
            )
            attempt_entry["recovery_only"] = recovery_only
            cast_attempts = entry["attempts"]
            assert isinstance(cast_attempts, list)
            cast_attempts.append(attempt_entry)

            if recovery_only and attempt < args.max_attempts:
                print(f"--- retrying {target} after recovery reset completion ---", flush=True)
                time.sleep(8.0)
                continue
            if cp.returncode == 0:
                entry["returncode"] = 0
                entry["summary_json"] = str(stage_dir / "summary.json")
                entry["summary"] = run_summary
                break
            if retry_pending_state and attempt < args.max_attempts:
                print(f"--- retrying {target} after pending-state recovery reset ---", flush=True)
                time.sleep(8.0)
                continue
            entry["returncode"] = cp.returncode
            entry["summary_json"] = str(stage_dir / "summary.json")
            if run_summary is not None:
                entry["summary"] = run_summary
            break

        deploy_summary["rounds"][target] = entry
        (out_root / "deploy_summary.json").write_text(json.dumps(deploy_summary, indent=2) + "\n", encoding="utf-8")
        if int(entry.get("returncode", 1)) != 0:
            return int(entry.get("returncode", 1))

    post_version_summary = query_tag_versions(args.port, targets, out_root)
    deploy_summary["post_version_query"] = post_version_summary
    for target in targets:
        actual_fw = str((post_version_summary.get("versions") or {}).get(target, {}).get("fw", "-"))
        print_version_banner(target, expected_fw_marker or "-", expected_fw_marker or "-", actual_fw)
        deploy_summary["rounds"][target]["post_version"] = {
            "expected_fw": expected_fw_marker,
            "actual_fw": actual_fw,
            "match": bool(expected_fw_marker) and actual_fw == expected_fw_marker,
        }
    (out_root / "deploy_summary.json").write_text(json.dumps(deploy_summary, indent=2) + "\n", encoding="utf-8")

    if expected_fw_marker:
        for target in targets:
            post = deploy_summary["rounds"][target].get("post_version", {})
            if not bool(post.get("match")):
                return 3

    deploy_summary["finished_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    (out_root / "deploy_summary.json").write_text(json.dumps(deploy_summary, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
