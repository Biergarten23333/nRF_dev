#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from biospur_tag_positioning_offline_solver.trajectory import solve_capture_trajectory, write_trajectory_json


def main() -> int:
    ap = argparse.ArgumentParser(description="Export a BioSpur tag trajectory with T-series offline solver.")
    ap.add_argument("--layout", required=True)
    ap.add_argument("--capture", required=True, help="Capture directory or tr_all.csv")
    ap.add_argument("--out", required=True)
    ap.add_argument("--method", choices=["T1", "T2", "T3", "T4", "T4_V6_IMU_GATE"], default="T1")
    ap.add_argument("--anchor-sigma")
    ap.add_argument("--tag-delay-json", help="Optional JSON file mapping tag BSXXXX to calibrated delay mm.")
    ap.add_argument("--tags", help="Comma-separated tag allow-list, for example BSF66F,BS2DCE")
    ap.add_argument("--solver-loss", choices=["linear", "huber", "tukey"], default="huber")
    ap.add_argument(
        "--solver-f-scale-mm",
        type=float,
        default=30.0,
        help="Huber transition in mm. Huber downweights positively biased NLOS range outliers.",
    )
    ap.add_argument("--max-frames", type=int, default=0)
    ap.add_argument("--tail-rows", type=int, default=0)
    args = ap.parse_args()

    tags = {v.strip().upper() for v in (args.tags or "").split(",") if v.strip()} or None
    tag_delay_by_tag = {}
    if args.tag_delay_json:
        tag_delay_by_tag = {
            str(k).strip().upper(): float(v)
            for k, v in json.loads(Path(args.tag_delay_json).read_text(encoding="utf-8")).items()
        }
    result = solve_capture_trajectory(
        layout_path=args.layout,
        capture_path=args.capture,
        method=args.method,
        anchor_sigma_path=args.anchor_sigma,
        tags=tags,
        tag_delay_by_tag=tag_delay_by_tag,
        solver_loss=args.solver_loss,
        solver_f_scale_mm=args.solver_f_scale_mm,
        max_frames=args.max_frames,
        tail_rows=args.tail_rows,
    )
    write_trajectory_json(result, args.out)
    print(f"[tagpos] method={args.method} frames={result.frames_solved}/{result.frames_input} out={args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
