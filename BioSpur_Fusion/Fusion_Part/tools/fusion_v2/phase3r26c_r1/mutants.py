"""Exact production-source mutations required by the R2.6C-R1 campaign."""


M03_LEGACY = {
    "semantic_intent": "subtract psi a second time from the K-space directed residual",
    "relative_file": "pipeline.py",
    "target_symbol": "_score_k_space_branch_candidate",
    "original_excerpt": (
        'delta = directed_residual_k(k, float(reference[segment]), target["azimuth"])'
    ),
    "mutated_excerpt": (
        "delta = directed_residual(k, float(reference[segment]), "
        'branch_state.psi_protocol_to_common_rad, target["azimuth"])'
    ),
    "defect": (
        "branch_state belongs to score_branch_candidate; the K-only helper receives "
        "only k_protocol_relative, reference, and bits"
    ),
}


M03_CORRECTIVE = {
    "semantic_intent": M03_LEGACY["semantic_intent"],
    "relative_file": "pipeline.py",
    "target_symbol": "score_branch_candidate",
    "structural_anchor": (
        "the unique first argument of the _score_k_space_branch_candidate call"
    ),
    "original_excerpt": "branch_state.k_protocol_relative_rad_by_coordinate",
    "mutated_excerpt": (
        "KProtocolRelativeByCoordinate("
        "coordinate_order=branch_state.coordinate_order,"
        "k_protocol_relative_rad_by_coordinate={name: float(wrap_2pi("
        "branch_state.k_protocol_relative_rad(name) - "
        "branch_state.psi_protocol_to_common_rad"
        ")) for name in branch_state.coordinate_order})"
    ),
    "semantic_equivalence": (
        "directed_residual_k(K - psi, axis, target) equals the legacy intended "
        "directed_residual(K, axis, psi, target) for directed point residuals"
    ),
    "designated_assertion": "R1_ASSERT_M03_K_RESIDUAL_HAS_NO_EXTRA_PSI",
    "expected_diagnostic": "R1_EXPECTED_SEMANTIC_KILL:M03_K_RESIDUAL_EXTRA_MINUS_PSI",
    "expected_exit_code": 17,
}


M05_LEGACY = {
    "semantic_intent": "protocol-frame axis calculation consumes H instead of K",
    "relative_file": "pipeline.py",
    "target_symbol": "_score_k_space_branch_candidate",
    "original_excerpt": (
        "protocol_axis_yaw = float(wrap_2pi(k + float(reference[segment])))"
    ),
    "mutated_excerpt": (
        "protocol_axis_yaw = float(wrap_2pi(h + float(reference[segment])))"
    ),
    "defect": (
        "the helper is deliberately K-only; h is neither a local nor a parameter, "
        "while branch_state.h_common_rad is available in score_branch_candidate"
    ),
}


M05_CORRECTIVE = {
    "semantic_intent": M05_LEGACY["semantic_intent"],
    "relative_file": "pipeline.py",
    "target_symbol": "score_branch_candidate",
    "structural_anchor": (
        "the unique first argument of the _score_k_space_branch_candidate call"
    ),
    "original_excerpt": "branch_state.k_protocol_relative_rad_by_coordinate",
    "mutated_excerpt": (
        "KProtocolRelativeByCoordinate("
        "coordinate_order=branch_state.coordinate_order,"
        "k_protocol_relative_rad_by_coordinate={name: "
        "branch_state.h_common_rad(name) "
        "for name in branch_state.coordinate_order})"
    ),
    "semantic_equivalence": (
        "the real caller computes H as wrap_2pi(K + psi); passing those H values "
        "through the existing typed call path makes the K-only axis calculation "
        "consume H without adding H semantics to production"
    ),
    "designated_assertion": "R1_ASSERT_M05_PROTOCOL_AXIS_USES_K",
    "expected_diagnostic": "R1_EXPECTED_SEMANTIC_KILL:M05_K_TREATED_AS_H",
    "expected_exit_code": 17,
}


M08_CORRECTIVE = {
    "semantic_intent": "heading-state serializer drops semantic version",
    "relative_file": "heading_gauge.py",
    "target_symbol": "HeadingGaugeState.to_payload",
    "structural_anchor": "the semantic_version entry of the returned payload Dict",
    "missing_key": "semantic_version",
    "probe_detector": (
        "'semantic_version' in payload and payload['semantic_version'] == "
        "HEADING_GAUGE_SEMANTIC_VERSION"
    ),
    "designated_assertion": "R1_ASSERT_M08_SERIALIZER_PRESERVES_VERSION",
    "expected_diagnostic": "R1_EXPECTED_SEMANTIC_KILL:M08_SERIALIZER_DROPS_VERSION",
    "expected_exit_code": 17,
}


M10_LEGACY = {
    "semantic_intent": "branch scoring changes under a common-gauge shift",
    "relative_file": "pipeline.py",
    "target_symbol": "_score_k_space_branch_candidate",
    "original_excerpt": "k = branch_state.k_protocol_relative_rad(segment)",
    "mutated_excerpt": (
        "k = float(wrap_2pi(branch_state.k_protocol_relative_rad(segment) - "
        "branch_state.psi_protocol_to_common_rad))"
    ),
    "defect": (
        "the current K-only helper indexes its typed K view and has no branch_state; "
        "the legacy assignment was removed by the K-only boundary refactor"
    ),
}


M10_CORRECTIVE = {
    "semantic_intent": M10_LEGACY["semantic_intent"],
    "relative_file": "pipeline.py",
    "target_symbol": "score_branch_candidate",
    "structural_anchor": (
        "the unique first argument of the _score_k_space_branch_candidate call"
    ),
    "original_excerpt": "branch_state.k_protocol_relative_rad_by_coordinate",
    "mutated_excerpt": M03_CORRECTIVE["mutated_excerpt"],
    "semantic_equivalence": (
        "the legacy defect contaminated each K with -psi; constructing the same "
        "K-minus-psi typed view at the real caller preserves that defect and makes "
        "the branch score change when the common gauge is shifted"
    ),
    "designated_assertion": "R1_ASSERT_M10_BRANCH_SCORE_GAUGE_INVARIANT",
    "expected_diagnostic": "R1_EXPECTED_SEMANTIC_KILL:M10_BRANCH_DEPENDS_ON_GAUGE",
    "expected_exit_code": 17,
}

MUTANTS = {
    "M01_H_MISSING_PSI": (
        "heading_gauge.py",
        "self.k_protocol_relative_rad(coordinate)\n            + self.psi_protocol_to_common_rad",
        "self.k_protocol_relative_rad(coordinate)",
    ),
    "M02_H_DOUBLE_PSI": (
        "heading_gauge.py",
        "self.k_protocol_relative_rad(coordinate)\n            + self.psi_protocol_to_common_rad",
        "self.k_protocol_relative_rad(coordinate)\n            + 2.0 * self.psi_protocol_to_common_rad",
    ),
    "M03_K_RESIDUAL_EXTRA_MINUS_PSI": (
        M03_CORRECTIVE["relative_file"],
        M03_CORRECTIVE["original_excerpt"],
        M03_CORRECTIVE["mutated_excerpt"],
    ),
    "M04_H_RESIDUAL_MISSING_MINUS_PSI": (
        "core.py",
        "raw = h_i + axis_yaw - psi_gp - target_yaw_p",
        "raw = h_i + axis_yaw - target_yaw_p",
    ),
    "M05_K_TREATED_AS_H": (
        M05_CORRECTIVE["relative_file"],
        M05_CORRECTIVE["original_excerpt"],
        M05_CORRECTIVE["mutated_excerpt"],
    ),
    "M06_H_TREATED_AS_K": (
        "heading_gauge.py",
        "return _rz(self.h_common_rad(coordinate)) @ matrix",
        "return _rz(self.k_protocol_relative_rad(coordinate)) @ matrix",
    ),
    "M07_SERIALIZER_DROPS_PSI": (
        "heading_gauge.py",
        '            "psi_protocol_to_common_rad": self.psi_protocol_to_common_rad,\n',
        "",
    ),
    "M08_SERIALIZER_DROPS_VERSION": (
        "heading_gauge.py",
        '            "semantic_version": self.semantic_version,\n',
        "",
    ),
    "M09_WRAP_2PI_TO_MOD_PI": (
        "core.py",
        "return float(wrap_2pi(\n        float(k_protocol_relative_rad) + float(axis_yaw) - float(target_yaw_p)\n    ))",
        "return float(wrap_mod_pi(\n        float(k_protocol_relative_rad) + float(axis_yaw) - float(target_yaw_p)\n    ))",
    ),
    "M10_BRANCH_DEPENDS_ON_GAUGE": (
        M10_CORRECTIVE["relative_file"],
        M10_CORRECTIVE["original_excerpt"],
        M10_CORRECTIVE["mutated_excerpt"],
    ),
    "M11_STALE_CACHE_ACCEPTED": (
        "heading_gauge.py",
        'if cache.get("semantic_cache_key") != HEADING_GAUGE_CACHE_KEY:',
        'if False and cache.get("semantic_cache_key") != HEADING_GAUGE_CACHE_KEY:',
    ),
    "M12_LEGACY_CANDIDATE_ACCEPTED": (
        "heading_gauge.py",
        'if payload.get("schema") != FUTURE_CANDIDATE_SCHEMA:',
        'if False and payload.get("schema") != FUTURE_CANDIDATE_SCHEMA:',
    ),
    "M13_INCONSISTENT_H_ACCEPTED": (
        "heading_gauge.py",
        'if abs(_wrap_2pi_scalar(float(row["h_common_rad_derived"]) - expected_h)) > 1e-12:',
        'if False and abs(_wrap_2pi_scalar(float(row["h_common_rad_derived"]) - expected_h)) > 1e-12:',
    ),
    "M14_COORDINATE_SWAP_ACCEPTED": (
        "heading_gauge.py",
        "if order != tuple(COORDINATE_ORDER):",
        "if False and order != tuple(COORDINATE_ORDER):",
    ),
}
