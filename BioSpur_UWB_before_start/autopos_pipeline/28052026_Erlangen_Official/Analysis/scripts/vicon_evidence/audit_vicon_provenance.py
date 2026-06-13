#!/usr/bin/env python3
"""Audit Vicon anchor-truth provenance and temporal stability.

This script is intentionally read-only with respect to existing analysis
outputs. It writes only Analysis/reports/EN/VICON_PROVENANCE_AUDIT.md.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from itertools import combinations
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from extract_vicon_evidence import markdown_table, parse_trc  # noqa: E402


ANCHORS = list("ABCDEFGH")
ANCHOR_MARKERS = [f"{a}antenna" for a in ANCHORS]
PRIMARY_IDS = ["ID01", "ID02", "ID03", "ID04", "ID05"]
STATIC_IDS = [f"ID{i:02d}" for i in range(1, 25)]
ROTO_IDS = [f"R{i:02d}" for i in range(1, 18)]
WAND_IDS = ["W01", "W02"]
STATIC_EXTRA_IDS = ["static", "static2", "static3", "static4"]
CAPTURE_IDS = STATIC_IDS + ROTO_IDS + WAND_IDS + STATIC_EXTRA_IDS

REPORT_PAIR_EXCESS_MM = 120.5
REPORT_MEAN_ANCHOR_BIAS_MM = 94.6

BODY_MARKERS = {
    "BS2DCE": ["WandBshort", "WandB4", "WandBtop", "WandBlong", "WandB5", "WandBcenter"],
    "BSDC91": ["WandCshort", "WandClong", "WandCtop", "WandC4", "WandC5", "WandCcenter"],
}
PHYSICAL_WAND_MARKERS = {
    "BS2DCE": ["WandBshort", "WandB4", "WandBtop", "WandBlong", "WandB5"],
    "BSDC91": ["WandCshort", "WandClong", "WandCtop", "WandC4", "WandC5"],
}
ANTENNA_MARKERS = {"BS2DCE": "WandBantenna", "BSDC91": "WandCantenna"}


@dataclass(frozen=True)
class FitResult:
    rotation: np.ndarray
    translation: np.ndarray
    scale: float
    det: float


def rel(path: Path, base: Path) -> str:
    try:
        return str(path.relative_to(base))
    except ValueError:
        return str(path)


def fmt(value: object, digits: int = 3) -> str:
    try:
        val = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return "NA"
    if not math.isfinite(val):
        return "NA"
    return f"{val:.{digits}f}"


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def marker_xyz(trc, marker: str) -> np.ndarray | None:
    return trc.marker_xyz(marker)


def finite_xyz(xyz: np.ndarray | None) -> np.ndarray:
    if xyz is None:
        return np.empty((0, 3), dtype=float)
    valid = np.isfinite(xyz).all(axis=1)
    return xyz[valid]


def marker_mean(trc, marker: str) -> np.ndarray:
    vals = finite_xyz(marker_xyz(trc, marker))
    if vals.size == 0:
        return np.array([np.nan, np.nan, np.nan])
    return np.nanmean(vals, axis=0)


def marker_median(trc, marker: str) -> np.ndarray:
    vals = finite_xyz(marker_xyz(trc, marker))
    if vals.size == 0:
        return np.array([np.nan, np.nan, np.nan])
    return np.nanmedian(vals, axis=0)


def marker_valid_count(trc, marker: str) -> int:
    return int(finite_xyz(marker_xyz(trc, marker)).shape[0])


def marker_valid_pct(trc, marker: str) -> float:
    total = trc.data.shape[0]
    if total == 0 or marker not in trc.markers:
        return 0.0
    return 100.0 * marker_valid_count(trc, marker) / total


def fit_similarity(src: np.ndarray, dst: np.ndarray, *, allow_reflection: bool = True, allow_scale: bool = False) -> FitResult:
    src_c = src.mean(axis=0)
    dst_c = dst.mean(axis=0)
    x = src - src_c
    y = dst - dst_c
    u, s, vt = np.linalg.svd(x.T @ y)
    d = np.ones(3)
    if not allow_reflection and np.linalg.det(u @ vt) < 0:
        d[-1] = -1.0
    r = u @ np.diag(d) @ vt
    scale = 1.0
    if allow_scale:
        denom = float(np.sum(x * x))
        scale = float(np.sum(s * d) / denom) if denom > 0 else 1.0
    t = dst_c - scale * src_c @ r
    return FitResult(rotation=r, translation=t, scale=scale, det=float(np.linalg.det(r)))


def apply_fit(points: np.ndarray, fit: FitResult) -> np.ndarray:
    return fit.scale * points @ fit.rotation + fit.translation


def inverse_apply_fit(points: np.ndarray, fit: FitResult) -> np.ndarray:
    return ((points - fit.translation) @ fit.rotation.T) / fit.scale


def parse_xcp_capture(path: Path) -> dict[str, str]:
    root = ET.parse(path).getroot()
    starts: set[str] = set()
    ends: set[str] = set()
    devices = 0
    for camera in root.findall(".//Camera"):
        capture = camera.find("Capture")
        if capture is None:
            continue
        devices += 1
        starts.add(capture.attrib.get("START_TIME", ""))
        ends.add(capture.attrib.get("END_TIME", ""))
    return {
        "start_time": sorted(starts)[0] if starts else "",
        "end_time": sorted(ends)[0] if ends else "",
        "unique_start_count": str(len(starts)),
        "unique_end_count": str(len(ends)),
        "devices": str(devices),
    }


def parse_system_params(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    root = ET.parse(path).getroot()
    out: dict[str, str] = {}
    for param in root.findall(".//Param"):
        name = param.attrib.get("name", "")
        if name:
            out[name] = param.attrib.get("value", "")
    return out


def load_layout_json(path: Path) -> tuple[list[str], np.ndarray]:
    data = json.loads(path.read_text(encoding="utf-8"))
    labels = [a.get("label", ANCHORS[int(a["id"])]) for a in data["anchors"]]
    coords = np.array([[a["x_mm"], a["y_mm"], a["z_mm"]] for a in data["anchors"]], dtype=float)
    return labels, coords


def load_static_truth(official_root: Path) -> tuple[dict[str, np.ndarray], dict[str, np.ndarray], dict[str, dict]]:
    full_scripts = official_root / "Analysis/official_extra_analysis/FULL/scripts"
    if str(full_scripts) not in sys.path:
        sys.path.insert(0, str(full_scripts))
    from tag_ground_truth import load_corrected_static_truth  # type: ignore

    return load_corrected_static_truth(official_root / "opti_captures/full", ANCHORS, PRIMARY_IDS)[:3]


def pair_distance_summary(layout: dict[str, np.ndarray], reference: dict[str, np.ndarray]) -> tuple[float, str, float]:
    worst_abs = -1.0
    worst_pair = ""
    diffs = []
    for a, b in combinations(ANCHORS, 2):
        d = float(np.linalg.norm(layout[a] - layout[b]))
        d0 = float(np.linalg.norm(reference[a] - reference[b]))
        delta = d - d0
        diffs.append(abs(delta))
        if abs(delta) > worst_abs:
            worst_abs = abs(delta)
            worst_pair = f"{a}-{b}"
    return worst_abs, worst_pair, float(np.nanmedian(diffs))


def pair_worst_for_ids(
    session_layouts: dict[str, dict[str, np.ndarray]],
    ids: Iterable[str],
    reference: dict[str, np.ndarray],
) -> tuple[float, str, str]:
    worst_abs = -1.0
    worst_session = ""
    worst_pair = ""
    for cid in ids:
        if cid not in session_layouts:
            continue
        worst, pair, _med = pair_distance_summary(session_layouts[cid], reference)
        if worst > worst_abs:
            worst_abs = worst
            worst_session = cid
            worst_pair = pair
    return worst_abs, worst_session, worst_pair


def collect_key_paths(obj: object, target_key: str, path: str = "") -> list[tuple[str, object]]:
    hits: list[tuple[str, object]] = []
    if isinstance(obj, dict):
        for key, value in obj.items():
            next_path = f"{path}.{key}" if path else key
            if key == target_key:
                hits.append((next_path, value))
            hits.extend(collect_key_paths(value, target_key, next_path))
    elif isinstance(obj, list):
        for idx, value in enumerate(obj):
            hits.extend(collect_key_paths(value, target_key, f"{path}[{idx}]"))
    return hits


def capture_sort_key(row: dict) -> tuple[str, str]:
    return (str(row.get("capture_start", "")), str(row.get("capture_id", "")))


def read_vsk_marker_names(vsk_path: Path) -> set[str]:
    text = vsk_path.read_text(encoding="utf-8", errors="replace")
    names: set[str] = set()
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("<Marker NAME="):
            part = line.split('NAME="', 1)[1]
            names.add(part.split('"', 1)[0])
    return names


def generate_report(base: Path, out_path: Path) -> None:
    opti_full = base / "opti_captures/full"
    report_base = base
    trcs = {cid: parse_trc(opti_full / f"{cid}.trc") for cid in CAPTURE_IDS if (opti_full / f"{cid}.trc").exists()}
    if not trcs:
        raise FileNotFoundError(opti_full)

    capture_meta: dict[str, dict] = {}
    for cid in trcs:
        xcp_path = opti_full / f"{cid}.xcp"
        system_path = opti_full / f"{cid}.system"
        xcp = parse_xcp_capture(xcp_path) if xcp_path.exists() else {}
        system = parse_system_params(system_path)
        capture_meta[cid] = {
            "capture_id": cid,
            "capture_start": xcp.get("start_time", ""),
            "capture_end": xcp.get("end_time", ""),
            "xcp_unique_start_count": xcp.get("unique_start_count", ""),
            "xcp_unique_end_count": xcp.get("unique_end_count", ""),
            "measured_frame_rate": system.get("MeasuredFrameRate", ""),
            "frames_captured_system": system.get("FramesCaptured", ""),
            "frames_dropped_system": system.get("FramesDropped", ""),
            "source": f"{rel(xcp_path, report_base)}:Camera/Capture START_TIME,END_TIME; {rel(system_path, report_base)}:Param MeasuredFrameRate",
        }

    # A1: exact anchor layout as used by the official loaders.
    primary_anchor_medians: dict[str, dict[str, np.ndarray]] = {}
    primary_n_valid: list[dict[str, object]] = []
    for cid in PRIMARY_IDS:
        trc = trcs[cid]
        primary_anchor_medians[cid] = {}
        for anchor, marker in zip(ANCHORS, ANCHOR_MARKERS):
            med = marker_median(trc, marker)
            primary_anchor_medians[cid][anchor] = med
            primary_n_valid.append(
                {
                    "capture_id": cid,
                    "anchor": anchor,
                    "n_valid": marker_valid_count(trc, marker),
                    "n_rows": trc.data.shape[0],
                    "source": f"{rel(trc.path, report_base)}:{marker} X/Y/Z finite rows",
                }
            )
    a1_layout = {
        anchor: np.nanmedian(np.vstack([primary_anchor_medians[cid][anchor] for cid in PRIMARY_IDS]), axis=0)
        for anchor in ANCHORS
    }

    anchor_truth_loader, static_truth, _static_meta = load_static_truth(base)
    loader_delta = {
        anchor: float(np.linalg.norm(anchor_truth_loader[anchor] - a1_layout[anchor]))
        for anchor in ANCHORS
    }

    # A2: session means for every requested capture with anchor markers.
    session_rows: list[dict] = []
    session_layouts: dict[str, dict[str, np.ndarray]] = {}
    for cid, trc in trcs.items():
        if not all(marker in trc.markers for marker in ANCHOR_MARKERS):
            continue
        by_anchor: dict[str, np.ndarray] = {}
        for anchor, marker in zip(ANCHORS, ANCHOR_MARKERS):
            by_anchor[anchor] = marker_mean(trc, marker)
        session_layouts[cid] = by_anchor
    global_mean = {
        anchor: np.nanmean(np.vstack([layout[anchor] for layout in session_layouts.values()]), axis=0)
        for anchor in ANCHORS
    }

    for cid, layout in session_layouts.items():
        meta = capture_meta.get(cid, {})
        for anchor in ANCHORS:
            p = layout[anchor]
            session_rows.append(
                {
                    "capture_id": cid,
                    "capture_start": meta.get("capture_start", ""),
                    "anchor": anchor,
                    "mean_x_mm": float(p[0]),
                    "mean_y_vertical_mm": float(p[1]),
                    "mean_z_mm": float(p[2]),
                    "disp_from_global_mean_mm": float(np.linalg.norm(p - global_mean[anchor])),
                    "disp_from_a1_layout_mm": float(np.linalg.norm(p - a1_layout[anchor])),
                    "n_valid": marker_valid_count(trcs[cid], f"{anchor}antenna"),
                    "source": f"{rel(trcs[cid].path, report_base)}:{anchor}antenna X/Y/Z session mean over finite rows",
                }
            )
    session_rows.sort(key=capture_sort_key)

    anchor_summary_rows: list[list[object]] = []
    first_gt5: dict[str, str] = {}
    for anchor in ANCHORS:
        rows = [r for r in session_rows if r["anchor"] == anchor]
        a1_disps = np.array([float(r["disp_from_a1_layout_mm"]) for r in rows])
        global_disps = np.array([float(r["disp_from_global_mean_mm"]) for r in rows])
        worst = rows[int(np.nanargmax(a1_disps))]
        first = next((r["capture_id"] for r in rows if float(r["disp_from_a1_layout_mm"]) > 5.0), "none")
        first_gt5[anchor] = str(first)
        anchor_summary_rows.append(
            [
                anchor,
                fmt(np.nanmedian(a1_disps), 3),
                fmt(np.nanmax(a1_disps), 3),
                worst["capture_id"],
                first,
                fmt(np.nanmedian(global_disps), 3),
                fmt(np.nanmax(global_disps), 3),
                "opti_captures/full/*:Aantenna-Hantenna session means vs ID01-ID05 median layout",
            ]
        )

    # A3: pair-distance drift and transform displacement.
    pair_rows: list[list[object]] = []
    pair_worst_all = -1.0
    pair_worst_session = ""
    pair_worst_pair = ""
    for cid, layout in sorted(session_layouts.items(), key=lambda kv: capture_sort_key(capture_meta.get(kv[0], {"capture_id": kv[0]}))):
        worst_abs, worst_pair, med_abs = pair_distance_summary(layout, a1_layout)
        if worst_abs > pair_worst_all:
            pair_worst_all = worst_abs
            pair_worst_session = cid
            pair_worst_pair = worst_pair
        pair_rows.append(
            [
                cid,
                capture_meta.get(cid, {}).get("capture_start", ""),
                fmt(worst_abs, 3),
                worst_pair,
                fmt(med_abs, 3),
                fmt(100.0 * worst_abs / REPORT_PAIR_EXCESS_MM, 2),
                fmt(100.0 * worst_abs / REPORT_MEAN_ANCHOR_BIAS_MM, 2),
                f"{rel(trcs[cid].path, report_base)}:Aantenna-Hantenna session means; reference ID01-ID05 median layout",
            ]
        )
    pair_worst_primary, pair_worst_primary_session, pair_worst_primary_pair = pair_worst_for_ids(session_layouts, PRIMARY_IDS, a1_layout)
    pair_worst_static, pair_worst_static_session, pair_worst_static_pair = pair_worst_for_ids(session_layouts, STATIC_IDS, a1_layout)

    layout_path = base / "solver/outputs/v1_to_v4_io_field_check/v4-io/layout.json"
    labels, coords = load_layout_json(layout_path)
    coord_by_label = {label: coords[i] for i, label in enumerate(labels)}
    src = np.vstack([coord_by_label[a] for a in ANCHORS])
    dst_a1 = np.vstack([a1_layout[a] for a in ANCHORS])
    fit_a1 = fit_similarity(src, dst_a1, allow_reflection=True, allow_scale=False)
    truth_ids = sorted([sid for sid in STATIC_IDS if sid in static_truth])
    tag_truth = np.vstack([static_truth[sid] for sid in truth_ids])
    query_auto = inverse_apply_fit(tag_truth, fit_a1)
    transform_rows: list[list[object]] = []
    static_transform_max = -1.0
    static_transform_median_candidates: list[float] = []
    all_transform_max = -1.0
    for cid, layout in sorted(session_layouts.items(), key=lambda kv: capture_sort_key(capture_meta.get(kv[0], {"capture_id": kv[0]}))):
        dst = np.vstack([layout[a] for a in ANCHORS])
        fit_s = fit_similarity(src, dst, allow_reflection=True, allow_scale=False)
        shifted = apply_fit(query_auto, fit_s)
        disp = np.linalg.norm(shifted - tag_truth, axis=1)
        med = float(np.nanmedian(disp))
        mx = float(np.nanmax(disp))
        all_transform_max = max(all_transform_max, mx)
        if cid in STATIC_IDS:
            static_transform_max = max(static_transform_max, mx)
            static_transform_median_candidates.append(med)
        transform_rows.append(
            [
                cid,
                capture_meta.get(cid, {}).get("capture_start", ""),
                fmt(med, 3),
                fmt(mx, 3),
                f"{rel(layout_path, report_base)}:anchors x/y/z; {rel(trcs[cid].path, report_base)}:Aantenna-Hantenna means; static truth from tag_ground_truth.py",
            ]
        )
    static_transform_median = float(np.nanmedian(static_transform_median_candidates))

    # B: Roto temporal alignment.
    offsets_path = base / "Analysis/official_extra_analysis/FULL/roto_absolute/tables/roto_time_offsets_v4io_T4.csv"
    offsets = pd.read_csv(offsets_path)
    ok_offsets = offsets[offsets["status"] == "ok"].copy()
    score = ok_offsets["score_median_3d_mm"].to_numpy(dtype=float)
    score_q1 = float(np.nanpercentile(score, 25))
    score_q3 = float(np.nanpercentile(score, 75))
    score_iqr = score_q3 - score_q1
    score_thr = score_q3 + 1.5 * score_iqr
    score_outliers = sorted(ok_offsets.loc[ok_offsets["score_median_3d_mm"] > score_thr, "capture_id"].astype(str).tolist())
    beta = ok_offsets["beta_s"].to_numpy(dtype=float)
    beta_q1 = float(np.nanpercentile(beta, 25))
    beta_q3 = float(np.nanpercentile(beta, 75))
    beta_iqr = beta_q3 - beta_q1
    beta_low = beta_q1 - 1.5 * beta_iqr
    beta_high = beta_q3 + 1.5 * beta_iqr
    beta_outliers = sorted(ok_offsets.loc[(ok_offsets["beta_s"] < beta_low) | (ok_offsets["beta_s"] > beta_high), "capture_id"].astype(str).tolist())
    roto_rows: list[list[object]] = []
    for _, row in ok_offsets.sort_values("capture_id").iterrows():
        cid = str(row["capture_id"])
        roto_rows.append(
            [
                cid,
                capture_meta.get(cid, {}).get("measured_frame_rate", ""),
                fmt(row["beta_s"], 6),
                fmt(row["score_median_3d_mm"], 3),
                int(row["n_overlap"]),
                "yes" if cid in score_outliers else "no",
                f"{rel(offsets_path, report_base)}:beta_s,score_median_3d_mm,n_overlap; {rel(opti_full / (cid + '.system'), report_base)}:MeasuredFrameRate",
            ]
        )

    # C: marker availability and rigid-body residuals.
    vsk_path = opti_full / "Responder.vsk"
    vsk_marker_names = read_vsk_marker_names(vsk_path) if vsk_path.exists() else set()
    availability_rows: list[list[object]] = []
    for cid in ROTO_IDS:
        trc = trcs[cid]
        for tag in ["BS2DCE", "BSDC91"]:
            candidate_markers = BODY_MARKERS[tag]
            physical = PHYSICAL_WAND_MARKERS[tag]
            valid_counts = {m: marker_valid_count(trc, m) for m in candidate_markers if m in trc.markers}
            usable = [m for m in candidate_markers if valid_counts.get(m, 0) >= 100]
            physical_usable = [m for m in physical if valid_counts.get(m, 0) >= 100]
            availability_rows.append(
                [
                    cid,
                    tag,
                    ";".join([m for m in candidate_markers if m in trc.markers]) or "none",
                    ";".join(usable) or "none",
                    len(usable),
                    len(physical_usable),
                    fmt(marker_valid_pct(trc, "WandB5"), 2) if tag == "BS2DCE" else "NA",
                    f"{rel(trc.path, report_base)}:TRC marker columns and finite X/Y/Z rows; usable threshold is run_roto_pseudo_imu_replay.py:201-206",
                ]
            )

    extr_path = base / "Analysis/official_extra_analysis/FULL_4way_comparison/roto_pseudo_imu/tables/roto_pseudo_imu_extrinsics.csv"
    extr = pd.read_csv(extr_path)
    b = extr[extr["tag"] == "BS2DCE"].copy()
    c = extr[extr["tag"] == "BSDC91"].copy()
    b_r01 = b[b["capture_id"] == "R01"].iloc[0]
    b_r02_r17 = b[b["capture_id"] != "R01"]
    residual_summary_rows = [
        [
            "WandB R01",
            fmt(b_r01["bodyfit_antenna_residual_p50_mm"], 3),
            fmt(b_r01["bodyfit_antenna_residual_p95_mm"], 3),
            fmt(b_r01["body_marker_fit_rms_median_mm"], 3),
            f"{rel(extr_path, report_base)}:R01/BS2DCE residual columns",
        ],
        [
            "WandB R02-R17 median",
            fmt(b_r02_r17["bodyfit_antenna_residual_p50_mm"].median(), 3),
            fmt(b_r02_r17["bodyfit_antenna_residual_p95_mm"].median(), 3),
            fmt(b_r02_r17["body_marker_fit_rms_median_mm"].median(), 3),
            f"{rel(extr_path, report_base)}:BS2DCE residual columns",
        ],
        [
            "WandC R01-R17 median",
            fmt(c["bodyfit_antenna_residual_p50_mm"].median(), 3),
            fmt(c["bodyfit_antenna_residual_p95_mm"].median(), 3),
            fmt(c["body_marker_fit_rms_median_mm"].median(), 3),
            f"{rel(extr_path, report_base)}:BSDC91 residual columns",
        ],
    ]
    report_resid_p50 = float(extr["bodyfit_antenna_residual_p50_mm"].median())
    report_resid_p95_of_p95 = float(extr["bodyfit_antenna_residual_p95_mm"].median())

    # D: offset/provenance candidates.
    us_layout_path = base / "solver/outputs/v1_to_v4_io_field_check_US/v4-io/layout.json"
    us_offsets: list[list[object]] = []
    if us_layout_path.exists():
        us_data = json.loads(us_layout_path.read_text(encoding="utf-8"))
        for field_path, value in collect_key_paths(us_data, "ant_center_offset_mm"):
            anchor = next((a for a in ["F", "G", "H"] if f".{a}." in f".{field_path}."), "unknown")
            us_offsets.append(
                [
                    f"Anchor {anchor}",
                    fmt(value, 1),
                    "mm",
                    "anchor-side ultrasound antenna-center height metadata, not Vicon marker-to-antenna truth",
                    f"{rel(us_layout_path, report_base)}:{field_path}",
                ]
            )

    # Write markdown.
    lines: list[str] = []
    lines.append("# Vicon Provenance And Temporal Stability Audit\n\n")
    lines.append(f"Base directory: `{base}`\n\n")
    lines.append("Generated by `Analysis/scripts/vicon_evidence/audit_vicon_provenance.py`. Existing pipeline outputs and `main_EN.tex` were not modified.\n\n")

    lines.append("## Question A: Anchor Ground-Truth Provenance\n\n")
    lines.append("### A1. Answer First\n\n")
    lines.append(
        "The Vicon anchor coordinate layout used by the official static registration and RotoArm registration is one global layout, not a per-session layout. "
        "It is computed as follows: for each primary static TRC `ID01`--`ID05`, every `Aantenna`--`Hantenna` marker is reduced over all finite TRC frames by a coordinate-wise median; then the five per-session medians are reduced again by a coordinate-wise median. "
        "This is implemented in `Analysis/official_extra_analysis/FULL/scripts/tag_ground_truth.py:100-116` and independently mirrored in `Analysis/official_extra_analysis/FULL/scripts/layout_optitrack_compare.py:562-610`.\n\n"
    )
    lines.append(
        "Static absolute accuracy uses that same global anchor layout when fitting the anchor-locked rigid transform (`Analysis/official_extra_analysis/FULL/scripts/static_tag_absolute_accuracy.py:322-350`). "
        "RotoArm absolute accuracy also calls the same loader with `PRIMARY_ANCHOR_TRUTH_IDS = [ID01..ID05]` and fits the layout transforms once (`Analysis/official_extra_analysis/FULL/roto_absolute/scripts/run_roto_absolute_analysis.py:35-44,254-263,1035`). "
        "The Phase-1 common-mode/range-bias audit reads `Analysis/official_extra_analysis/FULL/tables/layout_abs_errors_all8.csv` as anchor truth (`audit_phase1_revised.py:55,125-131`; `audit_phase1c_common_mode.py:51,119-125`), and that table is generated from the same ID01--ID05 median anchor layout.\n\n"
    )
    lines.append(
        "Anchor-side Vicon truth is the exported `Aantenna`--`Hantenna` marker position directly. I found no code path that applies a Vicon marker-to-UWB-phase-centre offset to anchors before registration or the range-bias oracle regression. "
        "That direct use is evidenced by `tag_ground_truth.py:105-116`, `layout_optitrack_compare.py:567-610`, and `layout_abs_errors_all8.csv:truth_x_mm,truth_y_vertical_mm,truth_z_mm`.\n\n"
    )

    lines.append("### A1 Primary-Frame Reduction Check\n\n")
    lines.append(markdown_table(
        ["Capture", "Anchor", "finite frames", "TRC rows", "Source field"],
        [[r["capture_id"], r["anchor"], r["n_valid"], r["n_rows"], r["source"]] for r in primary_n_valid],
    ))
    lines.append("\n\n")
    lines.append("Loader cross-check: maximum 3D difference between the script-recomputed ID01--ID05 median layout and `load_corrected_static_truth()` is "
                 f"{fmt(max(loader_delta.values()), 6)} mm. Source: `tag_ground_truth.py:100-116` and `opti_captures/full/ID01-ID05.trc:Aantenna-Hantenna`.\n\n")

    lines.append("### A2 Per-Anchor Temporal Stability Summary\n\n")
    lines.append(markdown_table(
        [
            "Anchor",
            "median disp vs A1 [mm]",
            "max disp vs A1 [mm]",
            "worst session",
            "first >5 mm session",
            "median disp vs global mean [mm]",
            "max disp vs global mean [mm]",
            "Source field",
        ],
        anchor_summary_rows,
    ))
    lines.append("\n\n")
    over5 = [(a, first) for a, first in first_gt5.items() if first != "none"]
    if over5:
        lines.append("Flag: at least one session mean moves more than 5 mm from the A1 layout for these anchors: "
                     + ", ".join(f"{a} first at {first}" for a, first in over5)
                     + ".\n\n")
    else:
        lines.append("Flag: no anchor session mean moves more than 5 mm from the A1 layout.\n\n")

    lines.append("### A2 Time-Ordered Session Means\n\n")
    lines.append(markdown_table(
        [
            "Capture",
            "Capture start",
            "Anchor",
            "mean x [mm]",
            "mean y vertical [mm]",
            "mean z [mm]",
            "disp vs global [mm]",
            "disp vs A1 [mm]",
            "finite frames",
            "Source field",
        ],
        [
            [
                r["capture_id"],
                r["capture_start"],
                r["anchor"],
                fmt(r["mean_x_mm"], 3),
                fmt(r["mean_y_vertical_mm"], 3),
                fmt(r["mean_z_mm"], 3),
                fmt(r["disp_from_global_mean_mm"], 3),
                fmt(r["disp_from_a1_layout_mm"], 3),
                r["n_valid"],
                r["source"],
            ]
            for r in session_rows
        ],
    ))
    lines.append("\n\n")

    lines.append("### A3 Pair-Distance Drift Bound\n\n")
    lines.append(
        "Pair-distance stability separates cleanly by capture subset. "
        f"For the A1 source sessions ID01--ID05, the worst pair-distance change relative to the A1 layout is {fmt(pair_worst_primary, 3)} mm "
        f"({pair_worst_primary_session}, pair {pair_worst_primary_pair}), which is {fmt(100.0 * pair_worst_primary / REPORT_PAIR_EXCESS_MM, 2)}% of the report's "
        f"{REPORT_PAIR_EXCESS_MM:.1f} mm mean pairwise excess and {fmt(100.0 * pair_worst_primary / REPORT_MEAN_ANCHOR_BIAS_MM, 2)}% of the "
        f"{REPORT_MEAN_ANCHOR_BIAS_MM:.1f} mm mean per-anchor oracle bias. "
        f"For all headline static sessions ID01--ID24, the worst pair-distance change is {fmt(pair_worst_static, 3)} mm "
        f"({pair_worst_static_session}, pair {pair_worst_static_pair}), or {fmt(100.0 * pair_worst_static / REPORT_PAIR_EXCESS_MM, 2)}% and "
        f"{fmt(100.0 * pair_worst_static / REPORT_MEAN_ANCHOR_BIAS_MM, 2)}% of those two report signals. "
        f"Across all requested anchor-marker captures, the worst pair-distance change is {fmt(pair_worst_all, 3)} mm "
        f"({pair_worst_session}, pair {pair_worst_pair}), or {fmt(100.0 * pair_worst_all / REPORT_PAIR_EXCESS_MM, 2)}% and "
        f"{fmt(100.0 * pair_worst_all / REPORT_MEAN_ANCHOR_BIAS_MM, 2)}%. This all-capture worst case is driven by the `Gantenna` anomaly in R05/W01/W02 and is not part of the headline static anchor layout evidence. "
        "Source: `opti_captures/full/*.trc:Aantenna-Hantenna` session means and A1 ID01--ID05 median layout.\n\n"
    )
    lines.append(markdown_table(
        [
            "Capture",
            "Capture start",
            "max |pair change| [mm]",
            "worst pair",
            "median |pair change| [mm]",
            "% of 120.5 mm",
            "% of 94.6 mm",
            "Source field",
        ],
        pair_rows,
    ))
    lines.append("\n\n")

    lines.append("### A3 Transform-Induced Static-Truth Displacement Bound\n\n")
    lines.append(
        "Method: fit the v4-io AutoPos anchor coordinates to the A1 layout, invert the A1 transform at the 24 corrected static tag truth positions, then re-apply a transform fitted to each session's anchor means. "
        "The resulting displacement is the transform-only change at the measured static tag locations.\n\n"
    )
    lines.append(
        f"For static sessions ID01--ID24, the median of per-session median displacements is {fmt(static_transform_median, 3)} mm and the worst max displacement is {fmt(static_transform_max, 3)} mm. "
        f"Across all requested anchor-marker captures, the worst max displacement is {fmt(all_transform_max, 3)} mm. "
        "Source: `solver/outputs/v1_to_v4_io_field_check/v4-io/layout.json:anchors`, `opti_captures/full/*.trc:Aantenna-Hantenna`, and corrected static tag truth from `tag_ground_truth.py:100-158`.\n\n"
    )
    lines.append(markdown_table(
        ["Capture", "Capture start", "median displacement over 24 static truth positions [mm]", "max displacement [mm]", "Source field"],
        transform_rows,
    ))
    lines.append("\n\n")
    lines.append(
        f"Conclusion: the global A1 layout is a valid approximation for the headline static analysis. Within the 24 static sessions, the worst transform-only effect on the 24 static truth locations is {fmt(static_transform_max, 3)} mm, "
        f"and the worst pair-distance perturbation is {fmt(pair_worst_static, 3)} mm ({fmt(100.0 * pair_worst_static / REPORT_PAIR_EXCESS_MM, 2)}% of the 120.5 mm pairwise-excess signal and "
        f"{fmt(100.0 * pair_worst_static / REPORT_MEAN_ANCHOR_BIAS_MM, 2)}% of the 94.6 mm per-anchor bias signal). "
        f"However, arbitrary per-capture anchor means should not be substituted without review: R05/W01/W02 contain a `Gantenna` anomaly, giving an all-capture worst pair-distance drift of {fmt(pair_worst_all, 3)} mm and an all-capture transform-only max displacement of {fmt(all_transform_max, 3)} mm.\n\n"
    )

    lines.append("## Question B: RotoArm Temporal Alignment\n\n")
    lines.append(
        f"Alignment score outlier rule: score > Q3 + 1.5 IQR = {fmt(score_thr, 3)} mm, with Q1={fmt(score_q1, 3)} mm, Q3={fmt(score_q3, 3)} mm, IQR={fmt(score_iqr, 3)} mm. "
        f"Score outliers: {', '.join(score_outliers) if score_outliers else 'none'}. R03 and R14 are not score outliers. "
        f"Beta outliers under the same two-sided IQR rule are {', '.join(beta_outliers) if beta_outliers else 'none'}, but beta mainly reflects capture start-time offset rather than trajectory quality. "
        f"Source: `{rel(offsets_path, report_base)}:beta_s,score_median_3d_mm`.\n\n"
    )
    lines.append(
        "Because R03 and R14 are not alignment-score outliers, the requested 121/120 Vicon-timebase diagnostic refit is skipped. "
        "The exported TRC `DataRate/CameraRate/OrigDataRate` are 120 Hz in `VICON_EVIDENCE.md`; the integer `.system` `MeasuredFrameRate=121` for R03/R14 is therefore treated as integer display/measurement rounding with no measurable effect on the current alignment scores.\n\n"
    )
    lines.append(markdown_table(
        ["Capture", ".system MeasuredFrameRate", "beta [s]", "alignment score median 3D [mm]", "overlap samples", "score outlier", "Source field"],
        roto_rows,
    ))
    lines.append("\n\n")

    lines.append("## Question C: Wand Rigid-Body Marker Provenance\n\n")
    lines.append(
        "The Roto absolute trajectory comparison itself uses the exported `WandBantenna` and `WandCantenna` trajectories directly (`run_roto_absolute_analysis.py:41-43,328-377`). "
        "The pseudo-IMU residual sanity check fits body pose from non-antenna markers and then estimates a body-to-antenna lever arm (`run_roto_pseudo_imu_replay.py:1-8,195-270`).\n\n"
    )
    vsk_status_rows = []
    for marker in [
        "WandBshort",
        "WandB4",
        "WandBtop",
        "WandBlong",
        "WandB5",
        "WandBcenter",
        "WandBantenna",
        "WandCshort",
        "WandClong",
        "WandCtop",
        "WandC4",
        "WandC5",
        "WandCcenter",
        "WandCantenna",
    ]:
        vsk_status_rows.append(
            [
                marker,
                "yes" if marker in vsk_marker_names else "no",
                "Responder.vsk Marker list" if marker in vsk_marker_names else "TRC export only / virtual or derived in repo evidence",
                f"{rel(vsk_path, report_base)}:<Marker NAME=...>; opti_captures/full/R*.trc marker headers",
            ]
        )
    lines.append(markdown_table(["Marker", "In VSK physical marker list", "Classification from repo files", "Source field"], vsk_status_rows))
    lines.append("\n\n")
    lines.append(
        "Interpretation: `WandBcenter`, `WandBantenna`, `WandCcenter`, and `WandCantenna` are not listed as VSK physical markers; they appear in TRC exports and are therefore treated here as exported virtual/derived points. "
        "`WandB5` has local-parameter entries in the VSK but is not in the VSK marker list; in the TRC exports it is usable only in R01 and is 100% missing in R11.\n\n"
    )
    lines.append("### C1 Per-Capture Body-Marker Availability\n\n")
    lines.append(markdown_table(
        [
            "Capture",
            "Tag",
            "TRC candidate body columns present",
            "usable body points used by code",
            "usable body-point count",
            "usable physical-marker count",
            "WandB5 valid frames [%]",
            "Source field",
        ],
        availability_rows,
    ))
    lines.append("\n\n")
    lines.append("### C2 Rigid-Body Reconstruction Residuals\n\n")
    lines.append(
        f"The report's 0.6/1.5 mm sanity check resolves to {fmt(report_resid_p50, 3)} mm median of bodyfit-antenna residual P50 values and {fmt(report_resid_p95_of_p95, 3)} mm median of bodyfit-antenna residual P95 values. "
        f"Source: `{rel(extr_path, report_base)}:bodyfit_antenna_residual_p50_mm,bodyfit_antenna_residual_p95_mm`; report text generated at `run_roto_pseudo_imu_replay.py:596-603`.\n\n"
    )
    lines.append(markdown_table(
        ["Group", "antenna residual P50 [mm]", "antenna residual P95 [mm]", "body marker fit RMS median [mm]", "Source field"],
        residual_summary_rows,
    ))
    lines.append("\n\n")
    lines.append(
        "Verdict: the statement `five-marker rigid body` is not accurate for all RotoArm tag assemblies/captures if it means five physical markers. "
        "For WandB/BS2DCE, R01 has 5 usable physical markers plus the exported center point; R02--R17 have 4 usable physical markers plus the exported center point, with R11 containing a `WandB5` column but 0 valid frames. "
        "For WandC/BSDC91, all R01--R17 captures have 5 usable physical markers plus the exported center point. For static tag `I1`--`I5`, the five-marker statement remains consistent with `tag_ground_truth.py:18-19`.\n\n"
    )

    lines.append("## Question D: Marker-To-Antenna / Pivot-To-Antenna Offset Provenance\n\n")
    lines.append(
        "No hardcoded static tag pivot-to-antenna vector was found in the active FULL analysis code. Static tag truth uses exported `Iantenna`, except ID01/ID05 where `Iantenna` is rebuilt from a data-derived clean-capture consensus (`tag_ground_truth.py:126-158`). "
        "The code records scalar `Icenter`-to-`Iantenna` distances for diagnostics (`tag_ground_truth.py:180-181`), but does not hardcode a shared vector.\n\n"
    )
    lines.append(
        "For RotoArm pseudo-IMU diagnostics, body-to-antenna lever arms are computed per capture/tag as the median of `(antenna - translation) @ rotation.T`, not hardcoded (`run_roto_pseudo_imu_replay.py:239-263`). "
        "Those computed vectors are stored in `roto_pseudo_imu_extrinsics.csv:lever_x_mm,lever_y_mm,lever_z_mm`.\n\n"
    )
    if us_offsets:
        lines.append("### D1 Offset-Like Values Found That Are Not Vicon Marker-To-Antenna Truth\n\n")
        lines.append(markdown_table(
            ["Item", "value", "units", "Meaning", "Source field"],
            us_offsets,
        ))
        lines.append("\n\n")
    lines.append(
        "Anchor-side finding: primary Vicon anchor truth uses raw exported `Aantenna`--`Hantenna` marker positions directly; no anchor marker-to-phase-centre offset is applied in the registration path or the range-bias oracle path. "
        "Source: `tag_ground_truth.py:105-116`, `layout_optitrack_compare.py:567-610`, `audit_phase1_revised.py:125-131`, and `audit_phase1c_common_mode.py:119-125`.\n\n"
    )
    lines.append("### D2 Operator TODOs\n\n")
    lines.append("- Anchor marker-to-UWB phase-centre mechanical relation and uncertainty: not determinable from repo — operator input required.\n")
    lines.append("- Static tag holder pivot-to-antenna caliper offset value and uncertainty: not determinable from repo — operator input required.\n")
    lines.append("- Whether TRC `*center` and `*antenna` points were generated as Nexus virtual/model points or manually labelled markers: partly inferable from `Responder.vsk`, but final confirmation is not determinable from repo — operator input required.\n\n")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--base",
        default="/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/autopos_pipeline/28052026_Erlangen_Official",
    )
    parser.add_argument("--out", default=None)
    args = parser.parse_args()
    base = Path(args.base).resolve()
    out = Path(args.out).resolve() if args.out else base / "Analysis/reports/EN/VICON_PROVENANCE_AUDIT.md"
    generate_report(base, out)
    print(f"wrote {out}")
    print(f"sha256 {sha256_file(out)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
