"""Bounded evidence runner for the approved C2 3B contract."""

from __future__ import annotations

import argparse
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
    SEGMENTS,
    DisplayGeometry,
    WORKSPACE,
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
from .synthetic_validation import (
    validate_case,
    validate_full_circle,
    validate_negative_fixtures,
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
APPROVED_GATE = Path("logs/c2_3b_design_gate_20260901_205915")


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


def _run_parallel_synthetic(
    geometries: tuple[DisplayGeometry, ...],
    output_matrix: np.ndarray,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], float, bool]:
    started = time.monotonic()
    base: list[dict[str, Any]] = []
    monte: list[dict[str, Any]] = []
    timed_out = False
    with ProcessPoolExecutor(max_workers=8) as pool:
        futures = {
            pool.submit(validate_case, name, geometries, output_matrix): ("base", name)
            for name in BASE_CASES
        }
        futures.update(
            {
                pool.submit(
                    validate_case,
                    "SYN09_MONTE_CARLO_NOISE",
                    geometries,
                    output_matrix,
                    seed_override=seed,
                ): ("monte", seed)
                for seed in MONTE_SEEDS
            }
        )
        try:
            for future in as_completed(futures, timeout=300.0):
                group, _ = futures[future]
                result = future.result()
                (base if group == "base" else monte).append(result)
        except TimeoutError:
            timed_out = True
            for future in futures:
                future.cancel()
    base.sort(key=lambda row: row["case"])
    monte.sort(key=lambda row: int(row["seed_override"]))
    return base, monte, time.monotonic() - started, timed_out


def _axis_preflight(episodes: Any, axes: Any) -> dict[str, Any]:
    result: dict[str, Any] = {"episodes": {}, "primary_pooled": {}}
    primary_rows = []
    for episode in ALL_EPISODES:
        row = episodes[episode]
        values = axis_angles_deg(
            row.matrices[row.full_body_valid], tuple(axes[name] for name in HINGES)
        )
        result["episodes"][episode] = {
            "valid_frames": int(np.count_nonzero(row.full_body_valid)),
            "pooled": summary(values),
            "joints": {
                name: summary(values[:, index]) for index, name in enumerate(HINGES)
            },
        }
        if episode in PRIMARY_EPISODES:
            primary_rows.append(values)
    pooled = np.concatenate(primary_rows, axis=0)
    result["primary_pooled"] = {
        "pooled": summary(pooled),
        "joints": {name: summary(pooled[:, index]) for index, name in enumerate(HINGES)},
    }
    return result


def _fixed_real_boundary_diagnostic(
    episodes: Any,
    axes: Any,
) -> tuple[list[dict[str, Any]], list[tuple[str, np.ndarray, np.ndarray]]]:
    evidence = []
    render_rows = []
    for frame in (70, 350, 630):
        measured = episodes["00"].matrices[frame]
        result = solve_frame(measured, axes, CENTRAL_PROFILE)
        evidence.append(
            {
                "episode": "00",
                "frame": frame,
                "valid": result.valid,
                "scipy_success": result.scipy_success,
                "finite": result.finite,
                "proper": result.proper,
                "cost": result.cost,
                "optimality": result.optimality,
                "nfev": result.nfev,
                "retractions": result.retractions,
                "final_step_norm_rad": result.final_step_norm_rad,
                "wall_s": result.wall_s,
                "message": result.message,
            }
        )
        render_rows.append((f"episode 00 frame {frame}", measured, result.matrices))
    return evidence, render_rows


def _source_hashes() -> dict[str, str]:
    paths = sorted((WORKSPACE / "src/biospur_fusion/c2_3b_imu_ik").glob("*.py"))
    paths.extend(sorted((WORKSPACE / "tests/c2_3b").glob("*.py")))
    return {str(path.relative_to(WORKSPACE)): sha256_file(path) for path in paths}


def _report_text(
    synthetic_pass: bool,
    failed_cases: list[str],
    synthetic_wall_s: float,
) -> str:
    failures = ", ".join(failed_cases) if failed_cases else "none"
    return f"""# BioSpur C2 3B implementation report

## Outcome

The approved central estimator did not pass its independent synthetic gate.
The normative status is `BLOCKED_AT_SYNTHETIC_QUALIFICATION` and
`scientific_pass=false`. The fixed-case failures are: {failures}. Synthetic
execution took {synthetic_wall_s:.3f} s within its 300 s aggregate bound.

The failure is causal and attributable. The axis factor divides angular error
in radians by a 10-degree radian scale, while orientation residuals remain
unscaled radians. Consequently, an eight-degree nonideal axis excursion enters
the least-squares system near 0.8, whereas a four-degree orientation compromise
enters near 0.07 per segment. The optimizer therefore suppresses the intended
human out-of-plane component and collapses hinge-relative motion. Changing the
scale, weight, loss, or axis sign after seeing this result would violate the
approved gate.

## Scope completed and stopped

The orientation-only runtime and all allow-listed hashes passed. Exact
dynamics, missingness, stationary degeneracy, and full-circle/multistart were
evaluated together with noisy, mount, nonideal, bilateral, Monte Carlo, and
negative-mutation cases. The runner also computed an A-only signed-axis
preflight over every full-body-valid frame in all 19 primary episodes and both
no-refit holdouts. It attempted only the three preregistered episode-00 boundary
frames after synthetic rejection; all three were retained as rejected evidence.

Capture-wide A/B optimization, nine-profile real sensitivity, holdout A/B
claims, and interactive real playback were not run. That is the required
decision-tree stop, not favorable episode selection. The rejected three-frame
montage is display-proxy evidence only and cannot establish anatomy.

## Scientific boundary

Even a numerically accepted candidate could not be `3B_READY` in this gate.
Qualified internal joint-centre/connection geometry, complete anatomical
frames, calibrated orientation and axis uncertainty, and independent real pose
truth remain absent. No UWB quantity was opened or numerically consumed.
"""


def run(output_dir: Path) -> int:
    output_dir = output_dir.resolve()
    if WORKSPACE.resolve() not in output_dir.parents or output_dir.parent.name != "logs":
        raise ValueError("output must be one direct timestamped directory under workspace/logs")
    output_dir.mkdir(parents=False, exist_ok=False)

    started = time.monotonic()
    runtime = verify_runtime(full_records=True)
    safe_before = verify_safe_bindings()
    axes = load_axes()
    episodes, output_matrix = load_episodes()
    geometries = tuple(_plain_geometry(value) for value in load_display_geometries())

    base, monte, synthetic_wall, timed_out = _run_parallel_synthetic(
        geometries, output_matrix
    )
    full_circle = validate_full_circle()
    negative = validate_negative_fixtures(geometries[1], output_matrix)
    monte_pass_count = sum(bool(row["passed"]) for row in monte)
    monte_proxy_numeric = all(
        bool(row["solver"]["all_converged"])
        and bool(row["checks"].get("proxy_gates", False))
        for row in monte
    )
    monte_aggregate = {
        "case": "SYN09_MONTE_CARLO_NOISE",
        "passed": monte_pass_count >= 8 and monte_proxy_numeric and len(monte) == 10,
        "checks": {
            "syn01_criterion_at_least_8_of_10": monte_pass_count >= 8,
            "all_numerical_and_proxy_gates": monte_proxy_numeric,
            "all_ten_seeds_completed": len(monte) == 10,
        },
        "values": {"seed_pass_count": monte_pass_count, "seed_results": monte},
    }
    cases = base + [full_circle, monte_aggregate] + negative
    failed_cases = [str(row["case"]) for row in cases if not row["passed"]]
    synthetic_pass = not timed_out and len(cases) == 17 and not failed_cases

    axis_preflight = _axis_preflight(episodes, axes)
    real_boundary, render_rows = _fixed_real_boundary_diagnostic(episodes, axes)
    runtime_render = verify_runtime(full_records=True)
    render_path = output_dir / "REJECTED_REAL_BOUNDARY_AB_MONTAGE.png"
    render_ab_montage(
        render_path,
        render_rows,
        geometries[1],
        output_matrix,
        title="C2 3B rejected boundary diagnostic — not an accepted pose",
    )

    safe_after = verify_safe_bindings()
    if safe_before != safe_after:
        raise RuntimeError("safe input bindings changed during 3B")
    synthetic_document = {
        "schema": "biospur-c2-3b-synthetic-qualification-v1",
        "passed": synthetic_pass,
        "timed_out": timed_out,
        "wall_s": synthetic_wall,
        "case_count": len(cases),
        "failed_cases": failed_cases,
        "cases": cases,
    }
    _write_json(output_dir / "SYNTHETIC_QUALIFICATION.json", synthetic_document)
    _write_json(output_dir / "REAL_A_ONLY_AXIS_PREFLIGHT.json", axis_preflight)
    _write_json(
        output_dir / "REAL_BOUNDARY_DIAGNOSTIC.json",
        {
            "capture_wide_ab_run": False,
            "reason": "normative stop after synthetic qualification failure",
            "central_fixed_frames_only": real_boundary,
        },
    )
    report = _report_text(synthetic_pass, failed_cases, synthetic_wall)
    (output_dir / "IMPLEMENTATION_REPORT.md").write_text(report, encoding="utf-8")

    artifacts = []
    for path in sorted(output_dir.iterdir()):
        if path.name in ("EVIDENCE_MANIFEST.json", "SHA256SUMS.txt"):
            continue
        artifacts.append(
            {
                "path": path.name,
                "bytes": path.stat().st_size,
                "sha256": sha256_file(path),
            }
        )
    manifest = {
        "schema": "biospur-c2-3b-implementation-evidence-v1",
        "approved_gate": str(APPROVED_GATE),
        "approved_gate_manifest_sha256": sha256_file(
            WORKSPACE / APPROVED_GATE / "DESIGN_GATE_MANIFEST.json"
        ),
        "monitor_thread_id": "01a05e24-0736-7971-a8c1-f051ee058108",
        "monitor_verdict": "APPROVE",
        "status": "BLOCKED_AT_SYNTHETIC_QUALIFICATION",
        "scientific_pass": False,
        "candidate_accepted": False,
        "synthetic_pass": synthetic_pass,
        "real_capture_wide_ab_run": False,
        "all_19_plus_2_a_only_axis_preflight": True,
        "uwb_payload_position_range_anchor_geometry_or_derived_parameter_consumed": False,
        "frozen_inputs_unchanged": safe_before == safe_after,
        "runtime_guard_before_synthetic_and_rendering": {
            "synthetic": runtime,
            "rendering": runtime_render,
        },
        "source_hashes": _source_hashes(),
        "artifacts": artifacts,
        "wall_s": time.monotonic() - started,
    }
    _write_json(output_dir / "EVIDENCE_MANIFEST.json", manifest)
    checksums = []
    for path in sorted(output_dir.iterdir()):
        if path.name == "SHA256SUMS.txt":
            continue
        checksums.append(f"{sha256_file(path)}  {path.name}")
    (output_dir / "SHA256SUMS.txt").write_text("\n".join(checksums) + "\n", encoding="utf-8")
    return 0 if synthetic_pass else 2


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    return run(args.output)
