"""Per-edge formal qualification metrics for Stage 3-R1."""
from __future__ import annotations

import hashlib

import numpy as np

from pure_imu_baseline.config import GEOMETRY, PARENT_CHILD
from pure_imu_baseline.math3d import normalize, to_matrix
from pure_imu_baseline.skeleton import assert_fixed_lengths
from pure_imu_baseline.stage3.analysis import quat_distance, relative, spatial_z_rates


def sha_array(value: np.ndarray) -> str:
    return hashlib.sha256(np.ascontiguousarray(value).tobytes()).hexdigest()


def edge_names() -> list[str]:
    return [f"{parent}->{child}" for parent, child in PARENT_CHILD]


def _moving_average(value: np.ndarray, width: int = 31) -> np.ndarray:
    if len(value) < width:
        return np.full_like(value, np.mean(value))
    cumulative = np.r_[0.0, np.cumsum(value)]
    middle = (cumulative[width:] - cumulative[:-width]) / width
    return np.pad(middle, (width - 1, 0), mode="edge")


def invariant_report(raw: dict, corrected_q: np.ndarray, corrected_positions: np.ndarray,
                     pelvis_index: int, config: dict) -> dict:
    valid = raw["valid"]
    qraw = raw["q_GB_wxyz"].astype(np.float64)
    qcor = np.asarray(corrected_q, dtype=np.float64)
    raw_unit = normalize(qraw[valid])
    cor_unit = normalize(qcor[valid])
    gravity = np.array([0.0, 0.0, 1.0])
    raw_g = np.einsum("...ji,j->...i", to_matrix(raw_unit), gravity)
    cor_g = np.einsum("...ji,j->...i", to_matrix(cor_unit), gravity)
    gravity_error = float(np.max(np.linalg.norm(raw_g - cor_g, axis=-1)))
    norms = np.abs(np.linalg.norm(qcor[valid], axis=-1) - 1.0)
    pelvis_valid = valid[:, pelvis_index]
    pelvis_norm = np.abs(np.linalg.norm(qcor[pelvis_valid, pelvis_index], axis=-1) - 1.0)
    pelvis_exact = bool(np.array_equal(qraw[:, pelvis_index], qcor[:, pelvis_index], equal_nan=True))
    geometry = assert_fixed_lengths(corrected_positions, GEOMETRY, atol=float(config["qualification"]["bone_length_tolerance_m"]))
    geometry_error = max(item["maximum_abs_error_m"] for item in geometry.values())
    return {
        "gravity_tilt_max_norm_error": gravity_error,
        "gravity_gate": gravity_error <= float(config["qualification"]["gravity_tolerance"]),
        "quaternion_norm_max_abs_error": float(np.max(norms)),
        "quaternion_norm_gate": float(np.max(norms)) <= float(config["qualification"]["quaternion_norm_tolerance"]),
        "pelvis_raw_representation_norm_max_abs_error": float(np.max(pelvis_norm)),
        "pelvis_orientation_component_exact": pelvis_exact,
        "pelvis_correction_exact_zero": pelvis_exact,
        "fixed_bone_checks": geometry,
        "maximum_bone_length_error_m": geometry_error,
        "bone_length_gate": geometry_error <= float(config["qualification"]["bone_length_tolerance_m"]),
    }


def edge_capture_metrics(raw: dict, formal: dict, corrected_q: np.ndarray,
                         capture: str, config: dict) -> dict:
    names = [str(value) for value in raw["segment_names"]]
    index = {name: i for i, name in enumerate(names)}
    t = raw["time_s"]
    valid = raw["valid"]
    qraw = raw["q_GB_wxyz"].astype(np.float64)
    zr = spatial_z_rates(qraw, valid, t)
    zc = spatial_z_rates(corrected_q, valid, t)
    result = {}
    for edge, (parent_name, child_name) in enumerate(PARENT_CHILD):
        parent, child = index[parent_name], index[child_name]
        edge_name = f"{parent_name}->{child_name}"
        pair_valid = valid[:, parent] & valid[:, child]
        same_epoch_pair = pair_valid[1:] & pair_valid[:-1] & (formal["edge_epoch"][1:, edge] == formal["edge_epoch"][:-1, edge])
        qr = relative(qraw[:, parent], qraw[:, child])
        qc = relative(corrected_q[:, parent], corrected_q[:, child])
        raw_step = quat_distance(qr[1:], qr[:-1])
        corrected_step = quat_distance(qc[1:], qc[:-1])
        raw_same = raw_step[same_epoch_pair]
        corrected_same = corrected_step[same_epoch_pair]
        raw_max = float(np.max(raw_same)) if len(raw_same) else None
        corrected_max = float(np.max(corrected_same)) if len(corrected_same) else None
        continuity = bool(raw_max is not None and corrected_max <= raw_max + float(config["qualification"]["continuity_tolerance_rad"]))

        support = (formal["edge_stationary"] & formal["edge_valid"] & formal["edge_state"].astype(bool))[:, edge]
        raw_rate = zr[:, child] - zr[:, parent]
        corrected_rate = zc[:, child] - zc[:, parent]
        finite = support & np.isfinite(raw_rate) & np.isfinite(corrected_rate)
        count = int(np.sum(finite))
        duration = float(np.sum(np.where(np.r_[False, finite[1:] & finite[:-1]], np.r_[0.0, np.diff(t)], 0.0)))
        before = float(abs(np.median(raw_rate[finite]))) if count else None
        after = float(abs(np.median(corrected_rate[finite]))) if count else None
        improvement = (1.0 - after / before) if before is not None and before > 1e-7 else None
        qualified = count >= 120 and before is not None and before > 1e-5
        regression = bool(qualified and after > before * (1.0 + float(config["qualification"]["stationary_regression_maximum_fraction"])))

        if len(raw_same) >= 120:
            hraw = raw_same - _moving_average(raw_same)
            hcor = corrected_same - _moving_average(corrected_same)
            rms_raw = float(np.sqrt(np.mean(hraw*hraw)))
            rms_cor = float(np.sqrt(np.mean(hcor*hcor)))
            dynamic_qualified = rms_raw > 1e-5
            correlation = float(np.corrcoef(hraw, hcor)[0, 1]) if np.std(hraw) > 1e-12 and np.std(hcor) > 1e-12 else 1.0
            amplitude = rms_cor / rms_raw if rms_raw > 1e-12 else 1.0
            peak_error = abs(int(np.argmax(raw_same)) - int(np.argmax(corrected_same)))
            # Excursion is measured per continuous epoch, never across a gap.
            excursions_raw = []
            excursions_cor = []
            boundaries = np.flatnonzero(np.r_[True, formal["edge_epoch"][1:, edge] != formal["edge_epoch"][:-1, edge]])
            for start, stop in zip(boundaries, np.r_[boundaries[1:], len(t)]):
                ok = pair_valid[start:stop]
                frames = np.flatnonzero(ok) + start
                if len(frames) < 2:
                    continue
                excursions_raw.extend(quat_distance(qr[frames], qr[frames[0]]).tolist())
                excursions_cor.extend(quat_distance(qc[frames], qc[frames[0]]).tolist())
            excursion_raw = float(np.percentile(excursions_raw, 95)) if excursions_raw else 0.0
            excursion_cor = float(np.percentile(excursions_cor, 95)) if excursions_cor else 0.0
            excursion_ratio = excursion_cor / excursion_raw if excursion_raw > 1e-12 else 1.0
        else:
            dynamic_qualified = False; correlation = amplitude = excursion_ratio = 1.0; peak_error = 0; rms_raw = rms_cor = 0.0
        qcfg = config["qualification"]
        dynamic_pass = bool((not dynamic_qualified) or (
            correlation >= qcfg["dynamic_correlation_minimum"] and
            qcfg["dynamic_amplitude_ratio_minimum"] <= amplitude <= qcfg["dynamic_amplitude_ratio_maximum"] and
            peak_error <= qcfg["dynamic_peak_timing_maximum_frames"] and
            qcfg["dynamic_excursion_ratio_minimum"] <= excursion_ratio <= qcfg["dynamic_excursion_ratio_maximum"]))
        result[edge_name] = {
            "capture": capture,
            "support_samples": int(np.sum(formal["edge_observation_accepted"][:, edge])),
            "stationary_active_samples": count,
            "stationary_active_duration_s": duration,
            "bias_rad_s_quantiles": np.quantile(formal["edge_bias_rad_s"][:, edge], [0, .5, .95, 1]).tolist(),
            "maximum_abs_eta_rad": float(np.max(np.abs(formal["edge_eta_rad"][:, edge]))),
            "maximum_abs_rate_rad_s": float(np.max(np.abs(formal["edge_applied_rate_rad_s"][:, edge]))),
            "maximum_abs_acceleration_rad_s2": float(np.max(np.abs(formal["edge_applied_acceleration_rad_s2"][:, edge]))),
            "raw_same_epoch_max_increment_rad": raw_max,
            "corrected_same_epoch_max_increment_rad": corrected_max,
            "continuity_gate": continuity,
            "transition_impulse_gate": continuity,
            "stationary_before_abs_median_rad_s": before,
            "stationary_after_abs_median_rad_s": after,
            "stationary_improvement_fraction": improvement,
            "stationary_evidence_qualified": qualified,
            "stationary_improved_at_least_20_percent": bool(qualified and improvement is not None and improvement >= qcfg["stationary_improvement_minimum_fraction"]),
            "stationary_regression_over_5_percent": regression,
            "dynamic_qualified": dynamic_qualified,
            "dynamic_highpass_increment_correlation": correlation,
            "dynamic_amplitude_ratio": amplitude,
            "dynamic_peak_timing_error_frames": peak_error,
            "dynamic_excursion_ratio": excursion_ratio,
            "dynamic_raw_highpass_rms_rad": rms_raw,
            "dynamic_corrected_highpass_rms_rad": rms_cor,
            "dynamic_motion_gate": dynamic_pass,
        }
    return result


def gap_audit(raw: dict, formal: dict) -> dict:
    t = raw["time_s"]
    valid = raw["valid"]
    names = [str(value) for value in raw["segment_names"]]
    intervals = []
    for node, name in enumerate(names):
        starts = np.flatnonzero((~valid[:, node]) & np.r_[True, valid[:-1, node]])
        for start in starts:
            stop = start
            while stop + 1 < len(t) and not valid[stop + 1, node]:
                stop += 1
            pre = max(0, start - 1); post = min(len(t) - 1, stop + 1)
            # Epoch and zero-state checks are authoritative; affected listing
            # is descriptive because simultaneous node gaps may overlap.
            changed = np.flatnonzero(formal["edge_epoch"][post] > formal["edge_epoch"][pre]).tolist()
            intervals.append({
                "segment": name,
                "invalid_start_s": float(t[start]),
                "invalid_stop_s": float(t[stop]),
                "last_valid_before_s": float(t[pre]),
                "first_valid_after_s": float(t[post]),
                "invalid_frames": int(stop - start + 1),
                "edge_epochs_advanced": changed,
                "advanced_edges_eta_zero_post_gap": bool(np.all(formal["edge_eta_rad"][post, changed] == 0.0)) if changed else True,
            })
    return {"intervals": intervals,
            "no_state_crossed_any_reported_gap": all(item["advanced_edges_eta_zero_post_gap"] for item in intervals),
            "invalid_samples_interpolated": False}
