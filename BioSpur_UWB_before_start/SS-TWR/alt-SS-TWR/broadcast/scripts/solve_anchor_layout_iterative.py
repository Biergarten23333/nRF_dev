#!/usr/bin/env python3
import argparse
import json
import math
import subprocess
from pathlib import Path


ANCHORS = ("A", "B", "C", "D", "E", "F", "G", "H")


def load_anchor_map(path: Path) -> dict[str, list[float]]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    anchors_raw = raw["anchors"]
    if isinstance(anchors_raw, dict):
        return anchors_raw
    return {
        entry["label"]: [
            float(entry["x_mm"]) / 1000.0,
            float(entry["y_mm"]) / 1000.0,
            float(entry["z_mm"]) / 1000.0,
        ]
        for entry in anchors_raw
    }


def max_anchor_delta_mm(old: dict[str, list[float]], new: dict[str, list[float]]) -> float:
    max_delta = 0.0
    for anchor in ANCHORS:
        dx = new[anchor][0] - old[anchor][0]
        dy = new[anchor][1] - old[anchor][1]
        dz = new[anchor][2] - old[anchor][2]
        delta_mm = math.sqrt(dx * dx + dy * dy + dz * dz) * 1000.0
        max_delta = max(max_delta, delta_mm)
    return max_delta


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Iteratively solve anchor layout until the solution converges."
    )
    parser.add_argument("--input", default="data/inter_anchor_matrix_ah.json")
    parser.add_argument("--output", default="data/anchor_layout_ah_calibrated.json")
    parser.add_argument("--initial-layout", default="data/anchor_layout_ah_calibrated.json")
    parser.add_argument("--max-iters", type=int, default=5)
    parser.add_argument("--converge-mm", type=float, default=2.0)
    parser.add_argument("--distance-sigma-mm", type=float, default=90.0)
    parser.add_argument("--distance-sigma-same-plane-mm", type=float, default=120.0)
    parser.add_argument("--distance-sigma-cross-plane-mm", type=float, default=180.0)
    parser.add_argument("--distance-sigma-vertical-pair-mm", type=float, default=120.0)
    parser.add_argument("--height-prior-m", type=float, default=1.4)
    parser.add_argument("--height-sigma-mm", type=float, default=300.0)
    # Vertical-pair XY alignment is often not physically true (upper/lower XY
    # projections may be offset). Keep it disabled by default.
    parser.add_argument("--vertical-sigma-mm", type=float, default=0.0)
    parser.add_argument("--lower-plane-sigma-mm", type=float, default=80.0)
    parser.add_argument("--upper-plane-sigma-mm", type=float, default=160.0)
    parser.add_argument("--upper-level-sigma-mm", type=float, default=35.0)
    parser.add_argument("--pair-height-sigma-mm", type=float, default=45.0)
    parser.add_argument("--reference-sigma-mm", type=float, default=60.0)
    parser.add_argument("--floating-reference-z-prior-mm", type=float, default=None)
    parser.add_argument("--floating-reference-z-sigma-mm", type=float, default=80.0)
    parser.add_argument("--prior-lower-xy-sigma-mm", type=float, default=1200.0)
    parser.add_argument("--prior-lower-z-sigma-mm", type=float, default=500.0)
    parser.add_argument("--prior-upper-xy-sigma-mm", type=float, default=800.0)
    parser.add_argument("--prior-upper-z-sigma-mm", type=float, default=350.0)
    parser.add_argument("--lower-parallelogram-sigma-mm", type=float, default=400.0)
    parser.add_argument("--upper-parallelogram-sigma-mm", type=float, default=400.0)
    parser.add_argument("--cuboid-translation-xy-sigma-mm", type=float, default=400.0)
    parser.add_argument("--cuboid-translation-z-sigma-mm", type=float, default=200.0)
    parser.add_argument("--rect-diagonal-sigma-mm", type=float, default=400.0)
    parser.add_argument("--space-diagonal-sigma-mm", type=float, default=600.0)
    parser.add_argument("--lower-ortho-sigma", type=float, default=0.0)
    parser.add_argument("--upper-ortho-sigma", type=float, default=0.0)
    parser.add_argument("--multi-start", type=int, default=8)
    parser.add_argument("--start-jitter-mm", type=float, default=450.0)
    parser.add_argument("--adaptive-edge-reweight-rounds", type=int, default=2)
    parser.add_argument(
        "--cir-pair-weights",
        default=None,
        help="Optional CIR-derived pair-weight JSON passed through to solve_anchor_layout.py.",
    )
    parser.add_argument(
        "--reference-session",
        action="append",
        default=[],
        help="Ground-truth session directory; may be passed multiple times.",
    )
    parser.add_argument(
        "--floating-reference-session",
        action="append",
        default=[],
        help="Static reference-tag session directory with ranges.csv only; may be passed multiple times.",
    )
    args = parser.parse_args()

    output = Path(args.output)
    previous_layout = Path(args.initial_layout)
    if not previous_layout.exists():
        previous_layout = None

    history = []
    for iteration in range(1, args.max_iters + 1):
        cmd = [
            "python3",
            "scripts/solve_anchor_layout.py",
            "--input",
            args.input,
            "--output",
            str(output),
            "--distance-sigma-mm",
            str(args.distance_sigma_mm),
            "--distance-sigma-same-plane-mm",
            str(args.distance_sigma_same_plane_mm),
            "--distance-sigma-cross-plane-mm",
            str(args.distance_sigma_cross_plane_mm),
            "--distance-sigma-vertical-pair-mm",
            str(args.distance_sigma_vertical_pair_mm),
            "--height-prior-m",
            str(args.height_prior_m),
            "--height-sigma-mm",
            str(args.height_sigma_mm),
            "--vertical-sigma-mm",
            str(args.vertical_sigma_mm),
            "--lower-plane-sigma-mm",
            str(args.lower_plane_sigma_mm),
            "--upper-plane-sigma-mm",
            str(args.upper_plane_sigma_mm),
            "--upper-level-sigma-mm",
            str(args.upper_level_sigma_mm),
            "--pair-height-sigma-mm",
            str(args.pair_height_sigma_mm),
            "--reference-sigma-mm",
            str(args.reference_sigma_mm),
            "--floating-reference-z-sigma-mm",
            str(args.floating_reference_z_sigma_mm),
            "--prior-lower-xy-sigma-mm",
            str(args.prior_lower_xy_sigma_mm),
            "--prior-lower-z-sigma-mm",
            str(args.prior_lower_z_sigma_mm),
            "--prior-upper-xy-sigma-mm",
            str(args.prior_upper_xy_sigma_mm),
            "--prior-upper-z-sigma-mm",
            str(args.prior_upper_z_sigma_mm),
            "--lower-parallelogram-sigma-mm",
            str(args.lower_parallelogram_sigma_mm),
            "--upper-parallelogram-sigma-mm",
            str(args.upper_parallelogram_sigma_mm),
            "--cuboid-translation-xy-sigma-mm",
            str(args.cuboid_translation_xy_sigma_mm),
            "--cuboid-translation-z-sigma-mm",
            str(args.cuboid_translation_z_sigma_mm),
            "--rect-diagonal-sigma-mm",
            str(args.rect_diagonal_sigma_mm),
            "--space-diagonal-sigma-mm",
            str(args.space_diagonal_sigma_mm),
            "--lower-ortho-sigma",
            str(args.lower_ortho_sigma),
            "--upper-ortho-sigma",
            str(args.upper_ortho_sigma),
            "--multi-start",
            str(args.multi_start),
            "--start-jitter-mm",
            str(args.start_jitter_mm),
            "--adaptive-edge-reweight-rounds",
            str(args.adaptive_edge_reweight_rounds),
        ]
        if args.cir_pair_weights:
            cmd.extend(["--cir-pair-weights", args.cir_pair_weights])
        if args.floating_reference_z_prior_mm is not None:
            cmd.extend(
                ["--floating-reference-z-prior-mm", str(args.floating_reference_z_prior_mm)]
            )
        if previous_layout is not None:
            cmd.extend(["--initial-layout", str(previous_layout)])
        for session_dir in args.reference_session:
            cmd.extend(["--reference-session", session_dir])
        for session_dir in args.floating_reference_session:
            cmd.extend(["--floating-reference-session", session_dir])

        subprocess.run(cmd, check=True)
        current = load_anchor_map(output)

        if previous_layout is not None:
            old = load_anchor_map(previous_layout)
            delta_mm = max_anchor_delta_mm(old, current)
        else:
            delta_mm = float("inf")

        history.append({"iteration": iteration, "max_anchor_delta_mm": delta_mm})
        print(
            f"iter={iteration} max_anchor_delta_mm="
            f"{delta_mm if math.isfinite(delta_mm) else -1:.3f}"
        )

        if math.isfinite(delta_mm) and delta_mm <= args.converge_mm:
            break

        previous_layout = output

    history_path = output.with_name(output.stem + "_iter_history.json")
    history_path.write_text(json.dumps(history, indent=2) + "\n", encoding="utf-8")
    print(f"history: {history_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
