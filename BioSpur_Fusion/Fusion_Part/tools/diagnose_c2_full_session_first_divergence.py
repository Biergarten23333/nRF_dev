#!/usr/bin/env python3
"""Locate the first fully committed B-root physical-policy crossing."""
from __future__ import annotations

import argparse, hashlib, json, os, resource, signal, time
import math
from copy import deepcopy
from dataclasses import fields, is_dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Mapping
import numpy as np

from tools.build_c2_full_session_ten_node_ab import EXPECTED, ROOT, build, _jsonable
from tools.diagnose_c2_phase_c_prefix import PhaseCPrefixObserver, _state_validation
from biospur_fusion.c2_coupled_progressive.continuous_group_epoch_owner import (
    _digest_payload,
)
from biospur_fusion.root_r3 import PositionObservation
from biospur_fusion.c2_uwb_root_world.continuous_consensus_drift import (
    ContinuousConsensusObservation, _package_digest,
)
from biospur_fusion.c2_uwb_root_world.split_fusion import DriftCorrectionDecision

SCHEMA = "biospur.c2.first_b_physical_crossing.v2"
FOLLOWUP_SCHEMA = "biospur.c2.first_b_physical_crossing_uwb_response.v1"
FIXED_TARGET_SCHEMA = "biospur.c2.fixed_bucket_1958009_chain.v1"
EVENT_CAP = 343_176
FOLLOWUP_EVENT_CAP = 271_732
FOLLOWUP_RECORD_CAP_EXCLUSIVE = 36_880
TARGET_COMPLETE_BUCKET_RECORDS = {
    36_833: {
        "bucket": 1_958_009,
        "events_after": 270_825,
        "raw_identity": (1212593, 221554405, 221554613,
                         "11e796baa4aaf5c7dcc4d4a95dc6923db8aa920459da25fa39ca2e1eee19ec25"),
        "event_digest": "9960ab5703e3d0462fc4aa84fc9edb3cd72d5c1f18d35061173756f18e82125f",
    },
    36_871: {
        "bucket": 1_958_010,
        "events_after": 271_097,
        "raw_identity": (1212640, 221563188, 221563396,
                         "f1e8be1775848f757bb39ec7cd887db805b6ae47f1ac57b837736374ace0375b"),
        "event_digest": "1e05d3a19862c967ec85be0880f470cd5ab0122706b69212ac4774c5f6cd95c7",
    },
}
# The sealed crossing ends record 36,815 at event 270,708.  The reader's
# immutable contract permits at most 16 events per raw record; admit exactly
# the next 64 complete record boundaries and no record beginning at 36,880.
SEALED_CROSSING_DIGEST = "9b7823cfd3c1a197cc8de99a7a0658faee320dca86cb2bd2d058c7a58ba4a002"
OLD_CROSSING_RECORD = {
    "record_ordinal": 36_815, "events_after": 270_708,
    "raw_identity": (1212573, 221550759, 221550937,
        "622a5fb3d2839509a17695bfe02616e3edb9c7f14dc3510aeb9311a6a43bbea7"),
}
INTERNAL_SECONDS = 1_800.0
OUTER_SECONDS = 1_860
MAX_OUTPUT_BYTES = 5 << 20
ANCHOR_MARGIN_M = 0.75
MAX_SPEED_MPS = 12.0
AUDIT_NONFINITE_SCHEMA = "biospur.audit.nonfinite_float.v1"


class _Stop(RuntimeError):
    def __init__(self, reason: str) -> None:
        super().__init__(reason); self.reason = reason


class _Crossing(_Stop):
    pass


class _FollowupComplete(_Stop):
    pass


class _FollowupViolation(_Stop):
    """Terminal diagnostic failure after its containing record committed."""
    pass


class _GroupDispositionComplete(_Stop):
    """Terminal pre-admission explanation after its record committed."""

    pass


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while block := source.read(1 << 20): digest.update(block)
    return digest.hexdigest()


def _state_doc(state: object, revision: int, digest: str) -> dict[str, Any]:
    covariance = np.asarray(state.covariance, float)
    vector = np.asarray(state.vector, float)
    canonical = hashlib.sha256()
    canonical.update(np.asarray([float(state.time_s)], dtype="<f8").tobytes())
    canonical.update(np.asarray(vector, dtype="<f8").tobytes())
    canonical.update(np.asarray(covariance, dtype="<f8").tobytes())
    return {
        "publication_revision": int(revision), "publication_digest": str(digest),
        "time_s": float(state.time_s), "vector": vector.tolist(),
        "covariance": covariance.tolist(),
        "root_state_canonical_sha256": canonical.hexdigest(),
        "covariance_sha256": hashlib.sha256(covariance.tobytes()).hexdigest(),
        "covariance_diagonal": np.diag(covariance).tolist(),
        "covariance_minimum_eigenvalue": float(
            np.linalg.eigvalsh((covariance + covariance.T) * .5)[0]),
    }


def _state_document_hashes(time_s: float, vector: np.ndarray,
                           covariance: np.ndarray) -> tuple[str, str]:
    canonical = hashlib.sha256()
    canonical.update(np.asarray([time_s], dtype="<f8").tobytes())
    canonical.update(np.asarray(vector, dtype="<f8").tobytes())
    canonical.update(np.asarray(covariance, dtype="<f8").tobytes())
    return (canonical.hexdigest(),
            hashlib.sha256(np.asarray(covariance, dtype=float).tobytes()).hexdigest())


def _frame_doc(frame: object | None) -> dict[str, Any] | None:
    if frame is None: return None
    raw = getattr(frame, "raw_provenance", None)
    return {
        "digest": str(getattr(frame, "digest", "")),
        "source_timer_us": int(getattr(frame, "source_timer_us", -1)),
        "source_global_ns": int(getattr(frame, "source_global_ns", -1)),
        "publication_revision": int(getattr(frame, "publication_revision", -1)),
        "sample_index": int(getattr(raw, "sample_index", -1)),
        "raw_identity": None if raw is None else [int(raw.record_index),
            int(raw.start_offset), int(raw.end_offset), str(raw.encoded_sha256)],
    }


def _audit_nonfinite(field: str, value: float) -> dict[str, str]:
    if math.isnan(value):
        raise ValueError(f"DriftCorrectionDecision.{field} audit is NaN")
    return {"schema": AUDIT_NONFINITE_SCHEMA,
            "owner_type": "DriftCorrectionDecision", "field": field,
            "value": "POSITIVE_INFINITY" if value > 0.0 else "NEGATIVE_INFINITY"}


def _project(value: object) -> object:
    """Canonical JSON projection without dataclasses.asdict/deepcopy aliases."""
    if isinstance(value, np.ndarray):
        if value.dtype.kind in {"f", "c"} and not np.isfinite(value).all():
            raise ValueError("nonfinite ndarray outside an audit-only condition")
        if value.dtype.kind not in {"b", "i", "u", "f", "U"}:
            raise ValueError(f"unsupported ndarray dtype {value.dtype.str}")
        return {"dtype": value.dtype.str, "shape": list(value.shape),
                "values": value.tolist(),
                "sha256": hashlib.sha256(value.tobytes()).hexdigest()}
    if isinstance(value, Enum): return value.value
    if type(value) is DriftCorrectionDecision:
        document = {"_type": "DriftCorrectionDecision"}
        for field in fields(value):
            item = getattr(value, field.name)
            if field.name in {"condition", "bias_fit_condition"} and isinstance(
                item, (float, np.floating),
            ) and not math.isfinite(float(item)):
                document[field.name] = _audit_nonfinite(field.name, float(item))
            else:
                document[field.name] = _project(item)
        return document
    if is_dataclass(value):
        return {field.name: _project(getattr(value, field.name)) for field in fields(value)
                if not field.name.startswith("_")}
    if isinstance(value, Mapping):
        return {str(key): _project(item) for key, item in sorted(value.items(), key=lambda row: str(row[0]))}
    if isinstance(value, (tuple, list)): return [_project(item) for item in value]
    if isinstance(value, (float, np.floating)):
        if not math.isfinite(float(value)):
            raise ValueError("nonfinite scalar outside an audit-only condition")
        return float(value)
    if isinstance(value, (str, int, bool, type(None))): return value
    return {"type": type(value).__name__}


def _validate_finite_projection(value: object, *, path: str = "$") -> None:
    """Reject every nonfinite value except typed drift audit conditions."""
    if isinstance(value, (float, np.floating)):
        if not math.isfinite(float(value)):
            raise ValueError(f"nonfinite scientific value at {path}")
        return
    if isinstance(value, np.ndarray):
        if value.dtype.kind in {"f", "c"} and not np.isfinite(value).all():
            raise ValueError(f"nonfinite scientific array at {path}")
        if value.dtype.kind not in {"b", "i", "u", "f", "U"}:
            raise ValueError(f"unsupported scientific array dtype at {path}")
        return
    if isinstance(value, Mapping):
        if value.get("schema") == AUDIT_NONFINITE_SCHEMA:
            raise ValueError(f"unbound audit nonfinite marker at {path}")
        decision = value.get("_type") == "DriftCorrectionDecision"
        for key, item in value.items():
            child = f"{path}.{key}"
            if decision and key in {"condition", "bias_fit_condition"}:
                if isinstance(item, Mapping):
                    expected = {"schema": AUDIT_NONFINITE_SCHEMA,
                        "owner_type": "DriftCorrectionDecision", "field": key,
                        "value": item.get("value")}
                    if (dict(item) != expected
                            or item.get("value") != "POSITIVE_INFINITY"):
                        raise ValueError(f"invalid audit nonfinite marker at {child}")
                    continue
                if not (isinstance(item, (int, float, np.integer, np.floating))
                        and math.isfinite(float(item))):
                    raise ValueError(f"invalid drift audit scalar at {child}")
                continue
            _validate_finite_projection(item, path=child)
        return
    if isinstance(value, (tuple, list)):
        for index, item in enumerate(value):
            _validate_finite_projection(item, path=f"{path}[{index}]")


def _validate_drift_decision_projection(value: object) -> bool:
    if not isinstance(value, Mapping) or value.get("_type") != "DriftCorrectionDecision":
        return False
    try:
        if set(value) != {"_type", *(field.name for field in fields(
                DriftCorrectionDecision))}:
            return False
        _validate_finite_projection(value, path="$.DriftCorrectionDecision")
        for field in ("condition", "bias_fit_condition"):
            item = value[field]
            if not (isinstance(item, (int, float)) and math.isfinite(float(item))
                    and float(item) >= 0.0
                    or isinstance(item, Mapping)
                    and item.get("schema") == AUDIT_NONFINITE_SCHEMA
                    and item.get("field") == field
                    and item.get("value") == "POSITIVE_INFINITY"):
                return False
        if (isinstance(value["condition"], Mapping)
                and not (value.get("accepted") is False
                         and value.get("rank") == 0)):
            return False
        if (isinstance(value["bias_fit_condition"], Mapping)
                and value.get("bias_fit_status") == "BIAS_FIT_ACCEPTED"):
            return False
        return True
    except (KeyError, TypeError, ValueError):
        return False


def _plan_doc(value: object, *, extended: bool = False) -> dict[str, Any]:
    causal = getattr(value, "causal_transaction", None)
    root_plan = None if causal is None else getattr(causal, "root_plan", None)
    drift_plan = getattr(value, "drift_plan", None)
    drift_dependency = None if drift_plan is None else getattr(
        drift_plan, "_dependency_plan", None)
    history = getattr(value, "history_plan", None)
    native = getattr(history, "native", None)
    frame = getattr(native, "frame", None) or getattr(history, "frame", None)
    frames = getattr(value, "frames", None)
    if frames is None: frames = getattr(history, "frames", ())
    imu_candidate = None; final_candidate = None
    if history is not None and (
        getattr(history, "future_imu", None) is not None
        or getattr(history, "current_imu", None) is not None
    ):
        future = getattr(history, "future_imu", None)
        imu_candidate = (future.imu_plan.candidate_state if future is not None
                         else history.current_imu.candidate_state)
        prepared_root = getattr(history, "root_plan", None)
        velocity_plan = getattr(prepared_root, "imu_velocity_plan", None)
        if velocity_plan is not None:
            final_candidate = velocity_plan._candidate_bundle.snapshots[-1].state
        elif hasattr(prepared_root, "_candidate_bundle"):
            final_candidate = prepared_root._candidate_bundle.snapshots[-1].state
        else: final_candidate = imu_candidate
    document = {
        "type": type(value).__name__, "digest": getattr(value, "digest", None),
        "frame": _frame_doc(frame),
        "frames": [_frame_doc(item) for item in tuple(frames or ())],
        "root_observation": getattr(value, "root_observation", None),
        "root_plan_digest": getattr(root_plan, "digest", None),
        "root_decision": getattr(root_plan, "decision", None),
        "trusted_partition": getattr(value, "trusted_partition", None),
        "prepared_result": getattr(value, "prepared_result", None),
        "drift_plan_digest": getattr(drift_plan, "digest", None),
        "drift_result": None if drift_plan is None else getattr(drift_plan, "result", None),
        "gap_history_digest": getattr(history, "digest", None),
    }
    if extended and imu_candidate is not None:
        document.update({"imu_candidate": _state_doc(imu_candidate, -1, "0" * 64),
            "final_root_candidate": _state_doc(final_candidate, -1, "0" * 64)})
    packet = getattr(value, "packet", None)
    if extended and packet is not None:
        document.update({"packet_digest": getattr(value, "packet_digest", None),
            "packet_availability_global_ns": getattr(packet, "availability_global_ns", None)})
    direct_result = getattr(value, "result", None)
    if extended and direct_result is not None:
        document["direct_drift_result"] = direct_result
    pending = getattr(getattr(value, "_candidate_state", None), "pending", ())
    if extended and pending: document["pending_packages"] = pending
    if extended and drift_dependency is not None:
        document["drift_queued_package"] = (
            getattr(drift_dependency._candidate_state, "pending", ()) or (None,))[-1]
    return _project(document)


def _unseen(before: object, after: object) -> tuple[object, ...]:
    previous, total = int(before.ab_transaction_total), int(after.ab_transaction_total)
    ring = tuple(after.ab_transaction_journal); start = total - len(ring)
    if previous < start or previous > total:
        raise RuntimeError("A/B transaction cursor escaped bounded journal")
    return ring[previous - start:]


def _canonical_digest(value: object) -> str:
    return hashlib.sha256(json.dumps(
        _jsonable(value), sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode()).hexdigest()


def _group_owner_state(owner: object) -> dict[str, Any]:
    """Bounded diagnostic projection of the authoritative group-owner state."""
    pending = []
    for bucket, rows in sorted(owner._pending.items()):
        pending.append({"bucket": int(bucket), "row_count": len(rows),
            "nodes": [str(item.row.node) for item in rows],
            "event_ids": [str(item.event.event_id) for item in rows]})
    journal = [_project(item) for item in owner.journal]
    document = {"revision": int(owner._revision), "pending": pending,
        "finalized_watermark": owner._finalized_watermark,
        "journal_digests": [_canonical_digest(item) for item in journal],
        "counters": dict(owner.counters)}
    document["state_sha256"] = _canonical_digest(document)
    return document


def _journal_delta(before: Mapping[str, Any], after: Mapping[str, Any],
                   journal: tuple[object, ...]) -> list[dict[str, Any]]:
    previous = list(before["journal_digests"])
    current = list(after["journal_digests"])
    overlap = 0
    for candidate in range(min(len(previous), len(current)), -1, -1):
        if previous[len(previous) - candidate:] == current[:candidate]:
            overlap = candidate
            break
    projected = [_project(item) for item in journal]
    return projected[overlap:]


class FirstBPhysicalCrossingObserver(PhaseCPrefixObserver):
    """Stage publications during a record; accept only after record success."""

    def __init__(self, owners: object, started: float) -> None:
        super().__init__(owners, started)
        self.event_cap = EVENT_CAP; self.crossing = None
        self.stop_at_crossing = True
        self.invalid_state = None
        self._active_record = self._active_operation = self._active_plan = None
        self._staged_crossing = self._staged_invalid = None
        self._bootstrap_invalid = None
        self._last_b = None; self._seen_b_publications = set()
        self._last_b_native_identity = None
        self._operation_order = 0; self._batch_indices = {"a": 0, "b": 0}
        self._wrap_atomic_routes()

    def _wrap_atomic_routes(self) -> None:
        coordinator = self.owners.coordinator
        for name, subtype in (("_atomic_ab", "SCALAR"),
                              ("_atomic_ab_native200_batch", "BATCH"),
                              ("_atomic_ab_gap_native200", "GAP_ENDPOINT")):
            original = getattr(coordinator, name)
            def wrapped(*args: object, _original=original, _subtype=subtype,
                        **kwargs: object) -> object:
                previous = self._active_operation; self._operation_order += 1
                events = tuple(args[0]) if _subtype == "BATCH" else tuple(args[:2])
                self._batch_indices = {"a": 0, "b": 0}
                self._active_operation = {"route": _subtype,
                    "order": self._operation_order,
                    "events": [self._event_doc(event) for event in events],
                    "_event_objects": events}
                try: return _original(*args, **kwargs)
                finally: self._active_operation = previous
            setattr(coordinator, name, wrapped)

    @staticmethod
    def _event_doc(event: object) -> dict[str, Any]:
        return {"event_id": str(getattr(event, "event_id", "")),
            "kind": str(getattr(event, "kind", "")),
            "node_id": str(getattr(event, "node_id", "")),
            "common_global_ns": int(getattr(event, "common_global_ns", -1)),
            "availability_global_ns": int(getattr(event, "availability_global_ns", -1))}

    def _install_hooks_on(self, a: object, b: object) -> None:
        super()._install_hooks_on(a, b)
        if getattr(self, "_crossing_detail_hooks", False): return
        for branch, composition in (("a", a), ("b", b)):
            for name in ("commit_admission", "commit_consensus_admission",
                         "commit_native200", "commit_gap",
                         "commit_gap_native200", "commit_native200_batch"):
                original = getattr(composition, name, None)
                if not callable(original): continue
                def detailed(plan: object, _original=original, _name=name,
                             _branch=branch, _composition=composition) -> object:
                    previous = self._active_plan
                    self._active_plan = {"operation": _name, "prepared": _plan_doc(
                        plan, extended=(not self.stop_at_crossing and self.crossing is not None),
                    )}
                    try:
                        before_token = self._token_from_composition(_composition)
                        before_drift = self._drift_doc(_composition)
                        result = _original(plan)
                        # The inherited observer already captures admission,
                        # scalar native, batch add_imu, and ordinary GAP.  The
                        # compound GAP endpoint has its own root assignment.
                        if _name == "commit_gap_native200":
                            self._capture_composition_state(_branch, _composition)
                        self._after_detailed_commit(
                            _branch, _name, plan, result, before_token,
                            self._token_from_composition(_composition),
                            before_drift, self._drift_doc(_composition),
                        )
                        return result
                    finally: self._active_plan = previous
                setattr(composition, name, detailed)
        self._crossing_detail_hooks = True

    @staticmethod
    def _token_from_composition(composition: object) -> dict[str, Any]:
        token = composition.engine.root.publication_token()
        return _state_doc(token.state, token.revision, token.digest)

    @staticmethod
    def _drift_doc(composition: object) -> object:
        owner = getattr(composition, "consensus_drift", None)
        if owner is None: return None
        snapshot = owner.snapshot()
        return {"revision": int(owner.revision), "owner_digest": owner.owner_digest,
            "pending": _project(snapshot._state.pending),
            "last_consumed_native200_time_s": (
                snapshot._state.last_consumed_native200_time_s)}

    def _after_detailed_commit(self, branch: str, operation: str, plan: object,
                               result: object, pre_root: dict[str, Any],
                               post_root: dict[str, Any], pre_drift: object,
                               post_drift: object) -> None:
        pass

    def _after_successful_record(self, record: dict[str, Any]) -> None:
        pass

    def _policy(self, state: object) -> tuple[bool, dict[str, Any]]:
        position = np.asarray(state.vector[:3], float)
        speed = float(np.linalg.norm(np.asarray(state.vector[3:6], float)))
        inside = bool(np.all(position >= self.anchor_lower_m)
                      and np.all(position <= self.anchor_upper_m))
        return inside and speed <= MAX_SPEED_MPS, {
            "inside_anchor_envelope": inside, "speed_mps": speed,
            "speed_limit_mps": MAX_SPEED_MPS,
            "lower_m": self.anchor_lower_m.tolist(),
            "upper_m": self.anchor_upper_m.tolist()}

    def _capture_state(self, name: str, state: object, *,
                       publication_revision: int | None = None,
                       publication_digest: str | None = None) -> None:
        super()._capture_state(name, state, publication_revision=publication_revision,
                               publication_digest=publication_digest)
        if name != "b" or publication_revision is None or publication_digest is None: return
        identity = (int(publication_revision), str(publication_digest))
        if identity in self._seen_b_publications: return
        self._seen_b_publications.add(identity)
        batch_index = None
        if (self._active_operation or {}).get("route") == "BATCH":
            batch_index = self._batch_indices["b"]
            self._batch_indices["b"] += 1
        current_native = None
        operation_name = (self._active_plan or {}).get("operation")
        if batch_index is not None:
            events = self._active_operation.get("_event_objects", ())
            if batch_index < len(events):
                current_native = _frame_doc(getattr(events[batch_index], "payload_owner", None))
        elif operation_name in {"commit_native200", "commit_gap_native200"}:
            prepared_frame = (self._active_plan or {}).get("prepared", {}).get("frame")
            current_native = prepared_frame
        previous_native = deepcopy(self._last_b_native_identity)
        if current_native is not None:
            self._last_b_native_identity = current_native
        validation = _state_validation(state)
        if not validation["valid"]:
            if self._staged_invalid is None:
                self._staged_invalid = {"reason": "INVALID_ROOTSTATE_BEFORE_CROSSING",
                    "validation": validation,
                    "publication": _state_doc(state, *identity)}
            return
        current_policy, policy = self._policy(state)
        if self._last_b is None:
            if not current_policy:
                self._bootstrap_invalid = {"reason": "INITIAL_B_NOT_IN_POLICY",
                    "policy": policy, "publication": _state_doc(state, *identity)}
            self._last_b = (state, *identity); return
        previous, previous_revision, previous_digest = self._last_b
        previous_policy, _ = self._policy(previous)
        self._last_b = (state, *identity)
        if (not previous_policy or current_policy or self._staged_crossing is not None
                or self.crossing is not None
                or self._active_record is None): return
        operation = {key: value for key, value in
                     (self._active_operation or {"route": "UNKNOWN"}).items()
                     if not key.startswith("_")}
        if batch_index is not None:
            operation["sequential_frame_index"] = batch_index
            operation["triggering_native_identity"] = current_native
        self._staged_crossing = {
            "pre_publication": _state_doc(previous, previous_revision, previous_digest),
            "post_publication": _state_doc(state, *identity), "post_policy": policy,
            "causal_operation": operation, "plan_or_decision": self._active_plan,
            "previous_native_identity": previous_native,
            "triggering_native_identity": current_native}

    @staticmethod
    def _token(branch: object) -> dict[str, Any]:
        token = branch._composition.engine.root.publication_token()
        return _state_doc(token.state, token.revision, token.digest)

    def __call__(self, ticket: object) -> None:
        if time.monotonic() - self.started >= INTERNAL_SECONDS:
            raise _Stop("INTERNAL_1800S_DEADLINE")
        if self._bootstrap_invalid is not None:
            raise _Stop("INVALID_ROOTSTATE_BEFORE_CROSSING:" +
                        self._bootstrap_invalid["reason"])
        coordinator = self.owners.coordinator; before = coordinator.audit()
        count = len(tuple(ticket.event_digests))
        if int(before.events) + count > self.event_cap:
            raise _Stop("EVENT_CAP_343176_BEFORE_CONSUME")
        region = self._source_region(ticket); pre = None
        if coordinator._a is not None and coordinator._b is not None:
            pre = {"a": self._token(coordinator._a), "b": self._token(coordinator._b)}
        frames = () if coordinator._b is None else coordinator._b._composition.history.frames
        self._active_record = {"record_ordinal": int(ticket.record_ordinal),
            "raw_identity": list(ticket.raw_identity),
            "event_digests": list(ticket.event_digests),
            "sensor_identity_digests": list(ticket.sensor_identity_digests),
            "region_id": str(region.region_id), "pre_ab": pre,
            "previous_b_native_identity": _frame_doc(frames[-1] if frames else None)}
        if self._last_b_native_identity is None:
            self._last_b_native_identity = self._active_record["previous_b_native_identity"]
        detection_checkpoint = (
            self._last_b, set(self._seen_b_publications),
            deepcopy(self._last_b_native_identity), dict(self._batch_indices),
        )
        self._staged_crossing = self._staged_invalid = None
        try: self.consume(ticket)
        except BaseException:
            (self._last_b, self._seen_b_publications,
             self._last_b_native_identity, self._batch_indices) = detection_checkpoint
            self._staged_crossing = self._staged_invalid = self._active_record = None
            raise
        self.records += 1; self._install_hooks(); after = coordinator.audit()
        a_present, b_present = coordinator._a is not None, coordinator._b is not None
        if a_present != b_present:
            self.invalid_state = {"reason": "ASYMMETRIC_BOOTSTRAP_OWNER_PRESENCE",
                "a_present": a_present, "b_present": b_present,
                "record_ordinal": int(ticket.record_ordinal),
                "events_after": int(after.events)}
            self._active_record = None
            raise _Stop("ASYMMETRIC_BOOTSTRAP_OWNER_PRESENCE")
        if not a_present:
            followup_effects = getattr(self, "_record_effects", ())
            if (self._staged_crossing is not None or self._staged_invalid is not None
                    or followup_effects):
                self.invalid_state = {"reason": "PREBOOTSTRAP_RECORD_STAGED_OWNER_EFFECT",
                    "record_ordinal": int(ticket.record_ordinal),
                    "events_after": int(after.events)}
                self._active_record = None
                raise _Stop("PREBOOTSTRAP_RECORD_STAGED_OWNER_EFFECT")
            self._active_record = None
            if int(after.events) >= self.event_cap:
                raise _Stop(f"EVENT_CAP_{self.event_cap}_WITHOUT_REQUIRED_EVIDENCE")
            return
        if self._staged_invalid is not None:
            self.invalid_state = dict(self._staged_invalid)
            reason = self._staged_invalid["reason"]; self._active_record = None
            raise _Stop("INVALID_ROOTSTATE_BEFORE_CROSSING:" + reason)
        record = dict(self._active_record); assert coordinator._a and coordinator._b
        transactions = _jsonable(_unseen(before, after))
        if "ROLLBACK" in json.dumps(transactions, sort_keys=True):
            self._active_record = None
            raise _Stop("RECORD_CONTAINS_ROLLBACK_DISPOSITION")
        record.update({"events_before": int(before.events), "events_after": int(after.events),
            "record_completed": True,
            "post_ab": {"a": self._token(coordinator._a), "b": self._token(coordinator._b)},
            "ab_transaction_total_before": int(before.ab_transaction_total),
            "ab_transaction_total_after": int(after.ab_transaction_total),
            "record_ab_transactions": transactions, "rollback_observed": False})
        if self._staged_crossing is not None:
            self.crossing = dict(self._staged_crossing)
            self.crossing["record_transaction"] = record
            if self.stop_at_crossing:
                self._active_record = None
                raise _Crossing("FIRST_COMMITTED_B_PHYSICAL_CROSSING")
        self._after_successful_record(record)
        self._active_record = None
        if int(after.events) >= self.event_cap:
            raise _Stop(f"EVENT_CAP_{self.event_cap}_WITHOUT_REQUIRED_EVIDENCE")


class CrossingUwbResponseObserver(FirstBPhysicalCrossingObserver):
    """Continue from the sealed crossing through one exact UWB response."""

    def __init__(self, owners: object, started: float) -> None:
        super().__init__(owners, started)
        self.stop_at_crossing = False
        self.event_cap = FOLLOWUP_EVENT_CAP
        self.admission = self.queue = self.native_response = None
        self.native_observation_order: list[dict[str, Any]] = []
        self.group_visibility: list[dict[str, Any]] = []
        self.decisive_group_disposition: dict[str, Any] | None = None
        self._group_before: dict[str, Any] | None = None
        self._record_effects: list[dict[str, Any]] = []

    def _after_detailed_commit(self, branch: str, operation: str, plan: object,
                               result: object, pre_root: dict[str, Any],
                               post_root: dict[str, Any], pre_drift: object,
                               post_drift: object) -> None:
        if (branch != "b"
                or (self.crossing is None and self._staged_crossing is None)):
            return
        self._record_effects.append({"operation": operation,
            "prepared": _plan_doc(plan, extended=True), "result": _project(result),
            "pre_root": pre_root, "post_root": post_root,
            "pre_drift": pre_drift, "post_drift": post_drift})

    @staticmethod
    def _find_b_audit(value: object) -> dict[str, Any] | None:
        if isinstance(value, Mapping):
            if value.get("branch") == "B_UWB": return dict(value)
            for item in value.values():
                found = CrossingUwbResponseObserver._find_b_audit(item)
                if found is not None: return found
        elif isinstance(value, (tuple, list)):
            for item in value:
                found = CrossingUwbResponseObserver._find_b_audit(item)
                if found is not None: return found
        return None

    def __call__(self, ticket: object) -> None:
        if int(ticket.record_ordinal) >= FOLLOWUP_RECORD_CAP_EXCLUSIVE:
            raise _Stop("FOLLOWUP_RECORD_CAP_BEFORE_CONSUME")
        checkpoint = (deepcopy(self.admission), deepcopy(self.queue),
                      deepcopy(self.native_response),
                      deepcopy(self.native_observation_order),
                      deepcopy(self.group_visibility),
                      deepcopy(self.decisive_group_disposition))
        branch = self.owners.coordinator._b
        self._group_before = (
            _group_owner_state(branch)
            if int(ticket.record_ordinal) in TARGET_COMPLETE_BUCKET_RECORDS
            and branch is not None else None
        )
        self._record_effects = []
        try: super().__call__(ticket)
        except (_Crossing, _FollowupComplete, _FollowupViolation,
                _GroupDispositionComplete): raise
        except BaseException:
            (self.admission, self.queue, self.native_response,
             self.native_observation_order, self.group_visibility,
             self.decisive_group_disposition) = checkpoint
            self._record_effects = []
            raise

    @staticmethod
    def _paired_b_dispositions(value: object) -> list[dict[str, Any]]:
        if not isinstance(value, (tuple, list)):
            return []
        return [dict(item["b"]) for item in value
                if isinstance(item, Mapping)
                and isinstance(item.get("b"), Mapping)]

    def _capture_target_group_visibility(self, record: Mapping[str, Any]) -> None:
        expected = TARGET_COMPLETE_BUCKET_RECORDS.get(int(record["record_ordinal"]))
        if expected is None:
            return
        if (tuple(record["raw_identity"]) != expected["raw_identity"]
                or list(record["event_digests"]) != [expected["event_digest"]]):
            raise _Stop("TARGET_COMPLETE_BUCKET_RECORD_IDENTITY_MISMATCH")
        owner = self.owners.coordinator._b
        if owner is None or self._group_before is None:
            raise _Stop("TARGET_COMPLETE_BUCKET_OWNER_MISSING")
        after = _group_owner_state(owner)
        delta = _journal_delta(self._group_before, after, owner.journal)
        bucket = int(expected["bucket"])
        target_audits = [item for item in delta if item.get("bucket") == bucket]
        dispositions = self._paired_b_dispositions(record["record_ab_transactions"])
        target_admissions = [item for item in dispositions
            if isinstance(item.get("admission"), Mapping)
            and item["admission"].get("bucket") == bucket]
        pending = {int(item["bucket"]): int(item["row_count"])
                   for item in after["pending"]}
        older = [item for item in after["pending"] if int(item["bucket"]) < bucket]
        if target_audits:
            reason = str(target_audits[-1]["reason"])
            attempted_bucket = bucket
        elif target_admissions:
            reason = str(target_admissions[-1]["reason"])
            attempted_bucket = bucket
        elif pending.get(bucket) == 10 and any(
                int(item.get("bucket", bucket)) < bucket for item in delta):
            reason = "BLOCKED_BY_EARLIER_BUCKET_ATTEMPT_THIS_RECORD"
            attempted_bucket = int(next(item["bucket"] for item in reversed(delta)
                if int(item.get("bucket", bucket)) < bucket))
        elif pending.get(bucket) == 10 and older:
            reason = "BLOCKED_BY_OLDER_PENDING_BUCKET"
            attempted_bucket = None
        else:
            reason = "TARGET_COMPLETE_WITHOUT_DECISIVE_DISPOSITION"
            attempted_bucket = None
        terminal = reason in {
            "STALE_POSE_LINK_REJECTED", "OBSOLETE_NATIVE200_SOURCE_PAIR",
        }
        nonterminal = reason in {
            "STALE_POSE_LINK_DEFERRED", "BLOCKED_BY_OLDER_PENDING_BUCKET",
            "BLOCKED_BY_EARLIER_BUCKET_ATTEMPT_THIS_RECORD",
        }
        evidence = {"target_bucket": bucket, "record_identity": {
                "record_ordinal": int(record["record_ordinal"]),
                "raw_identity": list(record["raw_identity"]),
                "event_digest": record["event_digests"][0],
                "region_id": record["region_id"],
                "record_completed": record.get("record_completed"),
                "rollback_observed": record.get("rollback_observed")},
            "before": self._group_before, "after": after,
            "journal_delta": delta, "paired_b_dispositions": dispositions,
            "attempted_bucket": attempted_bucket, "reason": reason,
            "terminal": terminal, "nonterminal": nonterminal,
            "stale_pose_link": next((item.get("stale_pose_link")
                for item in target_audits if item.get("stale_pose_link") is not None), None),
            "obsolete_native200_source_pair": next((
                item.get("obsolete_native200_source_pair") for item in target_audits
                if item.get("obsolete_native200_source_pair") is not None), None)}
        evidence["evidence_sha256"] = _canonical_digest(evidence)
        self.group_visibility.append(evidence)
        if reason in {"STALE_POSE_LINK_REJECTED", "STALE_POSE_LINK_DEFERRED",
                      "OBSOLETE_NATIVE200_SOURCE_PAIR",
                      "BLOCKED_BY_OLDER_PENDING_BUCKET",
                      "BLOCKED_BY_EARLIER_BUCKET_ATTEMPT_THIS_RECORD"}:
            self.decisive_group_disposition = evidence
            raise _GroupDispositionComplete("FIRST_COMPLETE_BUCKET_PRE_ADMISSION_DISPOSITION")

    def _capture_followup_effects(
        self, record: dict[str, Any], *, stop_on_resolution: bool = True,
    ) -> str | None:
        b_audit = self._find_b_audit(record["record_ab_transactions"])
        for effect in self._record_effects:
            operation = effect["operation"]
            if self.admission is None and operation == "commit_admission":
                pre_vector = np.asarray(effect["pre_root"]["vector"], float)
                post_vector = np.asarray(effect["post_root"]["vector"], float)
                self.admission = {"record": record, "audit": b_audit, **effect,
                    "applied_position_delta_m": (post_vector[:3] - pre_vector[:3]).tolist(),
                    "velocity_and_bias_byte_inert": (
                        pre_vector[3:9].tobytes() == post_vector[3:9].tobytes()),
                    "pre_post_covariance": {
                        "pre": effect["pre_root"]["covariance"],
                        "post": effect["post_root"]["covariance"]}}
            if operation == "commit_consensus_admission" and self.queue is None:
                pending = effect.get("post_drift", {}).get("pending", [])
                self.queue = {"record_identity": {
                    "record_ordinal": record["record_ordinal"],
                    "raw_identity": record["raw_identity"]},
                    "package": None if not pending else pending[-1], **effect}
            if operation in {"commit_native200", "commit_native200_batch"}:
                prepared = effect.get("prepared", {})
                if self.queue is None and prepared.get("drift_queued_package") is not None:
                    package = prepared["drift_queued_package"]
                    self.queue = {"record_identity": {
                        "record_ordinal": record["record_ordinal"],
                        "raw_identity": record["raw_identity"]},
                        "embedded_in_native": True,
                        "package_digest": package.get("digest"), "package": package}
                package = None if self.queue is None else self.queue.get("package")
                if not isinstance(package, Mapping): continue
                availability_ns = int(package["availability_time_ns"])
                frames = ([prepared.get("frame")] if operation == "commit_native200"
                          else list(prepared.get("frames", ())))
                for frame_index, frame in enumerate(frames):
                    if not isinstance(frame, Mapping): continue
                    observation = {"record_ordinal": record["record_ordinal"],
                        "operation": operation, "frame_index": frame_index,
                        "frame": frame, "availability_time_ns": availability_ns,
                        "eligible": int(frame["source_global_ns"]) >= availability_ns}
                    self.native_observation_order.append(observation)
                    if not observation["eligible"] or self.native_response is not None:
                        continue
                    drift_result = prepared.get("drift_result")
                    consumed = (None if not isinstance(drift_result, Mapping) else
                                drift_result.get("consumed_observation_digest"))
                    imu_doc = prepared.get("imu_candidate")
                    final_doc = prepared.get("final_root_candidate")
                    candidate_bound = isinstance(imu_doc, Mapping) and isinstance(final_doc, Mapping)
                    pre_vector = (np.zeros(9) if not candidate_bound else
                                  np.asarray(imu_doc["vector"], float))
                    post_vector = (np.zeros(9) if not candidate_bound else
                                   np.asarray(final_doc["vector"], float))
                    state = self.owners.coordinator._b._composition.engine.root.current_state
                    _in_policy, policy = self._policy(state)
                    velocity_delta = post_vector[3:6] - pre_vector[3:6]
                    self.native_response = {"record": record, **effect,
                        "first_eligible_native": observation,
                        "consumed_observation_digest": consumed,
                        "velocity_delta_mps": velocity_delta.tolist(),
                        "velocity_component_norm_mps": float(np.linalg.norm(velocity_delta)),
                        "position_bias_covariance_byte_inert": bool(candidate_bound
                            and pre_vector[:3].tobytes() == post_vector[:3].tobytes()
                            and pre_vector[6:9].tobytes() == post_vector[6:9].tobytes()
                            and np.asarray(imu_doc["covariance"], float).tobytes()
                            == np.asarray(final_doc["covariance"], float).tobytes()),
                        "eligible_by_integer_time": True, "post_policy": policy,
                        "reentered_anchor_envelope": policy["inside_anchor_envelope"]}
                    break
        if self.admission is None and b_audit is not None:
            self.admission = {"record": record, "audit": b_audit,
                              "operation": "NO_COMMIT_ADMISSION"}
        if (self.admission is not None and self.native_response is None
                and isinstance(self.admission.get("audit"), Mapping)
                and self.admission["audit"].get("prepared_accepted") is False):
            self.native_response = {"not_applicable": True,
                "reason": self.admission["audit"].get("prepared_reason")}
        if self.admission is not None and self.native_response is not None:
            if self.native_response.get("not_applicable") is True:
                reason = "FIRST_SUBSEQUENT_B_UWB_REJECTED"
                if stop_on_resolution: raise _FollowupComplete(reason)
                return reason
            package = None if self.queue is None else self.queue.get("package")
            expected = None if not isinstance(package, Mapping) else package.get("digest")
            actual = self.native_response.get("consumed_observation_digest")
            if actual is None:
                raise _FollowupViolation(
                    "FIRST_ELIGIBLE_NATIVE_DID_NOT_CONSUME_QUEUED_PACKAGE")
            if actual != expected:
                raise _FollowupViolation(
                    "FIRST_ELIGIBLE_NATIVE_CONSUMED_WRONG_PACKAGE")
            reason = "FIRST_ELIGIBLE_NATIVE_CONSUMED_QUEUED_PACKAGE"
            if stop_on_resolution: raise _FollowupComplete(reason)
            return reason
        return None

    def _after_successful_record(self, record: dict[str, Any]) -> None:
        if self.crossing is None: return
        crossing_digest = hashlib.sha256(json.dumps(
            _jsonable(self.crossing), sort_keys=True, separators=(",", ":"),
            allow_nan=False).encode()).hexdigest()
        if crossing_digest != SEALED_CROSSING_DIGEST:
            raise _Stop("SEALED_FIRST_CROSSING_IDENTITY_MISMATCH")
        self._capture_target_group_visibility(record)
        self._capture_followup_effects(record)


class FixedBucket1958009Observer(CrossingUwbResponseObserver):
    """Observe one sealed group transaction independently of physical crossing."""

    def __init__(self, owners: object, started: float) -> None:
        super().__init__(owners, started)
        self.old_crossing_checkpoint: dict[str, Any] | None = None
        self.target_chain_resolution: str | None = None

    def _after_detailed_commit(self, branch: str, operation: str, plan: object,
                               result: object, pre_root: dict[str, Any],
                               post_root: dict[str, Any], pre_drift: object,
                               post_drift: object) -> None:
        if branch != "b": return
        self._record_effects.append({"operation": operation,
            "prepared": _plan_doc(plan, extended=True), "result": _project(result),
            "pre_root": pre_root, "post_root": post_root,
            "pre_drift": pre_drift, "post_drift": post_drift})

    def _document_policy(self, document: Mapping[str, Any]) -> dict[str, Any]:
        vector = np.asarray(document["vector"], float)
        inside = bool(np.all(vector[:3] >= self.anchor_lower_m)
                      and np.all(vector[:3] <= self.anchor_upper_m))
        return {"inside_anchor_envelope": inside,
            "speed_mps": float(np.linalg.norm(vector[3:6])),
            "speed_limit_mps": MAX_SPEED_MPS,
            "lower_m": self.anchor_lower_m.tolist(),
            "upper_m": self.anchor_upper_m.tolist()}

    def _after_successful_record(self, record: dict[str, Any]) -> None:
        ordinal = int(record["record_ordinal"])
        if ordinal == OLD_CROSSING_RECORD["record_ordinal"]:
            if (tuple(record["raw_identity"]) != OLD_CROSSING_RECORD["raw_identity"]
                    or int(record["events_after"])
                    != OLD_CROSSING_RECORD["events_after"]):
                raise _FollowupViolation("OLD_CROSSING_CHECKPOINT_IDENTITY_MISMATCH")
            state = record["post_ab"]["b"]
            policy = self._document_policy(state)
            self.old_crossing_checkpoint = {"counterfactual_crossing_sha256":
                SEALED_CROSSING_DIGEST, "record_identity": {
                    "record_ordinal": ordinal,
                    "raw_identity": list(record["raw_identity"]),
                    "event_digests": list(record["event_digests"]),
                    "events_after": int(record["events_after"]),
                    "record_completed": True, "rollback_observed": False},
                "post_b": state, "policy": policy}
            if (not policy["inside_anchor_envelope"]
                    or policy["speed_mps"] > MAX_SPEED_MPS):
                raise _FollowupViolation("OLD_CROSSING_CHECKPOINT_REMAINS_OUT_OF_POLICY")

        if ordinal == 36_833:
            if self.old_crossing_checkpoint is None:
                raise _FollowupViolation("OLD_CROSSING_CHECKPOINT_MISSING")
            self._capture_target_group_visibility(record)
            resolution = self._capture_followup_effects(
                record, stop_on_resolution=False)
            if self.admission is None:
                raise _FollowupViolation("TARGET_COMPLETE_GROUP_WITHOUT_B_ADMISSION")
            if resolution == "FIRST_SUBSEQUENT_B_UWB_REJECTED":
                self.target_chain_resolution = resolution
                raise _FollowupComplete("TARGET_B_ADMISSION_SCIENTIFICALLY_REJECTED")
        elif self.admission is not None and self.target_chain_resolution is None:
            resolution = self._capture_followup_effects(
                record, stop_on_resolution=False)
            if resolution is not None:
                self.target_chain_resolution = resolution

        if (self.target_chain_resolution
                == "FIRST_ELIGIBLE_NATIVE_CONSUMED_QUEUED_PACKAGE"
                and self.crossing is not None):
            raise _FollowupComplete(
                "TARGET_CHAIN_RESOLVED_WITH_LATER_PHYSICAL_CROSSING")


def run(*, followup: bool = False, fixed_target: bool = False) -> dict[str, Any]:
    started = time.monotonic(); owners = build()
    anchors = np.asarray(owners.coordinator._initializer._static.anchors_m, float)
    if fixed_target:
        observer = FixedBucket1958009Observer(owners, started)
        followup = True
    else:
        observer = (CrossingUwbResponseObserver(owners, started) if followup
                    else FirstBPhysicalCrossingObserver(owners, started))
    observer.anchor_lower_m = anchors.min(axis=0) - ANCHOR_MARGIN_M
    observer.anchor_upper_m = anchors.max(axis=0) + ANCHOR_MARGIN_M
    owners.coordinator.consume_record_ticket = observer
    previous = signal.getsignal(signal.SIGALRM)
    def deadline(*_: object) -> None: raise _Stop("INTERNAL_1800S_DEADLINE")
    signal.signal(signal.SIGALRM, deadline); signal.setitimer(signal.ITIMER_REAL, INTERNAL_SECONDS)
    stop_reason, failure = "SOURCE_EOF_WITHOUT_CROSSING", None
    try: owners.coordinator.run(owners.reader)
    except _Stop as stopped: stop_reason = stopped.reason
    except BaseException as error:
        stop_reason = "UNEXPECTED_FAILURE"
        failure = {"type": type(error).__name__, "message": str(error)}
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0.0); signal.signal(signal.SIGALRM, previous)
    audit = owners.coordinator.audit()
    if fixed_target:
        response = observer.native_response
        package = None if observer.queue is None else observer.queue.get("package")
        expected_package = (None if not isinstance(package, Mapping)
                            else package.get("digest"))
        response_ok = bool(isinstance(response, Mapping)
            and expected_package is not None
            and response.get("consumed_observation_digest") == expected_package)
        rejected = bool(
            isinstance(observer.admission, Mapping)
            and isinstance(observer.admission.get("audit"), Mapping)
            and observer.admission["audit"].get("prepared_accepted") is False)
        complete = bool(observer.old_crossing_checkpoint is not None
            and observer.admission is not None and response_ok
            and observer.crossing is not None and failure is None)
        status = ("FIXED_TARGET_CHAIN_CAPTURED" if complete
                  else "FIXED_TARGET_DOWNSTREAM_REJECTION_CAPTURED" if rejected
                  else "FIXED_TARGET_CHAIN_STOP")
        schema = FIXED_TARGET_SCHEMA
    elif followup:
        response = observer.native_response
        package = None if observer.queue is None else observer.queue.get("package")
        expected_package = (None if not isinstance(package, Mapping)
                            else package.get("digest"))
        response_ok = bool(isinstance(response, Mapping) and (
            response.get("not_applicable") is True
            or (expected_package is not None
                and response.get("consumed_observation_digest") == expected_package)))
        response_complete = (observer.crossing is not None
            and observer.admission is not None and response_ok and failure is None)
        disposition_complete = (observer.crossing is not None
            and observer.decisive_group_disposition is not None and failure is None)
        status = ("FIRST_B_GROUP_DISPOSITION_CAPTURED" if disposition_complete
                  else "FIRST_B_UWB_RESPONSE_CAPTURED" if response_complete
                  else "FIRST_B_UWB_RESPONSE_STOP")
        schema = FOLLOWUP_SCHEMA
    else:
        status = ("FIRST_B_PHYSICAL_CROSSING_CAPTURED"
                  if observer.crossing is not None and failure is None
                  else "FIRST_B_PHYSICAL_CROSSING_STOP")
        schema = SCHEMA
    result = {"schema": schema, "status": status, "authorization": "STOP",
        "diagnostic_only": True, "product_ready": False, "scientific_pass": False,
        "fusion_decisions_modified": False, "failure": failure, "stop_reason": stop_reason,
        "limits": {"event_cap": EVENT_CAP, "internal_seconds": INTERNAL_SECONDS,
            "outer_seconds": OUTER_SECONDS, "rlimit_as_bytes": 1 << 30, "threads": 1,
            "maximum_output_bytes": MAX_OUTPUT_BYTES, "retry": False,
            "resume_capable": False},
        "events": int(audit.events), "records": int(observer.records),
        "crossing": observer.crossing, "observer_failures": list(observer.observer_failures),
        "invalid_state": observer.invalid_state,
        "anchor_envelope": {"anchors_sha256": hashlib.sha256(anchors.tobytes()).hexdigest(),
            "lower_m": observer.anchor_lower_m.tolist(),
            "upper_m": observer.anchor_upper_m.tolist(), "margin_m": ANCHOR_MARGIN_M},
        "provenance": {"source_sha256": {str(path.relative_to(ROOT)): _sha(path)
            for path in sorted((ROOT / "src/biospur_fusion").rglob("*.py"))},
            "factory_inputs": {str(path): digest for path, digest in EXPECTED.items()},
            "self_sha256": _sha(Path(__file__).resolve())},
        "wall_s": time.monotonic() - started,
        "peak_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss}
    if followup:
        result["first_subsequent_b_admission"] = observer.admission
        result["queued_drift_package"] = observer.queue
        result["first_eligible_native_response"] = observer.native_response
        result["native_observation_order"] = observer.native_observation_order
        result["b_group_visibility"] = observer.group_visibility
        result["decisive_b_group_disposition"] = observer.decisive_group_disposition
        result["sealed_crossing_digest"] = SEALED_CROSSING_DIGEST
        result["limits"].update({"event_cap": FOLLOWUP_EVENT_CAP,
            "record_cap_exclusive": FOLLOWUP_RECORD_CAP_EXCLUSIVE,
            "cap_basis": {"sealed_crossing_record_ordinal": 36_815,
                "sealed_crossing_events_after": 270_708,
                "following_complete_record_boundaries": 64,
                "maximum_events_per_record": 16}})
    if fixed_target:
        result["old_crossing_checkpoint"] = observer.old_crossing_checkpoint
        result["target_chain_resolution"] = observer.target_chain_resolution
        result["target_record"] = TARGET_COMPLETE_BUCKET_RECORDS[36_833]
    return result


def validate_result(result: Mapping[str, Any]) -> None:
    expected_limits = {"event_cap": EVENT_CAP, "internal_seconds": INTERNAL_SECONDS,
        "outer_seconds": OUTER_SECONDS, "rlimit_as_bytes": 1 << 30, "threads": 1,
        "maximum_output_bytes": MAX_OUTPUT_BYTES, "retry": False, "resume_capable": False}
    crossing = result.get("crossing")
    record = {} if not isinstance(crossing, Mapping) else crossing.get("record_transaction", {})
    pre = {} if not isinstance(crossing, Mapping) else crossing.get("pre_publication", {})
    post = {} if not isinstance(crossing, Mapping) else crossing.get("post_publication", {})
    policy = {} if not isinstance(crossing, Mapping) else crossing.get("post_policy", {})
    operation = {} if not isinstance(crossing, Mapping) else crossing.get("causal_operation", {})
    provenance = result.get("provenance", {})
    envelope = result.get("anchor_envelope", {})
    def sha256_text(value: object) -> bool:
        text = str(value)
        return len(text) == 64 and all(character in "0123456789abcdef"
                                       for character in text)
    def valid_state(document: object) -> bool:
        if not isinstance(document, Mapping): return False
        try:
            vector = np.asarray(document["vector"], float)
            covariance = np.asarray(document["covariance"], float)
            state_digest, covariance_digest = _state_document_hashes(
                float(document["time_s"]), vector, covariance)
            return bool(vector.shape == (9,) and covariance.shape == (9, 9)
                and np.isfinite(vector).all() and np.isfinite(covariance).all()
                and np.allclose(covariance, covariance.T, rtol=0., atol=1e-10)
                and np.linalg.cholesky(covariance) is not None
                and len(str(document["publication_digest"])) == 64
                and document["root_state_canonical_sha256"] == state_digest
                and document["covariance_sha256"] == covariance_digest)
        except (KeyError, TypeError, ValueError, np.linalg.LinAlgError): return False
    pre_inside = False; post_outside = False; speed_matches = False
    try:
        lower, upper = np.asarray(policy["lower_m"], float), np.asarray(policy["upper_m"], float)
        pre_vector, post_vector = np.asarray(pre["vector"], float), np.asarray(post["vector"], float)
        post_speed = float(np.linalg.norm(post_vector[3:6]))
        pre_inside = bool(np.all(pre_vector[:3] >= lower) and np.all(pre_vector[:3] <= upper)
                          and np.linalg.norm(pre_vector[3:6]) <= MAX_SPEED_MPS)
        post_outside = bool(np.any(post_vector[:3] < lower) or np.any(post_vector[:3] > upper)
                            or post_speed > MAX_SPEED_MPS)
        speed_matches = bool(np.isclose(float(policy["speed_mps"]), post_speed,
                                        rtol=0., atol=1e-12))
    except (KeyError, TypeError, ValueError): pass
    contextual_states = []
    try:
        contextual_states = [record[boundary][branch]
            for boundary in ("pre_ab", "post_ab") for branch in ("a", "b")]
    except (KeyError, TypeError): pass
    envelope_matches = False
    try:
        envelope_matches = bool(
            float(envelope["margin_m"]) == ANCHOR_MARGIN_M
            and sha256_text(envelope["anchors_sha256"])
            and np.array_equal(np.asarray(envelope["lower_m"], float), lower)
            and np.array_equal(np.asarray(envelope["upper_m"], float), upper))
    except (KeyError, TypeError, ValueError): pass
    self_path = Path(__file__).resolve()
    if (result.get("schema") != SCHEMA
            or result.get("status") != "FIRST_B_PHYSICAL_CROSSING_CAPTURED"
            or result.get("authorization") != "STOP"
            or result.get("diagnostic_only") is not True
            or result.get("product_ready") is not False
            or result.get("scientific_pass") is not False
            or result.get("fusion_decisions_modified") is not False
            or result.get("failure") is not None
            or result.get("stop_reason") != "FIRST_COMMITTED_B_PHYSICAL_CROSSING"
            or not isinstance(crossing, Mapping)
            or result.get("events", EVENT_CAP + 1) > EVENT_CAP
            or result.get("limits") != expected_limits
            or result.get("observer_failures") != []
            or not valid_state(pre) or not valid_state(post)
            or len(contextual_states) != 4
            or not all(valid_state(document) for document in contextual_states)
            or not pre_inside or not post_outside
            or not envelope_matches or not speed_matches
            or policy.get("speed_limit_mps") != MAX_SPEED_MPS
            or policy.get("inside_anchor_envelope") is not (
                not np.any(np.asarray(post.get("vector", [np.nan]*9))[:3]
                           < np.asarray(policy.get("lower_m", [np.nan]*3)))
                and not np.any(np.asarray(post.get("vector", [np.nan]*9))[:3]
                               > np.asarray(policy.get("upper_m", [np.nan]*3))))
            or record.get("record_completed") is not True
            or record.get("rollback_observed") is not False
            or len(record.get("raw_identity", ())) != 4
            or not record.get("event_digests") or not record.get("region_id")
            or not isinstance(record.get("pre_ab"), Mapping)
            or not isinstance(record.get("post_ab"), Mapping)
            or not operation.get("route") or not operation.get("events")
            or not isinstance(crossing.get("plan_or_decision"), Mapping)
            or len(str(provenance.get("self_sha256", ""))) != 64
            or provenance.get("self_sha256") != _sha(self_path)
            or not isinstance(provenance.get("source_sha256"), Mapping)
            or not provenance.get("source_sha256")
            or any(len(str(digest)) != 64
                   for digest in provenance.get("source_sha256", {}).values())):
        raise ValueError("invalid first-crossing diagnostic result")


def _validate_group_owner_state_document(document: object) -> bool:
    if not isinstance(document, Mapping):
        return False
    try:
        revision = document["revision"]
        watermark = document["finalized_watermark"]
        pending = document["pending"]
        journal = document["journal_digests"]
        if (type(revision) is not int or revision < 0
                or (watermark is not None and type(watermark) is not int)
                or not isinstance(pending, list) or not isinstance(journal, list)
                or any(not isinstance(item, str) or len(item) != 64 for item in journal)):
            return False
        buckets = []
        for item in pending:
            if not isinstance(item, Mapping): return False
            bucket, row_count = item["bucket"], item["row_count"]
            nodes, event_ids = item["nodes"], item["event_ids"]
            if (type(bucket) is not int or type(row_count) is not int
                    or not 1 <= row_count <= 10 or not isinstance(nodes, list)
                    or not isinstance(event_ids, list)
                    or len(nodes) != row_count or len(event_ids) != row_count
                    or len(set(nodes)) != row_count or len(set(event_ids)) != row_count
                    or nodes != sorted(nodes)
                    or any(not isinstance(value, str) or not value
                           for value in (*nodes, *event_ids))):
                return False
            buckets.append(bucket)
        if buckets != sorted(set(buckets)):
            return False
        body = {key: value for key, value in document.items()
                if key != "state_sha256"}
        return document.get("state_sha256") == _canonical_digest(body)
    except (KeyError, TypeError, ValueError):
        return False


def _derived_group_disposition(evidence: Mapping[str, Any]) -> tuple[object, str]:
    bucket = int(evidence["target_bucket"])
    delta = evidence["journal_delta"]
    dispositions = evidence["paired_b_dispositions"]
    after_pending = {int(item["bucket"]): int(item["row_count"])
                     for item in evidence["after"]["pending"]}
    target_audits = [item for item in delta
                     if isinstance(item, Mapping) and item.get("bucket") == bucket]
    target_admissions = [item for item in dispositions
        if isinstance(item, Mapping) and isinstance(item.get("admission"), Mapping)
        and item["admission"].get("bucket") == bucket]
    older = [item for item in evidence["after"]["pending"]
             if int(item["bucket"]) < bucket]
    if target_audits:
        return bucket, str(target_audits[-1]["reason"])
    if target_admissions:
        return bucket, str(target_admissions[-1]["reason"])
    earlier = [item for item in delta if isinstance(item, Mapping)
               and int(item.get("bucket", bucket)) < bucket]
    if after_pending.get(bucket) == 10 and earlier:
        return int(earlier[-1]["bucket"]), "BLOCKED_BY_EARLIER_BUCKET_ATTEMPT_THIS_RECORD"
    if after_pending.get(bucket) == 10 and older:
        return None, "BLOCKED_BY_OLDER_PENDING_BUCKET"
    return None, "TARGET_COMPLETE_WITHOUT_DECISIVE_DISPOSITION"


def validate_followup_result(result: Mapping[str, Any]) -> None:
    disposition = result.get("decisive_b_group_disposition")
    if result.get("status") == "FIRST_B_GROUP_DISPOSITION_CAPTURED":
        visibility = result.get("b_group_visibility")
        crossing = result.get("crossing")
        limits = {"event_cap": FOLLOWUP_EVENT_CAP,
            "internal_seconds": INTERNAL_SECONDS, "outer_seconds": OUTER_SECONDS,
            "rlimit_as_bytes": 1 << 30, "threads": 1,
            "maximum_output_bytes": MAX_OUTPUT_BYTES, "retry": False,
            "resume_capable": False,
            "record_cap_exclusive": FOLLOWUP_RECORD_CAP_EXCLUSIVE,
            "cap_basis": {"sealed_crossing_record_ordinal": 36_815,
                "sealed_crossing_events_after": 270_708,
                "following_complete_record_boundaries": 64,
                "maximum_events_per_record": 16}}
        record_identity = {} if not isinstance(disposition, Mapping) else (
            disposition.get("record_identity", {}))
        target = TARGET_COMPLETE_BUCKET_RECORDS.get(
            record_identity.get("record_ordinal")
            if isinstance(record_identity, Mapping) else None)
        before = {} if not isinstance(disposition, Mapping) else disposition.get("before")
        after = {} if not isinstance(disposition, Mapping) else disposition.get("after")
        journal_consistent = False
        try:
            previous = list(before["journal_digests"])
            current = list(after["journal_digests"])
            overlap = next(candidate for candidate in range(
                min(len(previous), len(current)), -1, -1)
                if previous[len(previous) - candidate:] == current[:candidate])
            appended = current[overlap:]
            journal_consistent = appended == [
                _canonical_digest(item) for item in disposition["journal_delta"]]
        except (KeyError, TypeError, ValueError, StopIteration):
            pass
        derived = (None, "")
        try: derived = _derived_group_disposition(disposition)
        except (KeyError, TypeError, ValueError): pass
        reason = disposition.get("reason") if isinstance(disposition, Mapping) else None
        target_audits = ([] if not isinstance(disposition, Mapping) else [item
            for item in disposition.get("journal_delta", ())
            if isinstance(item, Mapping)
            and item.get("bucket") == disposition.get("target_bucket")])
        stale = disposition.get("stale_pose_link") if isinstance(disposition, Mapping) else None
        obsolete = (disposition.get("obsolete_native200_source_pair")
                    if isinstance(disposition, Mapping) else None)
        diagnostics_match = bool(
            (reason in {"STALE_POSE_LINK_REJECTED", "STALE_POSE_LINK_DEFERRED"}
             and isinstance(stale, Mapping) and obsolete is None
             and any(item.get("stale_pose_link") == stale for item in target_audits))
            or (reason == "OBSOLETE_NATIVE200_SOURCE_PAIR"
                and isinstance(obsolete, Mapping) and stale is None
                and any(item.get("obsolete_native200_source_pair") == obsolete
                        for item in target_audits))
            or (reason in {"BLOCKED_BY_OLDER_PENDING_BUCKET",
                           "BLOCKED_BY_EARLIER_BUCKET_ATTEMPT_THIS_RECORD"}
                and stale is None and obsolete is None))
        target_complete = bool(
            any(item.get("bucket") == disposition.get("target_bucket")
                and item.get("row_count") == 10
                for item in after.get("pending", ()))
            or any(len(item.get("nodes", ())) == 10
                   and len(set(item.get("nodes", ()))) == 10
                   for item in target_audits))
        expected_terminal = reason in {
            "STALE_POSE_LINK_REJECTED", "OBSOLETE_NATIVE200_SOURCE_PAIR"}
        expected_nonterminal = reason in {
            "STALE_POSE_LINK_DEFERRED", "BLOCKED_BY_OLDER_PENDING_BUCKET",
            "BLOCKED_BY_EARLIER_BUCKET_ATTEMPT_THIS_RECORD"}
        crossing_digest = ""
        try:
            crossing_digest = _canonical_digest(crossing)
        except (TypeError, ValueError): pass
        if (result.get("schema") != FOLLOWUP_SCHEMA
                or result.get("authorization") != "STOP"
                or result.get("diagnostic_only") is not True
                or result.get("product_ready") is not False
                or result.get("scientific_pass") is not False
                or result.get("fusion_decisions_modified") is not False
                or result.get("failure") is not None
                or result.get("stop_reason")
                   != "FIRST_COMPLETE_BUCKET_PRE_ADMISSION_DISPOSITION"
                or crossing_digest != SEALED_CROSSING_DIGEST
                or result.get("sealed_crossing_digest") != SEALED_CROSSING_DIGEST
                or result.get("limits") != limits
                or target is None
                or result.get("events") != target.get("events_after")
                or result.get("records") != record_identity.get("record_ordinal") + 1
                or result.get("observer_failures") != []
                or not isinstance(visibility, list) or not visibility
                or not isinstance(disposition, Mapping)
                or disposition != visibility[-1]
                or disposition.get("target_bucket") not in {1_958_009, 1_958_010}
                or disposition.get("target_bucket") != target.get("bucket")
                or tuple(record_identity.get("raw_identity", ())) != target.get("raw_identity")
                or record_identity.get("event_digest") != target.get("event_digest")
                or not record_identity.get("region_id")
                or record_identity.get("record_completed") is not True
                or record_identity.get("rollback_observed") is not False
                or not _validate_group_owner_state_document(before)
                or not _validate_group_owner_state_document(after)
                or after["revision"] != before["revision"] + 1
                or (before["finalized_watermark"] is not None
                    and after["finalized_watermark"] is not None
                    and after["finalized_watermark"] < before["finalized_watermark"])
                or not journal_consistent
                or not target_complete
                or (disposition.get("attempted_bucket"), reason) != derived
                or disposition.get("reason") not in {
                    "STALE_POSE_LINK_REJECTED", "STALE_POSE_LINK_DEFERRED",
                    "OBSOLETE_NATIVE200_SOURCE_PAIR",
                    "BLOCKED_BY_OLDER_PENDING_BUCKET",
                    "BLOCKED_BY_EARLIER_BUCKET_ATTEMPT_THIS_RECORD"}
                or disposition.get("terminal") is not expected_terminal
                or disposition.get("nonterminal") is not expected_nonterminal
                or not diagnostics_match
                or disposition.get("evidence_sha256") != _canonical_digest({
                    key: value for key, value in disposition.items()
                    if key != "evidence_sha256"})):
            raise ValueError("invalid B group-disposition evidence")
        return
    if (result.get("schema") != FOLLOWUP_SCHEMA
            or result.get("status") != "FIRST_B_UWB_RESPONSE_CAPTURED"
            or result.get("authorization") != "STOP"
            or result.get("diagnostic_only") is not True
            or result.get("product_ready") is not False
            or result.get("scientific_pass") is not False
            or result.get("fusion_decisions_modified") is not False
            or result.get("failure") is not None
            or result.get("sealed_crossing_digest") != SEALED_CROSSING_DIGEST
            or result.get("events", FOLLOWUP_EVENT_CAP + 1) > FOLLOWUP_EVENT_CAP
            or result.get("limits", {}).get("record_cap_exclusive")
               != FOLLOWUP_RECORD_CAP_EXCLUSIVE
            or result.get("limits", {}).get("event_cap") != FOLLOWUP_EVENT_CAP
            or result.get("limits", {}).get("cap_basis") != {
                "sealed_crossing_record_ordinal": 36_815,
                "sealed_crossing_events_after": 270_708,
                "following_complete_record_boundaries": 64,
                "maximum_events_per_record": 16}
            or not isinstance(result.get("first_subsequent_b_admission"), Mapping)
            or not isinstance(result.get("first_eligible_native_response"), Mapping)
            or not isinstance(result.get("native_observation_order"), list)
            or result.get("observer_failures") != []):
        raise ValueError("invalid first-crossing UWB-response diagnostic result")
    crossing = result.get("crossing")
    actual = hashlib.sha256(json.dumps(
        _jsonable(crossing), sort_keys=True, separators=(",", ":"),
        allow_nan=False).encode()).hexdigest()
    if actual != SEALED_CROSSING_DIGEST:
        raise ValueError("sealed first crossing changed")
    admission = result["first_subsequent_b_admission"]
    response = result["first_eligible_native_response"]
    audit = admission.get("audit")
    admission_record = admission.get("record", {})
    if (not isinstance(audit, Mapping) or audit.get("branch") != "B_UWB"
            or not isinstance(admission_record, Mapping)
            or admission_record.get("record_completed") is not True
            or admission_record.get("rollback_observed") is not False):
        raise ValueError("missing authoritative B admission audit")
    if audit.get("prepared_accepted") is True:
        queue = result.get("queued_drift_package")
        prepared_admission = admission.get("prepared", {})
        package = None if not isinstance(queue, Mapping) else queue.get("package")
        if (not isinstance(queue, Mapping) or not isinstance(package, Mapping)
                or not package.get("digest")
                or not isinstance(prepared_admission.get("root_observation"), Mapping)
                or not prepared_admission.get("root_plan_digest")
                or not admission.get("velocity_and_bias_byte_inert")
                or np.asarray(admission.get("applied_position_delta_m", ()), float).shape != (3,)
                or not np.isfinite(np.asarray(
                    admission.get("applied_position_delta_m", ()), float)).all()):
            raise ValueError("accepted admission lacks queued package")
        if response.get("not_applicable") is True:
            raise ValueError("accepted admission lacks native response")
        prepared = response.get("prepared", {})
        drift = prepared.get("drift_result", {})
        response_record = response.get("record", {})
        order = result.get("native_observation_order", [])
        eligible = [item for item in order
                    if isinstance(item, Mapping) and item.get("eligible") is True]
        if (not isinstance(drift, Mapping)
                or drift.get("consumed_observation_digest") != package.get("digest")
                or response.get("consumed_observation_digest") != package.get("digest")
                or response.get("eligible_by_integer_time") is not True
                or response.get("position_bias_covariance_byte_inert") is not True
                or np.asarray(response.get("velocity_delta_mps", ()), float).shape != (3,)
                or not np.isfinite(np.asarray(response.get("velocity_delta_mps", ()), float)).all()
                or not isinstance(response_record, Mapping)
                or response_record.get("record_completed") is not True
                or response_record.get("rollback_observed") is not False
                or float(response.get("velocity_component_norm_mps", np.inf))
                   > 0.5 + 1e-12
                or not eligible
                or response.get("first_eligible_native") != eligible[0]
                or result.get("stop_reason")
                   != "FIRST_ELIGIBLE_NATIVE_CONSUMED_QUEUED_PACKAGE"):
            raise ValueError("native response did not consume queued package")
    elif audit.get("prepared_accepted") is False:
        if (response.get("not_applicable") is not True
                or result.get("stop_reason") != "FIRST_SUBSEQUENT_B_UWB_REJECTED"):
            raise ValueError("rejected admission unexpectedly consumed package")
    else:
        raise ValueError("non-decision admission cannot pass")


def validate_fixed_target_result(result: Mapping[str, Any]) -> None:
    """Fail-closed validation for the one bucket-1958009 causal probe."""
    _validate_finite_projection(result)
    def hex64(value: object) -> bool:
        return (isinstance(value, str) and len(value) == 64
                and all(character in "0123456789abcdef" for character in value))

    def projected_array(document: object, shape: tuple[int, ...]) -> np.ndarray:
        if not isinstance(document, Mapping): raise ValueError("missing projected array")
        array = np.asarray(document["values"], dtype=np.dtype(document["dtype"]))
        if tuple(document["shape"]) != shape or array.shape != shape:
            raise ValueError("invalid projected array shape")
        if hashlib.sha256(array.tobytes()).hexdigest() != document["sha256"]:
            raise ValueError("invalid projected array digest")
        array.setflags(write=False)
        return array

    def policy_for(document: Mapping[str, Any], lower: np.ndarray,
                   upper: np.ndarray) -> dict[str, Any]:
        vector = np.asarray(document["vector"], float)
        return {"inside_anchor_envelope": bool(
                    np.all(vector[:3] >= lower) and np.all(vector[:3] <= upper)),
                "speed_mps": float(np.linalg.norm(vector[3:6])),
                "speed_limit_mps": MAX_SPEED_MPS,
                "lower_m": lower.tolist(), "upper_m": upper.tolist()}

    def valid_package(document: object, *, prepared: Mapping[str, Any],
                      audit: Mapping[str, Any]) -> bool:
        if not isinstance(document, Mapping): return False
        try:
            if set(document) != {
                "observation", "availability_time_ns", "trusted_nodes",
                "anchors_used", "packet_digest", "epoch_digest",
                "root_plan_digest", "admission_digest",
                "post_absolute_position_at_measurement_m",
                "applied_absolute_position_delta_m",
                "cumulative_absolute_position_correction_m", "digest",
            }:
                return False
            observed = document["observation"]
            position = projected_array(observed["root_position_m"], (3,))
            covariance = projected_array(observed["covariance_m2"], (3, 3))
            observation = PositionObservation(
                float(observed["measurement_time_s"]),
                float(observed["availability_time_s"]), position, covariance,
                str(observed["tag_id"]), tuple(observed["anchors"]),
                str(observed["quality_state"]), bool(observed["frame_valid"]),
                bool(observed["physical_point_valid"]),
                int(observed["source_sequence"]),
            )
            observation.validate()
            package = ContinuousConsensusObservation(
                observation, int(document["availability_time_ns"]),
                tuple(document["trusted_nodes"]), tuple(document["anchors_used"]),
                str(document["packet_digest"]), str(document["epoch_digest"]),
                str(document["root_plan_digest"]), str(document["admission_digest"]),
                projected_array(document["post_absolute_position_at_measurement_m"], (3,)),
                projected_array(document["applied_absolute_position_delta_m"], (3,)),
                projected_array(document["cumulative_absolute_position_correction_m"], (3,)),
                str(document["digest"]),
            )
            return bool(hex64(package.digest)
                and package.digest == _package_digest(package)
                and package.packet_digest == audit["packet_digest"]
                and package.epoch_digest == audit["epoch_digest"]
                and package.admission_digest == audit["candidate_digest"]
                and package.root_plan_digest == prepared["root_plan_digest"]
                and package.availability_time_ns
                    == prepared["packet_availability_global_ns"]
                and package.availability_time_ns
                    == round(observation.availability_time_s * 1e9)
                and package.trusted_nodes == tuple(audit["trusted_partition"])
                and package.trusted_nodes == tuple(prepared["trusted_partition"])
                and package.anchors_used == observation.anchors
                and _canonical_digest(observed)
                    == _canonical_digest(prepared["root_observation"]))
        except (KeyError, TypeError, ValueError):
            return False

    def valid_state(document: object) -> bool:
        if not isinstance(document, Mapping): return False
        try:
            vector = np.asarray(document["vector"], float)
            covariance = np.asarray(document["covariance"], float)
            state_digest, covariance_digest = _state_document_hashes(
                float(document["time_s"]), vector, covariance)
            return bool(vector.shape == (9,) and covariance.shape == (9, 9)
                and np.isfinite(vector).all() and np.isfinite(covariance).all()
                and np.allclose(covariance, covariance.T, rtol=0., atol=1e-10)
                and np.linalg.cholesky(covariance) is not None
                and document.get("root_state_canonical_sha256") == state_digest
                and document.get("covariance_sha256") == covariance_digest)
        except (KeyError, TypeError, ValueError, np.linalg.LinAlgError): return False

    target = TARGET_COMPLETE_BUCKET_RECORDS[36_833]
    limits = {"event_cap": FOLLOWUP_EVENT_CAP,
        "internal_seconds": INTERNAL_SECONDS, "outer_seconds": OUTER_SECONDS,
        "rlimit_as_bytes": 1 << 30, "threads": 1,
        "maximum_output_bytes": MAX_OUTPUT_BYTES, "retry": False,
        "resume_capable": False, "record_cap_exclusive": FOLLOWUP_RECORD_CAP_EXCLUSIVE,
        "cap_basis": {"sealed_crossing_record_ordinal": 36_815,
            "sealed_crossing_events_after": 270_708,
            "following_complete_record_boundaries": 64,
            "maximum_events_per_record": 16}}
    checkpoint = result.get("old_crossing_checkpoint")
    admission = result.get("first_subsequent_b_admission")
    queue = result.get("queued_drift_package")
    response = result.get("first_eligible_native_response")
    crossing = result.get("crossing")
    visibility = result.get("b_group_visibility")
    try:
        envelope = result["anchor_envelope"]
        lower = np.asarray(envelope["lower_m"], float)
        upper = np.asarray(envelope["upper_m"], float)
        envelope_ok = bool(lower.shape == (3,) and upper.shape == (3,)
            and np.isfinite(lower).all() and np.isfinite(upper).all()
            and np.all(lower < upper) and envelope["margin_m"] == ANCHOR_MARGIN_M
            and hex64(envelope["anchors_sha256"]))
        checkpoint_state = checkpoint["post_b"]
        checkpoint_vector = np.asarray(checkpoint_state["vector"], float)
        checkpoint_policy = checkpoint["policy"]
        expected_checkpoint_policy = policy_for(checkpoint_state, lower, upper)
        checkpoint_ok = bool(valid_state(checkpoint_state)
            and checkpoint["counterfactual_crossing_sha256"] == SEALED_CROSSING_DIGEST
            and checkpoint["record_identity"]["record_ordinal"] == 36_815
            and tuple(checkpoint["record_identity"]["raw_identity"])
                == OLD_CROSSING_RECORD["raw_identity"]
            and checkpoint["record_identity"]["events_after"] == 270_708
            and checkpoint["record_identity"]["record_completed"] is True
            and checkpoint["record_identity"]["rollback_observed"] is False
            and checkpoint_policy == expected_checkpoint_policy
            and expected_checkpoint_policy["inside_anchor_envelope"] is True
            and np.linalg.norm(checkpoint_vector[3:6]) <= MAX_SPEED_MPS)
        group = visibility[-1]
        previous_journal = list(group["before"]["journal_digests"])
        current_journal = list(group["after"]["journal_digests"])
        overlap = next(candidate for candidate in range(
            min(len(previous_journal), len(current_journal)), -1, -1)
            if previous_journal[len(previous_journal) - candidate:]
                == current_journal[:candidate])
        appended_journal = current_journal[overlap:]
        group_ok = bool(group["target_bucket"] == target["bucket"]
            and _validate_group_owner_state_document(group["before"])
            and _validate_group_owner_state_document(group["after"])
            and group["record_identity"]["record_ordinal"] == 36_833
            and tuple(group["record_identity"]["raw_identity"]) == target["raw_identity"]
            and group["record_identity"]["event_digest"] == target["event_digest"]
            and group["record_identity"]["record_completed"] is True
            and group["record_identity"]["rollback_observed"] is False
            and group["reason"] == "PREPARED_COMPLETE_GROUP"
            and group["obsolete_native200_source_pair"] is None
            and all(item.get("reason") != "OBSOLETE_NATIVE200_SOURCE_PAIR"
                    for item in group["journal_delta"])
            and appended_journal == [
                _canonical_digest(item) for item in group["journal_delta"]]
            and group["evidence_sha256"] == _canonical_digest({key: value
                for key, value in group.items() if key != "evidence_sha256"}))
        group_accept = next(item for item in group["journal_delta"]
            if item.get("bucket") == target["bucket"]
            and item.get("reason") == "PREPARED_COMPLETE_GROUP")
        audit = admission["audit"]
        admission_record = admission["record"]
        pairs = admission_record["record_ab_transactions"]
        pair = next(item for item in pairs if isinstance(item, Mapping)
                    and isinstance(item.get("b"), Mapping)
                    and item["b"].get("admission") == audit)
        a_audit = pair["a"]["admission"]
        exact_record_ok = bool(
            admission_record["record_ordinal"] == 36_833
            and tuple(admission_record["raw_identity"]) == target["raw_identity"]
            and admission_record["events_after"] == target["events_after"]
            and admission_record["event_digests"] == [target["event_digest"]])
        common_admission_ok = bool(
            audit["bucket"] == target["bucket"] and audit["branch"] == "B_UWB"
            and exact_record_ok and a_audit["bucket"] == target["bucket"]
            and a_audit["branch"] == "A_BASELINE"
            and type(a_audit["prepared_accepted"]) is bool
            and (a_audit["prepared_accepted"] is True
                 or isinstance(a_audit.get("prepared_reason"), str)
                 and bool(a_audit["prepared_reason"]))
            and a_audit["packet_digest"] == audit["packet_digest"]
            and a_audit["epoch_digest"] == audit["epoch_digest"]
            and a_audit["source_sequence"] == audit["source_sequence"]
            and a_audit["source_identity"] == audit["source_identity"]
            and a_audit["commit_intent"] is False
            and a_audit["commit_attempted"] is False
            and a_audit["commit_succeeded"] is False
            and a_audit["outcome"] == "BASELINE_NO_UWB_COMMIT"
            and ((a_audit.get("diagnostic") is None
                  and a_audit.get("diagnostic_digest") is None)
                 or isinstance(a_audit.get("diagnostic"), Mapping)
                 and hex64(a_audit.get("diagnostic_digest"))
                 and _digest_payload(a_audit["diagnostic"])
                     == a_audit["diagnostic_digest"])
            and admission_record["record_completed"] is True
            and admission_record["rollback_observed"] is False
            and group_accept["packet_digest"] == audit["packet_digest"]
            and group_accept["epoch_digest"] == audit["epoch_digest"]
            and group_accept["candidate_digest"] == audit["candidate_digest"])
        rejected = audit["prepared_accepted"] is False
        if rejected:
            admission_ok = bool(common_admission_ok
                and admission["operation"] == "NO_COMMIT_ADMISSION"
                and audit["outcome"] != "UWB_COMMIT_SUCCEEDED"
                and isinstance(audit.get("prepared_reason"), str)
                and bool(audit["prepared_reason"])
                and queue is None and crossing is None
                and response["not_applicable"] is True
                and response["reason"] == audit["prepared_reason"])
            response_ok = later_crossing_ok = True
        else:
            prepared_admission = admission["prepared"]
            observation = prepared_admission["root_observation"]
            admission_ok = bool(common_admission_ok
                and audit["prepared_accepted"] is True
                and audit["prepared_reason"]
                    == "ACCEPTED_ROOT_FALLBACK_OBSOLETE_NATIVE200_SOURCE_PAIR"
                and audit["outcome"] == "UWB_COMMIT_SUCCEEDED"
                and isinstance(observation, Mapping)
                and isinstance(prepared_admission["root_plan_digest"], str)
                and len(prepared_admission["root_plan_digest"]) == 64
                and admission["velocity_and_bias_byte_inert"] is True
                and valid_state(admission["pre_root"])
                and valid_state(admission["post_root"])
                and np.asarray(admission["applied_position_delta_m"], float).shape == (3,))
            package = queue["package"]
            prepared = response["prepared"]
            drift_result = prepared["drift_result"]
            delta = np.asarray(response["velocity_delta_mps"], float)
            order = result["native_observation_order"]
            eligible = [item for item in order if item["eligible"]]
            response_ok = bool(valid_package(package,
                    prepared=prepared_admission, audit=audit)
                and _validate_drift_decision_projection(
                    drift_result.get("decision"))
                and response["consumed_observation_digest"] == package["digest"]
                and drift_result["consumed_observation_digest"] == package["digest"]
                and response["eligible_by_integer_time"] is True
                and isinstance(order, list) and eligible
                and response["first_eligible_native"] == eligible[0]
                and response["position_bias_covariance_byte_inert"] is True
                and delta.shape == (3,) and np.isfinite(delta).all()
                and np.linalg.norm(delta) <= 0.5 + 1e-12
                and response["record"]["record_completed"] is True
                and response["record"]["rollback_observed"] is False)
            crossing_vector = np.asarray(crossing["post_publication"]["vector"], float)
            crossing_policy = crossing["post_policy"]
            expected_crossing_policy = policy_for(
                crossing["post_publication"], lower, upper)
            pre_crossing_policy = policy_for(
                crossing["pre_publication"], lower, upper)
            later_crossing_ok = bool(
                crossing["record_transaction"]["record_ordinal"] > 36_833
                and valid_state(crossing["pre_publication"])
                and valid_state(crossing["post_publication"])
                and crossing_policy == expected_crossing_policy
                and pre_crossing_policy["inside_anchor_envelope"] is True
                and pre_crossing_policy["speed_mps"] <= MAX_SPEED_MPS
                and (expected_crossing_policy["inside_anchor_envelope"] is False
                     or np.linalg.norm(crossing_vector[3:6]) > MAX_SPEED_MPS))
    except (KeyError, TypeError, ValueError, StopIteration):
        checkpoint_ok = group_ok = admission_ok = response_ok = later_crossing_ok = False
    expected_status = ("FIXED_TARGET_DOWNSTREAM_REJECTION_CAPTURED"
                       if locals().get("rejected", False)
                       else "FIXED_TARGET_CHAIN_CAPTURED")
    expected_stop = ("TARGET_B_ADMISSION_SCIENTIFICALLY_REJECTED"
                     if locals().get("rejected", False)
                     else "TARGET_CHAIN_RESOLVED_WITH_LATER_PHYSICAL_CROSSING")
    expected_resolution = ("FIRST_SUBSEQUENT_B_UWB_REJECTED"
                           if locals().get("rejected", False)
                           else "FIRST_ELIGIBLE_NATIVE_CONSUMED_QUEUED_PACKAGE")
    if (result.get("schema") != FIXED_TARGET_SCHEMA
            or result.get("status") != expected_status
            or result.get("authorization") != "STOP"
            or result.get("diagnostic_only") is not True
            or result.get("product_ready") is not False
            or result.get("scientific_pass") is not False
            or result.get("fusion_decisions_modified") is not False
            or result.get("failure") is not None
            or result.get("stop_reason") != expected_stop
            or result.get("limits") != limits
            or result.get("observer_failures") != []
            or result.get("events", FOLLOWUP_EVENT_CAP + 1) > FOLLOWUP_EVENT_CAP
            or result.get("records", FOLLOWUP_RECORD_CAP_EXCLUSIVE + 1)
                > FOLLOWUP_RECORD_CAP_EXCLUSIVE
            or not isinstance(result.get("target_record"), Mapping)
            or result["target_record"].get("bucket") != target["bucket"]
            or result["target_record"].get("events_after") != target["events_after"]
            or tuple(result["target_record"].get("raw_identity", ()))
                != target["raw_identity"]
            or result["target_record"].get("event_digest") != target["event_digest"]
            or result.get("sealed_crossing_digest") != SEALED_CROSSING_DIGEST
            or result.get("target_chain_resolution") != expected_resolution
            or not isinstance(visibility, list) or not visibility
            or not all((envelope_ok, checkpoint_ok, group_ok, admission_ok,
                        response_ok, later_crossing_ok))):
        raise ValueError("invalid fixed bucket-1958009 causal evidence")


def _write(path: Path, result: Mapping[str, Any]) -> None:
    projected = _jsonable(result)
    _validate_finite_projection(projected)
    encoded = (json.dumps(projected, sort_keys=True, separators=(",", ":"),
                          allow_nan=False) + "\n").encode()
    if len(encoded) > MAX_OUTPUT_BYTES: raise RuntimeError("evidence exceeds 5 MiB")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o444)
    try: os.write(descriptor, encoded); os.fsync(descriptor); os.link(temporary, path)
    finally: os.close(descriptor); temporary.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--followup", action="store_true")
    parser.add_argument("--fixed-target-1958009", action="store_true")
    arguments = parser.parse_args(); result = run(
        followup=arguments.followup, fixed_target=arguments.fixed_target_1958009)
    _write(arguments.output, result)
    expected = ({"FIXED_TARGET_CHAIN_CAPTURED",
                 "FIXED_TARGET_DOWNSTREAM_REJECTION_CAPTURED"}
                if arguments.fixed_target_1958009 else
                {"FIRST_B_UWB_RESPONSE_CAPTURED",
                 "FIRST_B_GROUP_DISPOSITION_CAPTURED"} if arguments.followup
                else {"FIRST_B_PHYSICAL_CROSSING_CAPTURED"})
    return 0 if result["status"] in expected else 1


if __name__ == "__main__": raise SystemExit(main())
