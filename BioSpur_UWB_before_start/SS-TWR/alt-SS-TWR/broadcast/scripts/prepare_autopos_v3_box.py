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


def normalize_floating_reference_sessions(
    sessions: list[str],
    out_dir: Path,
    min_cm_lines: int,
) -> list[str]:
    normalized: list[str] = []
    auto_root = out_dir / "_auto_floating_refs"
    for idx, session in enumerate(sessions):
        d = Path(session)
        ranges_csv = d / "ranges.csv"
        if ranges_csv.exists():
            normalized.append(str(d))
            continue

        run_log = d / "run.log"
        if run_log.exists():
            target = auto_root / f"session_{idx+1}_{d.name}"
            target.mkdir(parents=True, exist_ok=True)
            run(
                [
                    "python3",
                    "scripts/autopos_extract_ranges_from_tag_cm_runlog.py",
                    "--run-log",
                    str(run_log),
                    "--out-dir",
                    str(target),
                    "--min-cm-lines",
                    str(min_cm_lines),
                ]
            )
            normalized.append(str(target))
            continue

        raise FileNotFoundError(
            f"Floating reference session must contain ranges.csv or run.log: {d}"
        )
    return normalized


def main() -> int:
    ap = argparse.ArgumentParser(
        description=(
            "Prepare and run AutoPos V3-box: V3 fusion + SDP/MDS seed + bias solve + "
            "Tukey IRLS under paired upper/lower column priors."
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
    ap.add_argument("--floating-reference-min-cm-lines", type=int, default=80)
    ap.add_argument("--floating-reference-z-prior-mm", type=float, default=None)
    ap.add_argument("--floating-reference-z-sigma-mm", type=float, default=80.0)
    ap.add_argument("--height-prior-m", type=float, default=1.4)
    ap.add_argument("--height-sigma-mm", type=float, default=300.0)
    ap.add_argument("--lower-plane-sigma-mm", type=float, default=80.0)
    ap.add_argument("--upper-level-sigma-mm", type=float, default=35.0)
    ap.add_argument("--pair-height-sigma-mm", type=float, default=45.0)
    ap.add_argument("--vertical-xy-sigma-mm", type=float, default=0.0)
    ap.add_argument(
        "--cir-pair-weights",
        default=None,
        help="Optional CIR-derived pair-weight JSON passed through to V3-full layout solver.",
    )
    ap.add_argument("--verbose", type=int, default=1)
    args = ap.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    normalized_ref_sessions = normalize_floating_reference_sessions(
        args.floating_reference_session,
        out_dir,
        int(args.floating_reference_min_cm_lines),
    )

    fused_dir = out_dir / "v3_box_fused"
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
    out_layout = out_dir / "anchor_layout_v3_box.json"
    solve_cmd = [
        "python3",
        "scripts/solve_anchor_layout_v3_full.py",
        "--input",
        str(matrix_json),
        "--output",
        str(out_layout),
        "--geometry-mode",
        "box",
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
        "--height-prior-m",
        str(args.height_prior_m),
        "--height-sigma-mm",
        str(args.height_sigma_mm),
        "--lower-plane-sigma-mm",
        str(args.lower_plane_sigma_mm),
        "--upper-level-sigma-mm",
        str(args.upper_level_sigma_mm),
        "--pair-height-sigma-mm",
        str(args.pair_height_sigma_mm),
        "--vertical-xy-sigma-mm",
        str(args.vertical_xy_sigma_mm),
        "--verbose",
        str(args.verbose),
    ]
    if args.bias_mu is not None:
        solve_cmd.extend(["--bias-mu", str(args.bias_mu)])
    if args.cir_pair_weights:
        solve_cmd.extend(["--cir-pair-weights", args.cir_pair_weights])
    if args.floating_reference_z_prior_mm is not None:
        solve_cmd.extend(["--floating-reference-z-prior-mm", str(args.floating_reference_z_prior_mm)])
    for s in normalized_ref_sessions:
        solve_cmd.extend(["--floating-reference-session", s])
    run(solve_cmd)

    manifest = {
        "solver": "V3-box",
        "pairs_csv": str(Path(args.pairs_csv).resolve()),
        "out_dir": str(out_dir.resolve()),
        "fused_dir": str(fused_dir.resolve()),
        "matrix_json": str(matrix_json.resolve()),
        "layout_json": str(out_layout.resolve()),
        "cir_pair_weights": str(Path(args.cir_pair_weights).resolve()) if args.cir_pair_weights else None,
        "geometry_mode": "box",
        "bias_sigma_mm": float(args.bias_sigma_mm),
        "bias_mu": float(args.bias_mu) if args.bias_mu is not None else None,
        "sigma_dist_mm": float(args.sigma_dist_mm),
        "sigma_ref_mm": float(args.sigma_ref_mm),
        "height_prior_m": float(args.height_prior_m),
        "height_sigma_mm": float(args.height_sigma_mm),
        "lower_plane_sigma_mm": float(args.lower_plane_sigma_mm),
        "upper_level_sigma_mm": float(args.upper_level_sigma_mm),
        "pair_height_sigma_mm": float(args.pair_height_sigma_mm),
        "vertical_xy_sigma_mm": float(args.vertical_xy_sigma_mm),
        "floating_reference_sessions": normalized_ref_sessions,
        "floating_reference_min_cm_lines": int(args.floating_reference_min_cm_lines),
    }
    (out_dir / "v3_box_manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"[ok] wrote {out_dir / 'v3_box_manifest.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
