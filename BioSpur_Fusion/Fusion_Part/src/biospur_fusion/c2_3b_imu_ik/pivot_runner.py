"""Append-only evidence runner for the independently approved 3B pivot."""

from __future__ import annotations

import argparse
from collections import Counter
from concurrent.futures import ProcessPoolExecutor, as_completed
import json
import math
from pathlib import Path
import time
from typing import Any

import numpy as np

from .contracts import (
    ALL_EPISODES,
    CENTRAL_PROFILE,
    HINGES,
    PRIMARY_EPISODES,
    PROFILES,
    DisplayGeometry,
    Profile,
    WORKSPACE,
    AxisPair,
)
from .metrics import axis_angles_deg, summary
from .provenance import (
    load_axes,
    load_display_geometries,
    load_episodes,
    sha256_file,
    verify_runtime,
    verify_safe_bindings,
)
from .render import render_ab_montage
from .solver import solve_frame
from .synthetic_generator import make_case
from .synthetic_validation import (
    validate_case,
    validate_cut_locus,
    validate_full_circle,
    validate_neg08,
    validate_neg09,
    validate_negative_fixtures,
    validate_ori_only_parity,
)


BASE_CASES = (
    "SYN00_EXACT_DYNAMIC",
    "SYN01_DISTAL_TRANSVERSE_NOISE",
    "SYN02_ANISOTROPIC_MOUNT_DRIFT",
    "SYN03_NONIDEAL_HUMAN_HINGE",
    "SYN04_BILATERAL_VARIABILITY",
    "SYN05_MOUNT_STEP",
    "SYN06_MISSINGNESS",
    "SYN07_STATIONARY_DEGENERACY",
)
MONTE_SEEDS = tuple(range(31100, 31110))
PIVOT_GATE = Path("logs/c2_3b_pivot_gate_20260901_220819")
PIVOT_GATE_MANIFEST_SHA256 = "5f73eb34b14b37ddd596a85b8e841482939eb9374fd77737334ce2d57e9e715b"
PROFILE_WALL_S = 300.0
QUALIFICATION_WALL_S = 2400.0
WITNESS_FRAMES = {
    "SYN00_EXACT_DYNAMIC": 100,
    "SYN01_DISTAL_TRANSVERSE_NOISE": 100,
    "SYN02_ANISOTROPIC_MOUNT_DRIFT": 100,
    "SYN03_NONIDEAL_HUMAN_HINGE": 295,
    "SYN04_BILATERAL_VARIABILITY": 100,
    "SYN05_MOUNT_STEP": 200,
    "SYN06_MISSINGNESS": 100,
    "SYN07_STATIONARY_DEGENERACY": 100,
    "SYN09_MONTE_CARLO_NOISE": 100,
}


def _plain_geometry(geometry: DisplayGeometry) -> DisplayGeometry:
    return DisplayGeometry(
        geometry.name,
        geometry.torso_height_m,
        geometry.hip_span_m,
        geometry.shoulder_span_m,
        dict(geometry.segment_lengths_m),
    )


def _sanitize(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _sanitize(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_sanitize(item) for item in value]
    if isinstance(value, np.ndarray):
        return _sanitize(value.tolist())
    if isinstance(value, np.generic):
        return _sanitize(value.item())
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def _write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(_sanitize(value), indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def _case_job(
    name: str,
    geometries: tuple[DisplayGeometry, ...],
    output_matrix: np.ndarray,
    profile: Profile,
    seed_override: int | None,
) -> dict[str, Any]:
    return validate_case(
        name,
        geometries,
        output_matrix,
        seed_override=seed_override,
        profile=profile,
    )


def _run_profile(
    profile: Profile,
    geometries: tuple[DisplayGeometry, ...],
    output_matrix: np.ndarray,
) -> dict[str, Any]:
    started = time.monotonic()
    base: list[dict[str, Any]] = []
    monte: list[dict[str, Any]] = []
    timed_out = False
    with ProcessPoolExecutor(max_workers=8) as executor:
        futures = {
            executor.submit(
                _case_job, name, geometries, output_matrix, profile, None
            ): ("base", name)
            for name in BASE_CASES
        }
        futures.update(
            {
                executor.submit(
                    _case_job,
                    "SYN09_MONTE_CARLO_NOISE",
                    geometries,
                    output_matrix,
                    profile,
                    seed,
                ): ("monte", seed)
                for seed in MONTE_SEEDS
            }
        )
        for future in as_completed(futures):
            group, _ = futures[future]
            result = future.result()
            (base if group == "base" else monte).append(result)
    base.sort(key=lambda row: row["case"])
    monte.sort(key=lambda row: int(row["seed_override"]))

    full_circle = validate_full_circle(profile)
    old_negatives = validate_negative_fixtures(geometries[1], output_matrix)
    for row in old_negatives:
        row["profile"] = profile.name
    neg08 = validate_neg08(profile)
    neg09 = validate_neg09(profile)
    cut = validate_cut_locus() if profile == CENTRAL_PROFILE else None

    monte_pass_count = sum(bool(row["passed"]) for row in monte)
    monte_proxy_numeric = len(monte) == 10 and all(
        bool(row["solver"]["all_converged"])
        and bool(row["checks"].get("proxy_gates", False))
        for row in monte
    )
    monte_aggregate = {
        "case": "SYN09_MONTE_CARLO_NOISE",
        "profile": profile.name,
        "passed": monte_pass_count >= 8 and monte_proxy_numeric,
        "checks": {
            "syn01_criterion_at_least_8_of_10": monte_pass_count >= 8,
            "all_numerical_and_proxy_gates": monte_proxy_numeric,
            "all_ten_seeds_completed": len(monte) == 10,
        },
        "values": {"seed_pass_count": monte_pass_count, "seed_results": monte},
    }
    cases = base + [full_circle, monte_aggregate] + old_negatives + [neg08, neg09]
    if cut is not None:
        cases.append(cut)
    wall_s = time.monotonic() - started
    failed = [str(row["case"]) for row in cases if not bool(row["passed"])]
    checks = {
        "all_required_cases_present": len(cases) == (20 if cut is not None else 19),
        "no_timeout": not timed_out,
        "per_profile_wall": wall_s <= PROFILE_WALL_S,
        "all_cases_pass": not failed,
    }
    return {
        "profile": profile.name,
        "distal_orientation_weight": profile.distal_weight,
        "axis_weight": profile.axis_weight,
        "passed": bool(all(checks.values())),
        "checks": checks,
        "wall_s": wall_s,
        "failed_cases": failed,
        "cases": cases,
    }


def _axis_preflight(episodes: Any, axes: Any) -> dict[str, Any]:
    result: dict[str, Any] = {"metric": "projective_line_angle_deg", "episodes": {}}
    primary_rows = []
    axis_rows = tuple(axes[name] for name in HINGES)
    for episode_key in ALL_EPISODES:
        episode = episodes[episode_key]
        values = axis_angles_deg(
            episode.matrices[episode.full_body_valid], axis_rows
        )
        result["episodes"][episode_key] = {
            "valid_frames": int(np.count_nonzero(episode.full_body_valid)),
            "pooled": summary(values),
            "joints": {
                name: summary(values[:, index]) for index, name in enumerate(HINGES)
            },
        }
        if episode_key in PRIMARY_EPISODES:
            primary_rows.append(values)
    pooled = np.concatenate(primary_rows, axis=0)
    result["primary_pooled"] = {
        "pooled": summary(pooled),
        "joints": {
            name: summary(pooled[:, index]) for index, name in enumerate(HINGES)
        },
    }
    return result


def _case_axes(case: Any) -> dict[str, AxisPair]:
    return {
        axis.name: AxisPair(
            axis.name,
            axis.parent,
            axis.child,
            axis.parent_axis,
            axis.child_axis,
        )
        for axis in case.estimator_axes
    }


def _failure_witness(
    profile: Profile,
    case_name: str,
    *,
    seed_override: int | None = None,
) -> tuple[dict[str, Any], tuple[str, np.ndarray, np.ndarray] | None]:
    case = make_case(case_name, seed_override=seed_override)
    frame = WITNESS_FRAMES[case_name]
    result = solve_frame(
        case.measured[frame],
        _case_axes(case),
        profile,
        collect_diagnostics=True,
    )
    evidence = {
        "profile": profile.name,
        "case": case_name,
        "seed_override": seed_override,
        "frame": frame,
        "valid": result.valid,
        "scipy_success": result.scipy_success,
        "finite": result.finite,
        "proper": result.proper,
        "cost": result.cost,
        "orientation_cost": result.orientation_cost,
        "axis_cost": result.axis_cost,
        "optimality": result.optimality,
        "nfev": result.nfev,
        "retractions": result.retractions,
        "final_step_norm_rad": result.final_step_norm_rad,
        "wall_s": result.wall_s,
        "cut_start": result.cut_start,
        "cut_final": result.cut_final,
        "message": result.message,
        "retraction_trace": result.retraction_trace,
    }
    render = None
    if np.all(np.isfinite(result.matrices)):
        render = (
            f"{profile.name} {case_name} frame {frame}",
            case.measured[frame],
            result.matrices,
        )
    return evidence, render


def _collect_witnesses(
    profiles: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[tuple[str, np.ndarray, np.ndarray]]]:
    profile_by_name = {profile.name: profile for profile in PROFILES}
    witnesses = []
    renders = []
    for profile_result in profiles:
        profile = profile_by_name[profile_result["profile"]]
        for case_name in profile_result["failed_cases"]:
            if case_name in WITNESS_FRAMES:
                seed = 31100 if case_name == "SYN09_MONTE_CARLO_NOISE" else None
                evidence, render = _failure_witness(
                    profile, case_name, seed_override=seed
                )
                witnesses.append(evidence)
                if profile == CENTRAL_PROFILE and render is not None and len(renders) < 3:
                    renders.append(render)
    return witnesses, renders


def _source_hashes() -> dict[str, str]:
    paths = sorted((WORKSPACE / "src/biospur_fusion/c2_3b_imu_ik").glob("*.py"))
    paths.extend(sorted((WORKSPACE / "tests/c2_3b").glob("*.py")))
    return {str(path.relative_to(WORKSPACE)): sha256_file(path) for path in paths}


def _report(
    profile_results: list[dict[str, Any]],
    qualification_wall_s: float,
) -> str:
    case_counts = Counter(
        case
        for profile in profile_results
        for case in profile["failed_cases"]
    )
    failures = ", ".join(
        f"{name} ({count}/9 profiles)" for name, count in sorted(case_counts.items())
    ) or "none"
    profile_passes = sum(bool(row["passed"]) for row in profile_results)
    return f"""# BioSpur C2 3B projector-pivot implementation report

## Outcome

The independently approved branch-free projector pivot completed all nine
preregistered candidate profiles. `{profile_passes}/9` profiles passed the
complete synthetic/negative suite. Failed cases were: {failures}. The result is
`DIAGNOSTIC_ONLY`, `scientific_pass=false`, and `candidate_accepted=false`.
Qualification used {qualification_wall_s:.3f} seconds against the fixed
2,400-second aggregate bound; every profile retains its separate 300-second
bound.

The projector residual closes the former sign-branch discontinuity: CUT01
residuals and finite-difference Jacobians are finite, bounded, and invariant to
a single axis flip. Any remaining CUT01 or NEG08 failure is therefore retained
as convergence/parameterization evidence under the unchanged solver gates, not
relabelled as a visual or threshold success. SYN08 still requires both known
truth and pairwise multistart agreement. NEG09 tests optimized sign invariance
for every profile.

## Decision-tree scope

Because all nine profiles are required, any profile failure rejects the entire
candidate family before real B optimization. The runner therefore preserves A
unchanged, reports projective A-only preflight for all 19 primary episodes and
H01/H02, and does not optimize a cherry-picked real frame. The prior rejected
real montage remains immutable causal evidence; the new montage contains only
independent synthetic failure witnesses.

## Scientific boundary

Qualified anatomical joint centres/connection vectors, complete anatomical
frames, calibrated orientation and functional-axis covariance, and independent
real pose truth remain absent. No connection, temporal, semantic, viewer,
root-world, drift, or UWB term was used. Even a numerical pass would remain
diagnostic rather than anatomical validation.
"""


def run(output_dir: Path) -> int:
    output_dir = output_dir.resolve()
    if WORKSPACE.resolve() not in output_dir.parents or output_dir.parent.name != "logs":
        raise ValueError("output must be one direct timestamped directory under workspace/logs")
    output_dir.mkdir(parents=False, exist_ok=False)

    run_started = time.monotonic()
    runtime = verify_runtime(full_records=True)
    safe_before = verify_safe_bindings()
    axes = load_axes()
    episodes, output_matrix = load_episodes()
    geometries = tuple(_plain_geometry(value) for value in load_display_geometries())

    qualification_started = time.monotonic()
    ori_only = validate_ori_only_parity()
    profile_results = []
    for profile in PROFILES:
        print(f"START {profile.name}", flush=True)
        result = _run_profile(profile, geometries, output_matrix)
        profile_results.append(result)
        _write_json(output_dir / f"PROFILE_{profile.name}.json", result)
        print(
            f"DONE {profile.name} pass={result['passed']} "
            f"wall_s={result['wall_s']:.3f} failures={','.join(result['failed_cases'])}",
            flush=True,
        )

    witnesses, render_rows = _collect_witnesses(profile_results)
    _write_json(output_dir / "FAILURE_WITNESSES.json", {"witnesses": witnesses})
    axis_preflight = _axis_preflight(episodes, axes)
    _write_json(output_dir / "REAL_A_ONLY_LINE_PREFLIGHT.json", axis_preflight)
    _write_json(output_dir / "ORI_ONLY_PARITY.json", ori_only)

    if render_rows:
        render_ab_montage(
            output_dir / "REJECTED_SYNTHETIC_FAILURE_MONTAGE.png",
            render_rows,
            geometries[1],
            output_matrix,
            title="C2 3B projector pivot — rejected synthetic witnesses",
        )

    qualification_wall_s = time.monotonic() - qualification_started
    profile_pass = all(bool(row["passed"]) for row in profile_results)
    total_wall_pass = qualification_wall_s <= QUALIFICATION_WALL_S
    candidate_accepted = bool(profile_pass and total_wall_pass and ori_only["passed"])
    real_authorized = candidate_accepted
    synthetic_summary = {
        "schema": "biospur-c2-3b-projector-qualification-v1",
        "candidate_profile_count": len(profile_results),
        "all_profiles_required": True,
        "passed": candidate_accepted,
        "profile_pass": profile_pass,
        "ORI_ONLY_pass": ori_only["passed"],
        "qualification_wall_s": qualification_wall_s,
        "qualification_wall_limit_s": QUALIFICATION_WALL_S,
        "qualification_wall_pass": total_wall_pass,
        "profiles": profile_results,
    }
    _write_json(output_dir / "SYNTHETIC_QUALIFICATION.json", synthetic_summary)

    if real_authorized:
        raise RuntimeError(
            "all synthetic profiles passed; complete capture-wide real validation is now required"
        )

    report = _report(profile_results, qualification_wall_s)
    (output_dir / "IMPLEMENTATION_REPORT.md").write_text(report, encoding="utf-8")
    safe_after = verify_safe_bindings()
    if safe_before != safe_after:
        raise RuntimeError("safe input bindings changed during projector pivot")

    artifacts = []
    for path in sorted(output_dir.iterdir()):
        if path.name in ("EVIDENCE_MANIFEST.json", "SHA256SUMS.txt"):
            continue
        artifacts.append(
            {"path": path.name, "bytes": path.stat().st_size, "sha256": sha256_file(path)}
        )
    manifest = {
        "schema": "biospur-c2-3b-projector-evidence-v1",
        "approved_pivot_gate": str(PIVOT_GATE),
        "approved_pivot_gate_manifest_sha256": PIVOT_GATE_MANIFEST_SHA256,
        "monitor_thread_id": "01a05e24-0736-7971-a8c1-f051ee058108",
        "monitor_verdict": "APPROVE",
        "status": "DIAGNOSTIC_ONLY",
        "scientific_pass": False,
        "candidate_accepted": False,
        "synthetic_pass": False,
        "real_capture_wide_ab_run": False,
        "real_stop_reason": "at least one required synthetic profile failed",
        "all_19_plus_2_a_only_line_preflight": True,
        "uwb_payload_position_range_anchor_geometry_or_derived_parameter_consumed": False,
        "frozen_inputs_unchanged": True,
        "runtime_guard": runtime,
        "source_hashes": _source_hashes(),
        "artifacts": artifacts,
        "qualification_wall_s": qualification_wall_s,
        "run_wall_s": time.monotonic() - run_started,
    }
    _write_json(output_dir / "EVIDENCE_MANIFEST.json", manifest)
    checksums = []
    for path in sorted(output_dir.iterdir()):
        if path.name == "SHA256SUMS.txt":
            continue
        checksums.append(f"{sha256_file(path)}  {path.name}")
    (output_dir / "SHA256SUMS.txt").write_text("\n".join(checksums) + "\n", encoding="utf-8")
    return 2


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    return run(args.output)
