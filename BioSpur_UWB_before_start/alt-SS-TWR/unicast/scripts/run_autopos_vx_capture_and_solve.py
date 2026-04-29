#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
import time
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def run(cmd: list[str]) -> None:
    print("$", " ".join(cmd), flush=True)
    subprocess.run(cmd, cwd=REPO_ROOT, check=True)


def main() -> int:
    ap = argparse.ArgumentParser(
        description=(
            "For a given AutoPos version, capture fresh data (Anchor sweep + Tag115 CM) "
            "then run the offline solver chain for that version."
        )
    )
    ap.add_argument("--version", required=True, choices=["v1", "v2", "v3"], help="Which offline chain to run")
    ap.add_argument("--port", required=True, help="52840 CDC port")
    ap.add_argument("--order", default="ABCDEFGH", help="Anchor labels order")
    ap.add_argument("--sw-sets", type=int, default=100, help="Sweep set count")
    ap.add_argument("--tag-name", default="BSF66F", help="Tag115 BLE target name")
    ap.add_argument("--cm-lines", type=int, default=100, help="Required aggregated CM notify lines")
    ap.add_argument("--timeout-s", type=int, default=1800, help="Sweep timeout seconds")
    ap.add_argument("--out-dir", required=True, help="Output directory for this Vx run")
    ap.add_argument("--skip-capture", action="store_true", help="Skip sweep/cm capture and only solve using existing logs")
    args = ap.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    stamp = time.strftime("%Y%m%d_%H%M%S")
    capture_dir = out_dir / f"capture_{stamp}"
    capture_dir.mkdir(parents=True, exist_ok=True)
    sweep_dir = capture_dir / "sweep"
    tag_dir = capture_dir / "tag115_cm"
    solve_dir = out_dir / f"solve_{stamp}_{args.version}"
    solve_dir.mkdir(parents=True, exist_ok=True)

    if not args.skip_capture:
        run(
            [
                "python3",
                "scripts/run_autopos_sweep_loop.py",
                "--port",
                args.port,
                "--order",
                args.order,
                "--timeout-s",
                str(args.timeout_s),
                "--sw-sets",
                str(args.sw_sets),
                "--out-dir",
                str(sweep_dir),
            ]
        )

        run(
            [
                "python3",
                "scripts/run_anchor_responder_then_tag_cm.py",
                "--port",
                args.port,
                "--target-name",
                args.tag_name,
                "--cm-lines",
                str(args.cm_lines),
                "--out-dir",
                str(tag_dir),
            ]
        )

    summary_json = sweep_dir / "summary.json"
    run_log = tag_dir / "run.log"
    if not summary_json.exists():
        raise SystemExit(f"[error] missing {summary_json}")
    if not run_log.exists():
        raise SystemExit(f"[error] missing {run_log}")

    pairs_csv = solve_dir / "pairs_all.csv"
    run(
        [
            "python3",
            "scripts/autopos_extract_pairs_from_sweep_summary.py",
            "--summary-json",
            str(summary_json),
            "--out-csv",
            str(pairs_csv),
        ]
    )

    floating_ref_dir = solve_dir / "floating_ref115_from_cm"
    run(
        [
            "python3",
            "scripts/autopos_extract_ranges_from_tag_cm_runlog.py",
            "--run-log",
            str(run_log),
            "--out-dir",
            str(floating_ref_dir),
            "--min-cm-lines",
            str(args.cm_lines),
        ]
    )

    if args.version == "v1":
        v1_dir = solve_dir / "v1"
        v1_dir.mkdir(parents=True, exist_ok=True)
        run(
            [
                "python3",
                "scripts/fuse_bidirectional_matrix_v1.py",
                "--pairs-csv",
                str(pairs_csv),
                "--out-dir",
                str(v1_dir),
                "--min-dir-samples",
                "3",
            ]
        )
    elif args.version == "v2":
        v2_dir = solve_dir / "v2"
        v2_dir.mkdir(parents=True, exist_ok=True)
        run(
            [
                "python3",
                "scripts/prepare_autopos_v2.py",
                "--pairs-csv",
                str(pairs_csv),
                "--out-dir",
                str(v2_dir),
                "--floating-reference-session",
                str(floating_ref_dir),
            ]
        )
    elif args.version == "v3":
        v3_dir = solve_dir / "v3_lite"
        v3_dir.mkdir(parents=True, exist_ok=True)
        run(
            [
                "python3",
                "scripts/prepare_autopos_v3_lite.py",
                "--pairs-csv",
                str(pairs_csv),
                "--out-dir",
                str(v3_dir),
                "--floating-reference-session",
                str(floating_ref_dir),
            ]
        )
    else:
        raise SystemExit("[error] unknown version")

    manifest = {
        "version": args.version,
        "port": args.port,
        "order": list(args.order),
        "sw_sets": args.sw_sets,
        "timeout_s": args.timeout_s,
        "tag_name": args.tag_name,
        "cm_lines": args.cm_lines,
        "capture_dir": str(capture_dir.resolve()),
        "sweep_summary_json": str(summary_json.resolve()),
        "tag_run_log": str(run_log.resolve()),
        "solve_dir": str(solve_dir.resolve()),
        "pairs_csv": str(pairs_csv.resolve()),
        "floating_reference_dir": str(floating_ref_dir.resolve()),
    }
    (out_dir / "latest_run_manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"[ok] wrote {out_dir / 'latest_run_manifest.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

