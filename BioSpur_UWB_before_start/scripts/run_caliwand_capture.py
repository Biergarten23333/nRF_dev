#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path


DEFAULT_TAG_PORT = (
    "/dev/serial/by-id/usb-Master_Tag_BioSpur_BLE_Control_6918E0384172A49F-if00"
)
DEFAULT_ANCHOR_PORT = (
    "/dev/serial/by-id/usb-Master_Anchor_BioSpur_BLE_Control_87EA2F4A526C5A02-if00"
)
CALIWAND_HZ = 30


def normalize_wand_target(value: str) -> str:
    raw = value.strip()
    upper = raw.upper()
    if upper.startswith("BS") and len(upper) == 6:
        int(upper[2:], 16)
        return upper
    parts = upper.split("-")
    if len(parts) == 3 and parts[0] == "WAND" and parts[1] in {"A", "B", "C"}:
        bs = parts[2]
        if bs.startswith("BS") and len(bs) == 6:
            int(bs[2:], 16)
            return f"Wand-{parts[1]}-{bs}"
    raise argparse.ArgumentTypeError(f"invalid Wand tag name: {value!r}")


def parse_targets(value: str) -> list[str]:
    targets = [normalize_wand_target(item) for item in value.replace(" ", ",").split(",") if item.strip()]
    if len(targets) != 3:
        raise argparse.ArgumentTypeError("CaliWand mode requires exactly 3 Wand targets")
    if len(set(targets)) != 3:
        raise argparse.ArgumentTypeError("CaliWand targets must be unique")
    return targets


def latest_capture_summary(out_dir: Path) -> Path | None:
    candidates = sorted(out_dir.glob("tag_capture_*/summary.json"))
    if candidates:
        return candidates[-1]
    candidates = sorted(out_dir.glob("recv_*/summary.json"))
    if candidates:
        return candidates[-1]
    return None


def summarize_capture(summary_path: Path) -> dict:
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    per_tag = summary.get("per_tag", {})
    freq_hz = summary.get("freq_hz", {})
    rows = {}
    duration_s = float(summary.get("duration_s") or 0.0)
    for tag, data in sorted(per_tag.items()):
        cm_rows = int(data.get("cm_rows") or 0)
        tr_rows = int(data.get("tr_rows") or 0)
        cf_rows = int(data.get("cf_rows") or 0)
        pos_rows = int(data.get("position_rows") or 0)
        rows[tag] = {
            "cm_rows": cm_rows,
            "tr_rows": tr_rows,
            "cf_rows": cf_rows,
            "position_rows": pos_rows,
            "cm_rows_per_s": round(cm_rows / duration_s, 3) if duration_s > 0 else None,
            "tr_rows_per_s": round(tr_rows / duration_s, 3) if duration_s > 0 else None,
            "tr_sweeps_per_s": round(tr_rows / duration_s, 3) if duration_s > 0 else None,
            "cf_rows_per_s": round(cf_rows / duration_s, 3) if duration_s > 0 else None,
            "position_rows_per_s": round(pos_rows / duration_s, 3) if duration_s > 0 else None,
            "anchors_seen": data.get("anchors_seen") or [],
            "tr_poll_counts": data.get("tr_poll_counts") or [],
            "tr_frame_us": data.get("tr_frame_us") or [],
            "status_counts": data.get("status_counts") or {},
        }
    return {
        "success": bool(summary.get("success")),
        "session_dir": summary.get("session_dir"),
        "duration_s": duration_s,
        "targets": summary.get("targets") or [],
        "profiles": summary.get("profiles") or {},
        "freq_hz": freq_hz,
        "cm_all": summary.get("cm_all"),
        "tr_all": summary.get("tr_all"),
        "cf_all": summary.get("cf_all"),
        "positions_all": summary.get("positions_all"),
        "per_tag": rows,
        "cleanup": summary.get("cleanup") or {},
    }


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=(
            "Run Calibration Wand mode: allow-list exactly three BS Tags, request the "
            "maximum practical current TDMA cadence, and capture only those tags."
        )
    )
    p.add_argument(
        "--targets",
        required=True,
        type=parse_targets,
        help="Exactly three wand Tags, comma/space separated, e.g. Wand-A-BSCCF4,Wand-B-BS9336,Wand-C-BS955A",
    )
    p.add_argument("--duration", type=float, default=120.0)
    p.add_argument("--tag-port", default=os.environ.get("BIOSPUR_TAG_PORT", DEFAULT_TAG_PORT))
    p.add_argument(
        "--anchor-port",
        default=os.environ.get("BIOSPUR_ANCHOR_PORT", DEFAULT_ANCHOR_PORT),
    )
    p.add_argument("--tag-snr", default=os.environ.get("BIOSPUR_TAG_SNR", "1050070698"))
    p.add_argument("--anchor-snr", default=os.environ.get("BIOSPUR_ANCHOR_SNR", "960148546"))
    p.add_argument("--out-dir", default="logs/caliwand_capture")
    p.add_argument("--with-listener", action="store_true")
    p.add_argument(
        "--listener-port",
        default=os.environ.get(
            "BIOSPUR_LISTENER_PORT",
            "/dev/serial/by-id/usb-SEGGER_J-Link_000760185886-if00",
        ),
    )
    p.add_argument("--listener-extra-s", type=float, default=40.0)
    p.add_argument("--skip-anchor-preflight", action="store_true")
    p.add_argument("--no-cleanup", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    return p


def main() -> int:
    args = build_parser().parse_args()
    stamp = time.strftime("%Y%m%d_%H%M%S")
    base = Path(f"{args.out_dir}_{stamp}")
    targets = args.targets
    target_csv = ",".join(targets)

    cmd = [
        sys.executable,
        "scripts/run_dual_master_tdma_capture.py",
        "--anchor-port",
        args.anchor_port,
        "--anchor-snr",
        args.anchor_snr,
        "--tag-port",
        args.tag_port,
        "--tag-snr",
        args.tag_snr,
        "--duration",
        str(args.duration),
        "--targets",
        target_csv,
        "--profiles",
        ",".join(f"{target}:motion" for target in targets),
        "--static-hz",
        str(CALIWAND_HZ),
        "--roto-hz",
        str(CALIWAND_HZ),
        "--motion-hz",
        str(CALIWAND_HZ),
        "--caliwand-mode",
        "--cm-probe-target",
        targets[0],
        "--out-dir",
        str(base),
        "--tag-link-timeout-s",
        "45",
    ]
    if args.skip_anchor_preflight:
        cmd.append("--skip-anchor-preflight")
    if args.with_listener:
        cmd.extend(
            [
                "--with-listener",
                "--listener-port",
                args.listener_port,
                "--listener-extra-s",
                str(args.listener_extra_s),
            ]
        )

    # Pass CaliWand-specific recv options by directly using the lower-level script.
    # The dual-master wrapper owns anchor preflight; the tag capture command it
    # launches inherits the same target/profiles/frequency. This environment flag
    # lets future wrappers detect that the session was intentionally 3-tag gated.
    env = os.environ.copy()
    env["BIOSPUR_CALIWAND_MODE"] = "1"

    print("[CALIWAND] targets=" + target_csv, flush=True)
    print(
        f"[CALIWAND] requested={CALIWAND_HZ}Hz/tag, aggregate target={CALIWAND_HZ * 3}Hz",
        flush=True,
    )
    print("[CALIWAND] run: " + " ".join(cmd), flush=True)
    if args.dry_run:
        return 0

    base.mkdir(parents=True, exist_ok=True)
    (base / "commands.json").write_text(json.dumps({"cmd": cmd, "targets": targets}, indent=2) + "\n")
    rc = subprocess.run(cmd, env=env, check=False).returncode

    summary_path = latest_capture_summary(base)
    if summary_path is not None:
        caliwand_summary = summarize_capture(summary_path)
        (base / "caliwand_summary.json").write_text(
            json.dumps(caliwand_summary, indent=2) + "\n",
            encoding="utf-8",
        )
        print("[CALIWAND] summary=" + str(base / "caliwand_summary.json"), flush=True)
        print(json.dumps(caliwand_summary, indent=2), flush=True)
    else:
        print("[CALIWAND] warning: no tag capture summary found under " + str(base), flush=True)

    return rc


if __name__ == "__main__":
    raise SystemExit(main())
