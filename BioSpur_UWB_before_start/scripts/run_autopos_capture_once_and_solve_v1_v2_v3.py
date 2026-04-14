#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
import time
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_FLOATING_REF_Z_PRIOR_MM = 820.0


def run(cmd: list[str]) -> None:
    print("$", " ".join(cmd), flush=True)
    subprocess.run(cmd, cwd=REPO_ROOT, check=True)


def build_inter_anchor_matrix_from_pair_csv(pair_csv: Path, output_json: Path) -> dict[str, Any]:
    """
    Build the inter-anchor matrix json expected by solve_anchor_layout_iterative.py
    from a pair-distance CSV with columns: a,b,distance_mm (or dist_mm).
    """
    import csv

    distances: dict[str, int] = {}
    with pair_csv.open(encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            a = (row.get("a") or "").strip().upper()
            b = (row.get("b") or "").strip().upper()
            if not a or not b:
                continue
            d_raw = row.get("distance_mm") or row.get("dist_mm")
            if d_raw is None or str(d_raw).strip() == "":
                continue
            d = int(round(float(d_raw)))
            if d <= 0:
                continue
            distances[f"{a}-{b}"] = d

    payload = {
        "units": "mm",
        "anchors": list("ABCDEFGH"),
        "distances": distances,
        "pair_stats": {},
        "source": {"pair_csv": str(pair_csv.resolve())},
        "notes": [
            "Auto-generated from pair distance CSV.",
            "Used for iterative layout solve with soft constraints.",
        ],
    }
    output_json.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return payload


def main() -> int:
    ap = argparse.ArgumentParser(
        description=(
            "Capture a fresh A-H sweep + Tag115 CM once, then run V1/V2/V3-lite offline chains and compare outputs."
        )
    )
    ap.add_argument("--port", required=True, help="52840 CDC serial port")
    ap.add_argument("--order", default="ABCDEFGH", help="Anchor label order")
    ap.add_argument("--sw-sets", type=int, default=100, help="Sweep sets per master (A..H)")
    ap.add_argument(
        "--timeout-s",
        type=int,
        default=None,
        help="Per-round timeout seconds. Default: auto-scale with --sw-sets in run_autopos_sweep_loop.py",
    )
    ap.add_argument("--tag-name", default="BSF66F", help="Tag BLE target name")
    ap.add_argument(
        "--quiet-tag-name",
        default="BSF66F",
        help="Best-effort quarantine this Tag during sweep/responder conversion so it stays online but does not range. Use '-' to disable.",
    )
    ap.add_argument(
        "--floating-reference-z-prior-mm",
        type=float,
        default=None,
        help=(
            "Optional soft prior for floating reference Tag Z (mm). If omitted but CM floating reference is used, "
            f"defaults to {DEFAULT_FLOATING_REF_Z_PRIOR_MM:.0f}mm."
        ),
    )
    ap.add_argument("--cm-lines", type=int, default=100, help="Required aggregated CM notify lines")
    ap.add_argument(
        "--cm-train-lines",
        type=int,
        default=80,
        help="Holdout evaluation: number of aggregated CM notify lines used for training constraints.",
    )
    ap.add_argument(
        "--cm-test-lines",
        type=int,
        default=30,
        help="Holdout evaluation: number of aggregated CM notify lines reserved for holdout evaluation.",
    )
    ap.add_argument(
        "--out-dir",
        default=None,
        help="Output directory. Default: autopos_V3/logs/v123_fresh_YYYYmmdd_HHMMSS",
    )
    args = ap.parse_args()

    stamp = time.strftime("%Y%m%d_%H%M%S")
    out_dir = Path(args.out_dir) if args.out_dir else (REPO_ROOT / "autopos_V3" / "logs" / f"v123_fresh_{stamp}")
    out_dir.mkdir(parents=True, exist_ok=True)

    capture_dir = out_dir / f"capture_{stamp}"
    sweep_dir = capture_dir / "sweep"
    tag_dir = capture_dir / "tag115_cm"
    solve_dir = out_dir / f"solve_{stamp}"
    solve_dir.mkdir(parents=True, exist_ok=True)
    sweep_dir.mkdir(parents=True, exist_ok=True)
    tag_dir.mkdir(parents=True, exist_ok=True)

    # 1) Capture sweep once.
    sweep_cmd = [
        "python3",
        "scripts/run_autopos_sweep_loop.py",
        "--port",
        args.port,
        "--order",
        args.order,
        "--sw-sets",
        str(args.sw_sets),
        "--quiet-tag-name",
        args.quiet_tag_name,
        "--out-dir",
        str(sweep_dir),
    ]
    if args.timeout_s is not None:
        sweep_cmd.extend(["--timeout-s", str(args.timeout_s)])
    run(sweep_cmd)

    # 2) Capture Tag CM once (after sweep).
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
            "--quiet-tag-name",
            args.quiet_tag_name,
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

    # 3) Extract shared inputs.
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

    # 3b) Extract Tag115 CM into floating-reference sessions (full + train/test split).
    floating_ref_full_dir = solve_dir / "floating_ref115_from_cm"
    run(
        [
            "python3",
            "scripts/autopos_extract_ranges_from_tag_cm_runlog.py",
            "--run-log",
            str(run_log),
            "--out-dir",
            str(floating_ref_full_dir),
            "--min-cm-lines",
            str(args.cm_lines),
        ]
    )

    # Compute actual available CM notify lines so train/test sizes never exceed what's captured.
    run_text = run_log.read_text(encoding="utf-8", errors="ignore")
    total_cm_notify_lines = sum(1 for line in run_text.splitlines() if f"{args.tag_name} notify: CM;" in line)
    train_n = max(0, min(args.cm_train_lines, total_cm_notify_lines))
    test_n = max(0, min(args.cm_test_lines, max(0, total_cm_notify_lines - train_n)))

    cm_split_dir = solve_dir / "cm_split"
    cm_split_dir.mkdir(parents=True, exist_ok=True)
    cm_train_log = cm_split_dir / "run_train.log"
    cm_test_log = cm_split_dir / "run_test.log"
    run(
        [
            "python3",
            "scripts/autopos_split_tag_cm_runlog.py",
            "--run-log",
            str(run_log),
            "--out-train",
            str(cm_train_log),
            "--out-test",
            str(cm_test_log),
            "--cm-train-lines",
            str(train_n),
            "--cm-test-lines",
            str(test_n),
            "--tag-name",
            args.tag_name,
        ]
    )

    floating_ref_train_dir = solve_dir / "floating_ref115_train"
    floating_ref_holdout_dir = solve_dir / "floating_ref115_holdout"
    run(
        [
            "python3",
            "scripts/autopos_extract_ranges_from_tag_cm_runlog.py",
            "--run-log",
            str(cm_train_log),
            "--out-dir",
            str(floating_ref_train_dir),
            "--min-cm-lines",
            str(max(1, train_n)),
        ]
    )
    run(
        [
            "python3",
            "scripts/autopos_extract_ranges_from_tag_cm_runlog.py",
            "--run-log",
            str(cm_test_log),
            "--out-dir",
            str(floating_ref_holdout_dir),
            "--min-cm-lines",
            str(max(1, test_n)),
        ]
    )

    # 4) V1 solve.
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
    # V1-soft: re-solve layout using the same iterative solver (soft constraints + optional Tag115 Z prior),
    # so V1 is comparable in the *layout* domain.
    v1_pairs = v1_dir / "final_pair_distances.csv"
    v1_matrix = v1_dir / "inter_anchor_matrix_v1.json"
    build_inter_anchor_matrix_from_pair_csv(v1_pairs, v1_matrix)
    v1_soft_layout = v1_dir / "anchor_layout_v1_soft_iterative.json"
    z_prior_mm = args.floating_reference_z_prior_mm if args.floating_reference_z_prior_mm is not None else DEFAULT_FLOATING_REF_Z_PRIOR_MM
    run(
        [
            "python3",
            "scripts/solve_anchor_layout_iterative.py",
            "--input",
            str(v1_matrix),
            "--output",
            str(v1_soft_layout),
            "--initial-layout",
            "data/anchor_layout_ah_calibrated.json",
            "--max-iters",
            "8",
            "--converge-mm",
            "1.5",
            "--floating-reference-session",
            str(floating_ref_train_dir),
            "--floating-reference-z-prior-mm",
            str(z_prior_mm),
        ]
    )

    # 5) V2 solve.
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
            str(floating_ref_train_dir),
            "--floating-reference-z-prior-mm",
            str(args.floating_reference_z_prior_mm if args.floating_reference_z_prior_mm is not None else DEFAULT_FLOATING_REF_Z_PRIOR_MM),
        ]
    )

    # 6) V3-lite solve.
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
            str(floating_ref_train_dir),
            "--floating-reference-z-prior-mm",
            str(args.floating_reference_z_prior_mm if args.floating_reference_z_prior_mm is not None else DEFAULT_FLOATING_REF_Z_PRIOR_MM),
        ]
    )

    # 7) Compare pair distances.
    # V1 file name differs from V2/V3.
    v2_pairs = v2_dir / "v2_fused" / "final_pair_distances_v2.csv"
    v3_pairs = v3_dir / "v3_fused" / "final_pair_distances_v2.csv"
    compare_md = out_dir / "compare_v1_v2_v3_pairs.md"
    run(
        [
            "python3",
            "scripts/autopos_compare_v1_v2_v3_pairs.py",
            "--zero-as-missing",
            "--v1",
            str(v1_pairs),
            "--v2",
            str(v2_pairs),
            "--v3",
            str(v3_pairs),
            "--out",
            str(compare_md),
        ]
    )

    # 8) Compare layouts (rigid-aligned) across versions.
    v1_layout = v1_soft_layout if v1_soft_layout.exists() else (v1_dir / "anchor_coords_v1.json")
    v2_layout = v2_dir / "v2_fused" / "anchor_layout_v2_iterative.json"
    v3_layout = v3_dir / "v3_fused" / "anchor_layout_v3_lite_iterative.json"
    compare_layout_md = out_dir / "compare_v1_v2_v3_layouts.md"
    if v1_layout.exists() and v2_layout.exists() and v3_layout.exists():
        run(
            [
                "python3",
                "scripts/autopos_compare_v1_v2_v3_layouts.py",
                "--v1",
                str(v1_layout),
                "--v2",
                str(v2_layout),
                "--v3",
                str(v3_layout),
                "--out",
                str(compare_layout_md),
            ]
        )

    # 9) Holdout evaluation (test CM ranges not used in solve).
    eval_dir = out_dir / "eval_holdout"
    eval_dir.mkdir(parents=True, exist_ok=True)
    v1_eval = eval_dir / "holdout_eval_v1.md"
    v2_eval = eval_dir / "holdout_eval_v2.md"
    v3_eval = eval_dir / "holdout_eval_v3.md"
    run(
        [
            "python3",
            "scripts/autopos_eval_holdout_floating_ref.py",
            "--layout",
            str(v1_layout),
            "--train-session",
            str(floating_ref_train_dir),
            "--holdout-session",
            str(floating_ref_holdout_dir),
            "--out",
            str(v1_eval),
        ]
    )
    run(
        [
            "python3",
            "scripts/autopos_eval_holdout_floating_ref.py",
            "--layout",
            str(v2_layout),
            "--train-session",
            str(floating_ref_train_dir),
            "--holdout-session",
            str(floating_ref_holdout_dir),
            "--out",
            str(v2_eval),
        ]
    )
    run(
        [
            "python3",
            "scripts/autopos_eval_holdout_floating_ref.py",
            "--layout",
            str(v3_layout),
            "--train-session",
            str(floating_ref_train_dir),
            "--holdout-session",
            str(floating_ref_holdout_dir),
            "--out",
            str(v3_eval),
        ]
    )

    manifest = {
        "port": args.port,
        "order": list(args.order),
        "sw_sets": args.sw_sets,
        "timeout_s": args.timeout_s,
        "tag_name": args.tag_name,
        "quiet_tag_name": None if args.quiet_tag_name == "-" else args.quiet_tag_name,
        "cm_lines": args.cm_lines,
        "cm_train_lines_requested": args.cm_train_lines,
        "cm_test_lines_requested": args.cm_test_lines,
        "cm_total_notify_lines_seen": total_cm_notify_lines,
        "cm_train_lines_effective": train_n,
        "cm_test_lines_effective": test_n,
        "out_dir": str(out_dir.resolve()),
        "capture_dir": str(capture_dir.resolve()),
        "sweep_summary_json": str(summary_json.resolve()),
        "tag_run_log": str(run_log.resolve()),
        "pairs_csv": str(pairs_csv.resolve()),
        "floating_reference_full_dir": str(floating_ref_full_dir.resolve()),
        "floating_reference_train_dir": str(floating_ref_train_dir.resolve()),
        "floating_reference_holdout_dir": str(floating_ref_holdout_dir.resolve()),
        "solve_dir": str(solve_dir.resolve()),
        "v1_pairs_csv": str(v1_pairs.resolve()),
        "v2_pairs_csv": str(v2_pairs.resolve()),
        "v3_pairs_csv": str(v3_pairs.resolve()),
        "compare_md": str(compare_md.resolve()),
        "compare_layout_md": str(compare_layout_md.resolve()) if compare_layout_md.exists() else None,
        "floating_reference_z_prior_mm": (
            float(args.floating_reference_z_prior_mm)
            if args.floating_reference_z_prior_mm is not None
            else DEFAULT_FLOATING_REF_Z_PRIOR_MM
        ),
        "v1_soft_layout": str(v1_soft_layout.resolve()) if v1_soft_layout.exists() else None,
        "holdout_eval_v1_md": str(v1_eval.resolve()),
        "holdout_eval_v2_md": str(v2_eval.resolve()),
        "holdout_eval_v3_md": str(v3_eval.resolve()),
    }
    (out_dir / "run_manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"[ok] wrote {out_dir / 'run_manifest.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
