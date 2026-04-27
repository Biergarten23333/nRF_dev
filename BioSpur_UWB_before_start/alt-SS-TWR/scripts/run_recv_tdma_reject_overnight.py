#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import subprocess
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]


def run(cmd: list[str], log_path: Path) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("w", encoding="utf-8") as logf:
        logf.write("$ " + " ".join(cmd) + "\n")
        logf.flush()
        subprocess.run(
            cmd,
            cwd=REPO_ROOT,
            check=True,
            text=True,
            stdout=logf,
            stderr=subprocess.STDOUT,
        )


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    fieldnames: list[str] = []
    for row in rows:
        for key in row:
            if key not in fieldnames:
                fieldnames.append(key)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def write_md(path: Path, rows: list[dict[str, Any]]) -> None:
    lines = [
        "# Reject Overnight Loop",
        "",
        "| Cycle | Status | Dominant B/D Reason | Patch Recommended | Session | Notes |",
        "|---|---|---|---|---|---|",
    ]
    for row in rows:
        lines.append(
            f"| {row.get('cycle','-')} | {row.get('status','-')} | "
            f"{row.get('dominant_bd_reason_global','-')} | "
            f"{row.get('patch_recommended','-')} | "
            f"{row.get('session_dir','-')} | {row.get('notes','-')} |"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def latest_capture_session(cycle_dir: Path, capture_base: Path) -> Path:
    # run_recv_tdma_capture.py appends a timestamp beside the requested base,
    # e.g. cycle_01/capture_YYYYmmdd_HHMMSS, not inside cycle_01/capture/.
    if capture_base.exists() and capture_base.is_dir():
        dirs = [p for p in capture_base.iterdir() if p.is_dir()]
    else:
        dirs = []
    dirs.extend(
        p for p in cycle_dir.glob(f"{capture_base.name}_*")
        if p.is_dir()
    )
    if not dirs:
        raise FileNotFoundError(f"no capture session under {cycle_dir}")
    return max(dirs, key=lambda p: p.stat().st_mtime)


def main() -> int:
    ap = argparse.ArgumentParser(description="Run overnight recv/TDMA reject-root-cause loop")
    ap.add_argument("--port", default="/dev/serial/by-id/usb-BioSpur_BioSpur_BLE_Control_87EA2F4A526C5A02-if00")
    ap.add_argument("--hours", type=float, default=8.0)
    ap.add_argument("--max-cycles", type=int, default=99)
    ap.add_argument("--duration", type=int, default=60)
    ap.add_argument("--targets", default="BSF66F,BS2DCE,BSDC91")
    ap.add_argument("--profiles", default="BSF66F:static,BS2DCE:roto,BSDC91:roto")
    ap.add_argument("--static-hz", type=int, default=5)
    ap.add_argument("--roto-hz", type=int, default=10)
    ap.add_argument("--motion-hz", type=int, default=5)
    ap.add_argument("--cm-probe-target", default="BSF66F")
    ap.add_argument("--skip-anchor-preflight", action="store_true")
    ap.add_argument("--skip-cm-probe", action="store_true")
    ap.add_argument("--out-dir", default=None)
    args = ap.parse_args()

    started_at = datetime.now()
    stop_at = started_at + timedelta(hours=args.hours)
    base_dir = Path(args.out_dir or f"logs/reject_overnight_{started_at.strftime('%Y%m%d_%H%M%S')}")
    base_dir.mkdir(parents=True, exist_ok=True)

    rows: list[dict[str, Any]] = []
    cycle = 1
    while cycle <= args.max_cycles and datetime.now() < stop_at:
        cycle_dir = base_dir / f"cycle_{cycle:02d}"
        cycle_dir.mkdir(parents=True, exist_ok=True)
        row: dict[str, Any] = {
            "cycle": cycle,
            "started_at": datetime.now().isoformat(timespec="seconds"),
            "status": "running",
            "session_dir": "-",
            "dominant_bd_reason_global": "-",
            "patch_recommended": False,
            "notes": "-",
        }
        rows.append(row)
        try:
            capture_base = cycle_dir / "capture"
            capture_cmd = [
                "python3",
                "scripts/run_recv_tdma_capture.py",
                "--port",
                args.port,
                "--duration",
                str(args.duration),
                "--targets",
                args.targets,
                "--profiles",
                args.profiles,
                "--static-hz",
                str(args.static_hz),
                "--roto-hz",
                str(args.roto_hz),
                "--motion-hz",
                str(args.motion_hz),
                "--cm-probe-target",
                args.cm_probe_target,
                "--out-dir",
                str(capture_base),
            ]
            if args.skip_anchor_preflight:
                capture_cmd.append("--skip-anchor-preflight")
            if args.skip_cm_probe:
                capture_cmd.append("--skip-cm-probe")
            run(capture_cmd, cycle_dir / "capture_driver.log")
            session_dir = latest_capture_session(cycle_dir, capture_base)
            row["session_dir"] = str(session_dir)

            analysis_json = cycle_dir / "reject_rootcause.json"
            analysis_md = cycle_dir / "reject_rootcause.md"
            run(
                [
                    "python3",
                    "scripts/analyze_recv_tdma_reject_rootcause.py",
                    "--session-dir",
                    str(session_dir),
                    "--out-json",
                    str(analysis_json),
                    "--out-md",
                    str(analysis_md),
                ],
                cycle_dir / "analysis_driver.log",
            )
            analysis = json.loads(analysis_json.read_text(encoding="utf-8"))
            row["dominant_bd_reason_global"] = analysis["conclusion"]["dominant_bd_reason_global"]
            row["patch_recommended"] = analysis["conclusion"]["patch_recommended"]
            row["notes"] = "capture+analysis ok"
            row["status"] = "ok"
        except subprocess.CalledProcessError as exc:
            row["status"] = "fail"
            row["notes"] = f"calledprocesserror rc={exc.returncode}"
        except Exception as exc:
            row["status"] = "fail"
            row["notes"] = f"{type(exc).__name__}: {exc}"
        finally:
            row["finished_at"] = datetime.now().isoformat(timespec="seconds")
            write_csv(base_dir / "overnight_summary.csv", rows)
            (base_dir / "overnight_summary.json").write_text(
                json.dumps(rows, indent=2) + "\n", encoding="utf-8"
            )
            write_md(base_dir / "overnight_summary.md", rows)

        cycle += 1

    print(base_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
