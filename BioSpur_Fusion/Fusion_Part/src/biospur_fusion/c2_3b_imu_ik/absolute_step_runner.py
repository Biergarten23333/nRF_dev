"""Append-only runner for the independently approved absolute-step pivot."""

from __future__ import annotations

import argparse
from collections import Counter
import math
from pathlib import Path
import time
from typing import Any

from .absolute_step_validation import validate_absolute_step_diagnostics
from .contracts import PROFILES, WORKSPACE
from .equivariance_validation import (
    validate_eqv04,
    validate_numerical_equivariance,
)
from .parent_local_runner import (
    IMMUTABLE_FINAL_CAUSAL,
    IMMUTABLE_FINAL_CAUSAL_MANIFEST_SHA256,
    IMMUTABLE_WORLD_RUN,
    IMMUTABLE_WORLD_RUN_MANIFEST_SHA256,
)
from .pivot_runner import (
    QUALIFICATION_WALL_S,
    _axis_preflight,
    _collect_witnesses,
    _plain_geometry,
    _run_profile,
    _source_hashes,
    _write_json,
)
from .provenance import (
    load_axes,
    load_display_geometries,
    load_episodes,
    sha256_file,
    verify_runtime,
    verify_safe_bindings,
)
from .render import render_ab_montage
from .synthetic_validation import validate_ori_only_parity


ABSOLUTE_STEP_GATE = Path("logs/c2_3b_absolute_step_gate_20260902_012230")
ABSOLUTE_STEP_GATE_MANIFEST_SHA256 = (
    "67f6778d25e7399096ef8b696e8b7ef7a43512231cc7b025d6f3d362b233e74a"
)
IMMUTABLE_PARENT_LOCAL_RUN = Path("logs/c2_3b_parent_local_20260902_003156")
IMMUTABLE_PARENT_LOCAL_RUN_MANIFEST_SHA256 = (
    "cc0a20b117b1e436fac8b724a8e97c3d36f2c5c26576183178dd52c04935cbbf"
)
IMMUTABLE_PARENT_LOCAL_CAUSAL = Path(
    "logs/c2_3b_parent_local_causal_20260902_011145"
)
IMMUTABLE_PARENT_LOCAL_CAUSAL_MANIFEST_SHA256 = (
    "1f7aa1a0ef9e0c978c093634e92ede526d2e47b312ea45484efe7400f2bdb414"
)


def _report(
    profile_results: list[dict[str, Any]],
    absolute_diagnostics: dict[str, Any],
    numerical: dict[str, Any],
    eqv04: dict[str, Any],
    ori_only: dict[str, Any],
    qualification_wall_s: float,
    candidate_accepted: bool,
) -> str:
    case_counts = Counter(
        case for profile in profile_results for case in profile["failed_cases"]
    )
    failures = ", ".join(
        f"{name} ({count}/9 profiles)" for name, count in sorted(case_counts.items())
    ) or "none"
    profile_passes = sum(bool(row["passed"]) for row in profile_results)
    abs_summary = ", ".join(
        f"{row['case']}={'PASS' if row['passed'] else 'FAIL'}"
        for row in absolute_diagnostics["gates"]
    )
    eqv_summary = ", ".join(
        f"{row['case']}={'PASS' if row['passed'] else 'FAIL'}"
        for row in numerical["gates"]
    )
    return f"""# BioSpur C2 3B absolute-step implementation report

## Outcome

The independently approved fixed-absolute Jacobian candidate completed the
entire preregistered matrix without profile selection. `{profile_passes}/9`
profiles passed the unchanged full synthetic/negative suite. Failed cases
were: {failures}. Candidate acceptance is
`{str(candidate_accepted).lower()}`. Maximum status remains
`DIAGNOSTIC_ONLY`, `scientific_pass=false`.

The corrected aggregate interval was {qualification_wall_s:.6f} seconds
against 2,400 seconds; every profile retained its independent 300-second wall.
ORI_ONLY was `{'PASS' if ori_only['passed'] else 'FAIL'}`. Absolute diagnostics
were: {abs_summary}. Inherited/corrected numerical gates were: {eqv_summary};
EQV04 was `{'PASS' if eqv04['passed'] else 'FAIL'}`.

## Causal scope

The public candidate changed only Jacobian perturbation ownership from SciPy's
heterogeneous relative steps to an explicit complete-residual central callable
with fixed `h=1e-6 rad`. The parent-local residual, scalar objective, state,
retraction, weights, starts, masks, biological gates, convergence thresholds,
and walls were unchanged. ABS03 and ABS04 are causal comparisons only and do
not substitute for any complete-suite failure.

The rejected world-coordinate and parent-local evidence trees remain
immutable. A remains unchanged. No UWB payload, position, range, anchor
geometry, or UWB-derived parameter was opened or consumed. No real B was run
unless every synthetic, negative, numerical, and wall gate passed. No analytic
Jacobian, manifold optimizer, temporal/connection/proxy factor, geometry
invention, action-semantic repair, or viewer repair was introduced.
"""


def _manifest_artifacts(output_dir: Path) -> list[dict[str, Any]]:
    return [
        {
            "path": path.name,
            "bytes": path.stat().st_size,
            "sha256": sha256_file(path),
        }
        for path in sorted(output_dir.iterdir())
        if path.name not in ("EVIDENCE_MANIFEST.json", "SHA256SUMS.txt")
    ]


def run(output_dir: Path) -> int:
    output_dir = output_dir.resolve()
    if WORKSPACE.resolve() not in output_dir.parents or output_dir.parent.name != "logs":
        raise ValueError("output must be one direct timestamped directory under workspace/logs")
    output_dir.mkdir(parents=False, exist_ok=False)

    aggregate_started = time.monotonic()
    runtime = verify_runtime(full_records=True)
    safe_before = verify_safe_bindings()
    axes = load_axes()
    episodes, output_matrix = load_episodes()
    geometries = tuple(_plain_geometry(value) for value in load_display_geometries())

    ori_only = validate_ori_only_parity()
    absolute_diagnostics = validate_absolute_step_diagnostics()
    numerical = validate_numerical_equivariance()
    eqv04 = validate_eqv04()
    _write_json(output_dir / "ORI_ONLY_PARITY.json", ori_only)
    _write_json(output_dir / "ABSOLUTE_STEP_DIAGNOSTICS.json", absolute_diagnostics)
    _write_json(output_dir / "EQV_NUMERICAL_GATES.json", numerical)
    _write_json(output_dir / "PAIRED_COORDINATE_DIAGNOSTIC.json", eqv04)

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

    safe_after = verify_safe_bindings()
    bindings_unchanged = safe_before == safe_after
    profile_pass = all(bool(row["passed"]) for row in profile_results)
    body_pass = bool(
        len(profile_results) == 9
        and profile_pass
        and ori_only["passed"]
        and absolute_diagnostics["passed"]
        and numerical["passed"]
        and eqv04["passed"]
        and bindings_unchanged
    )
    body = {
        "schema": "biospur-c2-3b-absolute-step-qualification-body-v1",
        "candidate_profile_count": len(profile_results),
        "all_profiles_required": True,
        "passed_before_wall": body_pass,
        "profile_pass": profile_pass,
        "ORI_ONLY_pass": ori_only["passed"],
        "ABS01_ABS02_ABS03_ABS04_pass": absolute_diagnostics["passed"],
        "EQV01_EQV02_EQV03_CUT02_pass": numerical["passed"],
        "EQV04_pass": eqv04["passed"],
        "safe_bindings_unchanged": bindings_unchanged,
        "profiles": profile_results,
    }
    _write_json(output_dir / "SYNTHETIC_QUALIFICATION_BODY.json", body)
    qualification_wall_s = time.monotonic() - aggregate_started

    wall_pass = bool(
        math.isfinite(qualification_wall_s)
        and qualification_wall_s <= QUALIFICATION_WALL_S
    )
    _write_json(
        output_dir / "QUALIFICATION_WALL.json",
        {
            "qualification_wall_s": qualification_wall_s,
            "qualification_wall_limit_s": QUALIFICATION_WALL_S,
            "equality_passes": True,
            "passed": wall_pass,
            "start_owner": "immediately_before_runtime_guard",
            "stop_owner": "first_monotonic_call_after_qualification_body_write_close",
        },
    )
    candidate_accepted = bool(body_pass and wall_pass)
    if candidate_accepted:
        raise RuntimeError(
            "all absolute-step gates passed; complete capture-wide real validation is now required"
        )

    witnesses, render_rows = _collect_witnesses(profile_results)
    _write_json(output_dir / "FAILURE_WITNESSES.json", {"witnesses": witnesses})
    _write_json(
        output_dir / "REAL_A_ONLY_LINE_PREFLIGHT.json", _axis_preflight(episodes, axes)
    )
    if render_rows:
        render_ab_montage(
            output_dir / "REJECTED_SYNTHETIC_FAILURE_MONTAGE.png",
            render_rows,
            geometries[1],
            output_matrix,
            title="C2 3B absolute-step pivot — rejected synthetic witnesses",
        )
    (output_dir / "IMPLEMENTATION_REPORT.md").write_text(
        _report(
            profile_results,
            absolute_diagnostics,
            numerical,
            eqv04,
            ori_only,
            qualification_wall_s,
            candidate_accepted,
        ),
        encoding="utf-8",
    )
    manifest = {
        "schema": "biospur-c2-3b-absolute-step-evidence-v1",
        "approved_absolute_step_gate": str(ABSOLUTE_STEP_GATE),
        "approved_absolute_step_gate_manifest_sha256": ABSOLUTE_STEP_GATE_MANIFEST_SHA256,
        "monitor_thread_id": "01a05e24-0736-7971-a8c1-f051ee058108",
        "monitor_verdict": "APPROVE",
        "immutable_world_run": str(IMMUTABLE_WORLD_RUN),
        "immutable_world_run_manifest_sha256": IMMUTABLE_WORLD_RUN_MANIFEST_SHA256,
        "immutable_world_causal": str(IMMUTABLE_FINAL_CAUSAL),
        "immutable_world_causal_manifest_sha256": IMMUTABLE_FINAL_CAUSAL_MANIFEST_SHA256,
        "immutable_parent_local_run": str(IMMUTABLE_PARENT_LOCAL_RUN),
        "immutable_parent_local_run_manifest_sha256": IMMUTABLE_PARENT_LOCAL_RUN_MANIFEST_SHA256,
        "immutable_parent_local_causal": str(IMMUTABLE_PARENT_LOCAL_CAUSAL),
        "immutable_parent_local_causal_manifest_sha256": IMMUTABLE_PARENT_LOCAL_CAUSAL_MANIFEST_SHA256,
        "status": "DIAGNOSTIC_ONLY",
        "scientific_pass": False,
        "candidate_accepted": candidate_accepted,
        "synthetic_pass": candidate_accepted,
        "real_capture_wide_ab_run": False,
        "real_stop_reason": "at least one required synthetic or numerical gate failed",
        "all_19_plus_2_a_only_line_preflight": True,
        "uwb_payload_position_range_anchor_geometry_or_derived_parameter_consumed": False,
        "frozen_inputs_unchanged": bindings_unchanged,
        "runtime_guard": runtime,
        "source_hashes": _source_hashes(),
        "artifacts": _manifest_artifacts(output_dir),
        "qualification_wall_s": qualification_wall_s,
        "qualification_wall_limit_s": QUALIFICATION_WALL_S,
        "qualification_wall_pass": wall_pass,
        "run_wall_s": time.monotonic() - aggregate_started,
    }
    _write_json(output_dir / "EVIDENCE_MANIFEST.json", manifest)
    checksums = [
        f"{sha256_file(path)}  {path.name}"
        for path in sorted(output_dir.iterdir())
        if path.name != "SHA256SUMS.txt"
    ]
    (output_dir / "SHA256SUMS.txt").write_text(
        "\n".join(checksums) + "\n", encoding="utf-8"
    )
    return 2


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    return run(args.output)
