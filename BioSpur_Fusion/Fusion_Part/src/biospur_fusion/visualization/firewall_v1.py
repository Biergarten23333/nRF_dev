"""One-way payload release state machine for visualization centerline V1."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from .centerline_v1 import DISCLAIMER, NODES


CALIBRATION_PREVIEW_ACTIONS = (
    "initial_still_attempt2",
    "t_pose",
    "arms",
    "left_elbow",
    "right_elbow_attempt2",
    "left_knee",
    "right_knee",
    "left_heel",
    "right_heel",
    "squats",
    "trunk",
    "golf_swing",
    "boxing",
)


class FirewallError(RuntimeError):
    """A requested state transition would leak a sealed payload."""


def evaluate_calibration_checks(
    report: Mapping[str, Any], gates: Mapping[str, Any]
) -> dict[str, Any]:
    """Apply predeclared gates to calibration-only diagnostic results."""
    gate = gates["calibration_gates"]
    failures: list[str] = []
    quotient = report.get("quotient_observability", {})
    if not quotient.get("axial_twist_removed", False):
        failures.append("AXIAL_TWIST_NOT_REMOVED_FROM_QUOTIENT")
    if float(quotient.get("minimum_retained_relative_singular_value", -1.0)) < float(
        gate["observability_relative_singular_value_threshold"]
    ):
        failures.append("QUOTIENT_OBSERVABILITY_FAIL")
    if not quotient.get("finite_null_perturbations_complete", False):
        failures.append("FINITE_NULL_PERTURBATIONS_INCOMPLETE")
    if not quotient.get("all_estimated_placements_in_measurement_jacobian", False):
        failures.append("PLACEMENTS_MISSING_FROM_JACOBIAN")
    for report_name, gate_name, failure in (
        ("maximum_null_segment_axis_angular_change_rad", "null_maximum_segment_axis_angular_change_rad", "NULL_AXIS_NOT_INVARIANT"),
        ("maximum_null_graphical_node_displacement_mm", "null_maximum_graphical_node_displacement_mm", "NULL_GRAPHICAL_NODES_NOT_INVARIANT"),
        ("maximum_null_antenna_displacement_mm", "null_maximum_antenna_displacement_mm", "NULL_ANTENNA_PREDICTION_NOT_INVARIANT"),
    ):
        if float(quotient.get(report_name, float("inf"))) > float(gate[gate_name]):
            failures.append(failure)

    geometry = report.get("geometry_audit", {})
    if not geometry.get("identity_map_fixed", False):
        failures.append("LEFT_RIGHT_IDENTITY_SWAP")
    if geometry.get("disconnected_frames") or not geometry.get("centerline_connected", True):
        failures.append("CENTERLINE_DISCONNECTED")
    if float(geometry.get("maximum_bone_length_change_mm", float("inf"))) > float(
        gates["geometry_gates"]["maximum_bone_length_change_mm"]
    ):
        failures.append("BONE_LENGTH_CHANGED")

    placement = report.get("placement_posterior_profile", {})
    if set(placement.get("per_node", {})) != set(NODES):
        failures.append("PLACEMENT_POSTERIOR_ACCOUNTING_INCOMPLETE")
    for node, value in placement.get("per_node", {}).items():
        if float(value.get("posterior_shift_sigma", float("inf"))) > float(
            gate["placement_maximum_posterior_shift_sigma"]
        ):
            failures.append(f"PLACEMENT_POSTERIOR_UNSTABLE:{node}")
        clearance = float(value.get("minimum_bound_clearance_fraction", -1.0))
        at_bound = clearance < float(gate["placement_minimum_bound_clearance_fraction"])
        if at_bound and not value.get("bound_hit_disclosed", False):
            failures.append(f"PLACEMENT_BOUND_HIT_UNDISCLOSED:{node}")
    if float(placement.get("profile_maximum_graphical_node_displacement_mm", float("inf"))) > float(
        gate["placement_profile_maximum_graphical_node_displacement_mm"]
    ):
        failures.append("PLACEMENT_PROFILE_GEOMETRY_UNSTABLE")

    multistart = report.get("multistart", {})
    if not multistart.get("identical_residual_and_weighting", False):
        failures.append("MULTISTART_RESIDUAL_CHANGED")
    if float(multistart.get("maximum_relative_cost_difference", float("inf"))) > float(
        gate["multistart_maximum_relative_cost_difference"]
    ):
        failures.append("MULTISTART_COST_UNSTABLE")
    if float(multistart.get("maximum_graphical_node_displacement_mm", float("inf"))) > float(
        gate["multistart_maximum_graphical_node_displacement_mm"]
    ):
        failures.append("MULTISTART_GEOMETRY_UNSTABLE")
    if float(multistart.get("maximum_segment_axis_angular_change_rad", float("inf"))) > float(
        gate["repeatability_maximum_segment_axis_angular_change_rad"]
    ):
        failures.append("MULTISTART_AXIS_UNSTABLE")
    if float(multistart.get("maximum_antenna_displacement_mm", float("inf"))) > float(
        gate["multistart_maximum_antenna_displacement_mm"]
    ):
        failures.append("MULTISTART_ANTENNA_UNSTABLE")

    interleaved = report.get("interleaved_sampling", {})
    if not interleaved.get("identical_residual_and_weighting", False):
        failures.append("INTERLEAVED_RESIDUAL_CHANGED")
    if float(interleaved.get("maximum_graphical_node_displacement_mm", float("inf"))) > float(
        gate["interleaved_maximum_graphical_node_displacement_mm"]
    ):
        failures.append("INTERLEAVED_GEOMETRY_UNSTABLE")
    if float(interleaved.get("maximum_placement_displacement_mm", float("inf"))) > float(
        gate["interleaved_maximum_placement_displacement_mm"]
    ):
        failures.append("INTERLEAVED_PLACEMENT_UNSTABLE")
    if float(interleaved.get("maximum_antenna_displacement_mm", float("inf"))) > float(
        gate["interleaved_maximum_antenna_displacement_mm"]
    ):
        failures.append("INTERLEAVED_ANTENNA_UNSTABLE")

    action_removal = report.get("action_removal", {})
    mandatory = action_removal.get("mandatory_action_dependence", {})
    if not mandatory.get("reported_separately", False):
        failures.append("MANDATORY_ACTION_DEPENDENCE_NOT_REPORTED")
    optional = action_removal.get("optional_action_removal", {})
    if not optional.get("identical_residual_and_weighting", False):
        failures.append("OPTIONAL_ACTION_RESIDUAL_CHANGED")
    if not optional.get("pass", False):
        failures.append("OPTIONAL_ACTION_REMOVAL_FAIL")
    for report_name, gate_name, failure in (
        ("maximum_segment_axis_angular_change_rad", "optional_action_removal_maximum_segment_axis_angular_change_rad", "OPTIONAL_ACTION_AXIS_UNSTABLE"),
        ("maximum_graphical_node_displacement_mm", "optional_action_removal_maximum_graphical_node_displacement_mm", "OPTIONAL_ACTION_GEOMETRY_UNSTABLE"),
        ("maximum_antenna_displacement_mm", "optional_action_removal_maximum_antenna_displacement_mm", "OPTIONAL_ACTION_ANTENNA_UNSTABLE"),
    ):
        if float(optional.get(report_name, float("inf"))) > float(gate[gate_name]):
            failures.append(failure)

    if float(placement.get("profile_maximum_segment_axis_angular_change_rad", float("inf"))) > float(
        gate["placement_profile_maximum_segment_axis_angular_change_rad"]
    ):
        failures.append("PLACEMENT_PROFILE_AXIS_UNSTABLE")
    if float(placement.get("profile_maximum_antenna_displacement_mm", float("inf"))) > float(
        gate["placement_profile_maximum_antenna_displacement_mm"]
    ):
        failures.append("PLACEMENT_PROFILE_ANTENNA_UNSTABLE")

    mismatch = report.get("model_mismatch", {})
    if float(mismatch.get("normalized_residual_median", float("inf"))) > float(
        gate["model_mismatch_maximum_normalized_residual_median"]
    ):
        failures.append("MODEL_MISMATCH_MEDIAN_FAIL")
    if float(mismatch.get("normalized_residual_p95", float("inf"))) > float(
        gate["model_mismatch_maximum_normalized_residual_p95"]
    ):
        failures.append("MODEL_MISMATCH_P95_FAIL")

    return {
        "schema": "biospur-visualization-calibration-gate-audit-v1",
        "pass": not failures,
        "verdict": (
            "VISUALIZATION_CENTERLINE_CALIBRATION_PASS"
            if not failures
            else "VISUALIZATION_CENTERLINE_CALIBRATION_BLOCKED"
        ),
        "failures": failures,
        "SCIENTIFIC_CENTERLINE": "UNCHANGED_FROZEN_V4_1_BLOCKED",
        "non_clinical": True,
        "disclaimer": DISCLAIMER,
    }


def calibration_preview_plan(available_actions: list[str], calibration_pass: bool) -> dict[str, Any]:
    if not calibration_pass:
        raise FirewallError("calibration previews require a passing calibration-only audit")
    forbidden = sorted(set(available_actions) & {"walk", "final_still"})
    if forbidden:
        raise FirewallError(f"held-out actions are forbidden in calibration previews: {forbidden}")
    unknown = sorted(set(available_actions) - set(CALIBRATION_PREVIEW_ACTIONS))
    if unknown:
        raise FirewallError(f"unclassified preview actions: {unknown}")
    return {
        "schema": "biospur-visualization-calibration-preview-plan-v1",
        "actions": [action for action in CALIBRATION_PREVIEW_ACTIONS if action in available_actions],
        "outputs": ["MP4", "GIF", "QUANTITATIVE_AUDIT"],
        "fixed_axes": True,
        "analysis_uses_render_interpolation": False,
        "walk_included": False,
        "final_still_included": False,
        "watermark_every_frame": DISCLAIMER,
    }


@dataclass
class VisualizationPayloadFirewall:
    """In-memory authority ledger; callers still perform the actual file open."""

    frozen_gates_sha256: str
    phase: str = "INPUTS_ONLY"
    calibration_ledger_open_count: int = 0
    walk_open_count: int = 0
    final_still_open_count: int = 0
    calibration_pass: bool = False
    previews_accepted: bool = False
    events: list[dict[str, Any]] = field(default_factory=list)

    def _assert_gates(self, gates_sha256: str) -> None:
        if gates_sha256 != self.frozen_gates_sha256:
            raise FirewallError("gates/threshold hash changed after input freeze")

    def authorize_calibration_ledger(self, gates_sha256: str) -> dict[str, Any]:
        self._assert_gates(gates_sha256)
        if self.phase != "INPUTS_ONLY" or self.calibration_ledger_open_count:
            raise FirewallError("calibration ledger may be authorized exactly once and first")
        self.calibration_ledger_open_count = 1
        self.phase = "CALIBRATION_ONLY"
        event = {
            "event": "CALIBRATION_LEDGER_AUTHORIZED",
            "walk": "SEALED",
            "final_still": "SEALED",
        }
        self.events.append(event)
        return event

    def record_calibration_audit(self, audit: Mapping[str, Any], gates_sha256: str) -> None:
        self._assert_gates(gates_sha256)
        if self.phase != "CALIBRATION_ONLY":
            raise FirewallError("calibration audit requires calibration-only phase")
        self.calibration_pass = bool(audit.get("pass", False))
        self.phase = "CALIBRATION_PASS" if self.calibration_pass else "CALIBRATION_BLOCKED"
        self.events.append({"event": "CALIBRATION_AUDIT_RECORDED", "pass": self.calibration_pass})

    def accept_calibration_previews(self, gates_sha256: str) -> None:
        self._assert_gates(gates_sha256)
        if self.phase != "CALIBRATION_PASS":
            raise FirewallError("preview acceptance requires passing calibration")
        self.previews_accepted = True
        self.phase = "CALIBRATION_PREVIEWS_ACCEPTED"
        self.events.append({"event": "CALIBRATION_PREVIEWS_ACCEPTED"})

    def authorize_walk_once(self, gates_sha256: str) -> dict[str, Any]:
        self._assert_gates(gates_sha256)
        if self.phase != "CALIBRATION_PREVIEWS_ACCEPTED" or not self.previews_accepted:
            raise FirewallError("walk remains sealed until calibration previews are accepted")
        if self.walk_open_count:
            raise FirewallError("walk may be opened exactly once")
        self.walk_open_count = 1
        self.phase = "WALK_CONSUMED_FOR_VISUALIZATION"
        event = {
            "event": "WALK_AUTHORIZED_ONCE",
            "WALK_HELDOUT_STATUS": "CONSUMED_FOR_VISUALIZATION",
            "scientific_validation_status": "NOT_UNTOUCHED_HELDOUT_AFTER_THIS_EVENT",
            "final_still": "SEALED",
            "post_walk_tuning": "FORBIDDEN",
        }
        self.events.append(event)
        return event

    def authorize_final_still(self, gates_sha256: str) -> None:
        self._assert_gates(gates_sha256)
        raise FirewallError("final_still remains sealed as the remaining validation segment")

    def manifest(self) -> dict[str, Any]:
        return {
            "schema": "biospur-visualization-payload-firewall-v1",
            "phase": self.phase,
            "frozen_gates_sha256": self.frozen_gates_sha256,
            "calibration_ledger_open_count": self.calibration_ledger_open_count,
            "walk_open_count": self.walk_open_count,
            "final_still_open_count": self.final_still_open_count,
            "WALK_HELDOUT_STATUS": (
                "CONSUMED_FOR_VISUALIZATION" if self.walk_open_count else "SEALED"
            ),
            "FINAL_STILL_STATUS": "SEALED",
            "events": list(self.events),
            "disclaimer": DISCLAIMER,
        }
