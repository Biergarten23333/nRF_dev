#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import subprocess
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]


def run(cmd: list[str]) -> None:
    print("$", " ".join(cmd), flush=True)
    subprocess.run(cmd, cwd=REPO_ROOT, check=True)


def build_inter_anchor_matrix_from_fused(fused_csv: Path, output_json: Path) -> dict[str, Any]:
    pair_stats: dict[str, Any] = {}
    distances: dict[str, int] = {}

    with fused_csv.open(encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            a = row["a"].strip().upper()
            b = row["b"].strip().upper()
            key = f"{a}-{b}"
            dist_raw = row.get("distance_mm")
            if not dist_raw:
                continue
            distances[key] = int(round(float(dist_raw)))
            pair_stats[key] = {
                "source": "v2_fused",
                "decision": row.get("decision"),
                "recommended_solver_mode": row.get("recommended_solver_mode"),
                "ci95_mm": float(row["ci95_mm"]) if row.get("ci95_mm") else None,
                "weight": float(row["weight"]) if row.get("weight") else None,
                "z": float(row["z"]) if row.get("z") else None,
                "bias_significant": row.get("bias_significant") in {"True", "true", "1"},
                "n_ab": int(row["n_ab"]) if row.get("n_ab") else 0,
                "n_ba": int(row["n_ba"]) if row.get("n_ba") else 0,
                "next_action": row.get("next_action"),
            }

    payload = {
        "units": "mm",
        "anchors": list("ABCDEFGH"),
        "distances": distances,
        "pair_stats": pair_stats,
        "source": {
            "fused_csv": str(fused_csv.resolve()),
        },
        "notes": [
            "V2 fused inter-anchor matrix.",
            "Distances are symmetric export values for constrained solve.",
            "Bias-significant pairs retain their better single-direction value but are flagged in pair_stats.",
        ],
    }
    output_json.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return payload


def load_cm_baseline(path: Path) -> dict[str, Any]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    metrics = raw.get("metrics", [])
    weights = []
    for row in metrics:
        pstdev = row.get("filt_pstdev_mm")
        ok_ratio = row.get("ok_ratio_anchor")
        if pstdev in (None, 0):
            score = None
        else:
            score = round(float(ok_ratio) / float(pstdev), 6)
        weights.append(
            {
                "anchor_id": row.get("anchor_id"),
                "ok_ratio_anchor": ok_ratio,
                "filt_pstdev_mm": pstdev,
                "quality_mean": row.get("quality_mean"),
                "reliability_score": score,
            }
        )
    return {
        "source": str(path.resolve()),
        "mode": raw.get("mode"),
        "total": raw.get("total"),
        "feedback_weights": weights,
    }


def main() -> int:
    p = argparse.ArgumentParser(description="Prepare tomorrow's V2 autopos offline chain.")
    p.add_argument("--pairs-csv", required=True)
    p.add_argument("--out-dir", required=True)
    p.add_argument("--initial-layout", default="data/anchor_layout_ah_calibrated.json")
    p.add_argument("--z-thresh", type=float, default=2.0)
    p.add_argument("--min-dir-samples", type=int, default=30)
    p.add_argument("--var-floor-mm2", type=float, default=0.25)
    p.add_argument("--ref115-cm-baseline", default=None)
    p.add_argument("--reference-session", action="append", default=[])
    p.add_argument("--floating-reference-session", action="append", default=[])
    p.add_argument(
        "--floating-reference-z-prior-mm",
        type=float,
        default=None,
        help=(
            "Optional soft prior for floating reference Tag Z (mm). If omitted but a floating reference session is "
            "provided, defaults to 820mm (historical Ref115 floor-height prior)."
        ),
    )
    p.add_argument("--skip-solve", action="store_true")
    args = p.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    fused_dir = out_dir / "v2_fused"
    fused_dir.mkdir(parents=True, exist_ok=True)

    run(
        [
            "python3",
            "scripts/fuse_bidirectional_matrix_v2.py",
            "--pairs-csv",
            args.pairs_csv,
            "--out-dir",
            str(fused_dir),
            "--z-thresh",
            str(args.z_thresh),
            "--min-dir-samples",
            str(args.min_dir_samples),
            "--var-floor-mm2",
            str(args.var_floor_mm2),
        ]
    )

    fused_csv = fused_dir / "final_pair_distances_v2.csv"
    matrix_json = fused_dir / "inter_anchor_matrix_v2fused.json"
    matrix_payload = build_inter_anchor_matrix_from_fused(fused_csv, matrix_json)

    layout_output = None
    if not args.skip_solve:
        layout_output = fused_dir / "anchor_layout_v2_iterative.json"
        solve_cmd = [
            "python3",
            "scripts/solve_anchor_layout_iterative.py",
            "--input",
            str(matrix_json),
            "--output",
            str(layout_output),
            "--initial-layout",
            args.initial_layout,
            "--max-iters",
            "8",
            "--converge-mm",
            "1.5",
            # Floating-ref CM ranges are noisy and sometimes biased; keep them
            # clearly "soft" so they don't collapse the inter-anchor geometry.
            "--reference-sigma-mm",
            "150",
        ]
        # Enable floating-ref Z prior by default when floating reference data is available.
        z_prior_mm = args.floating_reference_z_prior_mm
        if z_prior_mm is None and args.floating_reference_session:
            z_prior_mm = 820.0
        if z_prior_mm is not None and args.floating_reference_session:
            solve_cmd.extend(["--floating-reference-z-prior-mm", str(z_prior_mm)])
        for ref in args.reference_session:
            solve_cmd.extend(["--reference-session", ref])
        for ref in args.floating_reference_session:
            solve_cmd.extend(["--floating-reference-session", ref])
        run(solve_cmd)

    feedback_summary = None
    if args.ref115_cm_baseline:
        feedback_summary = load_cm_baseline(Path(args.ref115_cm_baseline))
        (fused_dir / "ref115_feedback_weights.json").write_text(
            json.dumps(feedback_summary, indent=2) + "\n", encoding="utf-8"
        )

    manifest = {
        "pairs_csv": str(Path(args.pairs_csv).resolve()),
        "out_dir": str(out_dir.resolve()),
        "fused_dir": str(fused_dir.resolve()),
        "fused_csv": str(fused_csv.resolve()),
        "pair_report_v2": str((fused_dir / "pair_decision_report_v2.json").resolve()),
        "matrix_json_v2": str(matrix_json.resolve()),
        "layout_output_v2": str(layout_output.resolve()) if layout_output else None,
        "ref115_cm_baseline": str(Path(args.ref115_cm_baseline).resolve()) if args.ref115_cm_baseline else None,
        "reference_sessions": args.reference_session,
        "floating_reference_sessions": args.floating_reference_session,
        "floating_reference_z_prior_mm": (
            float(args.floating_reference_z_prior_mm)
            if args.floating_reference_z_prior_mm is not None
            else (820.0 if args.floating_reference_session else None)
        ),
        "pair_count": len(matrix_payload.get("distances", {})),
        "next_live_loop": [
            "Run fresh bidirectional sweep=50 capture.",
            "Re-run prepare_autopos_v2.py against new pairs_all.csv.",
            "Run Ref115 static CM baseline compare.",
            "Run Tag127 rotation CM compare when token206 is available.",
            "If layout delta and tag residuals improve, keep new layout; otherwise revert.",
        ],
    }
    manifest_path = out_dir / "v2_prep_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    print(f"[ok] wrote {manifest_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
