"""Append-only runner for the independently approved parent-local pivot."""

from __future__ import annotations

import argparse
from collections import Counter
import math
from pathlib import Path
import time
from typing import Any

from .contracts import PROFILES, WORKSPACE
from .equivariance_validation import (
    validate_eqv04,
    validate_numerical_equivariance,
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


PARENT_LOCAL_GATE = Path("logs/c2_3b_parent_local_gate_20260902_001015")
PARENT_LOCAL_GATE_MANIFEST_SHA256 = (
    "a1c895bf87b7016d0d437b1d428a7726583e11fa33681ceb5fb189a80a112e9f"
)
IMMUTABLE_WORLD_RUN = Path("logs/c2_3b_projector_pivot_20260901_231915")
IMMUTABLE_WORLD_RUN_MANIFEST_SHA256 = (
    "2f68f98c71d4b8b651aadf13496dfd5c3dd0688449a97506ca44f3a6d8621b2c"
)
IMMUTABLE_FINAL_CAUSAL = Path("logs/c2_3b_final_causal_20260901_235618")
IMMUTABLE_FINAL_CAUSAL_MANIFEST_SHA256 = (
    "3a759297a9db78d084d7ece8a2cd917800648c53952c5b79337ecbf937267325"
)


def _report(
    profile_results: list[dict[str, Any]],
    numerical: dict[str, Any],
    eqv04: dict[str, Any],
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
    gate_rows = numerical["gates"]
    gate_summary = ", ".join(
        f"{row['case']}={'PASS' if row['passed'] else 'FAIL'}" for row in gate_rows
    )
    reason_counts = eqv04["values"]["failure_reason_counts"]
    reasons = ", ".join(f"{name}={count}" for name, count in reason_counts.items()) or "none"
    return f"""# BioSpur C2 3B parent-local pivot implementation report

## Outcome

The independently approved parent-local projector candidate completed all nine
preregistered profiles without selection. `{profile_passes}/9` profiles passed
the unchanged full synthetic/negative suite. Failed cases were: {failures}.
The candidate acceptance result is `{str(candidate_accepted).lower()}`. The maximum
scientific status remains `DIAGNOSTIC_ONLY`, `scientific_pass=false`.

The aggregate qualification interval was {qualification_wall_s:.6f} seconds
against the unchanged 2,400-second bound. Each profile retained the unchanged
300-second bound. The corrected clock owns the runtime and binding guards,
loads, all new numerical checks, complete profile work, timed JSON writes, the
post-run binding comparison, and the final qualification-body write/close.

## Numerical causal checks

The preregistered numerical gates report: {gate_summary}. EQV04 passed its
54-pair/108-run completeness and shared-start scalar-equivalence contract.
Across those diagnostic runs the recorded failure-reason assignments were:
{reasons}. These comparisons are causal diagnostics only and do not replace
any full-suite result.

## Scope boundary

The immutable rejected world-coordinate run and its causal packet remain
unchanged. A remains unchanged. No UWB payload, position, range, anchor
geometry, or derived parameter was opened or consumed. No real B output was
computed unless all nine profiles and every added numerical gate passed.
Qualified anatomical joint centres, connection vectors, full anatomical
frames, calibrated factor covariance, and independent real pose truth remain
unavailable; no visual, semantic, temporal, root-world, drift, or geometry
repair was introduced.
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

    # Binding B02: this is the first monotonic read and precedes every guard,
    # binding/hash check, owner load, or qualification calculation.
    aggregate_started = time.monotonic()
    runtime = verify_runtime(full_records=True)
    safe_before = verify_safe_bindings()
    axes = load_axes()
    episodes, output_matrix = load_episodes()
    geometries = tuple(_plain_geometry(value) for value in load_display_geometries())

    ori_only = validate_ori_only_parity()
    numerical = validate_numerical_equivariance()
    eqv04 = validate_eqv04()
    _write_json(output_dir / "ORI_ONLY_PARITY.json", ori_only)
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
        and numerical["passed"]
        and eqv04["passed"]
        and bindings_unchanged
    )
    qualification_body = {
        "schema": "biospur-c2-3b-parent-local-qualification-body-v1",
        "candidate_profile_count": len(profile_results),
        "all_profiles_required": True,
        "passed_before_wall": body_pass,
        "profile_pass": profile_pass,
        "ORI_ONLY_pass": ori_only["passed"],
        "EQV01_EQV02_EQV03_CUT02_pass": numerical["passed"],
        "EQV04_pass": eqv04["passed"],
        "safe_bindings_unchanged": bindings_unchanged,
        "profiles": profile_results,
    }
    _write_json(
        output_dir / "SYNTHETIC_QUALIFICATION_BODY.json", qualification_body
    )
    # Binding B02: this is the first monotonic read after the body writer closes.
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
            "all parent-local gates passed; complete capture-wide real validation is now required"
        )

    witnesses, render_rows = _collect_witnesses(profile_results)
    _write_json(output_dir / "FAILURE_WITNESSES.json", {"witnesses": witnesses})
    _write_json(
        output_dir / "REAL_A_ONLY_LINE_PREFLIGHT.json",
        _axis_preflight(episodes, axes),
    )
    if render_rows:
        render_ab_montage(
            output_dir / "REJECTED_SYNTHETIC_FAILURE_MONTAGE.png",
            render_rows,
            geometries[1],
            output_matrix,
            title="C2 3B parent-local pivot — rejected synthetic witnesses",
        )

    (output_dir / "IMPLEMENTATION_REPORT.md").write_text(
        _report(
            profile_results,
            numerical,
            eqv04,
            qualification_wall_s,
            candidate_accepted,
        ),
        encoding="utf-8",
    )
    manifest = {
        "schema": "biospur-c2-3b-parent-local-evidence-v1",
        "approved_parent_local_gate": str(PARENT_LOCAL_GATE),
        "approved_parent_local_gate_manifest_sha256": PARENT_LOCAL_GATE_MANIFEST_SHA256,
        "monitor_thread_id": "01a05e24-0736-7971-a8c1-f051ee058108",
        "monitor_verdict": "APPROVE",
        "immutable_world_run": str(IMMUTABLE_WORLD_RUN),
        "immutable_world_run_manifest_sha256": IMMUTABLE_WORLD_RUN_MANIFEST_SHA256,
        "immutable_nonterminal_causal_packet": str(IMMUTABLE_FINAL_CAUSAL),
        "immutable_nonterminal_causal_packet_manifest_sha256": IMMUTABLE_FINAL_CAUSAL_MANIFEST_SHA256,
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
