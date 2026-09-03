#!/usr/bin/env python3
"""Render RUN013 causal 19-prefix diagnostics without retrospective backflow."""
from __future__ import annotations

import sys

sys.dont_write_bytecode = True

from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
from typing import Any

import numpy as np


WORKSPACE = Path("/mnt/nrf_ssd/nRF_dev/BioSpur_Fusion/Fusion_Part")
RUN = WORKSPACE / "logs/c2_basis_progressive_20260829T102836Z"
MANIFEST = RUN / "C2_REAL_DIAGNOSTIC_FROZEN_STATE_013.json"
NPZ = RUN / "C2_REAL_DIAGNOSTIC_FROZEN_STATE_013.npz"
OUTPUT = RUN / "CONTINUATION_SPRINT/RUN013_CAUSAL_PROGRESSIVE_CURVES_001"


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


def _write_new_json(path: Path, value: Any) -> None:
    with path.open("x", encoding="utf-8") as handle:
        json.dump(value, handle, indent=2, sort_keys=True, allow_nan=False)
        handle.write("\n")


def _abbreviation(action: str) -> str:
    number, name = action.split("_", 1)
    words = name.split("_")
    compact = " ".join(word[:5] for word in words[:2])
    return f"{number} {compact}"


def main() -> int:
    if Path.cwd().resolve() != WORKSPACE:
        raise RuntimeError("causal curve renderer requires the canonical workspace")
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
    sys.dont_write_bytecode = True
    os.environ["TMPDIR"] = str(RUN / "tmp")
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    if (
        manifest.get("schema")
        != "biospur-c2-reloadable-frozen-scientific-state-manifest-v1"
        or manifest.get("diagnostic_execution_role") != "REAL_DIAGNOSTIC"
        or manifest.get("scientific_state_mutable") is not False
        or manifest.get("heldout_opened_when_exported") is not False
        or manifest.get("fresh_verification_claimed") is not False
        or manifest.get("fresh_verification") is not None
        or manifest.get("scientific_acceptance_pass") is not False
        or manifest.get("npz")
        != {"path": str(NPZ.relative_to(WORKSPACE)), "sha256": _sha(NPZ)}
    ):
        raise RuntimeError("RUN013 frozen causal authority is inconsistent")
    prefixes = list(manifest["structure"]["progressive_prefixes"])
    if (
        len(prefixes) != 19
        or [int(row["chronological_index"]) for row in prefixes] != list(range(19))
    ):
        raise RuntimeError("RUN013 causal prefixes do not match the exact 19-action chronology")
    if any(
        key.startswith("postfreeze_")
        for key in manifest
    ):
        raise RuntimeError("retrospective evidence appeared in the causal RUN013 manifest")

    rows: list[dict[str, Any]] = []
    consumed_bindings: dict[str, Any] = {}
    with np.load(NPZ, allow_pickle=False) as archive:
        if any(name.startswith("heading/00/") for name in archive.files):
            raise RuntimeError("post-freeze heading replay arrays appeared in causal RUN013 NPZ")
        for prefix_row in prefixes:
            index = int(prefix_row["chronological_index"])
            prefix = f"progressive_prefix/{index:02d}"
            names = {
                key: f"{prefix}/{key}"
                for key in (
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
            }
            arrays = {key: np.asarray(archive[name]) for key, name in names.items()}
            for key, name in names.items():
                binding = _array_binding(arrays[key])
                if manifest["array_bindings"].get(name) != binding:
                    raise RuntimeError(f"RUN013 causal prefix array binding failed: {name}")
                consumed_bindings[name] = binding
            scalars = arrays["prequential_and_information_scalars"]
            if scalars.shape != (9,):
                raise RuntimeError("RUN013 prequential scalar layout changed")
            branch_weights = arrays["branch_weights"]
            if (
                branch_weights.shape != (16,)
                or np.any(branch_weights < 0.0)
                or not np.isclose(np.sum(branch_weights), 1.0, atol=1e-10)
            ):
                raise RuntimeError("RUN013 causal branch posterior is invalid")
            positive = branch_weights[branch_weights > 0.0]
            entropy = -float(np.sum(positive * np.log(positive)))
            physical_support = manifest["structure"]["physical_trajectory_support"].get(
                str(index), {}
            )
            row = {
                "chronological_index": index,
                "action": str(prefix_row["action"]),
                "prequential_status": str(prefix_row["prequential_status"]),
                "prequential_nll_before_ingest": float(scalars[0]),
                "prequential_observed_rank": int(round(float(scalars[1]))),
                "prequential_nll_per_observed_coordinate": (
                    None if int(round(float(scalars[1]))) == 0
                    else float(scalars[0] / scalars[1])
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
                "maximum_branch_weight": float(np.max(branch_weights)),
                "positive_branch_count": int(np.count_nonzero(branch_weights > 0.0)),
                "effective_branch_support_exp_entropy": float(np.exp(entropy)),
                "branch_concentration": float(scalars[7]),
                "physical_validity": float(scalars[8]),
                "causal_physical_trajectory_branch_count": len(physical_support),
            }
            if (
                row["prequential_observed_rank"]
                != int(prefix_row["prequential_observed_rank"])
                or row["data_information_rank"]
                != int(prefix_row["data_information_rank"])
            ):
                raise RuntimeError("RUN013 causal scalar and structure rank disagree")
            rows.append(row)
        initial_information = np.asarray(
            archive["progressive_prefix/00/data_information"], dtype=float,
        )

    initial_zero_information = bool(
        rows[0]["data_information_rank"] == 0
        and rows[0]["prequential_observed_rank"] == 0
        and rows[0]["prequential_status"]
        == "RANK_ZERO_LOCAL_NO_UPDATE_NO_GAUSSIAN_COORDINATES_SCORED"
        and np.array_equal(initial_information, np.zeros_like(initial_information))
    )
    if not initial_zero_information:
        raise RuntimeError("initial still falsely contributes causal data information")

    OUTPUT.mkdir(parents=True, exist_ok=False)
    data_path = OUTPUT / "CAUSAL_19_PREFIX_CURVE_DATA.json"
    _write_new_json(data_path, {
        "schema": "biospur-c2-causal-19-prefix-curve-data-v1",
        "source_frozen_manifest": {
            "path": str(MANIFEST.relative_to(WORKSPACE)), "sha256": _sha(MANIFEST),
        },
        "source_frozen_npz": {
            "path": str(NPZ.relative_to(WORKSPACE)), "sha256": _sha(NPZ),
        },
        "rows": rows,
        "retrospective_trajectory_arrays_consumed": False,
        "heldout_opened": False,
    })

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    x = np.arange(19)
    labels = [_abbreviation(row["action"]) for row in rows]
    figure, axes = plt.subplots(2, 2, figsize=(18, 11), dpi=150)
    rank_axis = axes[0, 0]
    rank_axis.plot(x, [row["data_information_rank"] for row in rows], "o-", label="data information rank")
    rank_axis.plot(x, [row["prequential_observed_rank"] for row in rows], "s--", label="ingest-time observed rank")
    rank_axis.set_ylabel("rank (of 70D gauge-reduced state)")
    rank_axis.set_title("Causal information support")
    rank_axis.legend(loc="upper left", fontsize=8)
    pseudolog_axis = rank_axis.twinx()
    pseudolog_axis.plot(
        x,
        [np.nan if row["data_information_pseudologdet"] is None else row["data_information_pseudologdet"] for row in rows],
        color="#7c3aed", marker=".", label="data pseudologdet",
    )
    pseudolog_axis.set_ylabel("data information pseudologdet")

    uncertainty_axis = axes[0, 1]
    for key, label, style in (
        ("prequential_prior_uncertainty_trace", "pre-ingest predictive total", "--"),
        ("posterior_total_uncertainty_trace", "post-ingest total", "-"),
        ("measurement_statistical_uncertainty_trace", "measurement/statistical", "-"),
        ("shared_systematic_uncertainty_trace", "shared systematic", "-"),
        ("temporal_migration_uncertainty_trace", "temporal migration", ":"),
    ):
        uncertainty_axis.plot(x, [row[key] for row in rows], style, marker=".", label=label)
    uncertainty_axis.set_yscale("symlog", linthresh=1e-3)
    uncertainty_axis.set_ylabel("covariance trace (mixed registered state units²)")
    uncertainty_axis.set_title("Authoritative uncertainty decomposition")
    uncertainty_axis.legend(fontsize=7, ncol=2)

    branch_axis = axes[1, 0]
    branch_axis.plot(x, [row["maximum_branch_weight"] for row in rows], "o-", label="maximum branch weight")
    branch_axis.plot(x, [row["branch_concentration"] for row in rows], "s--", label="branch concentration")
    branch_axis.plot(x, [row["physical_validity"] for row in rows], "d-.", label="physical validity evidence")
    branch_axis.set_ylim(-0.03, 1.03)
    branch_axis.set_ylabel("weight / evidence scalar")
    branch_axis.set_title("Causal branch and physical evidence")
    branch_axis.legend(loc="upper left", fontsize=8)
    support_axis = branch_axis.twinx()
    support_axis.step(
        x, [row["causal_physical_trajectory_branch_count"] for row in rows],
        where="mid", color="#7c3aed", label="causal physical branches",
    )
    support_axis.set_ylim(-0.2, 16.2)
    support_axis.set_ylabel("causal physical branch count")

    prequential_axis = axes[1, 1]
    nll_per_coordinate = [
        np.nan if row["prequential_nll_per_observed_coordinate"] is None
        else row["prequential_nll_per_observed_coordinate"]
        for row in rows
    ]
    prequential_axis.plot(x, nll_per_coordinate, "o-", label="NLL / informed coordinate")
    prequential_axis.set_ylabel("pre-ingest NLL per informed coordinate")
    prequential_axis.set_title("Ingestion-time prequential score (rank-zero omitted)")
    prequential_axis.legend(loc="upper left", fontsize=8)
    observed_axis = prequential_axis.twinx()
    observed_axis.bar(
        x, [row["prequential_observed_rank"] for row in rows],
        width=0.62, alpha=0.18, color="#7c3aed",
    )
    observed_axis.set_ylabel("informed coordinates scored")

    for axis in axes.flat:
        axis.set_xticks(x)
        axis.set_xticklabels(labels, rotation=52, ha="right", fontsize=7)
        axis.set_xlabel("sealed chronological action / prefix")
        axis.grid(alpha=0.22)
    figure.suptitle(
        "RUN013 genuine causal progressive diagnostics — 19 prefixes\n"
        "training range only; retrospective QMT/avatar evidence excluded; NOT PASS",
        color="#991b1b", fontweight="bold", fontsize=15,
    )
    figure.subplots_adjust(top=0.89, bottom=0.17, hspace=0.48, wspace=0.30)
    plot_path = OUTPUT / "CAUSAL_19_PREFIX_PROGRESS_PREQUENTIAL_CURVES.png"
    figure.savefig(plot_path, facecolor="white")
    plt.close(figure)

    ranks = [row["data_information_rank"] for row in rows]
    audit_path = OUTPUT / "CAUSAL_19_PREFIX_PROGRESS_PREQUENTIAL_AUDIT.json"
    audit = {
        "schema": "biospur-c2-causal-19-prefix-progress-prequential-audit-v1",
        "created_local": datetime.now().astimezone().isoformat(),
        "source_script": {
            "path": str(Path(__file__).resolve().relative_to(WORKSPACE)),
            "sha256": _sha(Path(__file__).resolve()),
        },
        "source_frozen_manifest": {
            "path": str(MANIFEST.relative_to(WORKSPACE)), "sha256": _sha(MANIFEST),
        },
        "source_frozen_npz": {
            "path": str(NPZ.relative_to(WORKSPACE)), "sha256": _sha(NPZ),
        },
        "curve_data": {
            "path": str(data_path.relative_to(WORKSPACE)), "sha256": _sha(data_path),
        },
        "curve_png": {
            "path": str(plot_path.relative_to(WORKSPACE)), "sha256": _sha(plot_path),
            "pixel_dimensions": [2700, 1650],
        },
        "consumed_causal_array_bindings": consumed_bindings,
        "chronological_prefix_count": len(rows),
        "exact_chronological_indices": [row["chronological_index"] for row in rows],
        "initial_still_zero_data_information": initial_zero_information,
        "final_data_information_rank": ranks[-1],
        "data_information_rank_nondecreasing": all(
            right >= left for left, right in zip(ranks[:-1], ranks[1:], strict=True)
        ),
        "rank_zero_prequential_actions": [
            row["action"] for row in rows if row["prequential_observed_rank"] == 0
        ],
        "final_positive_branch_count": rows[-1]["positive_branch_count"],
        "final_maximum_branch_weight": rows[-1]["maximum_branch_weight"],
        "causal_physical_trajectory_first_prefix": next(
            (row["chronological_index"] for row in rows if row["causal_physical_trajectory_branch_count"] > 0),
            None,
        ),
        "retrospective_qmt_or_avatar_arrays_consumed": False,
        "postfreeze_frames_relabelled_as_causal_metrics": False,
        "fresh_verification_claimed": False,
        "heldout_opened": False,
        "scientific_acceptance_pass": False,
    }
    _write_new_json(audit_path, audit)
    for path in (data_path, plot_path, audit_path):
        path.chmod(0o444)
    print(json.dumps({
        "audit": {"path": str(audit_path.relative_to(WORKSPACE)), "sha256": _sha(audit_path)},
        "curve": {"path": str(plot_path.relative_to(WORKSPACE)), "sha256": _sha(plot_path)},
        "data": {"path": str(data_path.relative_to(WORKSPACE)), "sha256": _sha(data_path)},
        "initial_still_zero_data_information": initial_zero_information,
        "final_data_information_rank": ranks[-1],
        "heldout_opened": False,
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
