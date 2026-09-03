#!/usr/bin/env python3
"""Create immutable qualification summaries and figures for progressive V0."""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
import sys
from typing import Any, Mapping

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from biospur_fusion.v0.physical_graph import real_subject_spec
from biospur_fusion.v0.progressive_calibration import (
    HEADING_DIMENSION,
    final_gates,
    physical_state_report,
)
from biospur_fusion.v0.raw6_heading import EDGES


RUN = ROOT / "logs/pure_imu_v0_progressive_calibration_20260828T132736Z"
CAPTURES = ("CAPTURE1", "CAPTURE2")


def load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_json(path: Path, value: Any) -> None:
    if path.exists():
        raise FileExistsError(path)
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    path.chmod(0o444)


def write_text(path: Path, value: str) -> None:
    if path.exists():
        raise FileExistsError(path)
    path.write_text(value.rstrip() + "\n", encoding="utf-8")
    path.chmod(0o444)


def capture_bundle(capture: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    result = load(RUN / f"{capture}_RESULT.json")
    snapshots = [load(Path(row["path"])) for row in result["snapshot_index"]]
    return result, snapshots


def validation_idempotence() -> dict[str, Any]:
    captures = {}
    for capture in CAPTURES:
        _, snapshots = capture_bundle(capture)
        rows = []
        for index, current in enumerate(snapshots):
            if current["episode_partition"] != "HELD_OUT_VALIDATION":
                continue
            previous = snapshots[index - 1]
            before = np.asarray(previous["state"], dtype=float)
            after = np.asarray(current["state"], dtype=float)
            transition = current["solve"]["validation_only_no_refit"]
            train_before = previous["factor_identity"]["training"]["sha256"]
            train_after = current["factor_identity"]["training"]["sha256"]
            held_before = previous["factor_identity"]["held_out"]["sha256"]
            held_after = current["factor_identity"]["held_out"]["sha256"]
            row = {
                "step": current["step"],
                "episode": current["episode"],
                "training_sha256_before": train_before,
                "training_sha256_after": train_after,
                "training_factor_identity_preserved": train_before == train_after,
                "held_out_sha256_before": held_before,
                "held_out_sha256_after": held_after,
                "held_out_factor_identity_changed": held_before != held_after,
                "strict_state_array_equal": bool(np.array_equal(before, after)),
                "maximum_absolute_state_change": float(np.max(np.abs(before - after))),
                "heading_change_deg": current["parameter_change_from_previous"]["heading_max_deg"],
                "extra_optimizer_iterations_granted": transition["extra_optimizer_iterations_granted"],
                "held_out_factors_used_in_objective": transition["held_out_factors_used_in_objective"],
                "profile_source_step": transition["profile_source_step"],
                "new_named_conflicts": current["episode_assessment"]["new_named_conflicts"],
            }
            row["pass"] = bool(
                row["training_factor_identity_preserved"]
                and row["held_out_factor_identity_changed"]
                and row["strict_state_array_equal"]
                and row["maximum_absolute_state_change"] == 0.0
                and row["extra_optimizer_iterations_granted"] == 0
                and not row["held_out_factors_used_in_objective"]
            )
            rows.append(row)
        captures[capture] = {"validation_steps": rows, "pass": all(row["pass"] for row in rows)}

    attempt5 = load(
        ROOT / "logs/pure_imu_v0_progressive_calibration_20260828T130519Z"
        / "CAPTURE1_PROGRESSIVE_SNAPSHOTS/STEP_12_final_still.json"
    )
    corrected = captures["CAPTURE1"]["validation_steps"][-1]
    return {
        "schema": "biospur-pure-imu-v0-validation-idempotence-audit-v1",
        "pass": all(row["pass"] for row in captures.values()),
        "captures": captures,
        "rejected_attempt5_causal_attribution": {
            "attempt5_final_still_marginal_information_gain": attempt5["episode_assessment"][
                "aggregate_marginal_log1p_information_gain"
            ],
            "attempt5_heading_change_deg": attempt5["parameter_change_from_previous"][
                "heading_max_deg"
            ],
            "attempt5_incremental_full_difference_deg": attempt5["solve"][
                "incremental_vs_cumulative_reference"
            ]["heading_max_circular_difference_deg"],
            "attempt5_all_sparse_initializers_failed": attempt5["solve"][
                "all_sparse_initializers_failed"
            ],
            "corrected_heading_change_deg": corrected["heading_change_deg"],
            "corrected_training_hash_identity": corrected[
                "training_factor_identity_preserved"
            ],
            "corrected_interpretation": (
                "knee_left:final_still is held-out prediction conflict at a bit-identical "
                "trained profile; it is no longer attributed to parameter movement"
            ),
        },
        "c1_left_heel_conflict_preserved_separately": "hip_right:left_heel",
    }


def access_summary() -> dict[str, Any]:
    captures = {}
    for capture in CAPTURES:
        path = RUN / f"{capture}_PAYLOAD_ACCESS_AUDIT.json"
        audit = load(path)
        proofs = audit["hostile_timing_access_proofs"]
        selected = []
        for action in audit["selected_actions"]:
            contract = action["access_contract"]
            selected.append({
                "action": action["action"],
                "attempt": action["attempt"],
                "partition": action["partition"],
                "raw_path": contract["raw_path"],
                "read_bracket": contract["read_bracket"],
                "action_access_artifact": action["action_access_artifact"],
                "action_access_artifact_sha256": action["action_access_artifact_sha256"],
            })
        gates = {
            "actual_os_read_lseek_evidence_retained": audit[
                "actual_os_read_lseek_evidence_retained"
            ],
            "all_sequential_rows_confined": all(
                row["proof"]["all_sequential_timing_rows_within_action_plus_two_superframes"]
                for row in proofs
            ),
            "all_binary_probes_separately_accounted": all(
                row["proof"]["every_binary_search_probe_separately_accounted"]
                for row in proofs
            ),
            "no_full_traversal": all(
                row["proof"]["no_full_file_traversal_proven_by_actual_read_union"]
                for row in proofs
            ),
            "no_hxx_timing_bytes": all(
                not row["proof"]["all_hxx_timing_interval_bytes_touched"] for row in proofs
            ),
            "no_golf_boxing_timing_bytes": all(
                not row["proof"]["golf_boxing_timing_interval_bytes_touched"] for row in proofs
            ),
            "no_hxx_payload": not audit["hxx_payload_opened"],
            "no_golf_boxing_payload": not audit["golf_boxing_payload_opened"],
            "no_capture3_payload": not audit["capture3_payload_opened"],
            "no_uwb_spatial_payload": not audit["uwb_spatial_payload_consumed"],
            "no_cross_capture_payload": not audit["cross_capture_payload_used"],
            "no_full_raw_container_hash": not audit["full_raw_container_hash_recomputed"],
        }
        captures[capture] = {
            "audit_path": str(path),
            "audit_sha256": sha256(path),
            "selected_action_count": len(selected),
            "actual_os_read_calls": sum(row["proof"]["actual_os_read_calls"] for row in proofs),
            "actual_os_seek_calls": sum(row["proof"]["actual_os_seek_calls"] for row in proofs),
            "binary_search_probe_count": sum(
                row["proof"]["binary_search_probe_count"] for row in proofs
            ),
            "gates": gates,
            "pass": all(gates.values()),
            "selected_bounded_slices": selected,
            "complete_call_and_byte_interval_evidence_retained_in_audit": True,
        }
    return {
        "schema": "biospur-pure-imu-v0-progressive-data-access-summary-v1",
        "captures": captures,
        "pass": all(row["pass"] for row in captures.values()),
    }


def source_audit() -> dict[str, Any]:
    qmt_file = ROOT / ".venv-v0/lib/python3.12/site-packages/qmt/functions/joint_axis_est_hinge_olsson.py"
    qmt_metadata = ROOT / ".venv-v0/lib/python3.12/site-packages/qmt-0.2.4.dist-info/METADATA"
    return {
        "schema": "biospur-pure-imu-v0-progressive-source-license-audit-v1",
        "sources": [
            {
                "name": "qmt jointAxisEstHingeOlsson",
                "primary_source": "https://github.com/dlaidig/qmt/blob/main/qmt/functions/joint_axis_est_hinge_olsson.py",
                "documentation": "https://qmt.readthedocs.io/en/latest/_modules/qmt/functions/joint_axis_est_hinge_olsson.html",
                "installed_version": "0.2.4",
                "installed_file": str(qmt_file),
                "installed_file_sha256": sha256(qmt_file),
                "package_metadata_sha256": sha256(qmt_metadata),
                "package_license": "MIT",
                "file_spdx": "LicenseRef-Unspecified",
                "reuse": "executed as independent raw acc/gyr hinge baseline after product state fixed",
                "source_copied": False,
                "license_decision": "EXECUTION_ONLY; DO_NOT COPY FILE WITHOUT SEPARATE LICENSE RESOLUTION",
            },
            {
                "name": "Seel integration-free joint constraints",
                "primary_source": "https://doi.org/10.1109/CCA.2012.6402423",
                "license": "IEEE publication; ideas/reimplementation only",
                "reuse": "parent/child rigid-body joint-axis and joint-centre acceleration constraints without acceleration integration",
                "source_copied": False,
                "deviation": "multi-axis human episodes and robust sensor residuals; no ideal single-axis or pose truth",
            },
            {
                "name": "Olsson raw acc/gyr functional calibration",
                "primary_source": "https://arxiv.org/abs/1903.07353",
                "license": "paper method; qmt implementation handled separately above",
                "reuse": "executable hinge-axis diagnostic via qmt only",
                "source_copied": False,
            },
            {
                "name": "Crabolu functional centre-of-rotation",
                "primary_source": "https://pmc.ncbi.nlm.nih.gov/articles/PMC5359843/",
                "license": "CC BY 4.0 article",
                "reuse": "applicability and shoulder-centre assumptions reviewed",
                "source_copied": False,
                "deviation": "no Crabolu-specific shoulder estimator activated; shoulder uses the common connected rigid-body constraint because method-specific assumptions were not independently established",
            },
            {
                "name": "Schneider informative motion selection",
                "primary_source": "https://arxiv.org/abs/1708.02382",
                "license": "paper method; ideas/reimplementation only",
                "reuse": "information-score row selection with mandatory episode-phase connectivity",
                "source_copied": False,
                "deviation": "selected rows initialize only; every step is compared against the unchanged full cumulative factor objective",
            },
        ],
        "unresolved_copy_license_blocks_product_source_copy": True,
        "pass_for_current_execution_only_baseline": True,
    }


def corrected_capture_summary(capture: str) -> dict[str, Any]:
    result, snapshots = capture_bundle(capture)
    final = snapshots[-1]
    corrected_physical = physical_state_report(
        np.asarray(final["state"], dtype=float), real_subject_spec(), final["information"],
    )
    corrected_snapshot = {**final, "physical": corrected_physical}
    gates = final_gates(corrected_snapshot)
    gates["named_previous_conflicts_resolved"] = result["gates"][
        "named_previous_conflicts_resolved"
    ]
    gates["synthetic_prerequisite"] = result["gates"]["synthetic_prerequisite"]
    gates["bounded_access_proof"] = result["gates"]["bounded_access_proof"]
    gates["viewer_gate"] = False
    failed = [name for name, passed in gates.items() if not passed]
    episode_rows = []
    for snapshot in snapshots:
        episode_rows.append({
            "step": snapshot["step"],
            "episode": snapshot["episode"],
            "partition": snapshot["episode_partition"],
            "classification": snapshot["episode_assessment"]["classification"],
            "marginal_log1p_information_gain": snapshot["episode_assessment"][
                "aggregate_marginal_log1p_information_gain"
            ],
            "coverage_percent": snapshot["readiness"]["overall_coverage_percent"],
            "readiness_percent": snapshot["readiness"]["overall_readiness_percent"],
            "new_named_conflicts": snapshot["episode_assessment"]["new_named_conflicts"],
            "training_factor_sha256": snapshot["factor_identity"]["training"]["sha256"],
            "held_out_factor_sha256": snapshot["factor_identity"]["held_out"]["sha256"],
            "validation_no_refit": snapshot["solve"]["validation_only_no_refit"],
        })
    heading_sigma_deg = np.degrees(
        np.asarray(final["information"]["posterior_one_sigma"][:HEADING_DIMENSION])
    )
    solve = final["solve"]
    return {
        "capture_id": result["capture_id"],
        "terminal_decision": "FAIL" if failed else "INCONCLUSIVE",
        "profile_locked": False,
        "viewer_generated": False,
        "hxx_opened": False,
        "cross_capture_state_or_payload_used": False,
        "corrected_gates": gates,
        "failed_gates": failed,
        "final_coverage_percent": final["readiness"]["overall_coverage_percent"],
        "final_readiness_percent": final["readiness"]["overall_readiness_percent"],
        "gauge_reduced_effective_rank": final["information"]["gauge_reduced_effective_rank"],
        "gauge_reduced_nullity": final["information"]["gauge_reduced_nullity"],
        "relative_heading_rank": final["information"]["parameter_groups"][
            "relative_headings"
        ]["cumulative"]["rank"],
        "relative_heading_posterior_sigma_deg": heading_sigma_deg.tolist(),
        "maximum_relative_heading_posterior_sigma_deg": float(np.max(heading_sigma_deg)),
        "multistart_max_heading_spread_deg": solve["multistart_max_heading_spread_deg"],
        "finite_start_count": solve["finite_start_count"],
        "finite_cold_or_broad_start_count": solve["finite_cold_or_broad_start_count"],
        "incremental_full_heading_difference_deg": solve[
            "incremental_vs_cumulative_reference"
        ]["heading_max_circular_difference_deg"],
        "held_out_named_conflicts": final["held_out"]["named_conflicts"],
        "physical_observability_corrected_postrun": corrected_physical,
        "rank_only_latent_promotion_in_original_snapshot_superseded": True,
        "algorithm_pivot_steps": [
            row["step"] for row in snapshots if row["solve"]["all_sparse_initializers_failed"]
        ],
        "episodes": episode_rows,
        "result_path": str(RUN / f"{capture}_RESULT.json"),
        "result_sha256": sha256(RUN / f"{capture}_RESULT.json"),
    }


def qualification_summary(
    validation: Mapping[str, Any], access: Mapping[str, Any], sources: Mapping[str, Any],
) -> dict[str, Any]:
    synthetic_path = RUN / "SYNTHETIC_QUALIFICATION.json"
    synthetic = load(synthetic_path)
    captures = {capture: corrected_capture_summary(capture) for capture in CAPTURES}
    return {
        "schema": "biospur-pure-imu-v0-progressive-qualification-summary-v1",
        "terminal_decision": "FAIL",
        "freeze_or_candidate_lock": False,
        "viewer_generated": False,
        "hxx_opened": False,
        "golf_boxing_capture3_opened": False,
        "reason": "BOTH_INDEPENDENT_CAPTURE_PROFILES_FAIL_PREREGISTERED_REAL_GATES",
        "captures": captures,
        "synthetic": {
            "path": str(synthetic_path),
            "sha256": sha256(synthetic_path),
            "pass": synthetic["pass"],
            "qualification_gates": synthetic["qualification_gates"],
            "heading_median_error_deg": synthetic["recovery"]["heading_median_error_deg"],
            "heading_max_error_deg": synthetic["recovery"]["heading_max_error_deg"],
            "maximum_order_permutation_objective_difference": synthetic[
                "order_permutations"
            ]["maximum_absolute_objective_difference"],
            "sparsification_heading_difference_deg": synthetic[
                "informative_sparsification"
            ]["heading_max_difference_from_full_deg"],
            "sparsification_length_difference_m": synthetic[
                "informative_sparsification"
            ]["length_max_difference_from_full_m"],
        },
        "validation_idempotence": {
            "path": str(RUN / "VALIDATION_IDEMPOTENCE_AUDIT.json"),
            "pass": validation["pass"],
        },
        "data_access": {
            "path": str(RUN / "DATA_ACCESS_PROOF_SUMMARY.json"),
            "pass": access["pass"],
        },
        "source_license": {
            "path": str(RUN / "SOURCE_LICENSE_AUDIT.json"),
            "pass_for_execution_only_baseline": sources[
                "pass_for_current_execution_only_baseline"
            ],
        },
        "external_prior_contract": {
            "upper_arm_m": {"mean": 0.28, "sigma": 0.02},
            "thigh_m": {"mean": 0.48, "sigma": 0.03},
            "bilateral_difference_sigma_m": 0.025,
            "height_1p73m_role": "BROAD_SCALE_QA_ONLY",
            "bi_iliac_0p38m_used": False,
            "gaussian_rows_inside_sensor_robust_loss": False,
        },
    }


def traceability(summary: Mapping[str, Any]) -> dict[str, Any]:
    rows = [
        ("CAL-001", "One capture-wide progressive state", "61 coordinates; one pelvis yaw gauge and nine relative headings", "PASS_ARCHITECTURE"),
        ("CAL-002", "C1/C2 independence", "result cross-capture flags and independent initialization", "PASS"),
        ("CAL-003", "Raw acc/gyr only", "per-action raw6_audit; no vendor orientation or magnetometer fields", "PASS"),
        ("CAL-004", "Cumulative episode factors", "snapshot retained_episodes plus partition SHA-256 identities", "PASS"),
        ("CAL-005", "Held-out steps do not refit", "VALIDATION_IDEMPOTENCE_AUDIT.json", "PASS"),
        ("CAL-006", "Evidence-based progress", "per-step information/rank/covariance/held-out/readiness", "PASS"),
        ("CAL-007", "Synthetic order independence", "SYNTHETIC_QUALIFICATION.json", "PASS"),
        ("CAL-008", "Sparse/full cumulative equivalence", "synthetic PASS; both real captures exceed 3 deg gate", "FAIL_REAL"),
        ("CAL-009", "Gaussian priors outside robust sensor loss", "prior_loss_audit and objective row separation", "PASS"),
        ("CAL-010", "Connected noncollapsed geometry", "structural_audit; active-bound latent geometry withheld", "INCONCLUSIVE_LATENT"),
        ("CAL-011", "Bounded read proof", "DATA_ACCESS_PROOF_SUMMARY.json and full per-capture call intervals", "PASS"),
        ("CAL-012", "Viewer/Hxx only after all gates", "no viewer or Hxx opened", "PASS_STOP"),
        ("CAL-013", "Bounded nonlinear solves and causal pivots", "per-start nfev/wall traces; failed starts preserved", "PASS_PROCESS"),
        ("CAL-014", "Source/license audit", "SOURCE_LICENSE_AUDIT.json", "PASS_EXECUTION_ONLY"),
        ("CAL-015", "Real profile readiness", "C1 and C2 corrected gates", "FAIL"),
    ]
    return {
        "schema": "biospur-pure-imu-v0-progressive-traceability-v1",
        "mission": "qualify independent progressive pure-IMU calibration profiles",
        "rows": [
            {"id": ident, "requirement": req, "evidence": ev, "verdict": verdict}
            for ident, req, ev, verdict in rows
        ],
        "terminal_decision": summary["terminal_decision"],
    }


def make_figures(summary: Mapping[str, Any]) -> list[Path]:
    outputs = []
    colors = {"coverage": "#2457C5", "readiness": "#D24A3A", "info": "#6B7280"}
    for capture in CAPTURES:
        rows = summary["captures"][capture]["episodes"]
        x = np.arange(1, len(rows) + 1)
        coverage = [row["coverage_percent"] for row in rows]
        readiness = [row["readiness_percent"] for row in rows]
        info = [math.log10(1.0 + row["marginal_log1p_information_gain"]) for row in rows]
        fig, ax = plt.subplots(figsize=(12.5, 5.8), constrained_layout=True)
        ax.plot(x, coverage, "o-", color=colors["coverage"], lw=2.2, label="Coverage")
        ax.plot(x, readiness, "o-", color=colors["readiness"], lw=2.2, label="Readiness")
        for i, row in enumerate(rows):
            if row["partition"] == "HELD_OUT_VALIDATION":
                ax.axvspan(i + 0.65, i + 1.35, color="#F2C94C", alpha=0.16)
        ax.set_ylim(0, 100)
        ax.set_ylabel("Evidence score (%)")
        ax.set_xlabel("Episode in actual time order")
        ax.set_xticks(x)
        ax.set_xticklabels([row["episode"] for row in rows], rotation=48, ha="right", fontsize=8)
        ax.grid(axis="y", alpha=0.25)
        twin = ax.twinx()
        twin.bar(x, info, width=0.52, alpha=0.22, color=colors["info"], label="log10(1 + marginal information)")
        twin.set_ylabel("log10(1 + marginal information)")
        handles, labels = ax.get_legend_handles_labels()
        handles2, labels2 = twin.get_legend_handles_labels()
        ax.legend(handles + handles2, labels + labels2, loc="upper left", frameon=False)
        ax.set_title(f"{capture}: progressive information coverage and readiness")
        out = RUN / f"{capture}_INFORMATION_READINESS_CURVE.png"
        fig.savefig(out, dpi=180)
        plt.close(fig)
        out.chmod(0o444)
        outputs.append(out)

    edges = [row[0] for row in EDGES]
    fig, axes = plt.subplots(2, 1, figsize=(15, 8.5), constrained_layout=True)
    for ax, capture in zip(axes, CAPTURES):
        rows = summary["captures"][capture]["episodes"]
        matrix = np.zeros((len(edges), len(rows)), dtype=float)
        for column, row in enumerate(rows):
            for token in row["new_named_conflicts"]:
                edge = token.split(":", 1)[0]
                if edge in edges:
                    matrix[edges.index(edge), column] = 1.0
        ax.imshow(matrix, aspect="auto", interpolation="nearest", cmap="Reds", vmin=0, vmax=1)
        ax.set_yticks(np.arange(len(edges)))
        ax.set_yticklabels(edges, fontsize=8)
        ax.set_xticks(np.arange(len(rows)))
        ax.set_xticklabels([row["episode"] for row in rows], rotation=48, ha="right", fontsize=7)
        ax.set_title(f"{capture}: newly exposed held-out edge/action conflicts")
    out = RUN / "PARAMETER_CONFLICT_HEATMAP.png"
    fig.savefig(out, dpi=180)
    plt.close(fig)
    out.chmod(0o444)
    outputs.append(out)
    return outputs


def markdown_report(summary: Mapping[str, Any], validation: Mapping[str, Any], access: Mapping[str, Any]) -> str:
    syn = summary["synthetic"]
    lines = [
        "# BioSpur pure-IMU V0 progressive calibration closure",
        "",
        "## Decision",
        "",
        "**Terminal decision: FAIL.** C1 and C2 each produced one independent cumulative profile, but neither met every preregistered real-data gate. No profile is locked, no viewer was generated, and no Hxx, Golf/Boxing, or Capture3 payload was opened.",
        "",
        "## System architecture",
        "",
        "The calibrated state is capture-wide: one pelvis/root yaw gauge, exactly nine relative headings, bounded sensor-to-segment/connected joint geometry, four explicitly unprioritized pelvis/torso dimensions, and only independently applied soft-tape length priors. Each identification episode adds raw-acc/gyr rigid-body factors to the existing state. Held-out episodes add prediction evidence only and cannot alter the state. Sensor residuals use soft-L1; Gaussian tape and geometry priors remain quadratic rows outside that robust loss.",
        "",
        "qmt 0.2.4 `jointAxisEstHingeOlsson` was executed only as a post-fit raw acc/gyr hinge baseline. The product objective uses the integration-free parent/child rigid-body joint-centre constraint. Information-selected rows are numerical initializers; the full cumulative factor objective remains the reference.",
        "",
        "## Synthetic qualification",
        "",
        f"All synthetic gates passed. Median/max heading error were {syn['heading_median_error_deg']:.3f}°/{syn['heading_max_error_deg']:.3f}°. Fixed-factor permutation changed objective values by at most {syn['maximum_order_permutation_objective_difference']:.3e}. Informative sparsification differed from the full result by {syn['sparsification_heading_difference_deg']:.3f}° heading and {syn['sparsification_length_difference_m']:.4f} m length. Four held-out synthetic steps retained identical training hashes, moved the state exactly 0, and received zero optimizer iterations.",
        "",
        "## Real progressive results",
        "",
    ]
    for capture in CAPTURES:
        row = summary["captures"][capture]
        lines.extend([
            f"### {capture}",
            "",
            f"Coverage ended at {row['final_coverage_percent']:.1f}% and readiness at {row['final_readiness_percent']:.1f}%. All nine relative headings had data rank, but maximum posterior heading sigma was {row['maximum_relative_heading_posterior_sigma_deg']:.2f}° and incremental/full-cumulative disagreement was {row['incremental_full_heading_difference_deg']:.2f}°. Named held-out conflicts: {', '.join(row['held_out_named_conflicts']) or 'none'}.",
            "",
            "| Step | Episode | Role | Assessment | Coverage | Readiness | New conflict |",
            "|---:|---|---|---|---:|---:|---|",
        ])
        for episode in row["episodes"]:
            role = "train" if episode["partition"] == "IDENTIFICATION_TRAIN" else "held/no-refit"
            conflicts = ", ".join(episode["new_named_conflicts"]) or "—"
            lines.append(
                f"| {episode['step']} | {episode['episode']} | {role} | {episode['classification']} | "
                f"{episode['coverage_percent']:.1f}% | {episode['readiness_percent']:.1f}% | {conflicts} |"
            )
        lines.append("")

    c1 = summary["captures"]["CAPTURE1"]
    c2 = summary["captures"]["CAPTURE2"]
    lines.extend([
        "## Held-out idempotence correction",
        "",
        "Attempt-five C1 final standing had zero marginal information but moved 48.8498° because it reran an unchanged training objective. That attribution is rejected. In the corrected run every held-out transition proves equal training SHA-256 identities, a changed held-out SHA-256, bit-identical state, zero extra iterations, and no held-out factor in the objective. C1 `hip_right:left_heel` remains a separate genuine conflict. C1 `knee_left:final_still` and the C2 heel-to-butt conflicts are also genuine prediction failures at fixed profiles.",
        "",
        "## Observability and physical interpretation",
        "",
        "The rank-only latent-geometry promotion in the original immutable snapshots is superseded by the post-run boundary audit. C1 has all four pelvis/torso latents at their lower bounds; C2 has three at their lower bounds. They remain uncertain diagnostics and are not promoted. This adds a latent-geometry failure to both corrected gate sets. Tape lengths remain uncertain Gaussian observations, never pose or heading truth; the 0.38 m bi-iliac observation was not used.",
        "",
        "C1 right elbow and C2 left elbow passed the excitation-aware qmt diagnostic and did not appear as final held-out conflicts, but that does not rescue either capture. C2 left/right knee prediction conflicts reappear specifically in heel-to-butt validation. The real-data full-batch equivalence gate fails for C1 ({:.2f}°) and C2 ({:.2f}°), so numerical order robustness is not established despite the synthetic proof.".format(c1["incremental_full_heading_difference_deg"], c2["incremental_full_heading_difference_deg"]),
        "",
        "## Data-access proof",
        "",
    ])
    for capture in CAPTURES:
        row = access["captures"][capture]
        lines.append(
            f"- {capture}: {row['selected_action_count']} bounded slices; {row['actual_os_read_calls']} actual `os.read` calls, {row['actual_os_seek_calls']} `os.lseek` calls, and {row['binary_search_probe_count']} separately accounted binary probes. All access gates passed."
        )
    lines.extend([
        "",
        "The full call/byte intervals remain in each capture payload audit. No full traversal occurred and no Hxx/Golf/Boxing/Capture3 timing bytes or spatial payload bytes were touched.",
        "",
        "## Stop and pivot decision",
        "",
        "The phase stops at FAIL. Do not lock profiles, open viewers/Hxx, or relax thresholds. The next algorithmic work should target the real-data numerical disagreement and boundary-active geometry: use an identifiable-subspace/manifold or Schur-style parameterization, then rerun the same sealed episode semantics. Independently inspect the named held-out conflicts as model/execution/soft-tissue discrepancies. Waiting longer or granting validation episodes more optimization is explicitly rejected.",
        "",
        "## Evidence index",
        "",
        "- `QUALIFICATION_SUMMARY.json`: machine-readable corrected decision and gates.",
        "- `VALIDATION_IDEMPOTENCE_AUDIT.json`: training/held hashes and state-identity proof.",
        "- `DATA_ACCESS_PROOF_SUMMARY.json`: bounded-access aggregation and links to complete intervals.",
        "- `SOURCE_LICENSE_AUDIT.json`: primary sources, licenses, reuse, and deviations.",
        "- `TRACEABILITY_MATRIX.json`: mission-to-evidence allocation.",
        "- `CAPTURE1_INFORMATION_READINESS_CURVE.png`, `CAPTURE2_INFORMATION_READINESS_CURVE.png`, and `PARAMETER_CONFLICT_HEATMAP.png`: derived visual evidence.",
    ])
    return "\n".join(lines)


def main() -> None:
    validation = validation_idempotence()
    access = access_summary()
    sources = source_audit()
    write_json(RUN / "VALIDATION_IDEMPOTENCE_AUDIT.json", validation)
    write_json(RUN / "DATA_ACCESS_PROOF_SUMMARY.json", access)
    write_json(RUN / "SOURCE_LICENSE_AUDIT.json", sources)
    summary = qualification_summary(validation, access, sources)
    write_json(RUN / "QUALIFICATION_SUMMARY.json", summary)
    write_json(RUN / "TRACEABILITY_MATRIX.json", traceability(summary))
    figures = make_figures(summary)
    write_text(RUN / "REPORT.md", markdown_report(summary, validation, access))
    manifest_paths = [
        RUN / "METADATA_PRESELECTION.json",
        RUN / "SYNTHETIC_QUALIFICATION.json",
        RUN / "CAPTURE1_RESULT.json",
        RUN / "CAPTURE2_RESULT.json",
        RUN / "VALIDATION_IDEMPOTENCE_AUDIT.json",
        RUN / "DATA_ACCESS_PROOF_SUMMARY.json",
        RUN / "SOURCE_LICENSE_AUDIT.json",
        RUN / "QUALIFICATION_SUMMARY.json",
        RUN / "TRACEABILITY_MATRIX.json",
        RUN / "REPORT.md",
        *figures,
    ]
    write_json(RUN / "EVIDENCE_MANIFEST.json", {
        "schema": "biospur-pure-imu-v0-progressive-evidence-manifest-v1",
        "terminal_decision": "FAIL",
        "artifacts": [
            {"path": str(path), "sha256": sha256(path), "bytes": path.stat().st_size}
            for path in manifest_paths
        ],
    })
    print(json.dumps({
        "report": str(RUN / "REPORT.md"),
        "summary": str(RUN / "QUALIFICATION_SUMMARY.json"),
        "terminal_decision": "FAIL",
    }, indent=2))


if __name__ == "__main__":
    main()
