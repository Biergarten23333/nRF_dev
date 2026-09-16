#!/usr/bin/env python3
"""Bounded retrospective all-session calibration A/B; benchmark is default.

No online-causality or intrinsic-bias claim. A uses the current continuous
frontend; B changes only pelvis estimator acceleration using previous root
sensor-frame effective ba. Raw input files and old accepted fits stay immutable.
The --run-full switch must only be used after reviewing the benchmark projection.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict, fields
import hashlib
from importlib.metadata import version
import json
from pathlib import Path
import resource
import time

import numpy as np

from biospur_fusion.c2_coupled_progressive import estimator
from biospur_fusion.c2_coupled_progressive.continuous_calibration_inputs import (
    EffectivePelvisBiasHistory, build_continuous_factor_tape,
)
from biospur_fusion.c2_coupled_progressive.contracts import EDGES, sha256


ARRAY_INPUTS = ("time_root_s", "parent_acc_mps2", "child_acc_mps2",
                "parent_gyro_rads", "child_gyro_rads", "parent_quat_wxyz", "child_quat_wxyz")
ROOT = Path(__file__).resolve().parents[1]


def _write_new(path: Path, document):
    payload = json.dumps(estimator.jsonable(document), indent=2, allow_nan=False) + "\n"
    with path.open("x", encoding="utf-8") as stream:
        stream.write(payload)


def factor_input_identity(kind, span, windows):
    """Exact numeric input identity plus numerical owner/version binding."""
    if kind not in {"center", "hinge"}:
        raise ValueError("unknown factor kind")
    digest = hashlib.sha256()
    header = {"kind": kind, "edge": span.edge, "parent": span.parent_segment,
              "child": span.child_segment, "owner_sha256": sha256(Path(estimator.__file__)),
              "math_sha256": sha256(Path(estimator.__file__).with_name("math_utils.py")),
              "versions": {name: version(name) for name in ("numpy", "scipy", "qmt")},
              "windows": [asdict(window) for window in windows]}
    digest.update(json.dumps(header, sort_keys=True, separators=(",", ":")).encode())
    for name in ARRAY_INPUTS:
        values = np.ascontiguousarray(getattr(span, name))
        digest.update(json.dumps([name, values.dtype.str, list(values.shape)]).encode())
        digest.update(values.view(np.uint8))
    return digest.hexdigest()


def cumulative_edge(tape, edge_name):
    evidence = []
    for block in tape.episodes:
        lookup = {}
        for window in block.windows:
            lookup.setdefault((window.edge, window.span_index), []).append(window)
        for span in block.spans:
            if span.edge == edge_name:
                evidence.append((span, lookup.get((span.edge, span.span_index), ())))
    return estimator._cumulative_edge_span(evidence, prefix_index=0)


def _decode_factor(kind, row):
    cls = estimator.CenterFactor if kind == "center" else estimator.HingeAxisFactor
    result = {}
    for field in fields(cls):
        value = row[field.name]
        if isinstance(value, list):
            value = np.asarray(value, dtype=float)
        elif isinstance(value, str) and value in ("Infinity", "-Infinity", "NaN"):
            value = float(value)
        result[field.name] = value
    return cls(**result)


def fit_or_reuse(kind, span, windows, cache):
    key = factor_input_identity(kind, span, windows)
    path = cache / f"{kind}_{span.edge}_{key}.json"
    if path.exists():
        saved = json.loads(path.read_text())
        if saved["input_identity"] != key or saved["kind"] != kind:
            raise ValueError("factor cache identity mismatch")
        return _decode_factor(kind, saved["factor"]), key, True
    factor = (estimator.center_factor if kind == "center" else estimator.hinge_axis_factor)(span, windows)
    _write_new(path, {"input_identity": key, "kind": kind, "factor": asdict(factor)})
    return factor, key, False


def parameter_document(state):
    """Explicit numeric parameter shapes, separate from observation/status QA."""
    return {"mounts": {name: {"sensor_from_segment": row.sensor_from_segment_mean,
                              "covariance_diagonal_rad2": row.covariance_rad2,
                              "information": row.information, "rank": row.rank,
                              "evidence": row.evidence} for name, row in state.mounts.items()},
            "hinges": {name: {"parent_axis_sensor": row.axes()[0],
                              "child_axis_sensor": row.axes()[1], "information": row.information}
                       for name, row in state.hinges.items()},
            "centers": {name: {"parent_sensor_m": row.solve()[0],
                               "child_sensor_m": row.solve()[1], "rank": row.solve()[2],
                               "covariance_diagonal_m2": row.solve()[3]}
                        for name, row in state.centers.items()},
            "heading_streams_fitted": False, "pelvis_and_torso_mounts_remain_broad_prior": True}


def load_root_bias(path):
    with np.load(path, allow_pickle=False) as source:
        times = np.asarray(source["time_s"], float)
        state = np.asarray(source["root_state_b"], float)
    if state.shape != (len(times), 9) or not np.isfinite(times).all() or not np.isfinite(state).all():
        raise ValueError("expected finite global time_s and root_state_b[N,9]")
    return EffectivePelvisBiasHistory(np.rint(times * 1e9).astype(np.int64), state[:, 6:9],
                                     f"{path.resolve()}#root_state_b[:,6:9];sha256={sha256(path)}")


def _apply(state, kind, factor):
    if kind == "center":
        state.centers[factor.edge].add(factor)
    else:
        state.hinges[factor.edge].add(factor)
    state.refresh_mounts_and_branches()


def compare_parameters(a, b):
    return {"mount_rotation_change_deg": {
        name: float(np.degrees(np.arccos(np.clip((np.trace(
            np.asarray(b["mounts"][name]["sensor_from_segment"])
            @ np.asarray(row["sensor_from_segment"]).T) - 1) / 2, -1, 1))))
        for name, row in a["mounts"].items()},
        "center_parameter_change_m": {name: {
            side: float(np.linalg.norm(np.asarray(b["centers"][name][side]) - np.asarray(row[side])))
            for side in ("parent_sensor_m", "child_sensor_m")}
            for name, row in a["centers"].items()},
        "hinge_axis_line_change_deg": {name: {
            side: float(np.degrees(np.arccos(np.clip(abs(np.dot(
                b["hinges"][name][side], row[side])), 0, 1))))
            for side in ("parent_axis_sensor", "child_axis_sensor")}
            for name, row in a["hinges"].items()}}


def run(frontend, output, *, root_result=None, run_full=False, benchmark_edge="hip_left"):
    output, frontend = Path(output).resolve(), Path(frontend).resolve()
    if ROOT not in output.parents:
        raise ValueError("output must be below Fusion_Part")
    # The command should also be launched with an external timeout for benchmark.
    resource.setrlimit(resource.RLIMIT_AS, (2 << 30, 2 << 30))
    output.mkdir(parents=True, exist_ok=True)
    cache = output / "factors"
    cache.mkdir(exist_ok=True)
    started = time.perf_counter()
    tape_a = build_continuous_factor_tape(frontend)
    build_wall = time.perf_counter() - started
    binding = {"frontend": str(frontend), "source_sha256": tape_a.alignment_audit["source_sha256"],
               "estimator_sha256": sha256(Path(estimator.__file__))}
    binding_path = output / "INPUT_BINDING.json"
    if binding_path.exists():
        if json.loads(binding_path.read_text()) != binding:
            raise ValueError("output directory is bound to different immutable inputs/owner")
    else:
        _write_new(binding_path, binding)
    print(json.dumps({"stage": "TAPE_A_READY", "wall_s": build_wall}), flush=True)
    if not run_full:
        before = time.perf_counter()
        span, windows = cumulative_edge(tape_a, benchmark_edge)
        factor, identity, reused = fit_or_reuse("center", span, windows, cache)
        wall = time.perf_counter() - before
        state = estimator.prior_state()
        _apply(state, "center", factor)
        report = {"role": "BENCHMARK_ONLY_ONE_WHOLE_SESSION_CENTER_FACTOR",
                  "build_tape_wall_s": build_wall, "center_total_wall_s": wall,
                  "factor_internal_wall_s": factor.wall_s, "rows_in_full_span": len(span.time_root_s),
                  "factor_selected_rows": factor.rows, "center_status": factor.status,
                  "center_residual_rms_mps2": factor.residual_rms_mps2,
                  "input_identity": identity, "cache_reused": reused,
                  "remaining_full_ab_fit_count": "9 A centers +4 A hinges +3 changed B centers; exact identity decides reuse",
                  "center_only_projection_s": 12 * wall,
                  "hinge_runtime_unmeasured_do_not_assume_equal_to_center": True,
                  "full_fit_requires_parent_go": True,
                  "maximum_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
                  "parameter_checkpoint": parameter_document(state)}
        _write_new(output / "BENCHMARK.json", report)
        print(json.dumps(estimator.jsonable(report)), flush=True)
        return report
    if root_result is None:
        raise ValueError("full A/B calibration requires the B root effective-bias history")
    bias = load_root_bias(Path(root_result))
    tape_b = build_continuous_factor_tape(frontend, pelvis_bias=bias)
    if tape_b.alignment_audit["source_sha256"] != tape_a.alignment_audit["source_sha256"]:
        raise ValueError("immutable frontend changed between A and B input construction")
    states, provenance = {}, {}
    for side, tape in (("A", tape_a), ("B", tape_b)):
        state = estimator.prior_state()
        side_rows = []
        for edge in EDGES:
            span, windows = cumulative_edge(tape, edge.name)
            for kind in (("hinge", "center") if edge.joint_kind == "hinge" else ("center",)):
                before = time.perf_counter()
                factor, identity, reused = fit_or_reuse(kind, span, windows, cache)
                _apply(state, kind, factor)
                side_rows.append({"kind": kind, "edge": edge.name, "input_identity": identity,
                                  "cache_reused": reused, "status": factor.status,
                                  "wall_s": time.perf_counter() - before,
                                  "information": factor.information,
                                  "residual_rms_mps2": factor.residual_rms_mps2 if kind == "center" else None,
                                  "rank": factor.rank if kind == "center" else None,
                                  "parent_vector_norm_m": float(np.linalg.norm(factor.parent_vector_sensor_m)) if kind == "center" else None,
                                  "child_vector_norm_m": float(np.linalg.norm(factor.child_vector_sensor_m)) if kind == "center" else None})
                checkpoint = {"role": "RETROSPECTIVE_PARTIAL_PARAMETER_FIT_NOT_ONLINE",
                              "complete": False, "completed_factors": side_rows,
                              "parameters": parameter_document(state)}
                _write_new(output / f"{side}_checkpoint_{len(side_rows):02d}.json", checkpoint)
                print(json.dumps(dict(side=side, **side_rows[-1])), flush=True)
        states[side] = parameter_document(state)
        provenance[side] = side_rows
    report = {"role": "RETROSPECTIVE_FULL_SESSION_CALIBRATION_A_B_NOT_CAUSAL_ONLINE",
              "all_continuous_source_rows_available_including_inter_action_motion": True,
              "alignment_audit_A": tape_a.alignment_audit, "alignment_audit_B": tape_b.alignment_audit,
              "bias_is_effective_residual_not_intrinsic_sensor_calibration": True,
              "bias_may_absorb_attitude_proxy_or_systematic_errors": True,
              "do_not_subtract_again_in_root_propagator_with_active_ba": True,
              "pose_publication_or_heading_update_performed": False,
              "owner_factor_PASS_is_not_scientific_acceptance": True,
              "inherited_numerical_assumptions": {
                  "center_derivatives": "21-tap filtering with 12-row original-span boundary guards",
                  "center_objective": "existing deterministic cap of 240 samples over full retained evidence",
                  "Olsson_selection": "existing internal sample selection on concatenated raw rows; no external span-window argument"},
              "parameter_comparison": compare_parameters(states["A"], states["B"]),
              "parameters": states, "factors": provenance,
              "wall_s": time.perf_counter() - started,
              "maximum_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss}
    _write_new(output / "RESULT.json", report)
    if sum(p.stat().st_size for p in output.rglob("*") if p.is_file()) > 300 * 1024**2:
        raise RuntimeError("output exceeds 300 MB budget")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--frontend", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--root-result", type=Path)
    parser.add_argument("--run-full", action="store_true")
    parser.add_argument("--benchmark-edge", choices=[e.name for e in EDGES], default="hip_left")
    args = parser.parse_args()
    run(args.frontend, args.output, root_result=args.root_result, run_full=args.run_full,
        benchmark_edge=args.benchmark_edge)
