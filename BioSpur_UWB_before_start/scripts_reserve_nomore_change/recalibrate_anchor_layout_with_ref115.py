#!/usr/bin/env python3
import argparse
import collections
import csv
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
    "A": "760184781",
    "B": "760185876",
    "C": "760185878",
    "D": "760186081",
    "E": "760185904",
    "F": "760186124",
    "G": "760185889",
    "H": "760186121",
}
ANCHOR_FLASH_STATE_PATH = REPO_ROOT / "data" / "anchor_flash_state.json"
CAPTURE_ALLOWED_ANCHOR_FAMILIES = {"tag", "safe", "fast", "worker"}


def load_anchor_map(path: Path) -> dict[str, list[float]]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    anchors_raw = raw["anchors"]
    if isinstance(anchors_raw, dict):
        units = raw.get("units", "m")
        scale = 0.001 if units == "mm" else 1.0
        return {name: [float(v) * scale for v in values] for name, values in anchors_raw.items()}

    units = raw.get("units", "m")
    scale = 0.001 if units == "mm" else 1.0
    return {
        entry["label"]: [
            float(entry["x_mm"]) * scale,
            float(entry["y_mm"]) * scale,
            float(entry["z_mm"]) * scale,
        ]
        for entry in anchors_raw
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


def check_anchor_flash_state_for_capture(args) -> tuple[bool, str]:
    if args.session_dir is not None:
        return True, "session_dir provided; anchor family precheck skipped"

    if args.skip_anchor_family_check:
        return True, "anchor family precheck explicitly skipped"

    if not ANCHOR_FLASH_STATE_PATH.exists():
        return (
            False,
            f"missing {ANCHOR_FLASH_STATE_PATH}; cannot verify anchor responder family before capture",
        )

    try:
        state = json.loads(ANCHOR_FLASH_STATE_PATH.read_text(encoding="utf-8"))
    except Exception as exc:
        return False, f"failed to parse {ANCHOR_FLASH_STATE_PATH}: {exc}"

    anchors = state.get("anchors", {})
    missing = []
    bad = []

    for label in ANCHORS:
        entry = anchors.get(label)
        if not entry:
            missing.append(label)
            continue
        family = str(entry.get("family", "unknown")).lower()
        if family not in CAPTURE_ALLOWED_ANCHOR_FAMILIES:
            bad.append(f"{label}:{family}")

    if not missing and not bad:
        return True, "anchor responder family precheck passed"

    reason_parts = []
    if missing:
        reason_parts.append(f"missing anchors in state: {','.join(missing)}")
    if bad:
        reason_parts.append(
            "anchors on non-responder family: "
            + ",".join(bad)
            + " (expected one of tag/safe/fast/worker)"
        )
    reason = "; ".join(reason_parts)

    if args.allow_anchor_family_mismatch:
        return True, f"anchor family precheck override: {reason}"
    return False, reason


def _parse_anchor_id(row: dict[str, str]) -> int | None:
    for key in ("anchor_id", "anchor"):
        raw = row.get(key, "")
        if raw is None:
            continue
        raw = str(raw).strip()
        if raw == "":
            continue
        try:
            return int(raw)
        except ValueError:
            continue
    return None


def evaluate_session_sufficiency(session_dir: Path, args) -> dict:
    ranges_path = session_dir / "ranges.csv"
    summary_path = session_dir / "summary.json"
    raw_path = session_dir / "raw.log"

    counts = collections.Counter()
    total_rows = 0
    with ranges_path.open(encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            total_rows += 1
            aid = _parse_anchor_id(row)
            if aid is None or aid < 0 or aid >= len(ANCHORS):
                continue
            counts[aid] += 1

    valid_anchor_ids = sorted(
        aid for aid, n in counts.items() if n >= args.min_samples_per_anchor
    )
    lower_valid = [aid for aid in valid_anchor_ids if aid < 4]
    upper_valid = [aid for aid in valid_anchor_ids if aid >= 4]

    total_valid_samples = sum(counts[aid] for aid in valid_anchor_ids)
    dominant_anchor_ratio = 1.0
    if total_valid_samples > 0:
        dominant_anchor_ratio = max(
            counts[aid] / float(total_valid_samples) for aid in valid_anchor_ids
        )

    summary_range_anchor_count = 0
    if summary_path.exists():
        try:
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            summary_range_anchor_count = len(summary.get("range_stats_mm", {}))
        except Exception:
            summary_range_anchor_count = 0

    raw_signatures = {
        "has_motion_ts": False,
        "has_verbose_tagsummary": False,
        "has_runtime_slot": False,
    }
    if raw_path.exists():
        text = raw_path.read_text(encoding="utf-8", errors="replace")
        raw_signatures["has_motion_ts"] = "TS;" in text
        raw_signatures["has_verbose_tagsummary"] = "TagSummary " in text
        raw_signatures["has_runtime_slot"] = (
            ("src=MASTER" in text)
            or ("Tag slot guard" in text)
            or (" slot=" in text and "/" in text)
        )

    failures: list[str] = []
    if len(valid_anchor_ids) < args.min_valid_anchors:
        failures.append("insufficient_valid_anchors")
    if len(lower_valid) < args.min_valid_anchors_per_plane:
        failures.append("insufficient_lower_plane_coverage")
    if len(upper_valid) < args.min_valid_anchors_per_plane:
        failures.append("insufficient_upper_plane_coverage")
    if dominant_anchor_ratio >= args.max_dominant_anchor_ratio:
        failures.append("near_single_anchor_collapse")
    if summary_range_anchor_count < args.min_valid_anchors:
        failures.append("summary_degenerate_range_stats")
    if raw_signatures["has_motion_ts"] or raw_signatures["has_verbose_tagsummary"]:
        failures.append("rawlog_motion_family_signature")
    if raw_signatures["has_runtime_slot"]:
        failures.append("rawlog_runtime_tdma_signature")

    return {
        "accepted": len(failures) == 0,
        "failures": failures,
        "checks": {
            "total_rows": total_rows,
            "valid_anchor_ids": valid_anchor_ids,
            "valid_anchor_labels": [ANCHORS[aid] for aid in valid_anchor_ids],
            "lower_valid_labels": [ANCHORS[aid] for aid in lower_valid],
            "upper_valid_labels": [ANCHORS[aid] for aid in upper_valid],
            "dominant_anchor_ratio": dominant_anchor_ratio,
            "summary_range_anchor_count": summary_range_anchor_count,
            "sample_count_by_anchor": {ANCHORS[aid]: counts.get(aid, 0) for aid in range(len(ANCHORS))},
            "raw_signatures": raw_signatures,
        },
        "limits": {
            "min_valid_anchors": args.min_valid_anchors,
            "min_valid_anchors_per_plane": args.min_valid_anchors_per_plane,
            "max_dominant_anchor_ratio": args.max_dominant_anchor_ratio,
            "min_samples_per_anchor": args.min_samples_per_anchor,
        },
    }


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
            "--prior-lower-xy-sigma-mm",
            str(args.prior_lower_xy_sigma_mm),
            "--prior-lower-z-sigma-mm",
            str(args.prior_lower_z_sigma_mm),
            "--prior-upper-xy-sigma-mm",
            str(args.prior_upper_xy_sigma_mm),
            "--prior-upper-z-sigma-mm",
            str(args.prior_upper_z_sigma_mm),
            "--multi-start",
            str(args.multi_start),
            "--start-jitter-mm",
            str(args.start_jitter_mm),
            "--adaptive-edge-reweight-rounds",
            str(args.adaptive_edge_reweight_rounds),
            "--max-iters",
            str(args.max_iters),
            "--converge-mm",
            str(args.converge_mm),
        ]
    )


def load_solution(solution_path: Path) -> dict:
    return json.loads(solution_path.read_text(encoding="utf-8"))


def evaluate_solution_acceptance(solution: dict, initial_layout_path: Path, args) -> dict:
    solved = solution["anchors"]
    initial = load_anchor_map(initial_layout_path)

    lower = ("A", "B", "C", "D")
    upper = ("E", "F", "G", "H")

    def delta_mm(label: str) -> float:
        sx, sy, sz = solved[label]
        ix, iy, iz = initial[label]
        dx = (float(sx) - float(ix)) * 1000.0
        dy = (float(sy) - float(iy)) * 1000.0
        dz = (float(sz) - float(iz)) * 1000.0
        return (dx * dx + dy * dy + dz * dz) ** 0.5

    def lateral_delta_mm(label: str) -> float:
        sx, sy, _ = solved[label]
        ix, iy, _ = initial[label]
        dx = (float(sx) - float(ix)) * 1000.0
        dy = (float(sy) - float(iy)) * 1000.0
        return (dx * dx + dy * dy) ** 0.5

    lower_deltas = {k: delta_mm(k) for k in lower}
    upper_deltas = {k: delta_mm(k) for k in upper}
    lower_lateral = {k: lateral_delta_mm(k) for k in lower}

    upper_mean_z = sum(float(solved[k][2]) for k in upper) / len(upper)
    lower_mean_z = sum(float(solved[k][2]) for k in lower) / len(lower)
    upper_lower_sep_mm = (upper_mean_z - lower_mean_z) * 1000.0
    rms_error_mm = float(solution.get("rms_error_mm", 0.0))

    checks = {
        "max_lower_shift_mm": max(lower_deltas.values()),
        "max_upper_shift_mm": max(upper_deltas.values()),
        "max_lower_lateral_shift_mm": max(lower_lateral.values()),
        "upper_lower_separation_mm": upper_lower_sep_mm,
        "rms_error_mm": rms_error_mm,
    }
    limits = {
        "max_lower_shift_mm": args.accept_max_lower_shift_mm,
        "max_upper_shift_mm": args.accept_max_upper_shift_mm,
        "max_lower_lateral_shift_mm": args.accept_max_lower_lateral_shift_mm,
        "min_upper_lower_separation_mm": args.accept_min_upper_lower_separation_mm,
        "max_rms_error_mm": args.accept_max_rms_error_mm,
    }

    failures = []
    if checks["max_lower_shift_mm"] > limits["max_lower_shift_mm"]:
        failures.append("max_lower_shift_mm")
    if checks["max_upper_shift_mm"] > limits["max_upper_shift_mm"]:
        failures.append("max_upper_shift_mm")
    if checks["max_lower_lateral_shift_mm"] > limits["max_lower_lateral_shift_mm"]:
        failures.append("max_lower_lateral_shift_mm")
    if checks["upper_lower_separation_mm"] < limits["min_upper_lower_separation_mm"]:
        failures.append("min_upper_lower_separation_mm")
    if checks["rms_error_mm"] > limits["max_rms_error_mm"]:
        failures.append("max_rms_error_mm")

    return {
        "accepted": len(failures) == 0,
        "failures": failures,
        "checks": checks,
        "limits": limits,
        "lower_anchor_shift_mm": lower_deltas,
        "upper_anchor_shift_mm": upper_deltas,
        "lower_anchor_lateral_shift_mm": lower_lateral,
    }


def write_runtime_json(solution: dict, runtime_path: Path, ref_z_prior_mm: float):
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
            f"This baseline was tightened with a floating static reference-tag session from Tag 115 and a soft Z prior near {int(round(ref_z_prior_mm))} mm.",
            "The solver uses previous-layout priors so upper anchors stay near baseline while allowing modest lower-anchor lateral updates.",
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
        print(
            "Ref115 calibration build profile: source=apps/tag_usb "
            "BLE=0 MCUboot=0 TDMA=0 TRACK_ANCHOR_COUNT=8 VERBOSE_RANGING=1"
        )
    elif mode == "monitor":
        source_dir = "apps/tag"
        mode_args = build_monitor_args(monitor_anchor_count)
        print(
            f"Ref115 monitor build profile: source=apps/tag TDMA=1 monitor_anchor_count={monitor_anchor_count}"
        )
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
    parser.add_argument("--ref-z-prior-mm", type=float, default=None)
    parser.add_argument("--ref-z-sigma-mm", type=float, default=80.0)
    parser.add_argument("--anchor-a-floor-height-mm", type=float, default=280.0)
    parser.add_argument("--ref115-floor-height-mm", type=float, default=1100.0)
    parser.add_argument("--distance-sigma-mm", type=float, default=90.0)
    parser.add_argument("--distance-sigma-same-plane-mm", type=float, default=120.0)
    parser.add_argument("--distance-sigma-cross-plane-mm", type=float, default=180.0)
    parser.add_argument("--distance-sigma-vertical-pair-mm", type=float, default=120.0)
    parser.add_argument("--height-prior-m", type=float, default=1.4)
    parser.add_argument("--height-sigma-mm", type=float, default=300.0)
    # Keep vertical-pair XY alignment disabled by default; upper/lower XY
    # projections are not guaranteed to overlap in real installations.
    parser.add_argument("--vertical-sigma-mm", type=float, default=0.0)
    parser.add_argument("--lower-plane-sigma-mm", type=float, default=120.0)
    parser.add_argument("--upper-plane-sigma-mm", type=float, default=180.0)
    parser.add_argument("--upper-level-sigma-mm", type=float, default=120.0)
    parser.add_argument("--pair-height-sigma-mm", type=float, default=120.0)
    parser.add_argument("--reference-sigma-mm", type=float, default=60.0)
    parser.add_argument("--prior-lower-xy-sigma-mm", type=float, default=1200.0)
    parser.add_argument("--prior-lower-z-sigma-mm", type=float, default=500.0)
    parser.add_argument("--prior-upper-xy-sigma-mm", type=float, default=800.0)
    parser.add_argument("--prior-upper-z-sigma-mm", type=float, default=350.0)
    parser.add_argument("--multi-start", type=int, default=8)
    parser.add_argument("--start-jitter-mm", type=float, default=450.0)
    parser.add_argument("--adaptive-edge-reweight-rounds", type=int, default=2)
    parser.add_argument("--max-iters", type=int, default=5)
    parser.add_argument("--converge-mm", type=float, default=1.0)
    parser.add_argument("--accept-max-lower-shift-mm", type=float, default=500.0)
    parser.add_argument("--accept-max-lower-lateral-shift-mm", type=float, default=450.0)
    parser.add_argument("--accept-max-upper-shift-mm", type=float, default=180.0)
    parser.add_argument("--accept-min-upper-lower-separation-mm", type=float, default=1200.0)
    parser.add_argument("--accept-max-rms-error-mm", type=float, default=280.0)
    parser.add_argument("--force-accept", action="store_true")
    parser.add_argument("--build-dir", default="build-ref115-calibration-capture")
    parser.add_argument(
        "--capture-mode",
        choices=("calibration", "monitor"),
        default="calibration",
        help="Build/flash mode used before capturing the Ref115 session.",
    )
    parser.add_argument(
        "--post-mode",
        choices=("calibration", "monitor", "none"),
        default="none",
        help="Build/flash mode applied after solving the new anchor layout.",
    )
    parser.add_argument(
        "--monitor-anchor-count",
        type=int,
        default=4,
        help="Monitor-mode anchor count. 4 uses the fixed B,C,F,G static build; 5-8 use adaptive monitoring.",
    )
    parser.add_argument("--min-valid-anchors", type=int, default=4)
    parser.add_argument("--min-valid-anchors-per-plane", type=int, default=1)
    parser.add_argument("--max-dominant-anchor-ratio", type=float, default=0.85)
    parser.add_argument("--min-samples-per-anchor", type=int, default=5)
    parser.add_argument(
        "--allow-calibration-build-mismatch",
        action="store_true",
        help="Allow calibration mode build-dir names that look like motion/ota families.",
    )
    parser.add_argument("--skip-build", action="store_true")
    parser.add_argument("--skip-flash", action="store_true")
    parser.add_argument(
        "--skip-anchor-family-check",
        action="store_true",
        help="Skip pre-capture check of data/anchor_flash_state.json.",
    )
    parser.add_argument(
        "--allow-anchor-family-mismatch",
        action="store_true",
        help="Allow capture even if anchor_flash_state.json shows missing/mismatched families.",
    )
    args = parser.parse_args()

    if args.ref_z_prior_mm is None:
        args.ref_z_prior_mm = args.ref115_floor_height_mm - args.anchor_a_floor_height_mm

    if (
        args.capture_mode == "calibration"
        and not args.skip_build
        and not args.allow_calibration_build_mismatch
    ):
        build_name = str(args.build_dir).lower()
        if ("motion" in build_name) or ("ota" in build_name):
            print(
                "Ref115 calibration build blocked: build-dir looks like motion/ota family. "
                "Use a dedicated calibration build dir or pass "
                "--allow-calibration-build-mismatch."
            )
            return 2

    precheck_ok, precheck_msg = check_anchor_flash_state_for_capture(args)
    if not precheck_ok:
        print("Anchor family precheck failed before Ref115 capture.")
        print(f"reason: {precheck_msg}")
        print("run: scripts/restore_anchors_runtime_for_ref115.sh tag")
        print("or: pass --allow-anchor-family-mismatch (not recommended)")
        return 2
    print(f"Anchor family precheck: {precheck_msg}")

    session_dir = ensure_session(args)
    session_sufficiency = evaluate_session_sufficiency(session_dir, args)
    session_sufficiency_path = session_dir / "session_sufficiency.json"
    session_sufficiency_path.write_text(
        json.dumps(session_sufficiency, indent=2) + "\n", encoding="utf-8"
    )
    if not session_sufficiency["accepted"]:
        print("")
        print("Session sufficiency gate failed; solve/promotion skipped.")
        print(f"session_sufficiency: {session_sufficiency_path}")
        print(f"failures: {', '.join(session_sufficiency['failures'])}")
        return 2

    tmp_solution = REPO_ROOT / "data" / "anchor_layout_ah_calibrated.ref115_tmp.json"
    solve_layout(session_dir, tmp_solution, args)

    solution = load_solution(tmp_solution)
    acceptance = evaluate_solution_acceptance(
        solution,
        (REPO_ROOT / args.initial_layout).resolve(),
        args,
    )
    acceptance["promoted"] = False
    acceptance["promotion_targets"] = {
        "calibrated_json": str(REPO_ROOT / "data" / "anchor_layout_ah_calibrated.json"),
        "runtime_json": str(REPO_ROOT / "data" / "anchor_layout_ah_runtime.json"),
        "source_c": str(REPO_ROOT / "src" / "uwb_anchor_layout.c"),
    }
    acceptance_path = session_dir / "anchor_layout_acceptance.json"
    acceptance_path.write_text(json.dumps(acceptance, indent=2) + "\n", encoding="utf-8")
    if (not acceptance["accepted"]) and (not args.force_accept):
        print("")
        print("Acceptance gate failed; runtime layout was not replaced.")
        print(f"acceptance_report: {acceptance_path}")
        print(f"failures: {', '.join(acceptance['failures'])}")
        return 2

    calibrated_path = REPO_ROOT / "data" / "anchor_layout_ah_calibrated.json"
    runtime_path = REPO_ROOT / "data" / "anchor_layout_ah_runtime.json"
    source_path = REPO_ROOT / "src" / "uwb_anchor_layout.c"

    shutil.copyfile(tmp_solution, calibrated_path)
    write_runtime_json(solution, runtime_path, args.ref_z_prior_mm)
    write_anchor_layout_c(solution, source_path)
    acceptance["promoted"] = True
    acceptance_path.write_text(json.dumps(acceptance, indent=2) + "\n", encoding="utf-8")

    if not args.skip_build and args.post_mode != "none":
        build_dir = REPO_ROOT / args.build_dir
        build_tag(build_dir, args.post_mode, args.monitor_anchor_count)
        if not args.skip_flash:
            flash_tag(args.snr, build_dir / "zephyr" / "zephyr.hex")

    print("")
    print(f"session_dir: {session_dir}")
    print(f"acceptance_report: {acceptance_path}")
    print(f"acceptance_passed: {acceptance['accepted']}")
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
