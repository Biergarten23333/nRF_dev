#!/usr/bin/env python3
"""Bind every causal curve input to the fresh ATTEMPT_003 state."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

import numpy as np


WORKSPACE = Path("/mnt/nrf_ssd/nRF_dev/BioSpur_Fusion/Fusion_Part")
RUN = WORKSPACE / "logs/c2_basis_progressive_20260829T102836Z"
SPRINT = RUN / "CONTINUATION_SPRINT"
RUN013_MANIFEST = RUN / "C2_REAL_DIAGNOSTIC_FROZEN_STATE_013.json"
RUN013_NPZ = RUN / "C2_REAL_DIAGNOSTIC_FROZEN_STATE_013.npz"
FRESH_MANIFEST = SPRINT / "C2_FRESH_CONTINUATION_FROZEN_STATE_003.json"
FRESH_NPZ = SPRINT / "C2_FRESH_CONTINUATION_FROZEN_STATE_003.npz"
FRESH_GATE = SPRINT / "C2_FRESH_CONTINUATION_GATE_003.json"
CURVE_DIR = SPRINT / "RUN013_CAUSAL_PROGRESSIVE_CURVES_001"
CURVE_DATA = CURVE_DIR / "CAUSAL_19_PREFIX_CURVE_DATA.json"
CURVE_AUDIT = CURVE_DIR / "CAUSAL_19_PREFIX_PROGRESS_PREQUENTIAL_AUDIT.json"
CURVE_PNG = CURVE_DIR / "CAUSAL_19_PREFIX_PROGRESS_PREQUENTIAL_CURVES.png"
OUTPUT = CURVE_DIR / "FRESH_ATTEMPT003_EXACT_CURVE_EQUIVALENCE_001.json"

ARRAY_NAMES = (
    "prequential_and_information_scalars",
    "prequential_prior_covariance",
    "data_information",
    "data_information_nonzero_eigenvalues",
    "posterior_covariance",
    "measurement_statistical_covariance",
    "temporal_migration_covariance",
    "shared_systematic_covariance",
    "branch_weights",
)


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _array_binding(value: np.ndarray) -> dict[str, Any]:
    array = np.ascontiguousarray(np.asarray(value))
    header = json.dumps(
        {"dtype": str(array.dtype), "shape": list(array.shape)}, sort_keys=True,
    ).encode("utf-8")
    return {
        "dtype": str(array.dtype),
        "shape": list(array.shape),
        "sha256": hashlib.sha256(header + array.tobytes()).hexdigest(),
    }


def _curve_row(
    *,
    prefix_row: Mapping[str, Any],
    arrays: Mapping[str, np.ndarray],
    physical_branch_count: int,
) -> dict[str, Any]:
    scalars = np.asarray(arrays["prequential_and_information_scalars"])
    weights = np.asarray(arrays["branch_weights"])
    positive = weights[weights > 0.0]
    entropy = -float(np.sum(positive * np.log(positive)))
    observed_rank = int(round(float(scalars[1])))
    return {
        "chronological_index": int(prefix_row["chronological_index"]),
        "action": str(prefix_row["action"]),
        "prequential_status": str(prefix_row["prequential_status"]),
        "prequential_nll_before_ingest": float(scalars[0]),
        "prequential_observed_rank": observed_rank,
        "prequential_nll_per_observed_coordinate": (
            None if observed_rank == 0 else float(scalars[0] / scalars[1])
        ),
        "information_logdet": float(scalars[2]),
        "data_information_rank": int(round(float(scalars[3]))),
        "data_information_pseudologdet": (
            float(scalars[4]) if bool(round(float(scalars[5]))) else None
        ),
        "posterior_total_uncertainty_trace": float(
            np.trace(arrays["posterior_covariance"])
        ),
        "prequential_prior_uncertainty_trace": float(
            np.trace(arrays["prequential_prior_covariance"])
        ),
        "measurement_statistical_uncertainty_trace": float(
            np.trace(arrays["measurement_statistical_covariance"])
        ),
        "temporal_migration_uncertainty_trace": float(
            np.trace(arrays["temporal_migration_covariance"])
        ),
        "shared_systematic_uncertainty_trace": float(
            np.trace(arrays["shared_systematic_covariance"])
        ),
        "maximum_branch_weight": float(np.max(weights)),
        "positive_branch_count": int(np.count_nonzero(weights > 0.0)),
        "effective_branch_support_exp_entropy": float(np.exp(entropy)),
        "branch_concentration": float(scalars[7]),
        "physical_validity": float(scalars[8]),
        "causal_physical_trajectory_branch_count": physical_branch_count,
    }


def _write_new(path: Path, value: Any) -> None:
    with path.open("x", encoding="utf-8") as handle:
        json.dump(value, handle, indent=2, sort_keys=True, allow_nan=False)
        handle.write("\n")
    path.chmod(0o444)


def main() -> int:
    if Path.cwd().resolve() != WORKSPACE:
        raise RuntimeError("fresh curve audit requires canonical Fusion_Part")
    old_manifest = json.loads(RUN013_MANIFEST.read_text(encoding="utf-8"))
    fresh_manifest = json.loads(FRESH_MANIFEST.read_text(encoding="utf-8"))
    fresh_gate = json.loads(FRESH_GATE.read_text(encoding="utf-8"))
    curve_data = json.loads(CURVE_DATA.read_text(encoding="utf-8"))
    if (
        fresh_gate.get("fresh_raw_gate_pass") is not True
        or fresh_gate.get("heldout_opened") is not False
        or fresh_gate.get("scientific_acceptance_pass") is not False
        or fresh_manifest.get("heldout_opened") is not False
        or curve_data.get("heldout_opened") is not False
        or curve_data.get("retrospective_trajectory_arrays_consumed") is not False
    ):
        raise RuntimeError("fresh curve authority or scope is inconsistent")
    old_prefixes = old_manifest["structure"]["progressive_prefixes"]
    fresh_prefixes = fresh_manifest["structure"]["progressive_prefixes"]
    if len(old_prefixes) != 19 or len(fresh_prefixes) != 19:
        raise RuntimeError("fresh curve audit requires 19 causal prefixes")

    bindings: dict[str, Any] = {}
    recomputed_rows: list[dict[str, Any]] = []
    with (
        np.load(RUN013_NPZ, allow_pickle=False) as old_arrays,
        np.load(FRESH_NPZ, allow_pickle=False) as fresh_arrays,
    ):
        for index, (old_prefix, fresh_prefix) in enumerate(
            zip(old_prefixes, fresh_prefixes, strict=True)
        ):
            for field in (
                "chronological_index", "action", "prequential_status",
                "prequential_observed_rank", "data_information_rank",
            ):
                if old_prefix[field] != fresh_prefix[field]:
                    raise RuntimeError(f"prefix {index}: structure field differs: {field}")
            selected: dict[str, np.ndarray] = {}
            for leaf in ARRAY_NAMES:
                name = f"progressive_prefix/{index:02d}/{leaf}"
                old_value = np.asarray(old_arrays[name])
                fresh_value = np.asarray(fresh_arrays[name])
                old_binding = _array_binding(old_value)
                fresh_binding = _array_binding(fresh_value)
                if not np.array_equal(old_value, fresh_value) or old_binding != fresh_binding:
                    raise RuntimeError(f"fresh causal curve array differs: {name}")
                if old_manifest["array_bindings"].get(name) != old_binding:
                    raise RuntimeError(f"RUN013 manifest binding differs: {name}")
                if fresh_manifest["array_bindings"].get(name) != fresh_binding:
                    raise RuntimeError(f"fresh manifest binding differs: {name}")
                bindings[name] = old_binding
                selected[leaf] = fresh_value
            old_support = old_manifest["structure"]["physical_trajectory_support"].get(
                str(index), {}
            )
            fresh_support = fresh_manifest["structure"]["physical_trajectory_support"].get(
                str(index), {}
            )
            if set(old_support) != set(fresh_support):
                raise RuntimeError(f"prefix {index}: physical support branch set differs")
            recomputed_rows.append(_curve_row(
                prefix_row=fresh_prefix,
                arrays=selected,
                physical_branch_count=len(fresh_support),
            ))
        initial_information = np.asarray(
            fresh_arrays["progressive_prefix/00/data_information"]
        )

    if recomputed_rows != curve_data["rows"]:
        raise RuntimeError("fresh recomputed causal curve rows differ from RUN013 curve data")
    initial_zero = bool(
        recomputed_rows[0]["data_information_rank"] == 0
        and recomputed_rows[0]["prequential_observed_rank"] == 0
        and np.array_equal(initial_information, np.zeros_like(initial_information))
    )
    if not initial_zero:
        raise RuntimeError("fresh initial still contributes information")
    binding_semantic_sha = hashlib.sha256(json.dumps(
        bindings, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")).hexdigest()
    output = {
        "schema": "biospur-c2-fresh-attempt003-exact-causal-curve-equivalence-v1",
        "run013_manifest": {"path": str(RUN013_MANIFEST.relative_to(WORKSPACE)), "sha256": _sha(RUN013_MANIFEST)},
        "run013_npz": {"path": str(RUN013_NPZ.relative_to(WORKSPACE)), "sha256": _sha(RUN013_NPZ)},
        "fresh_manifest": {"path": str(FRESH_MANIFEST.relative_to(WORKSPACE)), "sha256": _sha(FRESH_MANIFEST)},
        "fresh_npz": {"path": str(FRESH_NPZ.relative_to(WORKSPACE)), "sha256": _sha(FRESH_NPZ)},
        "fresh_gate": {"path": str(FRESH_GATE.relative_to(WORKSPACE)), "sha256": _sha(FRESH_GATE)},
        "curve_data": {"path": str(CURVE_DATA.relative_to(WORKSPACE)), "sha256": _sha(CURVE_DATA)},
        "curve_audit": {"path": str(CURVE_AUDIT.relative_to(WORKSPACE)), "sha256": _sha(CURVE_AUDIT)},
        "curve_png": {"path": str(CURVE_PNG.relative_to(WORKSPACE)), "sha256": _sha(CURVE_PNG)},
        "consumed_array_count": len(bindings),
        "expected_consumed_array_count": 19 * len(ARRAY_NAMES),
        "all_consumed_run013_vs_fresh_arrays_exact_equal": True,
        "all_consumed_arrays_match_both_manifest_bindings": True,
        "consumed_array_bindings_semantic_sha256": binding_semantic_sha,
        "consumed_array_bindings": bindings,
        "all_19_recomputed_fresh_curve_rows_exact_equal": True,
        "initial_still_rank_zero_and_data_information_exact_zero": True,
        "retrospective_qmt_or_viewer_arrays_consumed": False,
        "causal_progressive_or_frozen_state_modified": False,
        "heldout_opened": False,
        "scientific_acceptance_pass": False,
    }
    _write_new(OUTPUT, output)
    print(json.dumps({
        "output": str(OUTPUT.relative_to(WORKSPACE)),
        "sha256": _sha(OUTPUT),
        "consumed_array_count": len(bindings),
        "fresh_rows_exact_equal": True,
        "heldout_opened": False,
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
