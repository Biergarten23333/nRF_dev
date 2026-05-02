#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def run(cmd: list[str]) -> None:
    print("$", " ".join(cmd), flush=True)
    subprocess.run(cmd, cwd=REPO_ROOT, check=True)


def main() -> int:
    ap = argparse.ArgumentParser(
        description=(
            "Prepare and run AutoPos V3_full (repo implementation): "
            "V3 fusion (MAD+MVUE) + SDP/MDS seed + antenna-delay bias + Tukey IRLS."
        )
    )
    ap.add_argument("--pairs-csv", required=True, help="pairs_all.csv")
    ap.add_argument("--out-dir", required=True, help="output directory")
    ap.add_argument("--min-dir-samples", type=int, default=20)
    ap.add_argument("--min-sigma-mm", type=float, default=3.0)
    ap.add_argument("--bias-sigma-mm", type=float, default=200.0)
    ap.add_argument("--bias-mu", type=float, default=None)
    ap.add_argument("--sigma-dist-mm", type=float, default=80.0)
    ap.add_argument("--sigma-ref-mm", type=float, default=120.0)
    ap.add_argument("--max-iters", type=int, default=15)
    ap.add_argument("--tukey-c-mult", type=float, default=4.685)
    ap.add_argument("--tukey-c-min-mm", type=float, default=120.0)
    ap.add_argument("--tukey-w-min", type=float, default=0.05)
    ap.add_argument("--floating-reference-session", action="append", default=[])
    ap.add_argument("--floating-reference-z-prior-mm", type=float, default=None)
    ap.add_argument("--floating-reference-z-sigma-mm", type=float, default=80.0)
    ap.add_argument("--verbose", type=int, default=1)
    args = ap.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    fused_dir = out_dir / "v3_full_fused"
    fused_dir.mkdir(parents=True, exist_ok=True)

    run(
        [
            "python3",
            "scripts/fuse_bidirectional_matrix_v3.py",
            "--pairs-csv",
            args.pairs_csv,
            "--out-dir",
            str(fused_dir),
            "--min-dir-samples",
            str(args.min_dir_samples),
            "--min-sigma-mm",
            str(args.min_sigma_mm),
        ]
    )

    matrix_json = fused_dir / "inter_anchor_matrix_v3fused.json"
    out_layout = out_dir / "anchor_layout_v3_full.json"
    solve_cmd = [
        "python3",
        "scripts/solve_anchor_layout_v3_full.py",
        "--input",
        str(matrix_json),
        "--output",
        str(out_layout),
        "--bias-sigma-mm",
        str(args.bias_sigma_mm),
        "--sigma-dist-mm",
        str(args.sigma_dist_mm),
        "--sigma-ref-mm",
        str(args.sigma_ref_mm),
        "--max-iters",
        str(args.max_iters),
        "--tukey-c-mult",
        str(args.tukey_c_mult),
        "--tukey-c-min-mm",
        str(args.tukey_c_min_mm),
        "--tukey-w-min",
        str(args.tukey_w_min),
        "--floating-reference-z-sigma-mm",
        str(args.floating_reference_z_sigma_mm),
        "--verbose",
        str(args.verbose),
    ]
    if args.bias_mu is not None:
        solve_cmd.extend(["--bias-mu", str(args.bias_mu)])
    if args.floating_reference_z_prior_mm is not None:
        solve_cmd.extend(["--floating-reference-z-prior-mm", str(args.floating_reference_z_prior_mm)])
    for s in args.floating_reference_session:
        solve_cmd.extend(["--floating-reference-session", s])
    run(solve_cmd)

    manifest = {
        "pairs_csv": str(Path(args.pairs_csv).resolve()),
        "out_dir": str(out_dir.resolve()),
        "fused_dir": str(fused_dir.resolve()),
        "matrix_json": str(matrix_json.resolve()),
        "layout_json": str(out_layout.resolve()),
        "bias_sigma_mm": float(args.bias_sigma_mm),
        "bias_mu": float(args.bias_mu) if args.bias_mu is not None else None,
        "sigma_dist_mm": float(args.sigma_dist_mm),
        "sigma_ref_mm": float(args.sigma_ref_mm),
        "tukey_c_mult": float(args.tukey_c_mult),
        "tukey_c_min_mm": float(args.tukey_c_min_mm),
        "tukey_w_min": float(args.tukey_w_min),
        "floating_reference_sessions": args.floating_reference_session,
        "floating_reference_z_prior_mm": args.floating_reference_z_prior_mm,
        "floating_reference_z_sigma_mm": float(args.floating_reference_z_sigma_mm),
        "notes": [
            "This is the repo implementation of V3_full (not V3-lite).",
            "If cvxpy is installed, sdp_init_v3.py will use SDP; otherwise it falls back to classical MDS.",
        ],
    }
    (out_dir / "v3_full_manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"[ok] wrote {out_dir / 'v3_full_manifest.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
