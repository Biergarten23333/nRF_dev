#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
import time
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_FLOATING_REF_Z_PRIOR_MM = 820.0


def run(cmd: list[str]) -> None:
    print("$", " ".join(cmd), flush=True)
    subprocess.run(cmd, cwd=REPO_ROOT, check=True)


def main() -> int:
    ap = argparse.ArgumentParser(
        description=(
            "Re-run offline V1/V2/V3-lite/V3-full solve chains from an existing pairs_all.csv + floating ref sessions."
        )
    )
    ap.add_argument("--run-dir", required=True, help="Existing v123_fresh_* directory (will write new solve_rerun_*)")
    ap.add_argument("--pairs-csv", default=None, help="pairs_all.csv (defaults to <run-dir>/solve_*/pairs_all.csv newest)")
    ap.add_argument(
        "--floating-reference-train",
        default=None,
        help="floating_ref115_train dir (defaults to <run-dir>/solve_*/floating_ref115_train newest)",
    )
    ap.add_argument(
        "--floating-reference-holdout",
        default=None,
        help="floating_ref115_holdout dir (optional; defaults to <run-dir>/solve_*/floating_ref115_holdout newest)",
    )
    ap.add_argument(
        "--floating-reference-z-prior-mm",
        type=float,
        default=DEFAULT_FLOATING_REF_Z_PRIOR_MM,
        help=f"Z prior for floating reference Tag (mm). Default: {DEFAULT_FLOATING_REF_Z_PRIOR_MM:.0f}.",
    )
    ap.add_argument(
        "--v3full-with-tag115-cm",
        action="store_true",
        help="Require Tag115 CM floating-reference session and run V3-full in explicit Tag115-CM mode.",
    )
    ap.add_argument("--out-name", default=None, help="Optional fixed rerun dir name; default: solve_rerun_YYYYmmdd_HHMMSS")
    ap.add_argument("--skip-v3full", action="store_true", help="Skip V3-full stage (debug).")
    args = ap.parse_args()

    run_dir = Path(args.run_dir).resolve()
    if not run_dir.exists():
        raise SystemExit(f"[error] run-dir not found: {run_dir}")

    stamp = time.strftime("%Y%m%d_%H%M%S")
    out_name = args.out_name or f"solve_rerun_{stamp}"

    # Resolve defaults from the newest solve_* directory.
    solve_candidates = sorted(
        [p for p in run_dir.glob("solve_*") if p.is_dir() and p.name != out_name],
        key=lambda p: p.name,
    )
    # Prefer directories that actually contain pairs_all.csv.
    solve_candidates = [p for p in solve_candidates if (p / "pairs_all.csv").exists()]
    newest_solve = solve_candidates[-1] if solve_candidates else None
    if args.pairs_csv:
        pairs_csv = Path(args.pairs_csv).resolve()
    elif newest_solve:
        pairs_csv = newest_solve / "pairs_all.csv"
    else:
        raise SystemExit("[error] --pairs-csv not provided and no solve_* directory found under run-dir")

    if args.floating_reference_train:
        floating_ref_train = Path(args.floating_reference_train).resolve()
    elif newest_solve:
        floating_ref_train = newest_solve / "floating_ref115_train"
    else:
        floating_ref_train = None

    if args.floating_reference_holdout:
        floating_ref_holdout = Path(args.floating_reference_holdout).resolve()
    elif newest_solve:
        floating_ref_holdout = newest_solve / "floating_ref115_holdout"
    else:
        floating_ref_holdout = None

    if not pairs_csv.exists():
        raise SystemExit(f"[error] pairs csv not found: {pairs_csv}")
    if floating_ref_train and not floating_ref_train.exists():
        floating_ref_train = None
    if floating_ref_holdout and not floating_ref_holdout.exists():
        floating_ref_holdout = None

    if args.v3full_with_tag115_cm and not floating_ref_train:
        raise SystemExit(
            "[error] --v3full-with-tag115-cm requires --floating-reference-train "
            "(or an existing solve_* containing floating_ref115_train)"
        )

    solve_dir = run_dir / out_name
    solve_dir.mkdir(parents=True, exist_ok=True)

    # 1) V1
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
    # V1-soft layout (comparable to v2/v3lite layout domain).
    v1_pairs = v1_dir / "final_pair_distances.csv"
    v1_matrix = v1_dir / "inter_anchor_matrix_v1.json"
    run(
        [
            "python3",
            "scripts/autopos_build_inter_anchor_matrix_from_pairs_csv.py",
            "--pairs-csv",
            str(v1_pairs),
            "--out-json",
            str(v1_matrix),
        ]
    )
    v1_soft_layout = v1_dir / "anchor_layout_v1_soft_iterative.json"
    solve_cmd = [
        "python3",
        "scripts/solve_anchor_layout_iterative.py",
        "--input",
        str(v1_matrix),
        "--output",
        str(v1_soft_layout),
        "--initial-layout",
        "data/anchor_layout_ah_calibrated.json",
        "--max-iters",
        "6",
        "--converge-mm",
        "1.5",
        "--multi-start",
        "3",
        "--start-jitter-mm",
        "300",
        "--adaptive-edge-reweight-rounds",
        "1",
        "--reference-sigma-mm",
        "150",
    ]
    if floating_ref_train:
        solve_cmd.extend(["--floating-reference-session", str(floating_ref_train)])
        solve_cmd.extend(["--floating-reference-z-prior-mm", str(args.floating_reference_z_prior_mm)])
    run(solve_cmd)

    # 2) V2
    v2_dir = solve_dir / "v2"
    v2_dir.mkdir(parents=True, exist_ok=True)
    v2_cmd = [
        "python3",
        "scripts/prepare_autopos_v2.py",
        "--pairs-csv",
        str(pairs_csv),
        "--out-dir",
        str(v2_dir),
        "--skip-solve",
    ]
    if floating_ref_train:
        v2_cmd.extend(["--floating-reference-session", str(floating_ref_train)])
        v2_cmd.extend(["--floating-reference-z-prior-mm", str(args.floating_reference_z_prior_mm)])
    run(v2_cmd)
    v2_matrix = v2_dir / "v2_fused" / "inter_anchor_matrix_v2fused.json"
    v2_layout = v2_dir / "v2_fused" / "anchor_layout_v2_iterative.json"
    v2_solve_cmd = [
        "python3",
        "scripts/solve_anchor_layout_iterative.py",
        "--input",
        str(v2_matrix),
        "--output",
        str(v2_layout),
        "--initial-layout",
        "data/anchor_layout_ah_calibrated.json",
        "--max-iters",
        "6",
        "--converge-mm",
        "1.2",
        "--multi-start",
        "3",
        "--start-jitter-mm",
        "300",
        "--adaptive-edge-reweight-rounds",
        "1",
        "--reference-sigma-mm",
        "150",
    ]
    if floating_ref_train:
        v2_solve_cmd.extend(["--floating-reference-session", str(floating_ref_train)])
        v2_solve_cmd.extend(["--floating-reference-z-prior-mm", str(args.floating_reference_z_prior_mm)])
    run(v2_solve_cmd)

    # 3) V3-lite
    v3_dir = solve_dir / "v3_lite"
    v3_dir.mkdir(parents=True, exist_ok=True)
    v3_cmd = [
        "python3",
        "scripts/prepare_autopos_v3_lite.py",
        "--pairs-csv",
        str(pairs_csv),
        "--out-dir",
        str(v3_dir),
        "--skip-solve",
    ]
    if floating_ref_train:
        v3_cmd.extend(["--floating-reference-session", str(floating_ref_train)])
        v3_cmd.extend(["--floating-reference-z-prior-mm", str(args.floating_reference_z_prior_mm)])
    run(v3_cmd)
    v3_matrix = v3_dir / "v3_fused" / "inter_anchor_matrix_v2fused.json"
    v3_layout = v3_dir / "v3_fused" / "anchor_layout_v3_lite_iterative.json"
    v3_solve_cmd = [
        "python3",
        "scripts/solve_anchor_layout_iterative.py",
        "--input",
        str(v3_matrix),
        "--output",
        str(v3_layout),
        "--initial-layout",
        "data/anchor_layout_ah_calibrated.json",
        "--max-iters",
        "8",
        "--converge-mm",
        "1.0",
        "--multi-start",
        "6",
        "--start-jitter-mm",
        "350",
        "--adaptive-edge-reweight-rounds",
        "2",
        "--distance-sigma-mm",
        "75",
        "--distance-sigma-same-plane-mm",
        "90",
        "--distance-sigma-cross-plane-mm",
        "140",
        "--reference-sigma-mm",
        "150",
    ]
    if floating_ref_train:
        v3_solve_cmd.extend(["--floating-reference-session", str(floating_ref_train)])
        v3_solve_cmd.extend(["--floating-reference-z-prior-mm", str(args.floating_reference_z_prior_mm)])
    run(v3_solve_cmd)

    # 4) V3-full
    v3full_dir_name = "v3_full_tag115_cm" if args.v3full_with_tag115_cm else "v3_full"
    v3full_dir = solve_dir / v3full_dir_name
    out_compare_pairs = solve_dir / "compare_v1_v2_v3_v3full_pairs.md"
    out_compare_layouts = solve_dir / "compare_v1_v2_v3_v3full_layouts.md"
    v3full_layout = None
    v3full_pairs = None
    if not args.skip_v3full:
        v3full_dir.mkdir(parents=True, exist_ok=True)
        v3full_cmd = [
            "python3",
            "scripts/prepare_autopos_v3_full.py",
            "--pairs-csv",
            str(pairs_csv),
            "--out-dir",
            str(v3full_dir),
            "--floating-reference-z-prior-mm",
            str(args.floating_reference_z_prior_mm),
            "--bias-sigma-mm",
            "200",
            "--sigma-dist-mm",
            "80",
            "--sigma-ref-mm",
            "150",
            "--max-iters",
            "15",
            "--verbose",
            "1",
        ]
        if floating_ref_train:
            v3full_cmd.extend(["--floating-reference-session", str(floating_ref_train)])
        run(v3full_cmd)
        v3full_pairs = v3full_dir / "v3_full_fused" / "final_pair_distances_v3.csv"
        v3full_layout = v3full_dir / "anchor_layout_v3_full.json"

    # Compare 4 versions.
    v2_pairs = v2_dir / "v2_fused" / "final_pair_distances_v2.csv"
    v3_pairs = v3_dir / "v3_fused" / "final_pair_distances_v2.csv"
    if v3full_pairs and v3full_pairs.exists():
        run(
            [
                "python3",
                "scripts/autopos_compare_v1_v2_v3_v3full_pairs.py",
                "--zero-as-missing",
                "--v1",
                str(v1_pairs),
                "--v2",
                str(v2_pairs),
                "--v3",
                str(v3_pairs),
                "--v3full",
                str(v3full_pairs),
                "--out",
                str(out_compare_pairs),
            ]
        )

    if v3full_layout and v3full_layout.exists():
        run(
            [
                "python3",
                "scripts/autopos_compare_v1_v2_v3_v3full_layouts.py",
                "--v1",
                str(v1_soft_layout),
                "--v2",
                str(v2_layout),
                "--v3",
                str(v3_layout),
                "--v3full",
                str(v3full_layout),
                "--out",
                str(out_compare_layouts),
            ]
        )

    # Optional: holdout eval.
    eval_dir = solve_dir / "eval_holdout"
    eval_dir.mkdir(parents=True, exist_ok=True)
    if floating_ref_train and floating_ref_holdout:
        run(
            [
                "python3",
                "scripts/autopos_eval_holdout_floating_ref.py",
                "--layout",
                str(v1_soft_layout),
                "--train-session",
                str(floating_ref_train),
                "--holdout-session",
                str(floating_ref_holdout),
                "--out",
                str(eval_dir / "holdout_eval_v1.md"),
            ]
        )
        run(
            [
                "python3",
                "scripts/autopos_eval_holdout_floating_ref.py",
                "--layout",
                str(v2_layout),
                "--train-session",
                str(floating_ref_train),
                "--holdout-session",
                str(floating_ref_holdout),
                "--out",
                str(eval_dir / "holdout_eval_v2.md"),
            ]
        )
        run(
            [
                "python3",
                "scripts/autopos_eval_holdout_floating_ref.py",
                "--layout",
                str(v3_layout),
                "--train-session",
                str(floating_ref_train),
                "--holdout-session",
                str(floating_ref_holdout),
                "--out",
                str(eval_dir / "holdout_eval_v3.md"),
            ]
        )
        if v3full_layout and v3full_layout.exists():
            run(
                [
                    "python3",
                    "scripts/autopos_eval_holdout_floating_ref.py",
                    "--layout",
                    str(v3full_layout),
                    "--train-session",
                    str(floating_ref_train),
                    "--holdout-session",
                    str(floating_ref_holdout),
                    "--out",
                    str(eval_dir / "holdout_eval_v3full.md"),
                ]
            )

    manifest = {
        "run_dir": str(run_dir),
        "solve_dir": str(solve_dir),
        "pairs_csv": str(pairs_csv),
        "floating_ref_train": str(floating_ref_train) if floating_ref_train else None,
        "floating_ref_holdout": str(floating_ref_holdout) if floating_ref_holdout else None,
        "floating_ref_z_prior_mm": float(args.floating_reference_z_prior_mm),
        "v3full_with_tag115_cm": bool(args.v3full_with_tag115_cm),
        "v1_pairs": str(v1_pairs),
        "v1_layout": str(v1_soft_layout),
        "v2_pairs": str(v2_pairs),
        "v2_layout": str(v2_layout),
        "v3_pairs": str(v3_pairs),
        "v3_layout": str(v3_layout),
        "v3full_pairs": str(v3full_pairs) if v3full_pairs else None,
        "v3full_layout": str(v3full_layout) if v3full_layout else None,
        "compare_pairs_md": str(out_compare_pairs) if out_compare_pairs.exists() else None,
        "compare_layouts_md": str(out_compare_layouts) if out_compare_layouts.exists() else None,
    }
    (solve_dir / "rerun_manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"[ok] wrote {solve_dir / 'rerun_manifest.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
