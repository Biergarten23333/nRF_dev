#!/usr/bin/env python3
from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


ANCHOR_COUNT = 8
FEATURE_NAMES = [
    "range_mm",
    "quality_percent",
    "geo_dist_mm",
    "uwb_x",
    "uwb_y",
    "uwb_z",
]


def find_base() -> Path:
    # .../28052026_Erlangen_Official/Analysis/official_extra_analysis/FULL/scripts/this.py
    return Path(__file__).resolve().parents[4]


BASE = find_base()
ANALYSIS = BASE / "Analysis/official_extra_analysis"
FULL = ANALYSIS / "FULL"
SAMPLES_PATH = ANALYSIS / "FULL_V5_roto_deepdive/tables/roto_v5_dloo_samples.csv"
RANGES_PATH = ANALYSIS / "FULL_V5_roto_deepdive/tables/roto_v5_dloo_ranges_long.csv"
LAYOUT_PATH = BASE / "solver/outputs/v1_to_v4_io_field_check/v5-commonmode/layout.json"
RAW_ROOT = BASE / "captures/erlangen_20260528_optitrack"
ROTO_DEEP_SCRIPT = ANALYSIS / "FULL_V5_roto_deepdive/scripts/run_roto_deepdive.py"
OUT_NPZ = FULL / "tables/roto_dnn_feature_tensor.npz"
OUT_FRAME_INDEX = FULL / "tables/roto_dnn_frame_index.csv"


def load_module(path: Path, name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load module from {path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def require_columns(df: pd.DataFrame, cols: list[str], label: str) -> None:
    missing = [c for c in cols if c not in df.columns]
    if missing:
        raise ValueError(f"{label} missing required columns: {missing}")


def load_raw_layout_anchors(path: Path) -> pd.DataFrame:
    data = json.loads(path.read_text(encoding="utf-8"))
    anchors = pd.DataFrame(data["anchors"])
    require_columns(anchors, ["id", "label", "x_mm", "y_mm", "z_mm"], "layout anchors")
    anchors = anchors.sort_values("id").reset_index(drop=True)
    if anchors["id"].tolist() != list(range(ANCHOR_COUNT)):
        raise ValueError(f"expected anchor ids 0..7 in {path}, got {anchors['id'].tolist()}")
    return anchors[["id", "label", "x_mm", "y_mm", "z_mm"]]


def load_aligned_anchor_coords(raw_layout: pd.DataFrame) -> np.ndarray:
    """Return Vicon/aligned-frame anchors.

    The v5-commonmode layout.json is in the solver layout frame. The ROTO sample
    table's x/y/z and truth_x/y/z are in the best-fit aligned Vicon frame. Use the
    existing ROTO deep-dive context so geo_dist_mm is computed in the same frame as
    truth_x/y/z.
    """

    if not ROTO_DEEP_SCRIPT.exists():
        raise FileNotFoundError(f"missing ROTO deep-dive script: {ROTO_DEEP_SCRIPT}")
    roto = load_module(ROTO_DEEP_SCRIPT, "roto_deep_for_tensor")
    ctx = roto.build_context()
    anchors_vicon = np.asarray(ctx["anchors_vicon"], dtype=np.float64)
    if anchors_vicon.shape != (ANCHOR_COUNT, 3):
        raise ValueError(f"unexpected aligned anchor shape: {anchors_vicon.shape}")

    raw = raw_layout[["x_mm", "y_mm", "z_mm"]].to_numpy(dtype=np.float64)
    if np.nanmedian(np.linalg.norm(raw - anchors_vicon, axis=1)) < 1.0:
        print("WARNING: aligned anchors are nearly identical to raw layout anchors; check frame assumptions.")
    return anchors_vicon


def load_clean_samples() -> pd.DataFrame:
    cols = [
        "capture_id",
        "tag",
        "sweep",
        "x",
        "y",
        "z",
        "truth_x",
        "truth_y",
        "truth_z",
        "err3d_mm",
    ]
    samples = pd.read_csv(SAMPLES_PATH, usecols=cols)
    require_columns(samples, cols, "samples")

    coord_cols = ["x", "y", "z", "truth_x", "truth_y", "truth_z", "err3d_mm"]
    before = len(samples)
    samples = samples[np.isfinite(samples[coord_cols]).all(axis=1)].copy()
    samples = samples[samples["err3d_mm"] <= 500.0].copy()
    samples = samples.sort_values(["capture_id", "tag", "sweep"], kind="mergesort").reset_index(drop=True)
    samples.insert(0, "frame_idx", np.arange(len(samples), dtype=np.int64))
    print(f"samples loaded: {before:,}; kept after err3d_mm<=500 and finite coords: {len(samples):,}")
    return samples


def load_ranges_for_samples(samples: pd.DataFrame) -> pd.DataFrame:
    cols = ["capture_id", "tag", "sweep", "anchor_id", "anchor_label", "range_measured_mm"]
    ranges = pd.read_csv(RANGES_PATH, usecols=cols)
    require_columns(ranges, cols, "ranges_long")
    before = len(ranges)
    ranges = ranges[(ranges["anchor_id"] >= 0) & (ranges["anchor_id"] < ANCHOR_COUNT)].copy()
    ranges["anchor_id"] = ranges["anchor_id"].astype(np.int16)

    keys = samples[["frame_idx", "capture_id", "tag", "sweep"]]
    merged = ranges.merge(keys, on=["capture_id", "tag", "sweep"], how="inner")
    dup_mask = merged.duplicated(["frame_idx", "anchor_id"], keep=False)
    dup_count = int(dup_mask.sum())
    if dup_count:
        print(f"WARNING: {dup_count:,} duplicated range rows after frame merge; averaging duplicates.")
        merged = (
            merged.groupby(["frame_idx", "capture_id", "tag", "sweep", "anchor_id"], as_index=False)
            .agg(range_measured_mm=("range_measured_mm", "mean"), anchor_label=("anchor_label", "first"))
        )
    print(f"ranges loaded: {before:,}; rows matched to kept frames: {len(merged):,}")
    return merged


def raw_quality_paths() -> list[tuple[str, Path]]:
    paths: list[tuple[str, Path]] = []
    for path in RAW_ROOT.glob("roto_R*/tag_capture*/tr_all.csv"):
        capture_dir = path.parents[1].name
        # Require underscore after Rxx. This excludes roto_R01-Static-middle-test...
        match = re.match(r"roto_(R\d+)_", capture_dir)
        if match:
            paths.append((match.group(1), path))
    return sorted(paths)


def load_raw_quality() -> pd.DataFrame:
    rows: list[pd.DataFrame] = []
    usecols = ["sweep", "peer_name", "anchor_id", "range_mm", "quality_percent", "valid"]
    for capture_id, path in raw_quality_paths():
        df = pd.read_csv(path, usecols=usecols)
        df = df.rename(columns={"peer_name": "tag", "range_mm": "raw_range_mm"})
        df.insert(0, "capture_id", capture_id)
        rows.append(df)
    if not rows:
        raise FileNotFoundError(f"no real ROTO tr_all.csv files found under {RAW_ROOT}")

    raw = pd.concat(rows, ignore_index=True)
    raw = raw[(raw["anchor_id"] >= 0) & (raw["anchor_id"] < ANCHOR_COUNT)].copy()
    raw["anchor_id"] = raw["anchor_id"].astype(np.int16)

    key_cols = ["capture_id", "tag", "sweep", "anchor_id"]
    dup_count = int(raw.duplicated(key_cols, keep=False).sum())
    if dup_count:
        print(f"WARNING: raw quality has {dup_count:,} duplicated keyed rows; averaging quality.")
        raw = (
            raw.groupby(key_cols, as_index=False)
            .agg(
                quality_percent=("quality_percent", "mean"),
                raw_range_mm=("raw_range_mm", "mean"),
                valid=("valid", "max"),
            )
        )
    print(f"raw quality files: {len(rows)}; quality keyed rows: {len(raw):,}; duplicate keyed rows: {dup_count:,}")
    return raw


def build_tensors(
    samples: pd.DataFrame,
    ranges: pd.DataFrame,
    quality: pd.DataFrame,
    anchors_vicon: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, pd.DataFrame, dict[str, float]]:
    n = len(samples)
    X = np.full((n, ANCHOR_COUNT, len(FEATURE_NAMES)), np.nan, dtype=np.float32)
    Y = np.full((n, ANCHOR_COUNT), np.nan, dtype=np.float32)

    uwb = samples[["x", "y", "z"]].to_numpy(dtype=np.float64)
    truth = samples[["truth_x", "truth_y", "truth_z"]].to_numpy(dtype=np.float64)
    geo = np.linalg.norm(truth[:, None, :] - anchors_vicon[None, :, :], axis=2)

    X[:, :, FEATURE_NAMES.index("geo_dist_mm")] = geo.astype(np.float32)
    X[:, :, FEATURE_NAMES.index("uwb_x")] = uwb[:, 0:1].astype(np.float32)
    X[:, :, FEATURE_NAMES.index("uwb_y")] = uwb[:, 1:2].astype(np.float32)
    X[:, :, FEATURE_NAMES.index("uwb_z")] = uwb[:, 2:3].astype(np.float32)

    qcols = ["capture_id", "tag", "sweep", "anchor_id", "quality_percent", "raw_range_mm", "valid"]
    require_columns(quality, qcols, "raw quality")
    enriched = ranges.merge(
        quality[qcols],
        on=["capture_id", "tag", "sweep", "anchor_id"],
        how="left",
        validate="many_to_one",
    )

    frame_idx = enriched["frame_idx"].to_numpy(dtype=np.int64)
    anchor_idx = enriched["anchor_id"].to_numpy(dtype=np.int64)
    range_mm = enriched["range_measured_mm"].to_numpy(dtype=np.float64)
    quality_percent = enriched["quality_percent"].to_numpy(dtype=np.float64)

    X[frame_idx, anchor_idx, FEATURE_NAMES.index("range_mm")] = range_mm.astype(np.float32)
    X[frame_idx, anchor_idx, FEATURE_NAMES.index("quality_percent")] = quality_percent.astype(np.float32)
    residual = range_mm - geo[frame_idx, anchor_idx]
    Y[frame_idx, anchor_idx] = residual.astype(np.float32)

    total_slots = int(n * ANCHOR_COUNT)
    range_slots = int(np.isfinite(X[:, :, FEATURE_NAMES.index("range_mm")]).sum())
    quality_slots = int(np.isfinite(X[:, :, FEATURE_NAMES.index("quality_percent")]).sum())
    matched_quality_rows = int(np.isfinite(quality_percent).sum())
    stats = {
        "total_anchor_slots": float(total_slots),
        "range_slots": float(range_slots),
        "missing_range_slots": float(total_slots - range_slots),
        "range_slot_rate": range_slots / total_slots,
        "matched_quality_rows": float(matched_quality_rows),
        "quality_match_rate_on_range_rows": matched_quality_rows / max(len(enriched), 1),
        "quality_slot_rate_all_slots": quality_slots / total_slots,
    }
    return X, Y, enriched, stats


def print_frame(samples: pd.DataFrame, X: np.ndarray, Y: np.ndarray, frame_idx: int) -> None:
    row = samples.iloc[frame_idx]
    print()
    print(
        f"Frame {frame_idx}: capture_id={row.capture_id}, tag={row.tag}, sweep={int(row.sweep)}, "
        f"uwb=({row.x:.3f}, {row.y:.3f}, {row.z:.3f}), "
        f"truth=({row.truth_x:.3f}, {row.truth_y:.3f}, {row.truth_z:.3f})"
    )
    frame_rows = []
    for anchor_id in range(ANCHOR_COUNT):
        rec = {"anchor_id": anchor_id}
        for j, name in enumerate(FEATURE_NAMES):
            rec[name] = float(X[frame_idx, anchor_id, j])
        rec["residual_mm_target_Y"] = float(Y[frame_idx, anchor_id])
        frame_rows.append(rec)
    with pd.option_context("display.max_columns", None, "display.width", 220, "display.float_format", "{:.3f}".format):
        print(pd.DataFrame(frame_rows).to_string(index=False))


def save_outputs(
    X: np.ndarray,
    Y: np.ndarray,
    samples: pd.DataFrame,
    raw_layout: pd.DataFrame,
    anchors_vicon: np.ndarray,
) -> None:
    OUT_NPZ.parent.mkdir(parents=True, exist_ok=True)
    OUT_FRAME_INDEX.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(
        OUT_NPZ,
        X=X,
        Y=Y,
        feature_names=np.asarray(FEATURE_NAMES),
        frame_idx=samples["frame_idx"].to_numpy(dtype=np.int64),
        capture_id=samples["capture_id"].astype(str).to_numpy(),
        tag=samples["tag"].astype(str).to_numpy(),
        sweep=samples["sweep"].to_numpy(dtype=np.int64),
        anchor_ids=np.arange(ANCHOR_COUNT, dtype=np.int64),
        anchor_labels=raw_layout["label"].astype(str).to_numpy(),
        anchor_coords_vicon_mm=anchors_vicon.astype(np.float32),
    )
    samples[["frame_idx", "capture_id", "tag", "sweep", "x", "y", "z", "truth_x", "truth_y", "truth_z", "err3d_mm"]].to_csv(
        OUT_FRAME_INDEX, index=False
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build ROTO dynamic DNN range-error feature tensor.")
    parser.add_argument("--no-save", action="store_true", help="Do not save NPZ/frame-index artifacts.")
    parser.add_argument("--print-frames", type=int, default=2, help="Number of leading frames to print.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    print(f"base: {BASE}")
    print(f"samples: {SAMPLES_PATH}")
    print(f"ranges: {RANGES_PATH}")
    print(f"layout: {LAYOUT_PATH}")

    raw_layout = load_raw_layout_anchors(LAYOUT_PATH)
    anchors_vicon = load_aligned_anchor_coords(raw_layout)
    print("anchor frame: v5-commonmode layout transformed to ROTO/Vicon aligned frame")
    with pd.option_context("display.max_columns", None, "display.width", 160, "display.float_format", "{:.3f}".format):
        anchor_report = raw_layout.copy()
        anchor_report[["aligned_x_mm", "aligned_y_mm", "aligned_z_mm"]] = anchors_vicon
        print(anchor_report.to_string(index=False))

    samples = load_clean_samples()
    ranges = load_ranges_for_samples(samples)
    quality = load_raw_quality()
    X, Y, enriched, stats = build_tensors(samples, ranges, quality, anchors_vicon)

    print()
    print("feature order:", FEATURE_NAMES)
    print(f"X shape: {X.shape}")
    print(f"Y shape: {Y.shape}")
    print("target Y: residual_mm = range_mm - geo_dist_mm")
    print(
        "range coverage: "
        f"{int(stats['range_slots']):,}/{int(stats['total_anchor_slots']):,} "
        f"({100.0 * stats['range_slot_rate']:.3f}%), "
        f"missing slots={int(stats['missing_range_slots']):,}"
    )
    print(
        "quality_percent match: "
        f"{int(stats['matched_quality_rows']):,}/{len(enriched):,} range rows "
        f"({100.0 * stats['quality_match_rate_on_range_rows']:.3f}%), "
        f"all tensor slots {100.0 * stats['quality_slot_rate_all_slots']:.3f}%"
    )

    if len(enriched):
        raw_range_delta = enriched["range_measured_mm"] - enriched["raw_range_mm"]
        finite = raw_range_delta[np.isfinite(raw_range_delta)]
        if len(finite):
            print(
                "range cross-check against raw tr_all.csv: "
                f"median delta={float(np.median(finite)):.3f} mm, "
                f"max abs delta={float(np.max(np.abs(finite))):.3f} mm"
            )

    for frame_idx in range(min(args.print_frames, len(samples))):
        print_frame(samples, X, Y, frame_idx)

    if not args.no_save:
        save_outputs(X, Y, samples, raw_layout, anchors_vicon)
        print()
        print(f"saved tensor: {OUT_NPZ}")
        print(f"saved frame index: {OUT_FRAME_INDEX}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
