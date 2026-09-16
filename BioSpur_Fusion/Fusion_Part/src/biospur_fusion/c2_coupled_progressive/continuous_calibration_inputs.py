"""Read-only full-stream inputs for the existing calibration factor owners.

This adapter associates original native samples on their measured common clock;
it does not fit clocks, manufacture pair reports, interpolate samples, reset
VQF, fit parameters, or publish poses. Inter-action motion is ordinary input.
An optional pelvis correction is an effective sensor-frame residual estimate,
not an assertion that intrinsic accelerometer bias has been identified.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import time

import numpy as np

from .contracts import EDGES, NODE_TO_SEGMENT, SEGMENT_TO_NODE, sha256
from .estimator import AlignedSpan, EpisodeFactorBlock, FactorTape, windows_for_span


PELVIS = SEGMENT_TO_NODE["pelvis"]
FULL_SESSION = "FULL_SESSION_CONTINUOUS_00_TO_19"
FIELDS = ("time_us", "boot_epoch", "common_global_ns", "contiguous_span_id",
          "acc_mps2", "gyro_rads", "quat_vqf_sensor_wxyz")


def _readonly(value):
    result = np.asarray(value).copy()
    result.setflags(write=False)
    return result


@dataclass(frozen=True)
class EffectivePelvisBiasHistory:
    """Past-available effective ba, in the BSFC2CC sensor coordinate frame.

    Rows become usable at availability_global_ns (default: measurement time).
    Both clocks must be monotonic. No interpolation, backfill, or ten-node
    replication is permitted. An estimate persists until the next revision.
    """
    time_global_ns: np.ndarray
    bias_sensor_mps2: np.ndarray
    source: str
    availability_global_ns: np.ndarray | None = None

    def __post_init__(self):
        t = np.asarray(self.time_global_ns)
        b = np.asarray(self.bias_sensor_mps2, dtype=float)
        available = t if self.availability_global_ns is None else np.asarray(self.availability_global_ns)
        if (t.ndim != 1 or len(t) == 0 or t.dtype.kind not in "iu"
                or available.shape != t.shape or available.dtype.kind not in "iu"
                or b.shape != (len(t), 3) or not np.isfinite(b).all()
                or np.any(np.diff(t) <= 0) or np.any(np.diff(available) < 0)
                or np.any(available < t) or not self.source):
            raise ValueError("invalid effective pelvis bias history or clocks")
        object.__setattr__(self, "time_global_ns", _readonly(t))
        object.__setattr__(self, "bias_sensor_mps2", _readonly(b))
        object.__setattr__(self, "availability_global_ns", _readonly(available))

    def at(self, query_global_ns):
        query = np.asarray(query_global_ns)
        rows = np.searchsorted(self.availability_global_ns, query, side="right") - 1
        result = np.zeros((len(query), 3))
        valid = rows >= 0
        result[valid] = self.bias_sensor_mps2[rows[valid]]
        return result, rows


def _load_node(path: Path):
    with np.load(path, allow_pickle=False) as source:
        data = {key: _readonly(source[key]) for key in FIELDS}
    n = len(data["time_us"])
    if n == 0:
        raise ValueError(f"empty native stream: {path.name}")
    for key in FIELDS[:4]:
        if data[key].shape != (n,) or data[key].dtype.kind not in "iu":
            raise ValueError(f"invalid native clock/span array: {key}")
    for key in ("time_us", "common_global_ns"):
        if np.any(np.diff(data[key]) <= 0):
            raise ValueError(f"nonmonotonic native clock: {key}")
    for key, width in (("acc_mps2", 3), ("gyro_rads", 3), ("quat_vqf_sensor_wxyz", 4)):
        if data[key].shape != (n, width) or not np.isfinite(data[key]).all():
            raise ValueError(f"invalid native estimator array: {key}")
    if not np.allclose(np.linalg.norm(data["quat_vqf_sensor_wxyz"], axis=1), 1, atol=2e-6):
        raise ValueError("unnormalized frontend orientation")
    return data


def _associate(parent, child, max_pair_offset_ns):
    """Nearest original common-clock samples; ties choose the earlier child."""
    pt, ct = parent["common_global_ns"], child["common_global_ns"]
    right = np.clip(np.searchsorted(ct, pt), 0, len(ct) - 1)
    left = np.maximum(right - 1, 0)
    ci = np.where(np.abs(ct[left] - pt) <= np.abs(ct[right] - pt), left, right)
    valid = np.abs(ct[ci] - pt) <= max_pair_offset_ns
    pi = np.flatnonzero(valid)
    ci = ci[valid]
    # Do not count one child observation twice when clocks drift past a tick.
    unique = np.r_[True, np.diff(ci) > 0] if len(ci) else np.zeros(0, dtype=bool)
    return pi[unique], ci[unique]


def _span_indices(parent, child, pi, ci, minimum_span_samples):
    if len(pi) == 0:
        return []
    breaks = (np.diff(pi) != 1) | (np.diff(ci) != 1)
    for node, indices in ((parent, pi), (child, ci)):
        breaks |= np.diff(node["time_us"][indices]) != 5000
        breaks |= np.diff(node["boot_epoch"][indices]) != 0
        breaks |= np.diff(node["contiguous_span_id"][indices]) != 0
    limits = np.r_[0, np.flatnonzero(breaks) + 1, len(pi)]
    return [(pi[a:b], ci[a:b]) for a, b in zip(limits[:-1], limits[1:])
            if b - a >= minimum_span_samples]


def build_continuous_factor_tape(frontend: Path, *,
                                 pelvis_bias: EffectivePelvisBiasHistory | None = None,
                                 max_pair_offset_ns: int = 2_500_000,
                                 minimum_span_samples: int = 80) -> FactorTape:
    """Build one full-coverage raw-evidence block, without running any fit.

    Native clocks govern gaps/rate; common clocks govern pairing and physical
    time_root_s. The default half-cadence pairing tolerance is an explicit
    association policy, not fitted correlation evidence. Existing window
    weighting conservatively treats this new alignment status as non-peak.
    Samples without a partner or in short true spans remain in the immutable
    source and are counted as not usable by this pair; they are never filled.
    """
    started = time.perf_counter()
    if not isinstance(max_pair_offset_ns, int) or not 0 <= max_pair_offset_ns <= 2_500_000:
        raise ValueError("pair tolerance must be integer ns within half native cadence")
    if not isinstance(minimum_span_samples, int) or minimum_span_samples < 80:
        raise ValueError("existing calibration owner requires spans of at least 80 rows")
    frontend = Path(frontend)
    nodes = {node: _load_node(frontend / f"{node}.npz") for node in NODE_TO_SEGMENT}
    spans, windows, pair_audit = [], [], {}
    for edge in EDGES:
        pn, cn = SEGMENT_TO_NODE[edge.parent], SEGMENT_TO_NODE[edge.child]
        parent, child = nodes[pn], nodes[cn]
        pi, ci = _associate(parent, child, max_pair_offset_ns)
        pieces = _span_indices(parent, child, pi, ci, minimum_span_samples)
        correction_rows = 0
        for span_index, (p, c) in enumerate(pieces):
            # Separate estimator input: never mutate the raw SI acceleration.
            pa, ca = parent["acc_mps2"][p].copy(), child["acc_mps2"][c].copy()
            bias_rows = None
            for node, source, indices, acceleration in ((pn, parent, p, pa), (cn, child, c, ca)):
                if node == PELVIS and pelvis_bias is not None:
                    correction, bias_rows = pelvis_bias.at(source["common_global_ns"][indices])
                    acceleration -= correction
                    correction_rows += int(np.count_nonzero(bias_rows >= 0))
            offset_ns = child["common_global_ns"][c] - parent["common_global_ns"][p]
            timing = {"source": "CURRENT_COMMON_CLOCK_ORIGINAL_NATIVE_ROWS",
                      "parent_node": pn, "child_node": cn,
                      "max_absolute_pair_offset_ns": int(np.max(np.abs(offset_ns))),
                      "native_timer_step_us": 5000, "rows_cross_gap": False,
                      "effective_pelvis_bias_revision_indices": None if bias_rows is None else _readonly(bias_rows)}
            span = AlignedSpan(
                0, FULL_SESSION, edge.name, span_index, edge.parent, edge.child,
                _readonly(p), _readonly(c),
                _readonly(parent["common_global_ns"][p].astype(float) * 1e-9),
                _readonly(parent["time_us"][p].astype(float) * 1e-6),
                _readonly(child["time_us"][c].astype(float) * 1e-6),
                _readonly(pa), _readonly(ca), _readonly(parent["gyro_rads"][p]),
                _readonly(child["gyro_rads"][c]),
                _readonly(parent["quat_vqf_sensor_wxyz"][p]),
                _readonly(child["quat_vqf_sensor_wxyz"][c]),
                float(np.max(np.abs(offset_ns))) * 1e-9,
                "COMMON_CLOCK_NATIVE_PAIRING_NOT_CORRELATION_PEAK", timing)
            spans.append(span)
            windows.extend(windows_for_span(span))
        used = sum(len(p) for p, _c in pieces)
        pair_audit[edge.name] = {"paired_rows": len(pi), "factor_rows": used,
                                "parent_rows_not_in_factors": len(parent["time_us"]) - used,
                                "child_rows_not_in_factors": len(child["time_us"]) - used,
                                "span_count": len(pieces), "past_bias_rows": correction_rows}
    manifest = frontend / "RESULT.json"
    regions = json.loads(manifest.read_text()).get("regions", []) if manifest.exists() else []
    audit = {"source": str(frontend.resolve()), "source_sha256": {
        node: sha256(frontend / f"{node}.npz") for node in nodes},
        "source_rows": {node: len(data["time_us"]) for node, data in nodes.items()},
        "source_global_coverage_ns": {node: [int(data["common_global_ns"][0]),
                                             int(data["common_global_ns"][-1])]
                                      for node, data in nodes.items()},
        "regions_qa_only": regions, "action_label_factor_routing": False,
        "inter_action_rows_excluded": False, "no_gap_interpolation": True,
        "raw_acceleration_mutated": False, "orientation_or_gyro_modified": False,
        "parameter_fit_executed": False, "pairing_max_offset_ns": max_pair_offset_ns,
        "pairs": pair_audit, "bias": {
            "source": None if pelvis_bias is None else pelvis_bias.source,
            "join_clock": "LAST_AVAILABLE_GLOBAL_NS_LE_SAMPLE_GLOBAL_NS",
            "before_first_available_revision": "ZERO_CORRECTION_NO_BACKFILL",
            "between_revisions": "HOLD_LAST_EFFECTIVE_CORRECTION_NO_INTERPOLATION",
            "scope": "PELVIS_SENSOR_EFFECTIVE_RESIDUAL_NOT_INTRINSIC_CALIBRATION",
            "replicated_to_other_nodes": False, "future_or_interpolated_bias": False,
            "may_absorb_attitude_or_geometry_error": True}}
    block = EpisodeFactorBlock(0, FULL_SESSION, tuple(spans), tuple(windows), (), (), ())
    return FactorTape("biospur-c2-continuous-calibration-inputs-v1",
                      time.perf_counter() - started, (block,), audit,
                      {"input_owner": "CURRENT_FULL_CONTINUOUS_RAW_WITH_OPTIONAL_PAST_PELVIS_EFFECTIVE_BIAS",
                       "parameter_fit_executed": False})
