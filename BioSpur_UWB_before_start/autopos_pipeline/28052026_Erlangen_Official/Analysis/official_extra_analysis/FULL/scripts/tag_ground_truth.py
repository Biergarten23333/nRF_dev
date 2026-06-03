#!/usr/bin/env python3
"""Corrected static-tag OptiTrack ground truth.

The ID01/ID05 tag fixture balls were auto-labeled with swapped I1..I5
identities.  The table below maps each corrected I1..I5 slot to the original
0-based ball index from the TRC export.  The antenna point is then rebuilt from
the consensus ball-local antenna location learned from the clean captures.
"""

from __future__ import annotations

import csv
from pathlib import Path

import numpy as np


TAG_BALL_MARKERS = ["I1", "I2", "I3", "I4", "I5"]
TAG_VIRTUAL_MARKERS = ["Icenter", "Iantenna"]
IDENTITY_PERMUTATION = (0, 1, 2, 3, 4)
TAG_BALL_LABEL_PERMUTATIONS = {
    "ID01": (0, 1, 4, 2, 3),
    "ID05": (3, 4, 2, 0, 1),
}
PAIR_ORDER = (
    (0, 1),
    (0, 2),
    (0, 3),
    (0, 4),
    (1, 2),
    (1, 3),
    (1, 4),
    (2, 3),
    (2, 4),
    (3, 4),
)


def parse_trc_medians(path: Path, marker_names: list[str]) -> dict[str, np.ndarray]:
    with path.open("r", errors="replace", newline="") as f:
        rows = list(csv.reader(f, delimiter="\t"))
    marker_row = [x.strip() for x in rows[3][2:] if x.strip()]
    marker_to_index = {name: i for i, name in enumerate(marker_row)}
    data = []
    for row in rows[5:]:
        vals = []
        for field in row:
            field = field.strip()
            if field == "":
                vals.append(np.nan)
            else:
                try:
                    vals.append(float(field))
                except ValueError:
                    vals.append(np.nan)
        data.append(vals)
    max_cols = max(len(r) for r in data)
    arr = np.full((len(data), max_cols), np.nan)
    for i, row in enumerate(data):
        arr[i, : len(row)] = row

    out = {}
    for marker in marker_names:
        if marker not in marker_to_index:
            raise KeyError(f"{marker} missing in {path}")
        start = 2 + marker_to_index[marker] * 3
        xyz = arr[:, start : start + 3]
        xyz = xyz[np.isfinite(xyz).all(axis=1)]
        out[marker] = np.nanmedian(xyz, axis=0)
    return out


def fit_rigid(src: np.ndarray, dst: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Fit src @ R + t ~= dst, no scale and no reflection."""
    src_c = src.mean(axis=0)
    dst_c = dst.mean(axis=0)
    x = src - src_c
    y = dst - dst_c
    u, _, vt = np.linalg.svd(x.T @ y)
    r = u @ vt
    if np.linalg.det(r) < 0:
        u[:, -1] *= -1.0
        r = u @ vt
    t = dst_c - src_c @ r
    return r, t


def pairwise_fingerprint(balls: np.ndarray) -> np.ndarray:
    return np.array([np.linalg.norm(balls[i] - balls[j]) for i, j in PAIR_ORDER], dtype=float)


def _max_abs_deviation(values: np.ndarray, consensus: np.ndarray) -> float:
    return float(np.nanmax(np.abs(values - consensus)))


def _permutation_for_id(session_id: str) -> tuple[int, int, int, int, int]:
    return TAG_BALL_LABEL_PERMUTATIONS.get(session_id, IDENTITY_PERMUTATION)


def load_corrected_static_truth(
    opti_dir: Path,
    anchors: list[str],
    primary_anchor_truth_ids: list[str],
) -> tuple[dict[str, np.ndarray], dict[str, np.ndarray], dict[str, dict], list[dict]]:
    marker_names = [f"{a}antenna" for a in anchors] + TAG_BALL_MARKERS + TAG_VIRTUAL_MARKERS
    medians_by_id: dict[str, dict[str, np.ndarray]] = {}
    for path in sorted(opti_dir.glob("ID*.trc")):
        medians_by_id[path.stem] = parse_trc_medians(path, marker_names)

    anchor_truth = {
        a: np.nanmedian(
            np.array([medians_by_id[pid][f"{a}antenna"] for pid in primary_anchor_truth_ids if pid in medians_by_id]),
            axis=0,
        )
        for a in anchors
    }

    balls_by_id = {
        sid: np.vstack([markers[name] for name in TAG_BALL_MARKERS])
        for sid, markers in medians_by_id.items()
    }
    clean_ids = sorted(sid for sid in medians_by_id if sid not in TAG_BALL_LABEL_PERMUTATIONS)
    reference_id = "ID02" if "ID02" in clean_ids else clean_ids[0]
    reference_balls = balls_by_id[reference_id]

    clean_antennas_in_ref = []
    clean_fingerprints = []
    for sid in clean_ids:
        balls = balls_by_id[sid]
        r, t = fit_rigid(balls, reference_balls)
        clean_antennas_in_ref.append(medians_by_id[sid]["Iantenna"] @ r + t)
        clean_fingerprints.append(pairwise_fingerprint(balls))

    consensus_antenna_ref = np.nanmedian(np.vstack(clean_antennas_in_ref), axis=0)
    consensus_fingerprint = np.nanmedian(np.vstack(clean_fingerprints), axis=0)
    clean_spread = float(
        np.nanmax(np.abs(np.vstack(clean_fingerprints) - consensus_fingerprint[None, :]))
    )

    tag_truth: dict[str, np.ndarray] = {}
    truth_meta: dict[str, dict] = {}
    correction_rows: list[dict] = []
    for sid, markers in sorted(medians_by_id.items()):
        original_balls = balls_by_id[sid]
        perm = _permutation_for_id(sid)
        corrected_balls = original_balls[list(perm)]
        motive_antenna = markers["Iantenna"]
        corrected = motive_antenna.copy()
        source = "motive_iantenna"
        if perm != IDENTITY_PERMUTATION:
            r, t = fit_rigid(corrected_balls, reference_balls)
            corrected = (consensus_antenna_ref - t) @ r.T
            source = "reconstructed_from_relabelled_balls"

        as_is_fp = pairwise_fingerprint(original_balls)
        corrected_fp = pairwise_fingerprint(corrected_balls)
        shift = float(np.linalg.norm(corrected - motive_antenna))
        tag_truth[sid] = corrected
        truth_meta[sid] = {
            "tag_truth_source": source,
            "tag_truth_corrected": bool(perm != IDENTITY_PERMUTATION),
            "tag_truth_permutation": ",".join(str(i) for i in perm),
            "tag_truth_shift_from_motive_mm": shift,
            "tag_ball_fingerprint_as_is_max_abs_dev_mm": _max_abs_deviation(as_is_fp, consensus_fingerprint),
            "tag_ball_fingerprint_corrected_max_abs_dev_mm": _max_abs_deviation(corrected_fp, consensus_fingerprint),
        }
        correction_rows.append(
            {
                "ID": sid,
                "tag_truth_source": source,
                "tag_truth_corrected": bool(perm != IDENTITY_PERMUTATION),
                "tag_truth_permutation": ",".join(str(i) for i in perm),
                "motive_iantenna_x_mm": float(motive_antenna[0]),
                "motive_iantenna_y_vertical_mm": float(motive_antenna[1]),
                "motive_iantenna_z_mm": float(motive_antenna[2]),
                "corrected_iantenna_x_mm": float(corrected[0]),
                "corrected_iantenna_y_vertical_mm": float(corrected[1]),
                "corrected_iantenna_z_mm": float(corrected[2]),
                "tag_truth_shift_from_motive_mm": shift,
                "motive_icenter_to_iantenna_mm": float(np.linalg.norm(motive_antenna - markers["Icenter"])),
                "corrected_icenter_to_iantenna_mm": float(np.linalg.norm(corrected - markers["Icenter"])),
                "fingerprint_as_is_max_abs_dev_mm": _max_abs_deviation(as_is_fp, consensus_fingerprint),
                "fingerprint_corrected_max_abs_dev_mm": _max_abs_deviation(corrected_fp, consensus_fingerprint),
                "consensus_reference_id": reference_id,
                "clean_consensus_n": len(clean_ids),
                "clean_fingerprint_max_spread_mm": clean_spread,
            }
        )

    return anchor_truth, tag_truth, truth_meta, correction_rows
