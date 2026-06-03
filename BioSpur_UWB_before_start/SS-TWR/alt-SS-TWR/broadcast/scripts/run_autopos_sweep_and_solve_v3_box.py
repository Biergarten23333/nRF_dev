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
            "Capture a fresh AutoPos sweep, extract pairs_all.csv, then solve V3-box "
            "in one end-to-end run."
        )
    )
    ap.add_argument("--port", required=True, help="BLE master CDC port")
    ap.add_argument("--order", default="ABCDEFGH", help="Anchor order")
    ap.add_argument("--sw-sets", type=int, default=100, help="Sweep set count")
    ap.add_argument("--timeout-s", type=int, default=3600, help="Sweep timeout seconds")
    ap.add_argument("--warmup-min-quality", type=int, default=0, help="Sweep warmup quality threshold")
    ap.add_argument("--quiet-tag-name", default="-", help="Passed through to sweep script; default disables heavy Tag quarantine")
    ap.add_argument("--capture-tag115", action="store_true", help="Capture a fresh Tag115 CM session after sweep and use it as floating reference")
    ap.add_argument("--tag-name", default="BSF66F", help="Tag115 BLE target name for fresh CM capture")
    ap.add_argument("--cm-lines", type=int, default=80, help="Required CM notify lines for fresh Tag115 capture")
    ap.add_argument("--out-dir", required=True, help="Top-level output directory")
    ap.add_argument("--skip-sweep", action="store_true", help="Reuse existing summary.json under out-dir/sweep")
    ap.add_argument("--no-bootstrap-autopos-reset", action="store_true", help="Passed through to sweep script")
    ap.add_argument("--floating-reference-session", action="append", default=[], help="Optional Tag115 floating reference dir(s)")
    ap.add_argument("--floating-reference-min-cm-lines", type=int, default=80)
    ap.add_argument("--floating-reference-z-prior-mm", type=float, default=None)
    ap.add_argument("--floating-reference-z-sigma-mm", type=float, default=80.0)
    ap.add_argument("--bias-sigma-mm", type=float, default=200.0)
    ap.add_argument("--bias-mu", type=float, default=None)
    ap.add_argument("--sigma-dist-mm", type=float, default=80.0)
    ap.add_argument("--sigma-ref-mm", type=float, default=120.0)
    ap.add_argument("--max-iters", type=int, default=15)
    ap.add_argument("--tukey-c-mult", type=float, default=4.685)
    ap.add_argument("--tukey-c-min-mm", type=float, default=120.0)
    ap.add_argument("--tukey-w-min", type=float, default=0.05)
    ap.add_argument("--min-dir-samples", type=int, default=20)
    ap.add_argument("--min-sigma-mm", type=float, default=3.0)
    ap.add_argument("--height-prior-m", type=float, default=1.4)
    ap.add_argument("--height-sigma-mm", type=float, default=300.0)
    ap.add_argument("--lower-plane-sigma-mm", type=float, default=80.0)
    ap.add_argument("--upper-level-sigma-mm", type=float, default=35.0)
    ap.add_argument("--pair-height-sigma-mm", type=float, default=45.0)
    ap.add_argument("--vertical-xy-sigma-mm", type=float, default=0.0)
    ap.add_argument(
        "--cir-pair-weights",
        default=None,
        help="Optional CIR-derived pair-weight JSON passed through to V3-box solver.",
    )
    ap.add_argument("--verbose", type=int, default=1)
    args = ap.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    sweep_dir = out_dir / "sweep"
    solve_dir = out_dir / "solve_v3_box"
    solve_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    normalized_ref_sessions = normalize_floating_reference_sessions(
        args.floating_reference_session,
        out_dir,
        int(args.floating_reference_min_cm_lines),
    )

    if not args.skip_sweep:
        sweep_dir.mkdir(parents=True, exist_ok=True)
        sweep_cmd = [
            "python3",
            "scripts/run_autopos_sweep_loop.py",
            "--port",
            args.port,
            "--order",
            args.order,
            "--sw-sets",
            str(args.sw_sets),
            "--timeout-s",
            str(args.timeout_s),
            "--warmup-min-quality",
            str(args.warmup_min_quality),
            "--quiet-tag-name",
            args.quiet_tag_name,
            "--out-dir",
            str(sweep_dir),
        ]
        if args.no_bootstrap_autopos_reset:
            sweep_cmd.append("--no-bootstrap-autopos-reset")
        run(sweep_cmd)

    if args.capture_tag115:
        tag_dir = out_dir / "tag115_cm"
        tag_dir.mkdir(parents=True, exist_ok=True)
        tag_cmd = [
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
        run(tag_cmd)
        normalized_ref_sessions = normalize_floating_reference_sessions(
            [str(tag_dir)],
            out_dir,
            int(args.cm_lines),
        )

    summary_json = sweep_dir / "summary.json"
    if not summary_json.exists():
        raise SystemExit(f"[error] missing {summary_json}")

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

    solve_cmd = [
        "python3",
        "scripts/prepare_autopos_v3_box.py",
        "--pairs-csv",
        str(pairs_csv),
        "--out-dir",
        str(solve_dir),
        "--min-dir-samples",
        str(args.min_dir_samples),
        "--min-sigma-mm",
        str(args.min_sigma_mm),
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
        "pipeline": "sweep -> pairs_all.csv -> V3-box",
        "stamp": stamp,
        "port": args.port,
        "order": list(args.order),
        "sw_sets": args.sw_sets,
        "timeout_s": args.timeout_s,
        "sweep_dir": str(sweep_dir.resolve()),
        "summary_json": str(summary_json.resolve()),
        "pairs_csv": str(pairs_csv.resolve()),
        "solve_dir": str(solve_dir.resolve()),
        "layout_json": str((solve_dir / "anchor_layout_v3_box.json").resolve()),
        "manifest_json": str((solve_dir / "v3_box_manifest.json").resolve()),
        "cir_pair_weights": str(Path(args.cir_pair_weights).resolve()) if args.cir_pair_weights else None,
        "floating_reference_sessions": normalized_ref_sessions,
        "floating_reference_min_cm_lines": int(args.floating_reference_min_cm_lines),
    }
    pipeline_manifest = out_dir / "pipeline_manifest.json"
    pipeline_manifest.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"[ok] wrote {pipeline_manifest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
