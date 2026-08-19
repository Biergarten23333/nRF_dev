"""Exact production-source mutations required by the R2.6C-R1 campaign."""

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
        "pipeline.py",
        'delta = directed_residual_k(k, float(reference[segment]), target["azimuth"])',
        'delta = directed_residual(k, float(reference[segment]), branch_state.psi_protocol_to_common_rad, target["azimuth"])',
    ),
    "M04_H_RESIDUAL_MISSING_MINUS_PSI": (
        "core.py",
        "raw = h_i + axis_yaw - psi_gp - target_yaw_p",
        "raw = h_i + axis_yaw - target_yaw_p",
    ),
    "M05_K_TREATED_AS_H": (
        "pipeline.py",
        "protocol_axis_yaw = float(wrap_2pi(k + float(reference[segment])))",
        "protocol_axis_yaw = float(wrap_2pi(h + float(reference[segment])))",
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
        "pipeline.py",
        "k = branch_state.k_protocol_relative_rad(segment)",
        "k = float(wrap_2pi(branch_state.k_protocol_relative_rad(segment) - branch_state.psi_protocol_to_common_rad))",
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
