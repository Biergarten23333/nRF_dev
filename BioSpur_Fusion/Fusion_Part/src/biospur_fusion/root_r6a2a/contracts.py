"""Fail-closed R6A2A registry, isolation, health, and mode contracts."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from biospur_fusion.root_r6a1c.adapter import CORRECT_NODE_MAP, evaluate_r6a2_request


FAMILY_COMMON_NINE = "COMMON_NINE_V0_20_PCB17"
FAMILY_BSF31CC = "BSF31CC_V0_20_N5BL"
COMMON_NINE = (
    "BSFEC35",
    "BSFB165",
    "BSFAA61",
    "BSF1120",
    "BSFC2CC",
    "BSF44AD",
    "BSF3C79",
    "BSF6C53",
    "BSF8BC4",
)
ALL_NODES = tuple(CORRECT_NODE_MAP)


class HealthState(str, Enum):
    HEALTHY = "HEALTHY"
    SUSPECT = "SUSPECT"
    DEGRADED = "DEGRADED"
    ISOLATED = "ISOLATED"
    RECOVERING = "RECOVERING"
    REQUALIFYING = "REQUALIFYING"


class DegradedMode(str, Enum):
    NORMAL = "NORMAL"
    SINGLE_UWB_LINK_DEGRADED = "SINGLE_UWB_LINK_DEGRADED"
    SINGLE_ANCHOR_ISOLATED = "SINGLE_ANCHOR_ISOLATED"
    SINGLE_TAG_UWB_DEGRADED = "SINGLE_TAG_UWB_DEGRADED"
    SINGLE_IMU_DEGRADED = "SINGLE_IMU_DEGRADED"
    SINGLE_NODE_IMU_AND_UWB_DEGRADED = "SINGLE_NODE_IMU_AND_UWB_DEGRADED"
    MULTI_ANCHOR_GEOMETRY_DEGRADED = "MULTI_ANCHOR_GEOMETRY_DEGRADED"
    GLOBAL_UWB_OUTAGE = "GLOBAL_UWB_OUTAGE"
    AMBIGUOUS_MODEL_OR_SLIP_MISMATCH = "AMBIGUOUS_MODEL_OR_SLIP_MISMATCH"
    RECOVERY_PENDING = "RECOVERY_PENDING"
    CONTROLLED_REENTRY = "CONTROLLED_REENTRY"


@dataclass(frozen=True)
class SyntheticHardwareProfile:
    family_id: str
    imu_to_uwb_nominal_m: tuple[float, float, float]
    provenance: str
    real_calibration_authority: bool = False

    def __post_init__(self) -> None:
        if not self.family_id.startswith(("COMMON_NINE_", "BSF31CC_")):
            raise ValueError("unknown hardware family")
        if self.provenance != "ROOT_R6A2A_SYNTHETIC_TEST_ONLY":
            raise ValueError("synthetic hardware profile needs explicit test-only provenance")
        if self.real_calibration_authority:
            raise ValueError("synthetic profile cannot authorize real calibration")


class UnifiedHardwareRegistry:
    """One ten-node registry selecting one of two mechanical profiles."""

    def __init__(self, family_by_node: Mapping[str, str], sealed_binding_sha256: str):
        self.family_by_node = {str(key): str(value) for key, value in family_by_node.items()}
        self.sealed_binding_sha256 = str(sealed_binding_sha256)
        self._validate()
        self.profiles = {
            FAMILY_COMMON_NINE: SyntheticHardwareProfile(
                FAMILY_COMMON_NINE,
                (0.031, -0.006, 0.004),
                "ROOT_R6A2A_SYNTHETIC_TEST_ONLY",
            ),
            FAMILY_BSF31CC: SyntheticHardwareProfile(
                FAMILY_BSF31CC,
                (-0.043, 0.011, 0.007),
                "ROOT_R6A2A_SYNTHETIC_TEST_ONLY",
            ),
        }

    def _validate(self) -> None:
        if set(self.family_by_node) != set(ALL_NODES):
            raise ValueError("hardware registry must cover the ten canonical nodes exactly once")
        if self.family_by_node.get("BSF31CC") != FAMILY_BSF31CC:
            raise ValueError("BSF31CC must use only its distinct hardware family")
        for node in COMMON_NINE:
            if self.family_by_node.get(node) != FAMILY_COMMON_NINE:
                raise ValueError(f"{node} must use only the common-nine family")
        if set(self.family_by_node.values()) != {FAMILY_COMMON_NINE, FAMILY_BSF31CC}:
            raise ValueError("the unified registry must expose exactly two geometry families")
        if not self.sealed_binding_sha256:
            raise ValueError("sealed family binding digest is mandatory")

    def family(self, node_id: str) -> str:
        if node_id not in self.family_by_node:
            raise KeyError(node_id)
        return self.family_by_node[node_id]

    def profile(self, node_id: str) -> SyntheticHardwareProfile:
        return self.profiles[self.family(node_id)]

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema": "biospur-root-r6a2a-unified-hardware-registry-v1",
            "system": "ONE_BIOSPUR_TEN_NODE_WHOLE_BODY_SYSTEM",
            "family_by_node": dict(self.family_by_node),
            "families": {
                family: {
                    "nodes": [node for node in ALL_NODES if self.family_by_node[node] == family],
                    "synthetic_profile": {
                        "imu_to_uwb_nominal_m": list(profile.imu_to_uwb_nominal_m),
                        "provenance": profile.provenance,
                        "real_calibration_authority": profile.real_calibration_authority,
                    },
                    "mechanical_transform_scope": f"{family}_ONLY",
                }
                for family, profile in self.profiles.items()
            },
            "cross_family_mechanical_transform_reuse": "FORBIDDEN",
            "electrical_equivalence_implies_mechanical_equivalence": False,
            "sealed_binding_sha256": self.sealed_binding_sha256,
        }


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def registry_from_sealed_addendum(fusion: Path) -> UnifiedHardwareRegistry:
    path = Path(fusion) / "logs/root_r6a1c_bsf31cc_hardware_addendum_20260825T105220Z/HARDWARE_FAMILY_BINDING.json"
    binding = json.loads(path.read_text())
    common = tuple(binding[FAMILY_COMMON_NINE]["nodes"])
    special = tuple(binding[FAMILY_BSF31CC]["nodes"])
    if common != COMMON_NINE or special != ("BSF31CC",):
        raise ValueError("sealed family membership differs from the R6A2A canonical registry")
    mapping = {node: FAMILY_COMMON_NINE for node in common}
    mapping.update({node: FAMILY_BSF31CC for node in special})
    return UnifiedHardwareRegistry(mapping, sha256_file(path))


def synthetic_authorization_request(registry: UnifiedHardwareRegistry) -> dict[str, Any]:
    return {
        "mode": "SYNTHETIC_FAULT_ARCHITECTURE",
        "node_identity_map": dict(CORRECT_NODE_MAP),
        "identity_contract_schema": "biospur-root-r6a1c-node-identity-donning-contract-v1",
        "donning_contract_schema": "biospur-root-r6a1c-node-identity-donning-contract-v1",
        "deferred_measurement_schema": "biospur-root-r6a1c-deferred-measurement-contract-v1",
        "hardware_frame_schema": "biospur-root-r6a1c-hardware-frame-lever-audit-v1",
        "world_frame_schema": "biospur-root-r6a1c-world-frame-bridge-contract-v1",
        "independent_parameter_owners": [
            "root_navigation_state",
            "relative_articulated_joint_state",
            "imu_bias_state",
            "static_session_calibration",
            "dynamic_skin_slip_nuisance",
            "measurement_noise_parameters",
            "world_gauge",
        ],
        "derived_parameter_ids": ["tag_levers", "bone_lengths", "torso_top"],
        "geometry_kind": "SYNTHETIC_TEST_ONLY",
        "synthetic_world_gauge": True,
        "writes_real_registry": False,
        "hardware_revision_ids": {
            node: "SYNTHETIC_" + registry.family(node) for node in ALL_NODES
        },
    }


def health_contract() -> dict[str, Any]:
    return {
        "schema": "biospur-root-r6a2a-measurement-health-attribution-v1",
        "states": [state.value for state in HealthState],
        "scopes": [
            "individual_measurement",
            "imu_stream",
            "uwb_tag_anchor_link",
            "node",
            "anchor",
            "segment_joint_consistency",
            "global_observability_mode",
        ],
        "transition_contract": {
            "HEALTHY_to_SUSPECT": {"entry_evidence": "one threshold violation", "persistence": 1, "weight": 0.5, "covariance": "inflate affected scope"},
            "SUSPECT_to_DEGRADED": {"entry_evidence": "two consecutive violations", "persistence": 2, "weight": 0.2, "covariance": "increase affected scope"},
            "DEGRADED_to_ISOLATED": {"entry_evidence": "three consecutive violations or typed hard fault", "persistence": 3, "weight": 0.0, "covariance": "propagate without that measurement"},
            "ISOLATED_to_RECOVERING": {"exit_evidence": "two consecutive normalized-consistent observations", "persistence": 2, "weight": 0.15, "covariance": "no immediate contraction"},
            "RECOVERING_to_REQUALIFYING": {"exit_evidence": "cross-sensor agreement and two more consistent observations", "persistence": 2, "weight": 0.35, "covariance": "bounded contraction"},
            "REQUALIFYING_to_HEALTHY": {"exit_evidence": "three further consistent observations", "persistence": 3, "weight": "ramp 0.65 to 1.0", "covariance": "bounded Kalman contraction"},
        },
        "attribution_rules": {
            "single_link": "one tag-anchor residual lacks cross-scope support",
            "bad_anchor": "many tags inconsistent with the same anchor",
            "bad_tag_or_lever": "one tag inconsistent with many anchors",
            "bad_imu_or_slip": "one IMU disagrees with adjacent joints and coherent UWB",
            "ambiguous": "conflicting or insufficient cross-system evidence -> AMBIGUOUS_MULTI_CAUSE",
        },
    }


def degraded_mode_matrix() -> dict[str, Any]:
    common = {
        "retained_constraints": ["shared articulated FK", "fixed bone lengths", "canonical parent/child ownership"],
        "invalid_measurement_direct_state_overwrite": False,
    }
    rows = {
        DegradedMode.NORMAL.value: ("all validated IMU and UWB", "none", "full synthetic shadow", "nominal", "all synthetic claims"),
        DegradedMode.SINGLE_UWB_LINK_DEGRADED.value: ("all except one downweighted link", "affected link", "all states", "link-local inflation", "link precision"),
        DegradedMode.SINGLE_ANCHOR_ISOLATED.value: ("IMU and seven anchors", "one anchor", "all states", "geometry-aware inflation", "eight-anchor geometry"),
        DegradedMode.SINGLE_TAG_UWB_DEGRADED.value: ("all IMU and other tags", "one tag UWB", "articulated and root states", "tag/segment inflation", "affected absolute segment correction"),
        DegradedMode.SINGLE_IMU_DEGRADED.value: ("UWB and nine IMUs", "one IMU", "adjacent constrained motion", "affected joint inflation", "affected high-rate orientation"),
        DegradedMode.SINGLE_NODE_IMU_AND_UWB_DEGRADED.value: ("remaining nine nodes", "one node IMU and UWB", "kinematic reconstruction", "node/limb inflation", "direct affected-segment observation"),
        DegradedMode.MULTI_ANCHOR_GEOMETRY_DEGRADED.value: ("IMU and informative UWB subspace", "weak geometry directions", "observable subspace", "inflate weak eigen-directions", "full 3D absolute observability"),
        DegradedMode.GLOBAL_UWB_OUTAGE.value: ("valid IMU and articulated constraints", "all UWB", "local articulated motion", "root position and global yaw grow", "absolute no-drift position"),
        DegradedMode.AMBIGUOUS_MODEL_OR_SLIP_MISMATCH.value: ("uncontested measurements", "ambiguous affected scope", "conservative propagation", "expand model/slip uncertainty", "unique fault identity"),
        DegradedMode.RECOVERY_PENDING.value: ("healthy plus probationary returns", "full returning weight", "continuous propagation", "no immediate contraction", "recovered status"),
        DegradedMode.CONTROLLED_REENTRY.value: ("ramped returning measurements", "instant full weight", "continuous bounded update", "bounded contraction", "full trust until requalified"),
    }
    return {
        "schema": "biospur-root-r6a2a-degraded-mode-matrix-v1",
        "modes": {
            name: {
                **common,
                "available_measurements": values[0],
                "disabled_or_downweighted": values[1],
                "allowed_propagation": values[2],
                "covariance_growth_policy": values[3],
                "claim_no_longer_authorized": values[4],
                "recovery_prerequisites": "persistence, normalized innovation consistency, cross-system agreement, covariance compatibility, bounded re-entry ramp",
            }
            for name, values in rows.items()
        },
    }


def state_ownership_contract() -> dict[str, Any]:
    return {
        "schema": "biospur-root-r6a2a-state-ownership-v1",
        "root_navigation_state": {"dynamic": True, "dimension": 9},
        "relative_articulated_joint_state": {"dynamic": True, "dimension": 54, "owner": "canonical nine-joint graph"},
        "imu_bias_state": {"dynamic": True, "dimension": 60},
        "static_session_calibration": {"dynamic": False, "real_values": None},
        "dynamic_skin_slip_nuisance": {"dynamic": True, "type": "bounded sparse smooth so(3)", "per_sample_unconstrained": False},
        "measurement_noise_parameters": {"optimizer_state": False, "production_values": None},
        "derived_body_tag_geometry": {"independent_optimizer_freedom": False, "formula": "t_SA=t_SI+R_SI*l_IA_family"},
        "bone_stretch_state": None,
        "torso_top": {"independent_optimizer_freedom": False, "derivation": "shoulder-centre midpoint"},
        "world_gauge": {"synthetic_convention_fixed": True, "real_T_N_V4": None},
    }


def isolation_contract(real_ledger_path: Path) -> dict[str, Any]:
    ledger = json.loads(Path(real_ledger_path).read_text())
    slots = ledger["slots"]
    return {
        "schema": "biospur-root-r6a2a-synthetic-real-isolation-v1",
        "namespaces": {
            "synthetic_truth": "ROOT_R6A2A_SYNTHETIC_TRUTH_TEST_ONLY",
            "synthetic_estimator_configuration": "ROOT_R6A2A_SYNTHETIC_TEST_ONLY",
            "real_deferred_calibration": "IMMUTABLE_87_SLOT_REGISTRY",
            "real_capture_bound_provenance": "UNOPENED_AND_UNAUTHORIZED",
        },
        "real_registry": {
            "path": str(real_ledger_path),
            "sha256": sha256_file(real_ledger_path),
            "total": len(slots),
            "value_null": sum(row["value"] is None for row in slots),
            "FROZEN_UNCERTAIN": sum(row["status"] == "FROZEN_UNCERTAIN" for row in slots),
            "writes_performed": 0,
        },
        "prohibitions": [
            "synthetic values cannot enter the real registry",
            "synthetic noise cannot become a production constant",
            "synthetic world cannot become T_N_V4",
            "synthetic UWB nominal point is not a qualified RF phase centre",
            "real C1 arrays and held-out actions are not opened",
        ],
    }


def recovery_contract() -> dict[str, Any]:
    return {
        "schema": "biospur-root-r6a2a-recovery-controlled-reentry-v1",
        "sequence": ["Detect", "Isolate", "Accommodate", "Recover", "Requalify", "Controlled re-entry"],
        "minimum_consistent_events": {"recover": 2, "requalify": 2, "healthy": 3},
        "weight_ramp": [0.0, 0.15, 0.35, 0.65, 1.0],
        "requirements": [
            "normalized innovation consistency",
            "cross-anchor or cross-node agreement",
            "covariance-compatible residuals",
            "bounded state correction",
            "no discontinuous state jump",
        ],
    }


def build_contract_bundle(fusion: Path) -> dict[str, Any]:
    fusion = Path(fusion)
    registry = registry_from_sealed_addendum(fusion)
    decision = evaluate_r6a2_request(synthetic_authorization_request(registry))
    if not decision.authorized:
        raise ValueError(decision.blockers)
    ledger = fusion / "logs/root_r6a1a_preintegrator_recovery_20260825T071931Z/CALIBRATION_SLOT_LEDGER.json"
    return {
        "registry": registry.as_dict(),
        "isolation": isolation_contract(ledger),
        "state": state_ownership_contract(),
        "health": health_contract(),
        "modes": degraded_mode_matrix(),
        "recovery": recovery_contract(),
        "architecture": {
            "schema": "biospur-root-r6a2a-system-architecture-v1",
            "system": "one ten-node estimator with two family-specific mechanical profiles",
            "pipeline": [
                "R6A1A NativeTimePreintegrator",
                "R6A1C corrected identity adapter",
                "R6A0 BodyModel shared articulated FK",
                "R6A0 RawUwbRangeFactor at physical measurement time",
                "hierarchical measurement health and attribution",
                "degraded accommodation",
                "controlled recovery and re-entry",
                "full state and covariance report",
            ],
            "separate_family_estimators": False,
            "shared_fk_only": True,
            "synthetic_authorization": decision.as_dict(),
            "real_body_update_authorized": False,
            "production_fusion_authorized": False,
        },
    }
