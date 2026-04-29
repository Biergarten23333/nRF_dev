#!/usr/bin/env python3
import argparse
import subprocess
from datetime import datetime
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Capture one ground-truth Tag session and analyze it against a known point."
    )
    parser.add_argument("snr", help="SEGGER serial number")
    parser.add_argument("port", help="Serial port path")
    parser.add_argument("--label", required=True, help="Ground-truth point label")
    parser.add_argument("--truth-x", type=float, required=True, help="True X in mm")
    parser.add_argument("--truth-y", type=float, required=True, help="True Y in mm")
    parser.add_argument("--truth-z", type=float, required=True, help="True Z in mm")
    parser.add_argument("--duration", type=float, default=120.0, help="Capture duration in seconds")
    parser.add_argument("--skip-sweeps", type=int, default=2, help="Ignore the first N sweeps in summary")
    parser.add_argument("--baud", type=int, default=115200, help="Serial baud rate")
    parser.add_argument("--notes", default="", help="Optional note stored with the result")
    parser.add_argument(
        "--out-dir",
        default="logs/ground_truth",
        help="Base directory for ground-truth capture sessions",
    )
    args = parser.parse_args()

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    session_name = f"gt_{args.label}_{timestamp}"
    session_dir = Path(args.out_dir) / session_name

    capture_cmd = [
        "python3",
        "scripts/capture_tag_session.py",
        args.snr,
        args.port,
        "--duration",
        str(args.duration),
        "--skip-sweeps",
        str(args.skip_sweeps),
        "--baud",
        str(args.baud),
        "--out-dir",
        args.out_dir,
        "--session-name",
        session_name,
    ]
    subprocess.run(capture_cmd, check=True)

    analyze_cmd = [
        "python3",
        "scripts/analyze_ground_truth_session.py",
        str(session_dir),
        "--label",
        args.label,
        "--truth-x",
        str(args.truth_x),
        "--truth-y",
        str(args.truth_y),
        "--truth-z",
        str(args.truth_z),
        "--notes",
        args.notes,
    ]
    subprocess.run(analyze_cmd, check=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
