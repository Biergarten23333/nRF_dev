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
    """
    Convert the fused pair-distance CSV into the inter-anchor matrix JSON that
    solve_anchor_layout_iterative.py expects.
    """
    pair_stats: dict[str, Any] = {}
    distances: dict[str, int] = {}

    with fused_csv.open(encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            a = (row.get("a") or "").strip().upper()
            b = (row.get("b") or "").strip().upper()
            if not a or not b:
                continue
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
                "bias_significant": str(row.get("bias_significant")) in {"True", "true", "1"},
                "n_ab": int(row["n_ab"]) if row.get("n_ab") else 0,
                "n_ba": int(row["n_ba"]) if row.get("n_ba") else 0,
                "next_action": row.get("next_action"),
            }

    payload = {
        "units": "mm",
        "anchors": list("ABCDEFGH"),
        "distances": distances,
        "pair_stats": pair_stats,
        "source": {"fused_csv": str(fused_csv.resolve())},
        "notes": [
            "V3-lite uses V2 fused distances as its solver input matrix.",
            "Distances are symmetric export values for constrained solve.",
        ],
    }
    output_json.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return payload


def main() -> int:
    ap = argparse.ArgumentParser(
        description=(
            "Prepare a V3-lite offline chain: improved bidirectional fusion (MVUE-ish) "
            "+ more aggressive iterative solve. This is a pragmatic stepping stone; "
            "it is not the full V3 from the design doc (no SDP init / no antenna delay)."
        )
    )
    ap.add_argument("--pairs-csv", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--initial-layout", default="data/anchor_layout_ah_calibrated.json")
    ap.add_argument("--z-thresh", type=float, default=2.0)
    ap.add_argument("--min-dir-samples", type=int, default=20)
    ap.add_argument("--var-floor-mm2", type=float, default=0.09)
    ap.add_argument("--floating-reference-session", action="append", default=[])
    ap.add_argument(
        "--floating-reference-z-prior-mm",
        type=float,
        default=None,
        help=(
            "Optional soft prior for floating reference Tag Z (mm). If omitted but a floating reference session is "
            "provided, defaults to 820mm (historical Ref115 floor-height prior)."
        ),
    )
    ap.add_argument("--skip-solve", action="store_true")
    args = ap.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    fused_dir = out_dir / "v3_fused"
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
        layout_output = fused_dir / "anchor_layout_v3_lite_iterative.json"
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
            "10",
            "--converge-mm",
            "1.0",
            "--multi-start",
            "12",
            "--start-jitter-mm",
            "350",
            "--adaptive-edge-reweight-rounds",
            "3",
            # Slightly tighter edge sigmas to reduce slow drift / warm-up.
            "--distance-sigma-mm",
            "75",
            "--distance-sigma-same-plane-mm",
            "90",
            "--distance-sigma-cross-plane-mm",
            "140",
            # Floating-ref CM ranges are noisy and sometimes biased; keep them
            # clearly "soft" so they don't collapse the inter-anchor geometry.
            "--reference-sigma-mm",
            "150",
        ]
        z_prior_mm = args.floating_reference_z_prior_mm
        if z_prior_mm is None and args.floating_reference_session:
            z_prior_mm = 820.0
        if z_prior_mm is not None and args.floating_reference_session:
            solve_cmd.extend(["--floating-reference-z-prior-mm", str(z_prior_mm)])
        for ref in args.floating_reference_session:
            solve_cmd.extend(["--floating-reference-session", ref])
        run(solve_cmd)

    manifest = {
        "pairs_csv": str(Path(args.pairs_csv).resolve()),
        "out_dir": str(out_dir.resolve()),
        "fused_dir": str(fused_dir.resolve()),
        "fused_csv": str(fused_csv.resolve()),
        "matrix_json_v3_lite": str(matrix_json.resolve()),
        "layout_output_v3_lite": str(layout_output.resolve()) if layout_output else None,
        "floating_reference_sessions": args.floating_reference_session,
        "floating_reference_z_prior_mm": (
            float(args.floating_reference_z_prior_mm)
            if args.floating_reference_z_prior_mm is not None
            else (820.0 if args.floating_reference_session else None)
        ),
        "pair_count": len(matrix_payload.get("distances", {})),
        "notes": [
            "V3-lite: uses existing v2 fusion with tighter floors and a more aggressive solve.",
            "Not the full V3 from docs/AutoPos_V1_to_V5_Implementation_Guide.md.",
        ],
    }
    (out_dir / "v3_lite_manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"[ok] wrote {out_dir / 'v3_lite_manifest.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
