"""Preregistered Action00-only engineering tilt-trust calibration.

This owner calibrates a single joint nonconformity score from authenticated,
common-clock VQF diagnostics.  It deliberately cannot make a product or
scientific claim and it does not drive ``GapTiltRecovery`` in this stage.
"""
from __future__ import annotations

from dataclasses import InitVar, dataclass, fields, is_dataclass
from importlib.metadata import version as package_version
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping, Sequence
import hashlib
import hmac
import json
import math
import os
import stat

import numpy as np
from vqf import VQF

from .authenticated_vqf_tilt_join import UnqualifiedVQFTiltClockEvidence
from .continuous_frontend import ContinuousClockOwner, continuous_clock_owner_digest
from biospur_fusion.c2_uwb_root_world.gap_tilt_recovery import TiltEvidenceStatus
from biospur_fusion.v0.c2_progressive.orientation import VQFTiltDiagnosticProvenance


ACTION00_ID = "00_initial_still"
ACTION00_PROTOCOL_INDEX = 0
ACTION00_ACQUIRED_CHRONOLOGICAL_INDEX = 0
BLOCK_SAMPLE_COUNT = 200
SAMPLE_STEP_US = 5_000
TARGET_BLOCK_FALSE_ALARM_ALPHA = 0.05
REQUIRED_CONSECUTIVE_QUALIFYING_FRAMES = 200
FORMULA_SCHEMA = "biospur.c2.action00_joint_tilt_nonconformity.v1"
ENGINEERING_POLICY_SCHEMA = "biospur.c2.engineering_action00_tilt_policy.v2"
ENGINEERING_RESULT_SCHEMA = "biospur.c2.action00_engineering_tilt_policy.real_result.v2"


class Action00TiltPolicySchemaError(ValueError):
    """A sealed policy result cannot be represented by the current schema."""


class LegacyAction00TiltPolicySchemaError(Action00TiltPolicySchemaError):
    """A legacy policy omitted identity that cannot be reconstructed honestly."""


def _canonical(value: Any) -> Any:
    if isinstance(value, np.ndarray):
        array = np.ascontiguousarray(value)
        return {
            "__ndarray__": True,
            "dtype": str(array.dtype),
            "shape": list(array.shape),
            "sha256": hashlib.sha256(array.tobytes()).hexdigest(),
        }
    if isinstance(value, Mapping):
        return {str(key): _canonical(item) for key, item in sorted(value.items())}
    if is_dataclass(value):
        return {field.name: _canonical(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, (tuple, list)):
        return [_canonical(item) for item in value]
    if isinstance(value, np.generic):
        return value.item()
    return value


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(
        _canonical(value), sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode()).hexdigest()


def _sha(value: str, name: str) -> str:
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise ValueError(f"{name} must be a SHA-256 digest")
    return value


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze(item) for key, item in value.items()})
    if isinstance(value, (tuple, list)):
        return tuple(_freeze(item) for item in value)
    if isinstance(value, np.ndarray):
        array = np.asarray(value).copy()
        array.setflags(write=False)
        return array
    return value


def exact_vqf_parameter_payload() -> Mapping[str, Any]:
    """Reconstruct the exact existing frontend's installed VQF parameters."""
    instance = VQF(0.005, magDistRejectionEnabled=False)
    return _freeze(dict(instance.params))


@dataclass(frozen=True)
class Action00TiltPolicyPreregistration:
    """Fixed design contract; it contains no learned threshold."""

    diagnostic_provenance_digest: str
    initial_state_file_sha256: str
    initial_state_semantic_sha256: str
    vqf_version: str
    vqf_parameters: Mapping[str, Any]
    vqf_parameters_digest: str
    formula_digest: str = ""
    digest: str = ""

    def __post_init__(self) -> None:
        for value, name in (
            (self.diagnostic_provenance_digest, "diagnostic provenance"),
            (self.initial_state_file_sha256, "initial state file"),
            (self.initial_state_semantic_sha256, "initial state semantic"),
            (self.vqf_parameters_digest, "VQF parameters"),
        ):
            _sha(value, name)
        if self.vqf_version != package_version("vqf"):
            raise ValueError("VQF package version differs from installed frontend")
        parameters = _freeze(self.vqf_parameters)
        if _digest(parameters) != self.vqf_parameters_digest:
            raise ValueError("VQF parameter payload digest mismatch")
        object.__setattr__(self, "vqf_parameters", parameters)
        formula = _digest({
            "schema": FORMULA_SCHEMA,
            "score": "max(world_tilt/tilt_scale,acc_norm/acc_scale,bias_sigma/bias_scale)",
            "tilt_scale": "atan2(sqrt(lambda_max(acc_cov)+restThAcc^2),gravity_norm)",
            "acc_scale": "sqrt(lambda_max(acc_cov)+restThAcc^2)",
            "bias_scale": "sqrt(lambda_max(gyro_bias_cov)+deg2rad(biasSigmaRest)^2)",
            "block_samples": BLOCK_SAMPLE_COUNT,
            "sample_step_us": SAMPLE_STEP_US,
            "block_score": "maximum_frame_score",
            "eligibility": {
                "rest_detected": "true_for_every_frame",
                "relative_rest_deviation_gyro": "<=1_for_every_frame",
                "relative_rest_deviation_acceleration": "<=1_for_every_frame",
                "boot_epoch": "exactly_equal_within_block",
                "span_id": "exactly_equal_within_block",
                "timer2_cadence_us": SAMPLE_STEP_US,
            },
            "segmentation": {
                "run_restart": "any_boot_span_or_5000us_discontinuity",
                "block_alignment": "nonoverlap_from_each_contiguous_run_start",
                "burn_in_discarded": False,
                "per_run_tail_accounted": True,
                "ineligible_complete_block_accounted": True,
            },
            "target_block_false_alarm_alpha": TARGET_BLOCK_FALSE_ALARM_ALPHA,
            "order_rank": "ceil((n+1)*(1-alpha))",
            "required_recovery_frames": REQUIRED_CONSECUTIVE_QUALIFYING_FRAMES,
        })
        if self.formula_digest and self.formula_digest != formula:
            raise ValueError("Action00 tilt formula digest mismatch")
        object.__setattr__(self, "formula_digest", formula)
        expected = _digest({
            "schema": "biospur.c2.action00_tilt_policy_preregistration.v1",
            "diagnostic_provenance_digest": self.diagnostic_provenance_digest,
            "initial_state_file_sha256": self.initial_state_file_sha256,
            "initial_state_semantic_sha256": self.initial_state_semantic_sha256,
            "vqf_version": self.vqf_version,
            "vqf_parameters": parameters,
            "vqf_parameters_digest": self.vqf_parameters_digest,
            "formula_digest": formula,
            "status": "ENGINEERING_DIAGNOSTIC",
            "product_ready": False,
            "scientific_pass": False,
        })
        if self.digest and self.digest != expected:
            raise ValueError("Action00 tilt preregistration digest mismatch")
        object.__setattr__(self, "digest", expected)


@dataclass(frozen=True)
class _NodeScales:
    tilt_rad: float
    acceleration_mps2: float
    bias_rad_s: float

    def __post_init__(self) -> None:
        if any(not math.isfinite(value) or value <= 0.0 for value in (
            self.tilt_rad, self.acceleration_mps2, self.bias_rad_s,
        )):
            raise ValueError("nonconformity normalization scale is not positive")


@dataclass(frozen=True)
class EngineeringTiltNodePolicy:
    node: str
    eligible_block_count: int
    ineligible_block_count: int
    incomplete_frame_count: int
    conformal_rank: int
    attainable_false_alarm_alpha: float | None
    nonconformity_threshold: float | None
    scales: _NodeScales
    status: str
    digest: str = ""

    def __post_init__(self) -> None:
        if self.status not in {"ENGINEERING_DIAGNOSTIC", "MISSING"}:
            raise ValueError("invalid engineering node-policy status")
        if self.status == "MISSING":
            if self.nonconformity_threshold is not None or self.attainable_false_alarm_alpha is not None:
                raise ValueError("missing node policy cannot carry a threshold")
        elif (
            self.nonconformity_threshold is None
            or self.attainable_false_alarm_alpha is None
            or self.attainable_false_alarm_alpha > TARGET_BLOCK_FALSE_ALARM_ALPHA + 1e-15
        ):
            raise ValueError("engineering node policy lacks conformal ownership")
        expected = _digest({key: value for key, value in self.__dict__.items() if key != "digest"})
        if self.digest and self.digest != expected:
            raise ValueError("engineering node-policy digest mismatch")
        object.__setattr__(self, "digest", expected)


_POLICY_ISSUANCE = object()


@dataclass(frozen=True)
class Action00TerminalIdentity:
    event_identity: str
    boot_epoch: int
    span_id: int
    source_sequence: int
    timer2_us: int
    common_global_ns: int
    availability_global_ns: int
    digest: str = ""

    def __post_init__(self) -> None:
        if (
            not self.event_identity
            or type(self.boot_epoch) is not int
            or self.boot_epoch < 0
            or type(self.span_id) is not int
            or self.span_id < 0
            or type(self.source_sequence) is not int
            or not 0 <= self.source_sequence <= 0xFFFF
            or type(self.timer2_us) is not int
            or self.timer2_us < 0
            or type(self.common_global_ns) is not int
            or self.common_global_ns < 0
            or type(self.availability_global_ns) is not int
            or self.availability_global_ns < self.common_global_ns
        ):
            raise ValueError("invalid Action00 terminal identity")
        expected = _digest({
            "schema": "biospur.c2.action00_terminal_identity.v2",
            **{key: value for key, value in self.__dict__.items() if key != "digest"},
        })
        if self.digest and self.digest != expected:
            raise ValueError("Action00 terminal identity digest mismatch")
        object.__setattr__(self, "digest", expected)


@dataclass(frozen=True)
class EngineeringAction00TiltPolicy:
    preregistration_digest: str
    action_artifact_sha256: str
    action_artifact_owner_digest: str
    diagnostic_source_binding_digests: Mapping[str, str]
    clock_owner_digests: Mapping[str, str]
    diagnostic_provenance_digest: str
    node_policies: Mapping[str, EngineeringTiltNodePolicy]
    expected_nodes: tuple[str, ...]
    terminal_identities: Mapping[str, Action00TerminalIdentity]
    status: str
    digest: str = ""
    _issuance: InitVar[object | None] = None

    def __post_init__(self, _issuance: object | None) -> None:
        if _issuance is not _POLICY_ISSUANCE:
            raise ValueError("engineering Action00 policy requires registry issuance")
        _sha(self.preregistration_digest, "preregistration")
        _sha(self.action_artifact_sha256, "Action00 artifact")
        _sha(self.action_artifact_owner_digest, "Action00 artifact owner")
        _sha(self.diagnostic_provenance_digest, "diagnostic provenance")
        bindings = {str(key): _sha(value, "diagnostic source binding")
                    for key, value in self.diagnostic_source_binding_digests.items()}
        clocks = {str(key): _sha(value, "clock owner")
                  for key, value in self.clock_owner_digests.items()}
        policies = dict(self.node_policies)
        expected_nodes = tuple(self.expected_nodes)
        terminals = dict(self.terminal_identities)
        if (
            any(type(row) is not EngineeringTiltNodePolicy or row.node != node
                for node, row in policies.items())
            or any(type(row) is not Action00TerminalIdentity for row in terminals.values())
        ):
            raise ValueError("engineering Action00 policy contains an untyped node owner")
        expected_status = (
            "ENGINEERING_DIAGNOSTIC"
            if (set(policies) == set(expected_nodes) == set(bindings) == set(clocks)
                == set(terminals)
                and all(row.status == "ENGINEERING_DIAGNOSTIC" for row in policies.values()))
            else "MISSING"
        )
        if self.status != expected_status or len(expected_nodes) != 10 or len(set(expected_nodes)) != 10:
            raise ValueError("engineering Action00 policy inventory/status mismatch")
        object.__setattr__(self, "diagnostic_source_binding_digests", MappingProxyType(bindings))
        object.__setattr__(self, "clock_owner_digests", MappingProxyType(clocks))
        object.__setattr__(self, "node_policies", MappingProxyType(policies))
        object.__setattr__(self, "terminal_identities", MappingProxyType(terminals))
        expected = _digest({
            "schema": ENGINEERING_POLICY_SCHEMA,
            "preregistration_digest": self.preregistration_digest,
            "action_artifact_sha256": self.action_artifact_sha256,
            "action_artifact_owner_digest": self.action_artifact_owner_digest,
            "diagnostic_provenance_digest": self.diagnostic_provenance_digest,
            "bindings": bindings,
            "clocks": clocks,
            "node_policies": {node: row.digest for node, row in sorted(policies.items())},
            "expected_nodes": expected_nodes,
            "terminal_identities": {node: row.digest for node, row in sorted(terminals.items())},
            "status": self.status,
            "product_ready": False,
            "scientific_pass": False,
        })
        if self.digest and self.digest != expected:
            raise ValueError("engineering Action00 policy digest mismatch")
        object.__setattr__(self, "digest", expected)

    @property
    def product_ready(self) -> bool:
        return False

    @property
    def scientific_pass(self) -> bool:
        return False


def _exact_keys(value: Any, expected: set[str], owner: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or set(value) != expected:
        raise Action00TiltPolicySchemaError(f"{owner} fields do not match its schema")
    return value


def load_engineering_action00_tilt_policy_result(
    result_path: str | Path,
    *,
    expected_result_sha256: str,
    clock_owner: ContinuousClockOwner,
) -> EngineeringAction00TiltPolicy:
    """Rehydrate one preregistered sealed result without exposing issuance.

    The expected file digest is owned by the caller's preregistration.  Clock
    identity is never accepted as a string: the typed owner is hashed and its
    exact node mappings are checked against every terminal record.
    """

    _sha(expected_result_sha256, "expected Action00 result")
    if type(clock_owner) is not ContinuousClockOwner:
        raise TypeError("Action00 policy loader requires ContinuousClockOwner")
    path = Path(result_path)
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError as error:
        raise Action00TiltPolicySchemaError("Action00 policy result cannot be opened safely") from error
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_mode & 0o222:
            raise Action00TiltPolicySchemaError("Action00 policy result is not sealed read-only data")
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            payload = stream.read()
        after = os.fstat(descriptor)
        if (
            (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
            != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
            or len(payload) != before.st_size
        ):
            raise Action00TiltPolicySchemaError("Action00 policy result changed while loading")
    finally:
        os.close(descriptor)
    if not hmac.compare_digest(hashlib.sha256(payload).hexdigest(), expected_result_sha256):
        raise Action00TiltPolicySchemaError("Action00 policy result SHA-256 mismatch")
    try:
        document = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise Action00TiltPolicySchemaError("Action00 policy result is not canonical JSON") from error
    if not isinstance(document, Mapping):
        raise Action00TiltPolicySchemaError("Action00 policy result is not an object")
    if document.get("schema") != ENGINEERING_RESULT_SCHEMA:
        if document.get("schema") == "biospur.c2.action00_engineering_tilt_policy.real_result.v1":
            raise LegacyAction00TiltPolicySchemaError(
                "legacy Action00 policy lacks terminal uint16 source_sequence"
            )
        raise Action00TiltPolicySchemaError("unsupported Action00 policy result schema")
    if document.get("product_ready") is not False or document.get("scientific_pass") is not False:
        raise Action00TiltPolicySchemaError("engineering result cannot claim product/science status")
    raw_policy = document.get("policy")
    policy_fields = {
        "preregistration_digest", "action_artifact_sha256",
        "action_artifact_owner_digest", "diagnostic_source_binding_digests",
        "clock_owner_digests", "diagnostic_provenance_digest", "node_policies",
        "expected_nodes", "terminal_identities", "status", "digest",
    }
    raw_policy = _exact_keys(raw_policy, policy_fields, "Action00 policy")
    raw_nodes = raw_policy["node_policies"]
    raw_terminals = raw_policy["terminal_identities"]
    if not isinstance(raw_nodes, Mapping) or not isinstance(raw_terminals, Mapping):
        raise Action00TiltPolicySchemaError("Action00 policy node inventories are not mappings")
    expected_nodes = tuple(raw_policy["expected_nodes"])
    inventory = set(expected_nodes)
    if (
        len(expected_nodes) != 10
        or len(inventory) != 10
        or any(not isinstance(value, Mapping) or set(value) != inventory for value in (
            raw_nodes,
            raw_terminals,
            raw_policy["diagnostic_source_binding_digests"],
            raw_policy["clock_owner_digests"],
        ))
    ):
        raise Action00TiltPolicySchemaError("sealed Action00 policy inventory is incomplete")
    node_policies: dict[str, EngineeringTiltNodePolicy] = {}
    terminals: dict[str, Action00TerminalIdentity] = {}
    try:
        for node, raw_node in raw_nodes.items():
            raw_node = _exact_keys(raw_node, {
                "node", "eligible_block_count", "ineligible_block_count",
                "incomplete_frame_count", "conformal_rank",
                "attainable_false_alarm_alpha", "nonconformity_threshold",
                "scales", "status", "digest",
            }, "Action00 node policy")
            raw_scales = _exact_keys(raw_node["scales"], {
                "tilt_rad", "acceleration_mps2", "bias_rad_s",
            }, "Action00 node scales")
            node_policies[str(node)] = EngineeringTiltNodePolicy(
                **{key: value for key, value in raw_node.items() if key != "scales"},
                scales=_NodeScales(**raw_scales),
            )
        for node, raw_terminal in raw_terminals.items():
            if not isinstance(raw_terminal, Mapping) or "source_sequence" not in raw_terminal:
                raise LegacyAction00TiltPolicySchemaError(
                    "sealed Action00 terminal identity lacks uint16 source_sequence"
                )
            raw_terminal = _exact_keys(raw_terminal, {
                "event_identity", "boot_epoch", "span_id", "source_sequence",
                "timer2_us", "common_global_ns", "availability_global_ns", "digest",
            }, "Action00 terminal identity")
            terminals[str(node)] = Action00TerminalIdentity(**raw_terminal)
        policy = EngineeringAction00TiltPolicy(
            preregistration_digest=raw_policy["preregistration_digest"],
            action_artifact_sha256=raw_policy["action_artifact_sha256"],
            action_artifact_owner_digest=raw_policy["action_artifact_owner_digest"],
            diagnostic_source_binding_digests=raw_policy["diagnostic_source_binding_digests"],
            clock_owner_digests=raw_policy["clock_owner_digests"],
            diagnostic_provenance_digest=raw_policy["diagnostic_provenance_digest"],
            node_policies=node_policies,
            expected_nodes=expected_nodes,
            terminal_identities=terminals,
            status=raw_policy["status"],
            digest=raw_policy["digest"],
            _issuance=_POLICY_ISSUANCE,
        )
    except (KeyError, TypeError, ValueError) as error:
        if isinstance(error, Action00TiltPolicySchemaError):
            raise
        raise Action00TiltPolicySchemaError("invalid sealed Action00 policy payload") from error
    if document.get("status") != policy.status:
        raise Action00TiltPolicySchemaError("result and policy status disagree")
    actual_clock_digest = continuous_clock_owner_digest(clock_owner)
    if set(policy.expected_nodes) != {row.node_id for row in clock_owner.bindings}:
        raise Action00TiltPolicySchemaError("clock owner node inventory differs from policy")
    for node in policy.expected_nodes:
        terminal = policy.terminal_identities[node]
        binding = clock_owner.binding_for(node)
        if (
            policy.clock_owner_digests[node] != actual_clock_digest
            or terminal.boot_epoch != binding.boot_epoch
            or terminal.common_global_ns != binding.global_ns(terminal.timer2_us)
        ):
            raise Action00TiltPolicySchemaError("terminal identity differs from clock owner")
    return policy


class Action00TiltPolicyRegistry:
    """Append-only Action00 collector and one-shot engineering policy issuer."""

    def __init__(self, *, provenance: VQFTiltDiagnosticProvenance,
                 initial_state: Mapping[str, Any]) -> None:
        if (
            type(provenance) is not VQFTiltDiagnosticProvenance
            or provenance.product_ready
            or provenance.initial_stochastic_state_source_status not in {
                "SEAL_OWNED", "SEALED_SETTINGS_PATH_AND_SEMANTIC_BOUND",
            }
            or provenance.initial_stochastic_state_source_sha256 is None
        ):
            raise ValueError("policy registry requires sealed, source-owned Action00 inputs")
        frozen_initial = _freeze(initial_state)
        if _digest(frozen_initial) != provenance.initial_stochastic_state_semantic_sha256:
            raise ValueError("initial covariance semantic digest mismatch")
        parameters = exact_vqf_parameter_payload()
        if _digest(parameters) != provenance.vqf_parameters_digest:
            raise ValueError("exact VQF parameter payload differs from issuer")
        self._provenance = provenance
        self._initial = frozen_initial
        self._parameters = parameters
        self._expected_nodes = tuple(sorted(str(node) for node in frozen_initial["nodes"]))
        if len(self._expected_nodes) != 10:
            raise ValueError("Action00 tilt policy requires the exact ten-node inventory")
        self._rows: tuple[UnqualifiedVQFTiltClockEvidence, ...] = ()
        self._finalized = False

    @property
    def preregistration(self) -> Action00TiltPolicyPreregistration:
        return Action00TiltPolicyPreregistration(
            diagnostic_provenance_digest=self._provenance.digest,
            initial_state_file_sha256=self._provenance.initial_stochastic_state_source_sha256,
            initial_state_semantic_sha256=self._provenance.initial_stochastic_state_semantic_sha256,
            vqf_version=self._provenance.vqf_version,
            vqf_parameters=self._parameters,
            vqf_parameters_digest=self._provenance.vqf_parameters_digest,
        )

    def owner_bytes(self) -> bytes:
        return json.dumps({
            "preregistration": self.preregistration.digest,
            "rows": [row.digest for row in self._rows],
            "finalized": self._finalized,
        }, sort_keys=True, separators=(",", ":")).encode()

    def ingest(self, rows: Sequence[UnqualifiedVQFTiltClockEvidence]) -> None:
        before = self.owner_bytes()
        try:
            if self._finalized or not rows:
                raise ValueError("Action00 policy registry is finalized or input is empty")
            candidate = self._rows + tuple(rows)
            seen: set[str] = set()
            last_by_node: dict[str, tuple[int, int]] = {}
            for row in candidate:
                if (
                    type(row) is not UnqualifiedVQFTiltClockEvidence
                    or row.action_id != ACTION00_ID
                    or row.protocol_action_index != ACTION00_PROTOCOL_INDEX
                    or row.acquired_chronological_index != ACTION00_ACQUIRED_CHRONOLOGICAL_INDEX
                    or row.diagnostic_provenance_digest != self._provenance.digest
                    or row.event_identity in seen
                ):
                    raise ValueError("policy registry accepts only unique authenticated Action00 rows")
                previous = last_by_node.get(row.node)
                if previous is not None and (
                    row.common_global_ns <= previous[0] or row.timer2_us <= previous[1]
                ):
                    raise ValueError("Action00 diagnostic chronology is stale")
                seen.add(row.event_identity)
                last_by_node[row.node] = (row.common_global_ns, row.timer2_us)
            self._rows = candidate
        except Exception:
            if self.owner_bytes() != before:
                self._rows = self._rows[:len(self._rows) - len(rows)]
            raise

    def _scales(self, node: str) -> _NodeScales:
        try:
            initial = self._initial["nodes"][node]
            acc_cov = np.asarray(initial["accelerometer_observation_covariance_m2_s4"], dtype=float)
            gyro_bias_cov = np.asarray(initial["gyro_bias_covariance_rad2_s2"], dtype=float)
            gravity = float(initial["accelerometer_norm_mps2"])
            rest_acc = float(self._parameters["restThAcc"])
            rest_bias = math.radians(float(self._parameters["biasSigmaRest"]))
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError("initial covariance/VQF scale payload is incomplete") from error
        if (
            acc_cov.shape != (3, 3) or gyro_bias_cov.shape != (3, 3)
            or not np.isfinite(acc_cov).all() or not np.isfinite(gyro_bias_cov).all()
            or gravity <= 0.0 or rest_acc <= 0.0 or rest_bias <= 0.0
        ):
            raise ValueError("invalid normalization owner payload")
        acc_scale = math.sqrt(float(np.linalg.eigvalsh(acc_cov).max()) + rest_acc**2)
        bias_scale = math.sqrt(float(np.linalg.eigvalsh(gyro_bias_cov).max()) + rest_bias**2)
        return _NodeScales(math.atan2(acc_scale, gravity), acc_scale, bias_scale)

    @staticmethod
    def _frame_score(row: UnqualifiedVQFTiltClockEvidence, scales: _NodeScales) -> float:
        return max(
            row.world_tilt_innovation_rad / scales.tilt_rad,
            row.acceleration_norm_residual_mps2 / scales.acceleration_mps2,
            row.bias_sigma_rad_s / scales.bias_rad_s,
        )

    def finalize(self) -> EngineeringAction00TiltPolicy:
        if self._finalized:
            raise ValueError("Action00 policy registry cannot finalize twice")
        grouped: dict[str, list[UnqualifiedVQFTiltClockEvidence]] = {}
        for row in self._rows:
            grouped.setdefault(row.node, []).append(row)
        policies: dict[str, EngineeringTiltNodePolicy] = {}
        bindings: dict[str, str] = {}
        clocks: dict[str, str] = {}
        terminals: dict[str, Action00TerminalIdentity] = {}
        for node, rows in sorted(grouped.items()):
            scales = self._scales(node)
            maxima: list[float] = []
            ineligible = 0
            runs: list[list[UnqualifiedVQFTiltClockEvidence]] = []
            for row in rows:
                if not runs or (
                    row.boot_epoch != runs[-1][-1].boot_epoch
                    or row.span_id != runs[-1][-1].span_id
                    or row.timer2_us - runs[-1][-1].timer2_us != SAMPLE_STEP_US
                ):
                    runs.append([])
                runs[-1].append(row)
            incomplete = 0
            for run in runs:
                complete = len(run) // BLOCK_SAMPLE_COUNT
                incomplete += len(run) % BLOCK_SAMPLE_COUNT
                for block_index in range(complete):
                    block = run[block_index * BLOCK_SAMPLE_COUNT:(block_index + 1) * BLOCK_SAMPLE_COUNT]
                    eligible = all(
                        row.rest_detected
                        and row.relative_rest_deviation_gyro <= 1.0
                        and row.relative_rest_deviation_acceleration <= 1.0
                        for row in block
                    )
                    if eligible:
                        maxima.append(max(self._frame_score(row, scales) for row in block))
                    else:
                        ineligible += 1
            n = len(maxima)
            rank = int(math.ceil((n + 1) * (1.0 - TARGET_BLOCK_FALSE_ALARM_ALPHA)))
            if rank > n:
                threshold = attainable = None
                status = "MISSING"
            else:
                threshold = float(np.partition(np.asarray(maxima), rank - 1)[rank - 1])
                attainable = float(1.0 - rank / (n + 1))
                status = "ENGINEERING_DIAGNOSTIC"
            policies[node] = EngineeringTiltNodePolicy(
                node=node, eligible_block_count=n, ineligible_block_count=ineligible,
                incomplete_frame_count=incomplete,
                conformal_rank=rank, attainable_false_alarm_alpha=attainable,
                nonconformity_threshold=threshold, scales=scales, status=status,
            )
            source_bindings = {row.diagnostic_source_binding_digest for row in rows}
            if len(source_bindings) != 1:
                raise ValueError("Action00 node rows disagree on source owner")
            bindings[node] = next(iter(source_bindings))
            clock_owners = {row.clock_owner_digest for row in rows}
            if len(clock_owners) != 1:
                raise ValueError("Action00 node rows disagree on clock owner")
            clocks[node] = next(iter(clock_owners))
            last = rows[-1]
            terminals[node] = Action00TerminalIdentity(
                last.event_identity, last.boot_epoch, last.span_id,
                last.source_sequence, last.timer2_us,
                last.common_global_ns, last.availability_global_ns,
            )
        exact_row_inventory = [row.digest for row in self._rows]
        artifact_sha256 = _digest({
            "schema": "biospur.c2.sealed_action00_joined_tilt_rows.v1",
            "rows": exact_row_inventory,
        })
        artifact_owner_digest = _digest({
            "schema": "biospur.c2.action00_tilt_artifact_owner.v1",
            "preregistration": self.preregistration.digest,
            "artifact_sha256": artifact_sha256,
            "row_count": len(exact_row_inventory),
        })
        policy = EngineeringAction00TiltPolicy(
            preregistration_digest=self.preregistration.digest,
            action_artifact_sha256=artifact_sha256,
            action_artifact_owner_digest=artifact_owner_digest,
            diagnostic_source_binding_digests=bindings,
            clock_owner_digests=clocks,
            diagnostic_provenance_digest=self._provenance.digest,
            node_policies=policies,
            expected_nodes=self._expected_nodes,
            terminal_identities=terminals,
            status=("ENGINEERING_DIAGNOSTIC" if set(policies) == set(self._expected_nodes) and all(
                row.status == "ENGINEERING_DIAGNOSTIC" for row in policies.values()
            ) else "MISSING"),
            _issuance=_POLICY_ISSUANCE,
        )
        self._finalized = True
        return policy


@dataclass(frozen=True)
class EngineeringTiltTrustDecision:
    event_identity: str
    status: TiltEvidenceStatus
    joint_nonconformity: float
    threshold: float
    consecutive_qualifying_frames: int
    policy_digest: str
    reason: str
    product_ready: bool = False
    scientific_pass: bool = False
    digest: str = ""

    def __post_init__(self) -> None:
        if self.product_ready or self.scientific_pass:
            raise ValueError("engineering tilt decision cannot claim product/science status")
        expected = _digest({key: (value.value if isinstance(value, TiltEvidenceStatus) else value)
                            for key, value in self.__dict__.items() if key != "digest"})
        if self.digest and self.digest != expected:
            raise ValueError("engineering tilt decision digest mismatch")
        object.__setattr__(self, "digest", expected)


class EngineeringAction00TiltPolicyIssuer:
    """Causal engineering classifier; it is not yet a recovery adapter."""

    def __init__(self, policy: EngineeringAction00TiltPolicy) -> None:
        if type(policy) is not EngineeringAction00TiltPolicy or policy.status != "ENGINEERING_DIAGNOSTIC":
            raise ValueError("engineering tilt issuer requires a complete calibrated policy")
        self.policy = policy
        self._trusted: dict[str, bool] = {node: True for node in policy.node_policies}
        self._consecutive: dict[str, int] = {
            node: REQUIRED_CONSECUTIVE_QUALIFYING_FRAMES for node in policy.node_policies
        }
        self._last_time: dict[str, int] = {
            node: terminal.common_global_ns
            for node, terminal in policy.terminal_identities.items()
        }
        self._last_identity: dict[str, tuple[int, int, int]] = {
            node: (terminal.boot_epoch, terminal.span_id, terminal.timer2_us)
            for node, terminal in policy.terminal_identities.items()
        }

    def classify(self, row: UnqualifiedVQFTiltClockEvidence) -> EngineeringTiltTrustDecision:
        before = self.owner_bytes()
        try:
            node_policy = self.policy.node_policies[row.node]
            if (
                type(row) is not UnqualifiedVQFTiltClockEvidence
                or row.clock_owner_digest != self.policy.clock_owner_digests[row.node]
                or row.diagnostic_provenance_digest != self.policy.diagnostic_provenance_digest
                or row.common_global_ns <= self._last_time.get(row.node, -1)
            ):
                raise ValueError("engineering tilt row is foreign, stale, or replayed")
            previous_identity = self._last_identity.get(row.node)
            discontinuity = previous_identity is not None and (
                row.boot_epoch != previous_identity[0]
                or row.span_id != previous_identity[1]
                or row.timer2_us - previous_identity[2] != SAMPLE_STEP_US
            )
            score = Action00TiltPolicyRegistry._frame_score(row, node_policy.scales)
            qualifies = (
                row.rest_detected
                and row.relative_rest_deviation_gyro <= 1.0
                and row.relative_rest_deviation_acceleration <= 1.0
                and score <= node_policy.nonconformity_threshold
            )
            if discontinuity or not qualifies:
                self._trusted[row.node] = False
                self._consecutive[row.node] = 0
                status = TiltEvidenceStatus.UNTRUSTED
                reason = (
                    "SOURCE_DISCONTINUITY_ENTER_UNTRUSTED"
                    if discontinuity else "FIRST_INVALID_FRAME_ENTER_UNTRUSTED"
                )
            elif self._trusted[row.node]:
                self._consecutive[row.node] = REQUIRED_CONSECUTIVE_QUALIFYING_FRAMES
                status = TiltEvidenceStatus.TRUSTED
                reason = "QUALIFIED_WHILE_TRUSTED"
            else:
                self._consecutive[row.node] += 1
                if self._consecutive[row.node] >= REQUIRED_CONSECUTIVE_QUALIFYING_FRAMES:
                    self._trusted[row.node] = True
                    status = TiltEvidenceStatus.TRUSTED
                    reason = "EXACT_200_FRAME_RECOVERY_COMPLETE"
                else:
                    status = TiltEvidenceStatus.UNTRUSTED
                    reason = "RECOVERY_CONSECUTIVE_FRAME_HOLD"
            self._last_time[row.node] = row.common_global_ns
            self._last_identity[row.node] = (row.boot_epoch, row.span_id, row.timer2_us)
            return EngineeringTiltTrustDecision(
                row.event_identity, status, score, node_policy.nonconformity_threshold,
                self._consecutive[row.node], self.policy.digest, reason,
            )
        except Exception:
            if self.owner_bytes() != before:
                raise RuntimeError("engineering tilt issuer mutated on rejected input")
            raise

    def owner_bytes(self) -> bytes:
        return json.dumps({
            "policy": self.policy.digest,
            "trusted": self._trusted,
            "consecutive": self._consecutive,
            "last_time": self._last_time,
            "last_identity": self._last_identity,
        }, sort_keys=True, separators=(",", ":")).encode()
