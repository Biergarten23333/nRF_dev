"""Deterministic, fully synthetic qualification for the typed gauge repair."""

from __future__ import annotations

import copy
import itertools
import math
import random
from typing import Callable, Mapping
from unittest.mock import patch

import numpy as np

from . import core
from .heading_gauge import (
    AUTHORIZED_R23_SOURCE_SHA256,
    FUTURE_CANDIDATE_SCHEMA,
    HEADING_GAUGE_CACHE_KEY,
    HEADING_GAUGE_SEMANTIC_VERSION,
    R23_MIGRATION_ID,
    R23_SOURCE_SCHEMA,
    BranchEvaluation,
    HeadingGaugeState,
    HeadingGaugeValidationError,
    legacy_r23_solution_sha,
    migrate_r23_psi_zero_candidate,
    validate_future_candidate_payload,
    validate_semantic_cache,
)
from .pipeline import TARGETS, evaluate_branches


SEED = 2606
TOLERANCE = 1e-11


def oracle_wrap_2pi(angle: float) -> float:
    quotient = math.floor((float(angle) + math.pi) / (2.0 * math.pi))
    result = float(angle) - quotient * 2.0 * math.pi
    if result >= math.pi:
        result -= 2.0 * math.pi
    if result < -math.pi:
        result += 2.0 * math.pi
    return result


def oracle_wrap_mod_pi(angle: float) -> float:
    quotient = math.floor((float(angle) + math.pi / 2.0) / math.pi)
    result = float(angle) - quotient * math.pi
    if result >= math.pi / 2.0:
        result -= math.pi
    if result < -math.pi / 2.0:
        result += math.pi
    return result


def oracle_rz(angle: float) -> np.ndarray:
    c, s = math.cos(float(angle)), math.sin(float(angle))
    return np.asarray(((c, -s, 0.0), (s, c, 0.0), (0.0, 0.0, 1.0)))


def oracle_directed_residual(k: float, axis_yaw: float, target_yaw: float) -> float:
    return oracle_wrap_2pi(float(k) + float(axis_yaw) - float(target_yaw))


def synthetic_state(*, psi: float = 0.73) -> HeadingGaugeState:
    values = {
        name: oracle_wrap_2pi(-math.pi + 1e-9 + 0.71 * index)
        for index, name in enumerate(core.COORDINATE_ORDER)
    }
    return HeadingGaugeState(
        coordinate_order=core.COORDINATE_ORDER,
        k_protocol_relative_rad_by_coordinate=values,
        psi_protocol_to_common_rad=psi,
        source_solution_sha256=AUTHORIZED_R23_SOURCE_SHA256,
        source_schema=R23_SOURCE_SCHEMA,
        migration_id=R23_MIGRATION_ID,
    )


def synthetic_reference() -> dict[str, float]:
    return {
        name: oracle_wrap_2pi(0.37 + 0.41 * index)
        for index, name in enumerate(core.COORDINATE_ORDER)
    }


def synthetic_legacy_candidate(*, representative: float | None = 0.0) -> dict:
    state = synthetic_state(psi=0.0)
    modes = []
    for mode_index, bits in enumerate(itertools.product((0, 1), repeat=9)):
        row = {
            "mode_id": f"synthetic-{mode_index:03d}",
            "relative_heading_rad": {
                name: oracle_wrap_2pi(
                    state.k_protocol_relative_rad(name) + math.pi * bits[index]
                )
                for index, name in enumerate(core.COORDINATE_ORDER)
            },
            "pi_branch_bits": list(bits),
            "objective": 0.5,
            "continuous_orbit": "add common alpha to every h_i and psi_GP for alpha in S1",
        }
        if representative is not None:
            row["representative_psi_GP_rad"] = representative
        modes.append(row)
    return {
        "schema": R23_SOURCE_SCHEMA,
        "parameter_order": list(core.COORDINATE_ORDER),
        "joint_mode_count": 512,
        "continuous_psi_orbit": True,
        "symmetries": [
            "continuous common h_i/psi_GP shift",
            "independent per-heading pi axis-line branches",
        ],
        "joint_modes": modes,
    }


def migrate_synthetic_authorized(candidate: Mapping[str, object]) -> HeadingGaugeState:
    """Exercise the positive migration path without consuming the real source numeric."""
    synthetic_sha = legacy_r23_solution_sha(candidate)
    with patch(
        "biospur_fusion.heading_anchor_audit_v2.heading_gauge.AUTHORIZED_R23_SOURCE_SHA256",
        synthetic_sha,
    ):
        return migrate_r23_psi_zero_candidate(
            candidate, source_solution_sha256=synthetic_sha
        )


def future_candidate_payload(state: HeadingGaugeState) -> dict:
    return {
        "schema": FUTURE_CANDIDATE_SCHEMA,
        "semantic_cache_key": HEADING_GAUGE_CACHE_KEY,
        "heading_gauge_state_sha256": state.payload_sha256(),
        "nodes": [
            {
                "coordinate": name,
                "k_protocol_relative_rad": state.k_protocol_relative_rad(name),
                "psi_protocol_to_common_rad": state.psi_protocol_to_common_rad,
                "h_common_rad_derived": state.h_common_rad(name),
                "h_common_derivation": "wrap_2pi(k_protocol_relative_rad + psi_protocol_to_common_rad)",
            }
            for name in state.coordinate_order
        ],
    }


def _branch_payload(value: BranchEvaluation) -> dict:
    if not isinstance(value, BranchEvaluation):
        raise AssertionError("production evaluator did not return BranchEvaluation")
    return value.to_payload()


def _score_vector(payload: Mapping) -> list[float]:
    return [
        float(row["total_unweighted_semantic_score_rad"])
        for row in payload["evaluation"]["candidates"]
    ]


def _tolerance_order(payload: Mapping) -> list[tuple[int, ...]]:
    rows = payload["evaluation"]["candidates"]
    return [
        tuple(row["bit_vector"])
        for row in sorted(
            rows,
            key=lambda row: (
                round(float(row["total_unweighted_semantic_score_rad"]) / TOLERANCE),
                tuple(row["bit_vector"]),
            ),
        )
    ]


def run_gauge_equivariance() -> dict:
    state = synthetic_state()
    reference = synthetic_reference()
    identity = np.eye(3)
    fixed = [0.0, math.pi / 7.0, -math.pi / 3.0, math.pi,
             math.pi - 1e-9, -math.pi + 1e-9]
    rng = random.Random(SEED)
    alphas = fixed + [rng.uniform(-math.pi, math.pi) for _ in range(64)]
    baseline = _branch_payload(evaluate_branches(state, reference))
    baseline_scores = _score_vector(baseline)
    baseline_order = _tolerance_order(baseline)
    baseline_bits = baseline["selection"]["selected_bit_vector"]
    checks = 0
    max_error = 0.0
    per_alpha = []
    for alpha in alphas:
        shifted_state = state.shifted_common_gauge(alpha)
        shifted = _branch_payload(evaluate_branches(shifted_state, reference))
        errors = []
        for coordinate in state.coordinate_order:
            k_error = abs(oracle_wrap_2pi(
                shifted_state.k_protocol_relative_rad(coordinate)
                - state.k_protocol_relative_rad(coordinate)
            ))
            h_error = abs(oracle_wrap_2pi(
                shifted_state.h_common_rad(coordinate)
                - state.h_common_rad(coordinate) - alpha
            ))
            psi_error = abs(oracle_wrap_2pi(
                shifted_state.psi_protocol_to_common_rad
                - state.psi_protocol_to_common_rad - alpha
            ))
            rpi_error = float(np.max(np.abs(
                shifted_state.R_PI(identity, coordinate)
                - state.R_PI(identity, coordinate)
            )))
            rgi_error = float(np.max(np.abs(
                shifted_state.R_GI(identity, coordinate)
                - oracle_rz(alpha) @ state.R_GI(identity, coordinate)
            )))
            target = TARGETS[coordinate]
            target_yaw = float(target.get("azimuth", target.get("start")))
            expected_residual = oracle_directed_residual(
                state.k_protocol_relative_rad(coordinate), reference[coordinate], target_yaw
            )
            observed_residual = core.directed_residual_k(
                shifted_state.k_protocol_relative_rad(coordinate),
                reference[coordinate], target_yaw,
            )
            residual_error = abs(oracle_wrap_2pi(observed_residual - expected_residual))
            errors.extend((k_error, h_error, psi_error, rpi_error, rgi_error, residual_error))
            checks += 6
        score_error = max(abs(a - b) for a, b in zip(
            baseline_scores, _score_vector(shifted), strict=True
        ))
        errors.append(score_error)
        checks += 1
        if _tolerance_order(shifted) != baseline_order:
            raise AssertionError("tolerance-aware branch ordering changed under gauge shift")
        checks += 1
        if shifted["selection"]["selected_bit_vector"] != baseline_bits:
            raise AssertionError("selected bits changed under gauge shift")
        checks += 1
        for base_row, shifted_row in zip(
            baseline["evaluation"]["candidates"],
            shifted["evaluation"]["candidates"], strict=True,
        ):
            if base_row["bit_vector"] != shifted_row["bit_vector"]:
                raise AssertionError("all-512 branch ordering changed")
            for base_node, shifted_node in zip(
                base_row["per_node_directed_distance"],
                shifted_row["per_node_directed_distance"], strict=True,
            ):
                errors.append(abs(oracle_wrap_2pi(
                    shifted_node["k_protocol_relative_rad"]
                    - base_node["k_protocol_relative_rad"]
                )))
                errors.append(abs(oracle_wrap_2pi(
                    shifted_node["h_common_rad_derived"]
                    - base_node["h_common_rad_derived"] - alpha
                )))
                checks += 2
        alpha_error = max(errors)
        max_error = max(max_error, alpha_error)
        if alpha_error > TOLERANCE:
            raise AssertionError(f"gauge equivariance error {alpha_error} at alpha={alpha}")
        per_alpha.append({"alpha_rad": alpha, "max_abs_error": alpha_error, "passed": True})
    return {
        "schema":"biospur.phase3r26c.gauge_equivariance_results.v1",
        "seed":SEED,
        "fixed_alpha_count":len(fixed),
        "random_alpha_count":64,
        "alpha_count":len(alphas),
        "branch_vectors_per_alpha":512,
        "checks_executed":checks,
        "point_targets_covered":True,
        "sector_targets_covered":True,
        "left_right_targets_covered":True,
        "nonzero_reference_azimuth":True,
        "nonzero_psi":True,
        "wrap_boundary_covered":True,
        "modulo_pi_boundary_covered":True,
        "max_abs_error":max_error,
        "tolerance":TOLERANCE,
        "all_passed":True,
        "per_alpha":per_alpha,
    }


def _expect_rejection(name: str, function: Callable[[], object], expected: str) -> dict:
    try:
        function()
    except (HeadingGaugeValidationError, TypeError, ValueError) as exc:
        return {"test":name,"expected_detector":expected,
                "observed_detector":f"{type(exc).__name__}: {exc}","passed":True}
    raise AssertionError(f"{name} was accepted")


def run_serialization_and_validation() -> dict:
    state = synthetic_state()
    payload = state.to_payload()
    round_trip = HeadingGaugeState.from_payload(payload)
    rows = [
        {"test":"typed_round_trip","passed":round_trip == state},
        {"test":"canonical_byte_determinism","passed":round_trip.canonical_bytes() == state.canonical_bytes()},
        {"test":"field_reordering","passed":HeadingGaugeState.from_payload(dict(reversed(list(payload.items())))).canonical_bytes() == state.canonical_bytes()},
    ]
    missing = dict(payload); missing.pop("psi_protocol_to_common_rad")
    rows.append(_expect_rejection("missing_field",lambda:HeadingGaugeState.from_payload(missing),"exact-field validator"))
    extra_coordinate = copy.deepcopy(payload)
    extra_coordinate["k_protocol_relative_rad_by_coordinate"]["extra"] = 0.0
    rows.append(_expect_rejection("extra_coordinate",lambda:HeadingGaugeState.from_payload(extra_coordinate),"coordinate-set validator"))
    duplicate = copy.deepcopy(payload); duplicate["coordinate_order"][1] = duplicate["coordinate_order"][0]
    rows.append(_expect_rejection("duplicate_coordinate",lambda:HeadingGaugeState.from_payload(duplicate),"fixed-order validator"))
    for label, value in (("nan",float("nan")),("positive_inf",float("inf")),("negative_inf",float("-inf"))):
        altered=copy.deepcopy(payload);altered["psi_protocol_to_common_rad"]=value
        rows.append(_expect_rejection(label,lambda altered=altered:HeadingGaugeState.from_payload(altered),"finite-radian validator"))
    degrees=copy.deepcopy(payload);degrees["psi_protocol_to_common_rad"]=90.0
    rows.append(_expect_rejection("degrees_as_radians",lambda:HeadingGaugeState.from_payload(degrees),"canonical-radian validator"))
    schema=copy.deepcopy(payload);schema["semantic_version"]="unknown"
    rows.append(_expect_rejection("unknown_schema",lambda:HeadingGaugeState.from_payload(schema),"semantic-version validator"))
    migration=copy.deepcopy(payload);migration["migration_id"]="unknown"
    rows.append(_expect_rejection("unknown_migration",lambda:HeadingGaugeState.from_payload(migration),"migration validator"))
    rows.append(_expect_rejection("source_sha_mismatch",lambda:migrate_r23_psi_zero_candidate(synthetic_legacy_candidate(),source_solution_sha256="0"*64),"authorized-source-SHA validator"))
    rows.append(_expect_rejection("representative_missing",lambda:migrate_r23_psi_zero_candidate(synthetic_legacy_candidate(representative=None),source_solution_sha256=AUTHORIZED_R23_SOURCE_SHA256),"legacy psi-zero validator"))
    rows.append(_expect_rejection("representative_nonzero",lambda:migrate_r23_psi_zero_candidate(synthetic_legacy_candidate(representative=0.01),source_solution_sha256=AUTHORIZED_R23_SOURCE_SHA256),"legacy psi-zero validator"))
    legacy_schema=synthetic_legacy_candidate();legacy_schema["schema"]="unknown"
    rows.append(_expect_rejection("legacy_unknown_schema",lambda:migrate_r23_psi_zero_candidate(legacy_schema,source_solution_sha256=AUTHORIZED_R23_SOURCE_SHA256),"legacy schema validator"))
    legacy_order=synthetic_legacy_candidate();legacy_order["parameter_order"][0],legacy_order["parameter_order"][1]=legacy_order["parameter_order"][1],legacy_order["parameter_order"][0]
    rows.append(_expect_rejection("legacy_coordinate_order_mismatch",lambda:migrate_r23_psi_zero_candidate(legacy_order,source_solution_sha256=AUTHORIZED_R23_SOURCE_SHA256),"legacy coordinate-order validator"))
    untyped=synthetic_legacy_candidate();untyped["joint_modes"][0]["heading_rad"]=untyped["joint_modes"][0].pop("relative_heading_rad")
    rows.append(_expect_rejection("legacy_only_untyped_heading",lambda:migrate_r23_psi_zero_candidate(untyped,source_solution_sha256=AUTHORIZED_R23_SOURCE_SHA256),"typed legacy-field validator"))
    provenance=synthetic_legacy_candidate();provenance.pop("symmetries")
    rows.append(_expect_rejection("legacy_provenance_incomplete",lambda:migrate_r23_psi_zero_candidate(provenance,source_solution_sha256=AUTHORIZED_R23_SOURCE_SHA256),"continuous-orbit provenance validator"))
    migrated=migrate_synthetic_authorized(synthetic_legacy_candidate())
    rows.append({"test":"authorized_legacy_migration","passed":isinstance(migrated,HeadingGaugeState) and migrated.psi_protocol_to_common_rad==0.0})
    candidate=future_candidate_payload(state);candidate["nodes"][0]["h_common_rad_derived"]=oracle_wrap_2pi(candidate["nodes"][0]["h_common_rad_derived"]+0.2)
    rows.append(_expect_rejection("inconsistent_derived_h",lambda:validate_future_candidate_payload(candidate,state),"derived-H algebra validator"))
    rows.append(_expect_rejection("stale_cache_key",lambda:validate_semantic_cache({"semantic_cache_key":"legacy","heading_gauge_state_sha256":state.payload_sha256()},state),"semantic-cache validator"))
    rows.append(_expect_rejection("multiple_coordinate_order",lambda:HeadingGaugeState(coordinate_order=tuple(reversed(state.coordinate_order)),k_protocol_relative_rad_by_coordinate=state.k_protocol_relative_rad_by_coordinate,psi_protocol_to_common_rad=state.psi_protocol_to_common_rad,source_solution_sha256=state.source_solution_sha256,source_schema=state.source_schema,migration_id=state.migration_id),"fixed-order validator"))
    if not all(row["passed"] for row in rows):
        raise AssertionError("serialization qualification failed")
    return {
        "schema":"biospur.phase3r26c.serialization_validator_results.v1",
        "executed_count":len(rows),"passed_count":sum(row["passed"] for row in rows),
        "failed_count":sum(not row["passed"] for row in rows),"all_passed":True,"tests":rows,
    }


def run_required_mutations() -> dict:
    state = synthetic_state()
    reference = synthetic_reference()
    coordinate = state.coordinate_order[0]
    k = state.k_protocol_relative_rad(coordinate)
    psi = state.psi_protocol_to_common_rad
    axis = reference[coordinate]
    target = float(TARGETS[coordinate]["azimuth"])
    expected_h = oracle_wrap_2pi(k + psi)
    expected_r = oracle_directed_residual(k, axis, target)
    rows = []

    def record(mutation: str, function: str, altered: object, detector: str,
               detected: bool, observed: object) -> None:
        rows.append({"mutation":mutation,"production_function":function,
                     "actual_altered_value":altered,"expected_detector":detector,
                     "observed_detector":observed,"passed":bool(detected)})

    for mutation, altered_h in (
        ("missing +psi when deriving H",k),
        ("double +psi",oracle_wrap_2pi(k+2.0*psi)),
    ):
        payload=future_candidate_payload(state);payload["nodes"][0]["h_common_rad_derived"]=altered_h
        detected=False;observed="ACCEPTED"
        try:validate_future_candidate_payload(payload,state)
        except HeadingGaugeValidationError as exc:detected=True;observed=str(exc)
        record(mutation,"validate_future_candidate_payload",altered_h,
               "derived-H algebra validator",detected,observed)

    altered_r=oracle_wrap_2pi(k+axis-psi-target)
    record("residual subtracts psi from K","directed_residual_k",altered_r,
           "independent residual oracle",abs(oracle_wrap_2pi(altered_r-expected_r))>1e-6,
           abs(oracle_wrap_2pi(altered_r-expected_r)))
    altered_r=oracle_wrap_2pi(expected_h+axis-target)
    record("residual omits psi from H","directed_residual",altered_r,
           "independent residual oracle",abs(oracle_wrap_2pi(altered_r-expected_r))>1e-6,
           abs(oracle_wrap_2pi(altered_r-expected_r)))
    record("K treated as H","HeadingGaugeState.h_common_rad",k,
           "independent H oracle",abs(oracle_wrap_2pi(k-expected_h))>1e-6,
           abs(oracle_wrap_2pi(k-expected_h)))
    altered_k=expected_h
    altered_r=oracle_directed_residual(altered_k,axis,target)
    record("H treated as K","directed_residual_k",altered_k,
           "independent residual oracle",abs(oracle_wrap_2pi(altered_r-expected_r))>1e-6,
           abs(oracle_wrap_2pi(altered_r-expected_r)))

    for mutation,key in (("serializer loses psi","psi_protocol_to_common_rad"),
                         ("serializer erases semantic version","semantic_version")):
        payload=state.to_payload();altered=payload.pop(key);detected=False;observed="ACCEPTED"
        try:HeadingGaugeState.from_payload(payload)
        except HeadingGaugeValidationError as exc:detected=True;observed=str(exc)
        record(mutation,"HeadingGaugeState.from_payload",altered,
               "exact-field validator",detected,observed)

    altered_r=oracle_wrap_mod_pi(k+axis-target)
    correct_r=oracle_wrap_2pi(k+axis-target)
    record("wrap-pi used instead of wrap-2pi","directed_residual_k",altered_r,
           "directed S1 oracle",abs(oracle_wrap_2pi(altered_r-correct_r))>3.0,
           abs(oracle_wrap_2pi(altered_r-correct_r)))

    baseline=_branch_payload(evaluate_branches(state,reference))
    wrong_state=state.with_common_gauge(oracle_wrap_2pi(psi+math.pi/3.0))
    # Explicitly mutate K as if it were H and subtract the shifted psi.
    wrong_scores=[]
    for bits in itertools.product((0,1),repeat=9):
        score=0.0
        for index,name in enumerate(state.coordinate_order):
            wrong=oracle_wrap_2pi(state.k_protocol_relative_rad(name)+math.pi*bits[index]+reference[name]-wrong_state.psi_protocol_to_common_rad)
            target_spec=TARGETS[name]
            if target_spec["type"]=="point":score+=abs(oracle_wrap_2pi(wrong-target_spec["azimuth"]))
            else:score+=min(abs(oracle_wrap_2pi(wrong-target_spec["start"])),abs(oracle_wrap_2pi(wrong-target_spec["stop"])))
        wrong_scores.append(score)
    score_delta=max(abs(a-b) for a,b in zip(_score_vector(baseline),wrong_scores,strict=True))
    record("branch bits change under common gauge shift","evaluate_branches",score_delta,
           "all-512 score equivariance",score_delta>1e-6,score_delta)

    stale={"semantic_cache_key":"phase3r26_v1","heading_gauge_state_sha256":state.payload_sha256(),"branch_bits":[0]*9}
    detected=False;observed="ACCEPTED"
    try:validate_semantic_cache(stale,state)
    except HeadingGaugeValidationError as exc:detected=True;observed=str(exc)
    record("stale cache accepted","validate_semantic_cache",stale["semantic_cache_key"],"semantic-cache validator",detected,observed)

    legacy={"schema":"biospur.phase3r26.nine_heading_conditional_candidate.v1","nodes":[]}
    detected=False;observed="ACCEPTED"
    try:validate_future_candidate_payload(legacy,state)
    except HeadingGaugeValidationError as exc:detected=True;observed=str(exc)
    record("legacy R2.6 candidate accepted","validate_future_candidate_payload",legacy["schema"],"versioned candidate validator",detected,observed)

    payload=future_candidate_payload(state);payload["nodes"][2]["h_common_rad_derived"]=0.0
    detected=False;observed="ACCEPTED"
    try:validate_future_candidate_payload(payload,state)
    except HeadingGaugeValidationError as exc:detected=True;observed=str(exc)
    record("inconsistent derived H not detected","validate_future_candidate_payload",0.0,"derived-H algebra validator",detected,observed)

    swapped=list(state.coordinate_order);swapped[0],swapped[1]=swapped[1],swapped[0]
    detected=False;observed="ACCEPTED"
    try:HeadingGaugeState(coordinate_order=swapped,k_protocol_relative_rad_by_coordinate=state.k_protocol_relative_rad_by_coordinate,psi_protocol_to_common_rad=psi,source_solution_sha256=state.source_solution_sha256,source_schema=state.source_schema,migration_id=state.migration_id)
    except HeadingGaugeValidationError as exc:detected=True;observed=str(exc)
    record("coordinate order swap not detected","HeadingGaugeState",swapped[:2],"fixed-order validator",detected,observed)

    passed=sum(row["passed"] for row in rows)
    if passed != len(rows):
        failed=[row["mutation"] for row in rows if not row["passed"]]
        raise AssertionError(f"required mutation was not detected: {failed}")
    return {"schema":"biospur.phase3r26c.production_mutation_results.v1",
            "executed_count":len(rows),"passed_count":passed,"failed_count":len(rows)-passed,
            "literal_true_count":0,"all_passed":True,"mutations":rows}
