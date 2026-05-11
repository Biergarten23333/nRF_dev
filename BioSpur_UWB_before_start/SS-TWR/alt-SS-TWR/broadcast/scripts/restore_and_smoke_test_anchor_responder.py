#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
BROADCAST_DIR = SCRIPT_DIR.parent

DEFAULT_ANCHOR_PORT = "/dev/serial/by-id/usb-BioSpur_BioSpur_BLE_Control_87EA2F4A526C5A02-if00"
DEFAULT_TAG_PORT = "/dev/serial/by-id/usb-Master_Tag_BioSpur_BLE_Control_6918E0384172A49F-if00"
DEFAULT_OLD3 = "BSF66F,BS2DCE,BSDC91"


def timestamped_dir(base: Path) -> Path:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return base.parent / f"{base.name}_{stamp}"


def run_logged(cmd: list[str], log_path: Path) -> tuple[int, str]:
    print("[SMOKE] run: " + " ".join(cmd), flush=True)
    output_parts: list[str] = []
    with log_path.open("w", encoding="utf-8") as logf:
        proc = subprocess.Popen(
            cmd,
            cwd=BROADCAST_DIR,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            bufsize=1,
        )
        assert proc.stdout is not None
        for line in proc.stdout:
            output_parts.append(line)
            logf.write(line)
            logf.flush()
            print(line, end="", flush=True)
        return proc.wait(), "".join(output_parts)


def latest_summary_under(base: Path) -> Path | None:
    parent = base.parent
    prefix = base.name
    candidates = sorted(
        (
            path / "summary.json"
            for path in parent.glob(prefix + "*")
            if path.is_dir() and (path / "summary.json").exists()
        ),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    return candidates[0] if candidates else None


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def evaluate_capture(summary: dict, targets: list[str], min_valid_anchors: int, min_good_ratio: float) -> dict:
    per_target: dict[str, dict] = {}
    success = bool(summary.get("success"))
    for target in targets:
        tag_summary = (summary.get("per_tag") or {}).get(target) or {}
        validity = tag_summary.get("sweep_validity") or {}
        sweeps_total = int(validity.get("sweeps_total") or 0)
        key = f"ratio_ge{min_valid_anchors}"
        if min_valid_anchors == 8:
            key = "ratio_ge8"
        elif min_valid_anchors <= 4:
            key = "ratio_ge4"
        ratio = float(validity.get(key) or 0.0)
        target_ok = sweeps_total > 0 and ratio >= min_good_ratio
        per_target[target] = {
            "ok": target_ok,
            "sweeps_total": sweeps_total,
            "ratio_key": key,
            "ratio": ratio,
            "sweep_validity": validity,
            "anchors_seen": tag_summary.get("anchors_seen", []),
            "tr_rows": tag_summary.get("tr_rows", 0),
            "tr_valid_rows": tag_summary.get("tr_valid_rows", 0),
        }
        success = success and target_ok
    return {
        "success": success,
        "min_valid_anchors": min_valid_anchors,
        "min_good_ratio": min_good_ratio,
        "per_target": per_target,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Restore all anchors to runtime responder, then prove responder is "
            "actually usable by running a short old-3-Tag TR smoke test."
        )
    )
    parser.add_argument("--anchor-port", default=DEFAULT_ANCHOR_PORT)
    parser.add_argument("--tag-port", default=DEFAULT_TAG_PORT)
    parser.add_argument("--targets", default=DEFAULT_OLD3)
    parser.add_argument("--duration", type=float, default=30.0)
    parser.add_argument("--tr-hz", type=int, default=8)
    parser.add_argument("--min-valid-anchors", type=int, default=7)
    parser.add_argument("--min-good-ratio", type=float, default=0.80)
    parser.add_argument("--anchor-command-timeout-s", type=float, default=45.0)
    parser.add_argument("--anchor-retry-count", type=int, default=3)
    parser.add_argument("--tag-link-timeout-s", type=float, default=120.0)
    parser.add_argument("--tag-link-stable-s", type=float, default=5.0)
    parser.add_argument(
        "--out-dir",
        default="logs/anchor_responder_restore_smoke",
        help="Base output directory under the broadcast directory unless absolute.",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    base = Path(args.out_dir)
    if not base.is_absolute():
        base = BROADCAST_DIR / base
    out_dir = timestamped_dir(base)
    out_dir.mkdir(parents=True, exist_ok=True)

    targets = [item.strip().upper() for item in args.targets.split(",") if item.strip()]
    restore_base = out_dir / "restore_all_responder"
    capture_base = out_dir / "old3_tr_smoke"

    restore_cmd = [
        sys.executable,
        "scripts/verify_all_anchor_responder_runtime.py",
        "--port",
        args.anchor_port,
        "--command-timeout-s",
        str(args.anchor_command_timeout_s),
        "--retry-count",
        str(args.anchor_retry_count),
        "--out-dir",
        str(restore_base),
        "--live-output",
    ]
    restore_rc, _ = run_logged(restore_cmd, out_dir / "restore.console.log")
    restore_summary_path = latest_summary_under(restore_base)
    restore_summary = load_json(restore_summary_path) if restore_summary_path else {}

    capture_summary: dict = {}
    smoke_eval: dict = {"success": False, "error": "capture_not_run"}
    capture_rc = 1
    if restore_rc == 0 and restore_summary.get("success"):
        capture_cmd = [
            sys.executable,
            "scripts/run_recv_tdma_capture.py",
            "--port",
            args.tag_port,
            "--duration",
            str(args.duration),
            "--targets",
            ",".join(targets),
            "--tr-hz",
            str(args.tr_hz),
            "--tdma-config-retries",
            "3",
            "--tag-link-timeout-s",
            str(args.tag_link_timeout_s),
            "--tag-link-stable-s",
            str(args.tag_link_stable_s),
            "--skip-anchor-preflight",
            "--out-dir",
            str(capture_base),
        ]
        capture_rc, _ = run_logged(capture_cmd, out_dir / "capture.console.log")
        capture_summary_path = latest_summary_under(capture_base)
        capture_summary = load_json(capture_summary_path) if capture_summary_path else {}
        smoke_eval = evaluate_capture(
            capture_summary,
            targets,
            args.min_valid_anchors,
            args.min_good_ratio,
        )
    else:
        smoke_eval = {
            "success": False,
            "error": "restore_failed",
            "restore_returncode": restore_rc,
        }

    result = {
        "success": bool(
            restore_rc == 0
            and restore_summary.get("success")
            and capture_rc == 0
            and smoke_eval.get("success")
        ),
        "out_dir": str(out_dir),
        "restore_returncode": restore_rc,
        "capture_returncode": capture_rc,
        "restore_summary_json": str(restore_summary_path) if restore_summary_path else "",
        "capture_summary_json": str(latest_summary_under(capture_base) or ""),
        "restore_summary": restore_summary,
        "smoke_eval": smoke_eval,
    }
    (out_dir / "summary.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2), flush=True)
    return 0 if result["success"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
