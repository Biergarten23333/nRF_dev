#!/usr/bin/env python3
import argparse
import json
import os
import re
import shutil
import subprocess
from datetime import datetime
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
ANCHORS = ("A", "B", "C", "D", "E", "F", "G", "H")
ANCHOR_SERIALS = {
    "A": "760186071",
    "B": "760185876",
    "C": "760185878",
    "D": "760186081",
    "E": "760185904",
    "F": "760186124",
    "G": "760185889",
    "H": "760186121",
}


def run(cmd, *, cwd=REPO_ROOT):
    print("$", " ".join(str(part) for part in cmd), flush=True)
    env = os.environ.copy()
    extra_pythonpath = "/usr/lib/python3/dist-packages"
    current_pythonpath = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = (
        f"{extra_pythonpath}:{current_pythonpath}"
        if current_pythonpath
        else extra_pythonpath
    )
    subprocess.run([str(part) for part in cmd], cwd=cwd, env=env, check=True)


def build_shared_args() -> list[str]:
    return [
        "-DAPP_TAG_ID=1",
        "-DAPP_TAG_RANGE_SOFT_RESIDUAL_MM=140",
        "-DAPP_TAG_RANGE_HARD_RESIDUAL_MM=260",
    ]


def build_capture_args() -> list[str]:
    return [
        "-DAPP_TAG_BLE_ENABLE=0",
        "-DAPP_TAG_MCUBOOT_ENABLE=0",
        "-DAPP_TAG_USB_DIAG_TRACE=0",
        "-DAPP_TAG_TDMA_ENABLE=0",
        "-DAPP_TAG_FIXED_MODE=0",
        "-DAPP_TAG_FULL_SWEEP_INTERVAL=1",
        "-DAPP_TAG_TRACK_ANCHOR_COUNT=8",
        "-DAPP_TAG_VERBOSE_RANGING=1",
        "-DAPP_TAG_VERBOSE_MEASUREMENTS=0",
        "-DAPP_TAG_LOC_MIN_QUALITY_PERCENT=20",
        "-DAPP_TAG_RANGE_CONTINUITY_ENABLE=0",
    ]


def build_monitor_args(monitor_anchor_count: int) -> list[str]:
    if monitor_anchor_count < 4 or monitor_anchor_count > 8:
        raise ValueError(f"monitor_anchor_count must be in [4,8], got {monitor_anchor_count}")

    base = [
        "-DAPP_TAG_TDMA_ENABLE=1",
        "-DAPP_TAG_TDMA_SLOT_INDEX=1",
        "-DAPP_TAG_TDMA_SLOT_COUNT=6",
        "-DAPP_TAG_TDMA_SLOT_PERIOD_MS=10",
        "-DAPP_TAG_TDMA_SLOT_ACTIVE_MS=9",
        "-DAPP_TAG_EKF_ENABLE=0",
        "-DAPP_TAG_LOC_MIN_QUALITY_PERCENT=20",
        "-DAPP_TAG_RANGE_CONTINUITY_ENABLE=0",
        "-DAPP_TAG_VERBOSE_RANGING=0",
        "-DAPP_TAG_VERBOSE_MEASUREMENTS=0",
    ]

    if monitor_anchor_count == 4:
        return [
            *base,
            "-DAPP_TAG_FIXED_MODE=1",
            "-DAPP_TAG_FIXED_ANCHOR_COUNT=4",
            "-DAPP_TAG_FIXED_ANCHOR_0_ID=1",
            "-DAPP_TAG_FIXED_ANCHOR_1_ID=2",
            "-DAPP_TAG_FIXED_ANCHOR_2_ID=5",
            "-DAPP_TAG_FIXED_ANCHOR_3_ID=6",
        ]

    return [
        *base,
        "-DAPP_TAG_FIXED_MODE=0",
        "-DAPP_TAG_FULL_SWEEP_INTERVAL=8",
        f"-DAPP_TAG_TRACK_ANCHOR_COUNT={monitor_anchor_count}",
    ]


def ensure_session(args) -> Path:
    if args.session_dir:
        session_dir = (REPO_ROOT / args.session_dir).resolve()
        if not (session_dir / "ranges.csv").exists():
            raise FileNotFoundError(f"Missing ranges.csv in {session_dir}")
        return session_dir

    if not args.skip_build:
        build_dir = REPO_ROOT / args.build_dir
        build_tag(build_dir, args.capture_mode, args.monitor_anchor_count)
        if not args.skip_flash:
            flash_tag(args.snr, build_dir / "zephyr" / "zephyr.hex")

    session_name = args.session_name or f"ref115_autopos_{datetime.now():%Y%m%d_%H%M%S}"
    run(
        [
            "python3",
            "scripts/capture_tag_session.py",
            args.snr,
            args.port,
            "--duration",
            str(args.duration),
            "--settle",
            str(args.settle),
            "--skip-sweeps",
            str(args.skip_sweeps),
            "--session-name",
            session_name,
        ]
    )
    return REPO_ROOT / "logs" / "tag_sessions" / session_name


def solve_layout(session_dir: Path, output_path: Path, args):
    run(
        [
            "python3",
            "scripts/solve_anchor_layout_iterative.py",
            "--input",
            args.input,
            "--output",
            str(output_path),
            "--initial-layout",
            args.initial_layout,
            "--floating-reference-session",
            str(session_dir),
            "--floating-reference-z-prior-mm",
            str(args.ref_z_prior_mm),
            "--floating-reference-z-sigma-mm",
            str(args.ref_z_sigma_mm),
            "--distance-sigma-mm",
            str(args.distance_sigma_mm),
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
            "--max-iters",
            str(args.max_iters),
            "--converge-mm",
            str(args.converge_mm),
        ]
    )


def load_solution(solution_path: Path) -> dict:
    return json.loads(solution_path.read_text(encoding="utf-8"))


def write_runtime_json(solution: dict, runtime_path: Path):
    anchors = []
    for idx, label in enumerate(ANCHORS):
        xyz = solution["anchors"][label]
        anchors.append(
            {
                "id": idx,
                "label": label,
                "serial": ANCHOR_SERIALS[label],
                "x_mm": int(round(float(xyz[0]) * 1000.0)),
                "y_mm": int(round(float(xyz[1]) * 1000.0)),
                "z_mm": int(round(float(xyz[2]) * 1000.0)),
            }
        )

    runtime = {
        "units": "mm",
        "reference_frame": {
            "origin_anchor": "A",
            "x_axis_anchor": "B",
            "xy_plane_anchor": "D",
        },
        "anchors": anchors,
        "source": {
            "matrix": "data/inter_anchor_matrix_ah.json",
            "solution": str(Path("data") / "anchor_layout_ah_calibrated.json"),
            "solver": "scripts/recalibrate_anchor_layout_with_ref115.py",
        },
        "notes": [
            "This is the current runtime anchor layout baseline.",
            "Tag 760186127 is not part of the anchor layout.",
            "ABCD and EFGH are modeled as near-planar clusters, not perfectly coplanar planes.",
            "This baseline was tightened with a floating static reference-tag session from Tag 115 and a soft Z prior near 700 mm.",
            "If anchors are physically moved, regenerate this file from a fresh inter-anchor matrix.",
        ],
    }
    runtime_path.write_text(json.dumps(runtime, indent=2) + "\n", encoding="utf-8")


def write_anchor_layout_c(solution: dict, source_path: Path):
    entries = []
    for idx, label in enumerate(ANCHORS):
        xyz = solution["anchors"][label]
        entries.append(
            f"    {{{idx}U, '{label}', {int(round(float(xyz[0]) * 1000.0))}, "
            f"{int(round(float(xyz[1]) * 1000.0))}, {int(round(float(xyz[2]) * 1000.0))}}},"
        )
    replacement = (
        "static const struct uwb_anchor_pose_mm uwb_anchor_layout[UWB_MAX_ANCHORS] = {\n"
        + "\n".join(entries)
        + "\n};"
    )
    text = source_path.read_text(encoding="utf-8")
    text = re.sub(
        r"static const struct uwb_anchor_pose_mm uwb_anchor_layout\[UWB_MAX_ANCHORS\] = \{\n.*?\n\};",
        replacement,
        text,
        flags=re.S,
    )
    source_path.write_text(text, encoding="utf-8")


def build_tag(build_dir: Path, mode: str, monitor_anchor_count: int):
    if build_dir.exists():
        shutil.rmtree(build_dir)

    if mode == "calibration":
        source_dir = "apps/tag_usb"
        mode_args = build_capture_args()
    elif mode == "monitor":
        source_dir = "apps/tag"
        mode_args = build_monitor_args(monitor_anchor_count)
    else:
        raise ValueError(f"Unsupported build mode: {mode}")

    run(
        [
            "west",
            "build",
            "-b",
            "decawave_dwm1001_dev/nrf52832",
            "-s",
            source_dir,
            "-d",
            str(build_dir),
            "--no-sysbuild",
            "--pristine=always",
            "--",
            *build_shared_args(),
            *mode_args,
        ],
    )
    run(
        [
            "python3",
            "scripts/write_build_source.py",
            "--build-dir",
            str(build_dir),
            "--source",
            "scripts/recalibrate_anchor_layout_with_ref115.py",
            "--command",
            f"python3 scripts/recalibrate_anchor_layout_with_ref115.py --capture-mode {mode} --monitor-anchor-count {monitor_anchor_count}",
            "--note",
            f"mode={mode}",
            "--note",
            f"build_source={source_dir}",
        ]
    )


def flash_tag(snr: str, hex_path: Path):
    run(["scripts/reset_then_flash.sh", snr, str(hex_path)])


def main() -> int:
    parser = argparse.ArgumentParser(
        description="One-shot host-side anchor-layout recalibration with Ref Tag 115."
    )
    parser.add_argument("--snr", default="760186115")
    parser.add_argument(
        "--port",
        default="/dev/serial/by-id/usb-SEGGER_J-Link_000760186115-if00",
    )
    parser.add_argument(
        "--session-dir",
        default=None,
        help="Use an existing tag session directory instead of capturing a new one.",
    )
    parser.add_argument("--session-name", default=None)
    parser.add_argument("--duration", type=float, default=20.0)
    parser.add_argument("--settle", type=float, default=1.0)
    parser.add_argument("--skip-sweeps", type=int, default=2)
    parser.add_argument("--input", default="data/inter_anchor_matrix_ah.json")
    parser.add_argument("--initial-layout", default="data/anchor_layout_ah_calibrated.json")
    parser.add_argument("--ref-z-prior-mm", type=float, default=700.0)
    parser.add_argument("--ref-z-sigma-mm", type=float, default=80.0)
    parser.add_argument("--distance-sigma-mm", type=float, default=90.0)
    parser.add_argument("--height-prior-m", type=float, default=1.4)
    parser.add_argument("--height-sigma-mm", type=float, default=300.0)
    parser.add_argument("--vertical-sigma-mm", type=float, default=180.0)
    parser.add_argument("--lower-plane-sigma-mm", type=float, default=40.0)
    parser.add_argument("--upper-plane-sigma-mm", type=float, default=0.0)
    parser.add_argument("--upper-level-sigma-mm", type=float, default=20.0)
    parser.add_argument("--pair-height-sigma-mm", type=float, default=25.0)
    parser.add_argument("--reference-sigma-mm", type=float, default=60.0)
    parser.add_argument("--max-iters", type=int, default=5)
    parser.add_argument("--converge-mm", type=float, default=1.0)
    parser.add_argument("--build-dir", default="build-ref115-monitor-4")
    parser.add_argument(
        "--capture-mode",
        choices=("calibration", "monitor"),
        default="calibration",
        help="Build/flash mode used before capturing the Ref115 session.",
    )
    parser.add_argument(
        "--post-mode",
        choices=("calibration", "monitor", "none"),
        default="monitor",
        help="Build/flash mode applied after solving the new anchor layout.",
    )
    parser.add_argument(
        "--monitor-anchor-count",
        type=int,
        default=4,
        help="Monitor-mode anchor count. 4 uses the fixed B,C,F,G static build; 5-8 use adaptive monitoring.",
    )
    parser.add_argument("--skip-build", action="store_true")
    parser.add_argument("--skip-flash", action="store_true")
    args = parser.parse_args()

    session_dir = ensure_session(args)

    tmp_solution = REPO_ROOT / "data" / "anchor_layout_ah_calibrated.ref115_tmp.json"
    solve_layout(session_dir, tmp_solution, args)

    solution = load_solution(tmp_solution)
    calibrated_path = REPO_ROOT / "data" / "anchor_layout_ah_calibrated.json"
    runtime_path = REPO_ROOT / "data" / "anchor_layout_ah_runtime.json"
    source_path = REPO_ROOT / "src" / "uwb_anchor_layout.c"

    shutil.copyfile(tmp_solution, calibrated_path)
    write_runtime_json(solution, runtime_path)
    write_anchor_layout_c(solution, source_path)

    if not args.skip_build and args.post_mode != "none":
        build_dir = REPO_ROOT / args.build_dir
        build_tag(build_dir, args.post_mode, args.monitor_anchor_count)
        if not args.skip_flash:
            flash_tag(args.snr, build_dir / "zephyr" / "zephyr.hex")

    print("")
    print(f"session_dir: {session_dir}")
    print(f"calibrated_json: {calibrated_path}")
    print(f"runtime_json: {runtime_path}")
    print(f"source_c: {source_path}")
    if not args.skip_build:
        print(f"build_dir: {REPO_ROOT / args.build_dir}")
        print(f"capture_mode: {args.capture_mode}")
        print(f"post_mode: {args.post_mode}")
        print(f"monitor_anchor_count: {args.monitor_anchor_count}")
        print(f"flash_snr: {args.snr if not args.skip_flash else 'skipped'}")
    else:
        print("build_dir: skipped")
        print("flash_snr: skipped")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
