#!/usr/bin/env python3
"""Fail-closed current-room wrapper around the frozen AutoPos SW100 collector.

The wrapper does not implement AutoPos.  It adds the current-room identity gate,
safe CDC open semantics, a read-only map verifier, and a command allow-list to
the frozen, previously qualified collector.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import serial


REPO = Path(__file__).resolve().parents[2]
FROZEN_OPS = REPO / "UWB_Part/2026-07-15-FREEZE/scripts/ops"
COLLECTOR = FROZEN_OPS / "run_autopos_sweep_loop.py"
EXPECTED_PORT_NAME = "usb-Master_Anchor_Master_Anchor_Control_87EA2F4A526C5A02-if00"
EXPECTED_APP_SERIAL = "87EA2F4A526C5A02"
EXPECTED_JLINK_SNR = "960148546"
EXPECTED_UUIDS = {
    "A": "F3BB7A04104F9CB8561DDDACB9E53714",
    "B": "B9179575C776C98F1CB132DD6EDC6223",
    "C": "CEE5A7EFCB35F8A56B430047629F5309",
    "D": "B2B5FA625534A8C617135DCAFC9E036A",
    "E": "A892AF05DD59CF0D0D3408AD74F364A1",
    "F": "840C68591E90019821AACFF1B73AAA34",
    "G": "B3087BC3D87CCCD316AEDC6B71D6677F",
    "H": "B1E487C2B1FD740D1442206A1857DFA1",
}
VERSION_RE = re.compile(
    r"ANCHOR_VERSION query=(?P<query>[A-H])\s+"
    r"uuid=(?P<uuid>[0-9A-F]{32})\s+fw=(?P<fw>\S+)\s+"
    r"label=(?P<label>[A-H])\s+role=(?P<role>\S+)"
)
MSTAT_RE = re.compile(
    r"MSTAT peer=(?P<peer>\d+) name=(?P<name>BS[0-9A-F?]{4}) "
    r"conn=(?P<conn>[01]) ready=(?P<ready>[01])"
)
DIRECT_VERSION_RE = re.compile(
    r"ANCHOR_CTRL\[\d+\] notify: ANCHOR_FW fw=(?P<fw>\S+)\s+"
    r"bs=(?P<bs>BS[0-9A-F]{4})\s+uuid=(?P<uuid>[0-9A-F]{32})\s+"
    r"label=(?P<label>[A-H])(?:\s+role=(?P<role>\S+)|\s+ro\s*$)",
    re.MULTILINE,
)


def atomic_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def preserve_previous(path: Path) -> None:
    if not path.exists():
        return
    index = 1
    while True:
        candidate = path.with_name(f"{path.stem}_attempt{index}{path.suffix}")
        if not candidate.exists():
            path.replace(candidate)
            return
        index += 1


def safe_open(port: str, timeout_s: float) -> serial.Serial:
    deadline = time.monotonic() + timeout_s
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            ser = serial.Serial(port=None)
            ser.baudrate = 115200
            ser.timeout = 0.2
            ser.write_timeout = 5.0
            ser.rtscts = False
            ser.dsrdtr = False
            ser.dtr = False
            ser.rts = False
            ser.port = port
            ser.open()
            ser.dtr = False
            ser.rts = False
            return ser
        except Exception as exc:  # pragma: no cover - hardware dependent
            last_error = exc
            try:
                ser.close()
            except Exception:
                pass
            time.sleep(0.4)
    if last_error is None:
        raise TimeoutError(f"serial open timed out: {port}")
    raise last_error


def port_identity(port: Path) -> dict[str, object]:
    if not port.exists() or port.name != EXPECTED_PORT_NAME:
        raise RuntimeError(f"unexpected Master Anchor port: {port}")
    resolved = port.resolve()
    cp = subprocess.run(
        ["udevadm", "info", "--query=property", "--name", str(resolved)],
        check=False,
        capture_output=True,
        text=True,
    )
    props: dict[str, str] = {}
    for line in cp.stdout.splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            props[key] = value
    serial_ok = props.get("ID_SERIAL_SHORT") == EXPECTED_APP_SERIAL
    model = props.get("ID_MODEL", "")
    model_ok = "Master_Anchor" in model
    if cp.returncode != 0 or not serial_ok or not model_ok:
        raise RuntimeError(
            f"Master Anchor udev identity failed rc={cp.returncode} "
            f"serial={props.get('ID_SERIAL_SHORT')} model={model}"
        )
    return {
        "by_id": str(port),
        "resolved": str(resolved),
        "app_serial": props.get("ID_SERIAL_SHORT"),
        "model": model,
        "jlink_snr_registry": EXPECTED_JLINK_SNR,
        "baud": 115200,
        "framing": "8N1",
        "dtr": False,
        "rts": False,
    }


def collect_text(ser: serial.Serial, duration_s: float) -> str:
    deadline = time.monotonic() + duration_s
    chunks: list[bytes] = []
    while time.monotonic() < deadline:
        data = ser.read(4096)
        if data:
            chunks.append(data)
        else:
            time.sleep(0.03)
    return b"".join(chunks).decode("utf-8", "replace")


def send_read_only(ser: serial.Serial, command: str, duration_s: float, transcript) -> str:
    if command not in {"status", "device show", "autopos status", "autopos map show", "anchor version all"}:
        raise RuntimeError(f"not a read-only preflight command: {command}")
    transcript.write(f"[{time.monotonic():.6f}] >>> {command}\n")
    ser.write((command + "\n").encode("ascii"))
    ser.flush()
    text = collect_text(ser, duration_s)
    transcript.write(text)
    transcript.flush()
    return text


def parse_latest_mstat(text: str) -> dict[int, dict[str, object]]:
    out: dict[int, dict[str, object]] = {}
    for match in MSTAT_RE.finditer(text):
        peer = int(match.group("peer"))
        out[peer] = {
            "peer": peer,
            "name": match.group("name"),
            "connected": match.group("conn") == "1",
            "ready": match.group("ready") == "1",
        }
    return out


def preflight(port: Path, deployment_dir: Path, operator_quote: str) -> dict[str, object]:
    deployment_dir.mkdir(parents=True, exist_ok=True)
    started = datetime.now().astimezone().isoformat()
    identity = port_identity(port)
    transcript_path = deployment_dir / "preflight_master_raw.log"
    spontaneous_path = deployment_dir / "decode_before_send_guard.log"
    preserve_previous(transcript_path)
    preserve_previous(spontaneous_path)

    ser = safe_open(str(port), 10.0)
    try:
        spontaneous = collect_text(ser, 7.0)
        spontaneous_path.write_text(spontaneous, encoding="utf-8")
        guard_records = parse_latest_mstat(spontaneous)
        if not guard_records:
            raise RuntimeError("decode-before-send guard failed: no valid MSTAT record; zero TX")

        with transcript_path.open("w", encoding="utf-8", buffering=1) as transcript:
            status = send_read_only(ser, "status", 1.4, transcript)
            device = send_read_only(ser, "device show", 1.4, transcript)
            apos_status = send_read_only(ser, "autopos status", 1.4, transcript)
            apos_map = send_read_only(ser, "autopos map show", 1.4, transcript)
            versions_text = send_read_only(ser, "anchor version all", 32.0, transcript)
            tail = collect_text(ser, 5.5)
            transcript.write(tail)
            # anchor_version_all reconstructs a complete volatile map from the
            # eight already-connected peers.  Read it back after the query;
            # unlike `autopos map A UUID`, this does not write settings/NVS.
            apos_map_after = send_read_only(ser, "autopos map show", 1.4, transcript)
    finally:
        ser.close()

    combined = "\n".join(
        [spontaneous, status, device, apos_status, apos_map, versions_text, tail, apos_map_after]
    )
    mstats = parse_latest_mstat(combined)
    versions: dict[str, dict[str, str]] = {}
    for match in VERSION_RE.finditer(versions_text + tail):
        versions[match.group("query")] = match.groupdict()
    direct_versions: dict[str, dict[str, str]] = {}
    for match in DIRECT_VERSION_RE.finditer(versions_text + tail):
        item = match.groupdict()
        if item.get("role") is None:
            item["role"] = "SOURCE_LINE_TRUNCATED_AFTER_ROLE_KEY_PREFIX"
        direct_versions[match.group("label")] = item
    maps = {
        label: uuid
        for label, uuid in re.findall(
            r"AUTOPOS map ([A-H])=([0-9A-F]{32})", apos_map + apos_map_after
        )
    }

    identity_versions = versions if len(versions) == 8 else direct_versions

    checks = {
        "decode_before_send": bool(guard_records),
        "control_mode_autopos": "Control status: mode=AUTOPOS" in status,
        "system_target_anchor": "System target: kind=anchor" in device,
        "autopos_not_failed": "AUTOPOS: mode=AUTOPOS state=failed" not in apos_status,
        "exact_map": maps == EXPECTED_UUIDS,
        "exact_version_labels": set(identity_versions) == set(EXPECTED_UUIDS),
        "exact_version_uuids": all(
            label in identity_versions and identity_versions[label]["uuid"] == uuid
            for label, uuid in EXPECTED_UUIDS.items()
        ),
        "reported_labels_match_queries": all(
            label in identity_versions and identity_versions[label]["label"] == label
            for label in EXPECTED_UUIDS
        ),
        "role_snapshot_sufficient": all(
            label in identity_versions
            and (
                identity_versions[label]["role"].lower().startswith("res")
                or identity_versions[label]["role"]
                == "SOURCE_LINE_TRUNCATED_AFTER_ROLE_KEY_PREFIX"
            )
            for label in EXPECTED_UUIDS
        ),
        "exact_eight_peer_slots": set(mstats) == set(range(8)),
        "all_peers_connected_ready": len(mstats) == 8 and all(
            item["connected"] and item["ready"] for item in mstats.values()
        ),
        "anchor_control_reply_path": (
            "anchor version rc=0 target=all" in (versions_text + tail)
            or len(direct_versions) == 8
        ),
    }
    passed = all(checks.values())
    git_head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=REPO, check=True, capture_output=True, text=True
    ).stdout.strip()
    dirty = subprocess.run(
        ["git", "status", "--porcelain=v1", "-z"], cwd=REPO, check=True, capture_output=True
    ).stdout
    disk = os.statvfs(REPO)
    state: dict[str, object] = {
        "verdict": "PREFLIGHT_PASS" if passed else "BLOCKED_AUTOPOS_PREFLIGHT",
        "started_at": started,
        "finished_at": datetime.now().astimezone().isoformat(),
        "operator_attestation": {
            "quote": operator_quote,
            "recorded_at": started,
            "interpretation": "No Anchor moved, rotated, remounted, or re-AutoPos'd since the v47 capture.",
        },
        "master_identity": identity,
        "checks": checks,
        "autopos_map": maps,
        "anchor_versions": identity_versions,
        "anchor_version_command_rc": (
            0 if "anchor version rc=0 target=all" in (versions_text + tail) else -116
        ),
        "anchor_version_late_reply_count": len(direct_versions),
        "peer_state": [mstats[k] for k in sorted(mstats)],
        "git_head": git_head,
        "git_dirty_sha256": hashlib.sha256(dirty).hexdigest(),
        "disk_free_bytes": disk.f_bavail * disk.f_frsize,
        "hardware_tx": ["status", "device show", "autopos status", "autopos map show", "anchor version all"],
        "hardware_mutation": False,
        "jlink_used": False,
    }
    preserve_previous(deployment_dir / "PRE_STATE.json")
    atomic_json(deployment_dir / "PRE_STATE.json", state)
    atomic_json(
        deployment_dir / "OPERATOR_ANCHOR_ATTESTATION.json",
        state["operator_attestation"],
    )
    return state


def load_frozen_collector():
    sys.path.insert(0, str(FROZEN_OPS))
    spec = importlib.util.spec_from_file_location("frozen_autopos_sw100", COLLECTOR)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load frozen collector: {COLLECTOR}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def guarded_payload(payload: bytes) -> None:
    text = payload.decode("ascii", "strict")
    for raw in text.splitlines():
        command = raw.strip()
        if not command:
            continue
        lower = command.lower()
        allowed = (
            lower in {
                "status", "device show", "autopos status", "autopos map show",
                "autopos apply", "autopos result show", "autopos cir 0",
            }
            or re.fullmatch(r"anchor version (?:all|[a-h])", lower)
            or re.fullmatch(r"anchor role all (?:matrix|responder)(?: cir (?:0|compact|full))?", lower)
            or re.fullmatch(r"autopos round [a-h] 100", lower)
        )
        if not allowed:
            raise RuntimeError(f"fail-closed command guard rejected: {command}")


def run_frozen_sw100(port: Path, deployment_dir: Path) -> int:
    pre = json.loads((deployment_dir / "PRE_STATE.json").read_text(encoding="utf-8"))
    if pre.get("verdict") != "PREFLIGHT_PASS":
        raise RuntimeError("formal SW100 refused: PRE_STATE is not PREFLIGHT_PASS")
    module = load_frozen_collector()
    original_write = module._write_bytes_with_recovery

    def safe_collector_open(port_arg: str, timeout_s: float):
        return safe_open(port_arg, timeout_s)

    def guarded_write(ser, payload: bytes):
        guarded_payload(payload)
        return original_write(ser, payload)

    def verify_existing_map(ser, logf, port_arg, live_output, verbose, context=None, progress_cb=None, status_cb=None):
        if status_cb is not None:
            status_cb("verify existing AUTOPOS map")
        ser, text = module.send_cmd_collect_text(
            ser, logf, port_arg, "autopos map show", 1.0, live_output, verbose,
            resend_after_reopen=False, progress_cb=progress_cb,
        )
        observed = {
            label: uuid
            for label, uuid in re.findall(r"AUTOPOS map ([A-H])=([0-9A-F]{32})", text)
        }
        if observed != EXPECTED_UUIDS:
            raise RuntimeError(f"existing AutoPos map changed; refusing persistent rewrite: {observed}")
        if context is not None:
            context["autopos_initialized"] = True
        module.emit(logf, "PRECHECK: exact existing AUTOPOS map verified read-only\n", live_output, verbose)
        return ser

    module.open_port = safe_collector_open
    module._write_bytes_with_recovery = guarded_write
    module.ensure_autopos_maps = verify_existing_map
    run_dir = deployment_dir / "SW100"
    old_argv = sys.argv
    sys.argv = [
        str(COLLECTOR),
        "--port", str(port),
        "--order", "ABCDEFGH",
        "--sw-sets", "100",
        "--prewarm-sw-sets", "0",
        "--round-retries", "0",
        "--reuse-resident-anchor-master",
        "--out-dir", str(run_dir),
        "--verbose", "1",
    ]
    try:
        return int(module.main())
    finally:
        sys.argv = old_argv


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=Path, required=True)
    parser.add_argument("--deployment-dir", type=Path, required=True)
    parser.add_argument("--operator-quote", default="confirm: Anchor没有被动过")
    parser.add_argument("--run", action="store_true", help="Run the single formal SW100 after preflight")
    args = parser.parse_args()
    state = preflight(args.port, args.deployment_dir, args.operator_quote)
    print(json.dumps(state, indent=2, sort_keys=True), flush=True)
    if state["verdict"] != "PREFLIGHT_PASS":
        return 2
    if not args.run:
        return 0
    return run_frozen_sw100(args.port, args.deployment_dir)


if __name__ == "__main__":
    raise SystemExit(main())
