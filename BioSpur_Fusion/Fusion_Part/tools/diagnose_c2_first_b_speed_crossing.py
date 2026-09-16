#!/usr/bin/env python3
"""Stop after the first committed B-root publication exceeding 12 m/s."""
from __future__ import annotations

import argparse
from collections import deque
from copy import deepcopy
import hashlib
import json
import resource
import signal
import time
from pathlib import Path
from dataclasses import replace
from typing import Any, Mapping

import numpy as np

from tools import diagnose_c2_full_session_first_divergence as base
from tools.build_c2_full_session_ten_node_ab import EXPECTED, ROOT, build, _jsonable
from biospur_fusion.root_r3 import PositionObservation
from biospur_fusion.c2_uwb_root_world.continuous_consensus_drift import (
    ContinuousConsensusObservation,
    _package_digest,
)


SCHEMA = "biospur.c2.first_b_speed_crossing.v1"
EVENT_CAP = 343_176
INTERNAL_SECONDS = 1_800.0
OUTER_SECONDS = 1_860
MAX_OUTPUT_BYTES = 5 << 20
MAX_SPEED_MPS = 12.0
LEDGER_RECORD_LIMIT = 16


def _hex64(value: object) -> bool:
    return (isinstance(value, str) and len(value) == 64
            and all(character in "0123456789abcdef" for character in value))


def _valid_state(document: object) -> bool:
    if not isinstance(document, Mapping):
        return False
    try:
        vector = np.asarray(document["vector"], float)
        covariance = np.asarray(document["covariance"], float)
        state_digest, covariance_digest = base._state_document_hashes(
            float(document["time_s"]), vector, covariance)
        np.linalg.cholesky(covariance)
        return bool(vector.shape == (9,) and covariance.shape == (9, 9)
            and np.isfinite(vector).all() and np.isfinite(covariance).all()
            and np.allclose(covariance, covariance.T, rtol=0., atol=1e-10)
            and _hex64(document.get("publication_digest"))
            and document.get("root_state_canonical_sha256") == state_digest
            and document.get("covariance_sha256") == covariance_digest)
    except (KeyError, TypeError, ValueError, np.linalg.LinAlgError):
        return False


def _projected_array(document: object, shape: tuple[int, ...]) -> np.ndarray:
    """Rebuild one exact read-only ndarray from its evidence projection."""

    if not isinstance(document, Mapping) or set(document) != {
            "dtype", "shape", "values", "sha256"}:
        raise ValueError("invalid projected ndarray")
    dtype = np.dtype(document["dtype"])
    if dtype.kind != "f" or dtype.itemsize != 8:
        raise ValueError("projected ndarray is not float64")
    array = np.asarray(document["values"], dtype=dtype)
    if tuple(document["shape"]) != shape or array.shape != shape:
        raise ValueError("projected ndarray shape mismatch")
    if hashlib.sha256(array.tobytes()).hexdigest() != document["sha256"]:
        raise ValueError("projected ndarray byte digest mismatch")
    if array.dtype.kind == "f" and not np.isfinite(array).all():
        raise ValueError("projected ndarray is nonfinite")
    array.setflags(write=False)
    return array


def _authenticated_package(document: object) -> bool:
    """Authenticate a projected consensus package using its production digest."""

    try:
        if not isinstance(document, Mapping):
            return False
        if set(document) != {
            "observation", "availability_time_ns", "trusted_nodes",
            "anchors_used", "packet_digest", "epoch_digest",
            "root_plan_digest", "admission_digest",
            "post_absolute_position_at_measurement_m",
            "applied_absolute_position_delta_m",
            "cumulative_absolute_position_correction_m", "digest",
        }:
            return False
        raw_observation = document["observation"]
        if not isinstance(raw_observation, Mapping) or set(raw_observation) != {
            "measurement_time_s", "availability_time_s", "root_position_m",
            "covariance_m2", "tag_id", "anchors", "quality_state",
            "frame_valid", "physical_point_valid", "source_sequence",
        }:
            return False
        observation = PositionObservation(
            measurement_time_s=float(raw_observation["measurement_time_s"]),
            availability_time_s=float(raw_observation["availability_time_s"]),
            root_position_m=_projected_array(
                raw_observation["root_position_m"], (3,)),
            covariance_m2=_projected_array(
                raw_observation["covariance_m2"], (3, 3)),
            tag_id=str(raw_observation["tag_id"]),
            anchors=tuple(int(item) for item in raw_observation["anchors"]),
            quality_state=str(raw_observation["quality_state"]),
            frame_valid=bool(raw_observation["frame_valid"]),
            physical_point_valid=bool(raw_observation["physical_point_valid"]),
            source_sequence=int(raw_observation["source_sequence"]),
        )
        observation.validate()
        blank = ContinuousConsensusObservation(
            observation=observation,
            availability_time_ns=int(document["availability_time_ns"]),
            trusted_nodes=tuple(str(item) for item in document["trusted_nodes"]),
            anchors_used=tuple(int(item) for item in document["anchors_used"]),
            packet_digest=str(document["packet_digest"]),
            epoch_digest=str(document["epoch_digest"]),
            root_plan_digest=str(document["root_plan_digest"]),
            admission_digest=str(document["admission_digest"]),
            post_absolute_position_at_measurement_m=_projected_array(
                document["post_absolute_position_at_measurement_m"], (3,)),
            applied_absolute_position_delta_m=_projected_array(
                document["applied_absolute_position_delta_m"], (3,)),
            cumulative_absolute_position_correction_m=_projected_array(
                document["cumulative_absolute_position_correction_m"], (3,)),
            digest="",
        )
        rebuilt = replace(blank, digest=_package_digest(blank))
        return bool(
            _hex64(document.get("digest"))
            and all(_hex64(document[field]) for field in (
                "packet_digest", "epoch_digest", "root_plan_digest",
                "admission_digest"))
            and 1 <= len(rebuilt.trusted_nodes) <= 10
            and len(set(rebuilt.trusted_nodes)) == len(rebuilt.trusted_nodes)
            and 1 <= len(rebuilt.anchors_used) <= 8
            and len(set(rebuilt.anchors_used)) == len(rebuilt.anchors_used)
            and rebuilt.digest == document["digest"]
            and observation.availability_time_s
                == int(document["availability_time_ns"]) * 1e-9
            and tuple(observation.anchors) == rebuilt.anchors_used
        )
    except (KeyError, TypeError, ValueError):
        return False


def _velocity_only_effect(prepared: object) -> tuple[bool, list[float]]:
    """Validate the drift sub-step, excluding the preceding IMU propagation."""

    if not isinstance(prepared, Mapping):
        return False, []
    before = prepared.get("imu_candidate")
    after = prepared.get("final_root_candidate")
    drift = prepared.get("drift_result")
    if not (_valid_state(before) and _valid_state(after)
            and isinstance(drift, Mapping)):
        return False, []
    try:
        before_vector = np.asarray(before["vector"], dtype=float)
        after_vector = np.asarray(after["vector"], dtype=float)
        delta = after_vector[3:6] - before_vector[3:6]
        reported = _projected_array(drift["velocity_delta_mps"], (3,))
    except (KeyError, TypeError, ValueError):
        return False, []
    inert = bool(
        float(before["time_s"]) == float(after["time_s"])
        and
        before_vector[:3].tobytes() == after_vector[:3].tobytes()
        and before_vector[6:9].tobytes() == after_vector[6:9].tobytes()
        and np.asarray(before["covariance"], dtype=float).tobytes()
            == np.asarray(after["covariance"], dtype=float).tobytes()
    )
    valid = bool(
        inert and delta.shape == (3,) and reported.shape == (3,)
        and np.allclose(delta, reported, rtol=0.0, atol=1e-15)
        and np.linalg.norm(delta) <= 0.5 + 1e-12
    )
    return valid, delta.tolist()


class FirstBSpeedCrossingObserver(base.FirstBPhysicalCrossingObserver):
    """Reuse publication hooks while replacing only the physical policy."""

    def __init__(self, owners: object, started: float) -> None:
        super().__init__(owners, started)
        self.event_cap = EVENT_CAP
        self._record_effects: list[dict[str, Any]] = []
        self.recent_causal_records: deque[dict[str, Any]] = deque(
            maxlen=LEDGER_RECORD_LIMIT)
        self.completed_causal_chain: dict[str, Any] | None = None
        self._a_publications_by_frame: dict[tuple[object, ...], dict[str, Any]] = {}

    @staticmethod
    def _frame_key(order: int, frame: Mapping[str, Any]) -> tuple[object, ...]:
        raw = frame.get("raw_identity")
        if not isinstance(raw, list) or len(raw) != 4:
            raise ValueError("native frame lacks exact raw identity")
        return (int(order), str(frame.get("digest")),
                int(frame.get("source_global_ns", -1)),
                int(frame.get("sample_index", -1)), *raw)

    def _active_native_frame(self, branch: str) -> tuple[dict[str, Any] | None,
                                                          int | None]:
        operation = self._active_operation or {}
        route = operation.get("route")
        if route == "BATCH":
            index = self._batch_indices[branch]
            if branch == "a":
                self._batch_indices["a"] += 1
            events = operation.get("_event_objects", ())
            if index >= len(events):
                return None, index
            return base._frame_doc(getattr(events[index], "payload_owner", None)), index
        operation_name = (self._active_plan or {}).get("operation")
        if operation_name in {"commit_native200", "commit_gap_native200"}:
            return (self._active_plan or {}).get("prepared", {}).get("frame"), None
        return None, None

    def _policy(self, state: object) -> tuple[bool, dict[str, Any]]:
        velocity = np.asarray(state.vector[3:6], float)
        speed = float(np.linalg.norm(velocity))
        return bool(np.isfinite(speed) and speed <= MAX_SPEED_MPS), {
            "speed_mps": speed, "speed_limit_mps": MAX_SPEED_MPS,
            "criterion": "VELOCITY_NORM_ONLY"}

    def _capture_state(self, name: str, state: object, *,
                       publication_revision: int | None = None,
                       publication_digest: str | None = None) -> None:
        frame, frame_index = self._active_native_frame(name)
        if (name == "a" and frame is not None
                and publication_revision is not None
                and publication_digest is not None):
            order = int((self._active_operation or {}).get("order", -1))
            key = self._frame_key(order, frame)
            if key in self._a_publications_by_frame:
                self._staged_invalid = {"reason": "DUPLICATE_A_FRAME_PUBLICATION",
                                        "frame_key": list(key)}
            self._a_publications_by_frame[key] = {
                "operation_order": order, "sequential_frame_index": frame_index,
                "native_identity": deepcopy(frame),
                "publication": base._state_doc(
                    state, int(publication_revision), str(publication_digest)),
            }
        staged = self._staged_crossing
        super()._capture_state(name, state,
            publication_revision=publication_revision,
            publication_digest=publication_digest)
        if name != "b" or staged is self._staged_crossing \
                or self._staged_crossing is None:
            return
        triggering = self._staged_crossing.get("triggering_native_identity")
        order = int(self._staged_crossing.get("causal_operation", {}).get(
            "order", -1))
        if not isinstance(triggering, Mapping):
            self._staged_invalid = {"reason": "B_SPEED_CROSSING_LACKS_NATIVE_FRAME"}
            return
        paired = self._a_publications_by_frame.get(self._frame_key(order, triggering))
        if paired is None:
            self._staged_invalid = {"reason": "A_SAME_FRAME_PUBLICATION_MISSING",
                                    "operation_order": order,
                                    "triggering_native_identity": triggering}
            return
        a_token = paired["publication"]
        pre = np.asarray(self._staged_crossing["pre_publication"]["vector"], float)
        post = np.asarray(self._staged_crossing["post_publication"]["vector"], float)
        a_speed = float(np.linalg.norm(np.asarray(a_token["vector"], float)[3:6]))
        self._staged_crossing.update({
            "same_frame_a": paired,
            "velocity_delta_mps": (post[3:6] - pre[3:6]).tolist(),
            "a_speed_at_same_frame_mps": a_speed,
            "branch_classification": (
                "A_ALREADY_OVER_LIMIT_AT_SAME_FRAME"
                if a_speed > MAX_SPEED_MPS else "B_ONLY_AT_SAME_FRAME")})

    def _after_detailed_commit(self, branch: str, operation: str, plan: object,
                               result: object, pre_root: dict[str, Any],
                               post_root: dict[str, Any], pre_drift: object,
                               post_drift: object) -> None:
        if branch != "b":
            return
        prepared = base._plan_doc(plan, extended=True)
        velocity_only, drift_velocity_delta = _velocity_only_effect(prepared)
        self._record_effects.append({"operation": operation,
            "prepared": prepared,
            "result": base._project(result), "pre_root": pre_root,
            "post_root": post_root, "pre_drift": pre_drift,
            "post_drift": post_drift,
            "drift_velocity_only": velocity_only,
            "drift_velocity_delta_mps": drift_velocity_delta})

    @staticmethod
    def _b_audits(record: Mapping[str, Any]) -> list[dict[str, Any]]:
        audits = []
        for pair in record.get("record_ab_transactions", ()):
            if not isinstance(pair, Mapping):
                continue
            admission = (pair.get("b") or {}).get("admission")
            if isinstance(admission, Mapping) and admission.get("branch") == "B_UWB":
                audits.append(dict(admission))
        return audits

    def _record_ledger(self, record: Mapping[str, Any]) -> None:
        entry = {"record": deepcopy(dict(record)),
                 "b_admissions": self._b_audits(record),
                 "b_effects": deepcopy(self._record_effects)}
        if entry["b_admissions"] or entry["b_effects"]:
            entry["digest"] = base._canonical_digest(entry)
            self.recent_causal_records.append(entry)
        self._record_effects = []

    def _derive_completed_chain(self) -> dict[str, Any] | None:
        packages: dict[str, dict[str, Any]] = {}
        latest: dict[str, Any] | None = None
        for entry in self.recent_causal_records:
            accepted = [audit for audit in entry["b_admissions"]
                if audit.get("prepared_accepted") is True
                and audit.get("outcome") == "UWB_COMMIT_SUCCEEDED"]
            for effect in entry["b_effects"]:
                prepared = effect.get("prepared", {})
                if effect.get("operation") == "commit_consensus_admission":
                    pending = (effect.get("post_drift") or {}).get("pending", ())
                    if pending and accepted and _hex64(pending[-1].get("digest")):
                        package = pending[-1]
                        packages[package["digest"]] = {
                            "admission_record": entry["record"],
                            "admission_audit": accepted[-1],
                            "admission_effect": next((item for item in entry["b_effects"]
                                if item.get("operation") == "commit_admission"), None),
                            "queue_effect": effect, "package": package}
                embedded = prepared.get("drift_queued_package")
                if accepted and isinstance(embedded, Mapping) \
                        and _hex64(embedded.get("digest")):
                    packages[embedded["digest"]] = {
                        "admission_record": entry["record"],
                        "admission_audit": accepted[-1],
                        "admission_effect": next((item for item in entry["b_effects"]
                            if item.get("operation") == "commit_admission"), None),
                        "queue_effect": effect, "package": embedded}
                drift = prepared.get("drift_result")
                consumed = (drift.get("consumed_observation_digest")
                            if isinstance(drift, Mapping) else None)
                if consumed in packages:
                    package = packages[consumed]["package"]
                    # A pending observation makes native200 batching unsafe in
                    # production.  Therefore consumption must be scalar and
                    # its preceding root time must be strictly pre-availability.
                    frame = prepared.get("frame")
                    prior_ns = round(float(effect["pre_root"]["time_s"]) * 1e9)
                    availability_ns = int(package["availability_time_ns"])
                    if (effect.get("operation") != "commit_native200"
                            or not isinstance(frame, Mapping)
                            or not prior_ns < availability_ns
                                <= int(frame.get("source_global_ns", -1))
                            or not effect.get("drift_velocity_only")):
                        continue
                    candidate = deepcopy(packages[consumed])
                    candidate.update({"consume_record": entry["record"],
                        "consume_effect": effect,
                        "first_eligible_native": {
                            "operation": "commit_native200",
                            "previous_root_time_ns": prior_ns,
                            "availability_time_ns": availability_ns,
                            "frame": frame,
                        },
                        "decision": drift.get("decision"),
                        "consumed_observation_digest": consumed})
                    candidate["digest"] = base._canonical_digest(candidate)
                    latest = candidate
        return latest

    def _finish_crossing_record(self) -> None:
        assert self.crossing is not None
        record = self.crossing["record_transaction"]
        self._record_ledger(record)
        self.completed_causal_chain = self._derive_completed_chain()
        if self.completed_causal_chain is None:
            self.invalid_state = {"reason": "MISSING_ACCEPTED_UWB_DRIFT_CAUSAL_LEDGER",
                                  "record_ordinal": record["record_ordinal"]}
            raise base._Stop("MISSING_ACCEPTED_UWB_DRIFT_CAUSAL_LEDGER")
        pre_a = np.asarray(record["pre_ab"]["a"]["vector"], float)
        post_a = np.asarray(record["post_ab"]["a"]["vector"], float)
        self.crossing["a_crossed_speed_limit_in_same_record"] = bool(
            np.linalg.norm(pre_a[3:6]) <= MAX_SPEED_MPS
            and np.linalg.norm(post_a[3:6]) > MAX_SPEED_MPS)
        self.crossing["causal_ledger_digest"] = self.completed_causal_chain["digest"]

    def _after_successful_record(self, record: dict[str, Any]) -> None:
        self._record_ledger(record)

    def __call__(self, ticket: object) -> None:
        checkpoint = (deepcopy(self.recent_causal_records),
                      deepcopy(self.completed_causal_chain),
                      deepcopy(self._a_publications_by_frame))
        self._record_effects = []
        self._a_publications_by_frame = {}
        try:
            super().__call__(ticket)
        except base._Crossing:
            self._finish_crossing_record()
            raise
        except BaseException:
            self.recent_causal_records = checkpoint[0]
            self.completed_causal_chain = checkpoint[1]
            self._a_publications_by_frame = checkpoint[2]
            self._record_effects = []
            raise


def run() -> dict[str, Any]:
    started = time.monotonic()
    owners = build()
    observer = FirstBSpeedCrossingObserver(owners, started)
    owners.coordinator.consume_record_ticket = observer
    previous = signal.getsignal(signal.SIGALRM)

    def deadline(*_: object) -> None:
        raise base._Stop("INTERNAL_1800S_DEADLINE")

    signal.signal(signal.SIGALRM, deadline)
    signal.setitimer(signal.ITIMER_REAL, INTERNAL_SECONDS)
    stop_reason, failure = "SOURCE_EOF_WITHOUT_SPEED_CROSSING", None
    try:
        owners.coordinator.run(owners.reader)
    except base._Stop as stopped:
        stop_reason = stopped.reason
    except BaseException as error:
        stop_reason = "UNEXPECTED_FAILURE"
        failure = {"type": type(error).__name__, "message": str(error)}
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0.0)
        signal.signal(signal.SIGALRM, previous)
    audit = owners.coordinator.audit()
    complete = bool(observer.crossing is not None
        and observer.completed_causal_chain is not None and failure is None
        and stop_reason == "FIRST_COMMITTED_B_PHYSICAL_CROSSING")
    result = {"schema": SCHEMA,
        "status": "FIRST_B_SPEED_CROSSING_CAPTURED" if complete
                  else "FIRST_B_SPEED_CROSSING_STOP",
        "authorization": "STOP", "diagnostic_only": True,
        "product_ready": False, "scientific_pass": False,
        "fusion_decisions_modified": False, "failure": failure,
        "stop_reason": stop_reason,
        "limits": {"event_cap": EVENT_CAP, "internal_seconds": INTERNAL_SECONDS,
            "outer_seconds": OUTER_SECONDS, "rlimit_as_bytes": 1 << 30,
            "threads": 1, "maximum_output_bytes": MAX_OUTPUT_BYTES,
            "retry": False, "resume_capable": False,
            "ledger_record_limit": LEDGER_RECORD_LIMIT},
        "events": int(audit.events), "records": int(observer.records),
        "crossing": observer.crossing,
        "most_recent_completed_b_uwb_drift_chain": observer.completed_causal_chain,
        "recent_causal_ledger": list(observer.recent_causal_records),
        "observer_failures": list(observer.observer_failures),
        "invalid_state": observer.invalid_state,
        "provenance": {"source_sha256": {
            str(path.relative_to(ROOT)): base._sha(path)
            for path in sorted((ROOT / "src/biospur_fusion").rglob("*.py"))},
            "factory_inputs": {str(path): digest for path, digest in EXPECTED.items()},
            "base_observer_sha256": base._sha(Path(base.__file__).resolve()),
            "self_sha256": base._sha(Path(__file__).resolve())},
        "wall_s": time.monotonic() - started,
        "peak_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss}
    return result


def validate_result(result: Mapping[str, Any]) -> None:
    base._validate_finite_projection(result)
    limits = {"event_cap": EVENT_CAP, "internal_seconds": INTERNAL_SECONDS,
        "outer_seconds": OUTER_SECONDS, "rlimit_as_bytes": 1 << 30,
        "threads": 1, "maximum_output_bytes": MAX_OUTPUT_BYTES,
        "retry": False, "resume_capable": False,
        "ledger_record_limit": LEDGER_RECORD_LIMIT}
    crossing = result.get("crossing")
    chain = result.get("most_recent_completed_b_uwb_drift_chain")
    try:
        pre = crossing["pre_publication"]
        post = crossing["post_publication"]
        same_frame_a = crossing["same_frame_a"]
        a_state = same_frame_a["publication"]
        record = crossing["record_transaction"]
        triggering = crossing["triggering_native_identity"]
        operation = crossing["causal_operation"]
        pre_velocity = np.asarray(pre["vector"], float)[3:6]
        post_velocity = np.asarray(post["vector"], float)[3:6]
        delta = np.asarray(crossing["velocity_delta_mps"], float)
        pre_speed = float(np.linalg.norm(pre_velocity))
        post_speed = float(np.linalg.norm(post_velocity))
        audit = chain["admission_audit"]
        package = chain["package"]
        consume = chain["consume_effect"]
        drift = consume["prepared"]["drift_result"]
        eligible = chain["first_eligible_native"]
        eligible_frame = eligible["frame"]
        chain_ok = bool(audit["branch"] == "B_UWB"
            and audit["prepared_accepted"] is True
            and audit["outcome"] == "UWB_COMMIT_SUCCEEDED"
            and chain["admission_effect"]["operation"] == "commit_admission"
            and chain["queue_effect"]["operation"] in {
                "commit_consensus_admission", "commit_native200"}
            and _authenticated_package(package)
            and package["digest"] == chain["consumed_observation_digest"]
            and drift["consumed_observation_digest"] == package["digest"]
            and consume["operation"] == "commit_native200"
            and consume["drift_velocity_only"] is True
            and np.linalg.norm(np.asarray(
                consume["drift_velocity_delta_mps"], float)) <= 0.5 + 1e-12
            and eligible["operation"] == "commit_native200"
            and int(eligible["previous_root_time_ns"])
                < int(package["availability_time_ns"])
                == int(eligible["availability_time_ns"])
                <= int(eligible_frame["source_global_ns"])
            and eligible_frame == consume["prepared"]["frame"]
            and base._validate_drift_decision_projection(drift["decision"])
            and chain["digest"] == base._canonical_digest({key: value
                for key, value in chain.items() if key != "digest"}))
        crossing_ok = bool(_valid_state(pre) and _valid_state(post)
            and _valid_state(a_state) and pre_speed <= MAX_SPEED_MPS
            and post_speed > MAX_SPEED_MPS
            and crossing["post_policy"] == {"speed_mps": post_speed,
                "speed_limit_mps": MAX_SPEED_MPS,
                "criterion": "VELOCITY_NORM_ONLY"}
            and np.array_equal(delta, post_velocity - pre_velocity)
            and crossing["causal_ledger_digest"] == chain["digest"]
            and record["record_completed"] is True
            and record["rollback_observed"] is False
            and _valid_state(record["pre_ab"]["a"])
            and _valid_state(record["pre_ab"]["b"])
            and _valid_state(record["post_ab"]["a"])
            and _valid_state(record["post_ab"]["b"])
            and isinstance(operation, Mapping)
            and operation.get("route") in {
                "SCALAR", "BATCH", "GAP_ENDPOINT"}
            and operation.get("events")
            and int(same_frame_a["operation_order"]) == int(operation["order"])
            and same_frame_a["native_identity"] == triggering
            and triggering["raw_identity"] == record["raw_identity"]
            and any(int(event["common_global_ns"])
                == int(triggering["source_global_ns"])
                for event in operation["events"])
            and round(float(a_state["time_s"]) * 1e9)
                == int(triggering["source_global_ns"])
            and (operation.get("route") != "BATCH"
                or int(same_frame_a["sequential_frame_index"])
                    == int(operation["sequential_frame_index"]))
            and len(record["raw_identity"]) == 4 and record["event_digests"])
    except (KeyError, TypeError, ValueError):
        chain_ok = crossing_ok = False
    provenance = result.get("provenance", {})
    ledger = result.get("recent_causal_ledger")
    if (result.get("schema") != SCHEMA
            or result.get("status") != "FIRST_B_SPEED_CROSSING_CAPTURED"
            or result.get("authorization") != "STOP"
            or result.get("diagnostic_only") is not True
            or result.get("product_ready") is not False
            or result.get("scientific_pass") is not False
            or result.get("fusion_decisions_modified") is not False
            or result.get("failure") is not None
            or result.get("stop_reason") != "FIRST_COMMITTED_B_PHYSICAL_CROSSING"
            or result.get("limits") != limits
            or result.get("events", EVENT_CAP + 1) > EVENT_CAP
            or result.get("observer_failures") != []
            or not isinstance(ledger, list) or not ledger
            or len(ledger) > LEDGER_RECORD_LIMIT
            or not all(entry.get("digest") == base._canonical_digest({key: value
                for key, value in entry.items() if key != "digest"}) for entry in ledger)
            or not crossing_ok or not chain_ok
            or provenance.get("self_sha256") != base._sha(Path(__file__).resolve())
            or provenance.get("base_observer_sha256")
                != base._sha(Path(base.__file__).resolve())
            or not isinstance(provenance.get("source_sha256"), Mapping)
            or not provenance.get("source_sha256")):
        raise ValueError("invalid first B speed-crossing evidence")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    result = run()
    base._write(arguments.output, result)
    return 0 if result["status"] == "FIRST_B_SPEED_CROSSING_CAPTURED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
