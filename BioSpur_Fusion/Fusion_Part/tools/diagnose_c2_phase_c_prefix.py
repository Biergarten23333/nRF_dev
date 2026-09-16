#!/usr/bin/env python3
"""Observation-only Phase-C mechanism gate at one exact raw-record boundary."""
from __future__ import annotations

import argparse
import bisect
from collections import Counter
from dataclasses import asdict
import hashlib
import json
import math
import os
from pathlib import Path
import resource
import signal
import time
from typing import Any, Mapping

import numpy as np

from biospur_fusion.c2_uwb_root_world.full_session_body_pose import SESSION_ID
from biospur_fusion.root_r3.models import RootState

if __package__:
    from tools.build_c2_full_session_ten_node_ab import (
        CONSENSUS_DRIFT_CONFIG_SOURCE,
        EXPECTED,
        ROOT,
        _jsonable,
        build,
    )
else:
    from build_c2_full_session_ten_node_ab import (
        CONSENSUS_DRIFT_CONFIG_SOURCE,
        EXPECTED,
        ROOT,
        _jsonable,
        build,
    )


SCHEMA = "biospur.c2.phase_c.continuous_consensus_prefix.v1"
EVENT_CAP = 268_749
RECORD_COUNT = 36_549
INTERNAL_SECONDS = 1_500.0
OUTER_SECONDS = 1_560
MAX_OUTPUT_BYTES = 5 << 20
RLIMIT_AS_BYTES = 1 << 30
LOWER_M = np.array((-0.75, -0.8402885344763945, -0.75))
UPPER_M = np.array((5.051492662611885, 3.8481456094710684, 2.5979504368063617))
MAX_SPEED_MPS = 12.0
REPORT_ONLY_STEP_M = 0.10
CHECKPOINT_EVENT_THRESHOLDS = (50_000, 100_000, 150_000, 200_000, 250_000, EVENT_CAP)
HISTORICAL_BOUNDARY = (
    36_547,
    (1212258, 221493385, 221493563,
     "a66833d6760717411d7ab881619480f69954fd0791165fb8460b085ef021a127"),
    268_739,
)
FOLLOWING_BOUNDARY = (
    36_548,
    (1212259, 221493563, 221493741,
     "336d460cfba53359e594e5f59c1428e43e325494ffa3d4a693b5a8dd02dc98b1"),
    268_749,
)


class _Stop(RuntimeError):
    pass


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while block := source.read(1 << 20):
            digest.update(block)
    return digest.hexdigest()


def _source_paths() -> tuple[Path, ...]:
    return tuple(sorted((ROOT / "src/biospur_fusion").rglob("*.py"))) + (
        ROOT / "tools/build_c2_full_session_ten_node_ab.py",
        ROOT / "tools/diagnose_c2_phase_c_prefix.py",
        CONSENSUS_DRIFT_CONFIG_SOURCE,
    )


def _source_hashes() -> dict[str, str]:
    return {str(path.relative_to(ROOT)): _sha(path) for path in _source_paths()}


def _state_summary(state: RootState) -> dict[str, Any]:
    covariance = np.asarray(state.covariance, dtype=float)
    return {
        "time_s": float(state.time_s),
        "vector": np.asarray(state.vector, dtype=float).tolist(),
        "covariance_diagonal": np.diag(covariance).tolist(),
        "covariance_minimum_eigenvalue": float(np.linalg.eigvalsh(covariance)[0]),
        "publication_shape": [9, 9],
    }


def _state_validation(state: RootState) -> dict[str, Any]:
    """Apply the exact numerical contract owned by ``RootState``."""

    vector = np.asarray(state.vector, dtype=float)
    covariance = np.asarray(state.covariance, dtype=float)
    finite = bool(
        math.isfinite(float(state.time_s))
        and np.isfinite(vector).all()
        and np.isfinite(covariance).all()
    )
    maximum_skew = (
        float(np.max(np.abs(covariance - covariance.T)))
        if finite else None
    )
    minimum_eigenvalue = (
        float(np.linalg.eigvalsh(0.5 * (covariance + covariance.T))[0])
        if finite else None
    )
    if not finite:
        reason = "NONFINITE_ROOT_STATE"
    elif not np.allclose(covariance, covariance.T, atol=1e-10):
        reason = "ROOT_COVARIANCE_ASYMMETRIC"
    else:
        try:
            np.linalg.cholesky(covariance)
        except np.linalg.LinAlgError:
            reason = "ROOT_COVARIANCE_NOT_POSITIVE_DEFINITE"
        else:
            reason = "VALID"
    return {
        "valid": reason == "VALID",
        "reason": reason,
        "maximum_covariance_skew": maximum_skew,
        "minimum_symmetric_covariance_eigenvalue": minimum_eigenvalue,
    }


def _state_valid(state: RootState) -> bool:
    return bool(_state_validation(state)["valid"])


class PhaseCPrefixObserver:
    """Observe canonical coordinator commits without changing their decisions."""

    def __init__(self, owners: object, started: float) -> None:
        self.owners = owners
        self.started = started
        self.consume = owners.coordinator.consume_record_ticket
        self.records = 0
        self.boundaries: dict[int, dict[str, Any]] = {}
        self.actions: set[str] = set()
        self.regions: set[str] = set()
        inventory = getattr(getattr(owners, "reader", None), "inventory", None)
        self.source_regions = tuple(getattr(inventory, "regions", ()))
        self.source_region_starts = tuple(
            int(region.start_offset) for region in self.source_regions
        )
        self.routing_calls = 0
        self.core_consume_wall_s = 0.0
        self.hook_observer_wall_s = 0.0
        self.observer_self_wall_s = 0.0
        self.bootstrap_hook_installs = 0
        self.checkpoints: list[dict[str, Any]] = []
        self.next_checkpoint_index = 0
        self.branch = {
            name: {
                "minimum_position_m": np.full(3, np.inf),
                "maximum_position_m": np.full(3, -np.inf),
                "maximum_speed_mps": 0.0,
                "maximum_record_displacement_m": 0.0,
                "maximum_radius_from_initial_m": 0.0,
                "initial_position_m": None,
                "previous_record_position_m": None,
            }
            for name in ("a", "b")
        }
        self.all_states_valid = True
        self.first_state_failure: dict[str, Any] | None = None
        self.b_inside_volume = True
        self.b_speed_bounded = True
        self.step_report_only_crossings = {"a": 0, "b": 0}
        self.same_root_time = True
        self.same_history_stream = True
        self.a_uwb = Counter()
        self.b_uwb = Counter()
        self.trusted_partition_histogram = Counter()
        self.last_ab_total = 0
        self.hooks_installed = False
        self.a_drift_absent = True
        self.b_drift_present = False
        self.stream_owner_digest: str | None = None
        self.initial_drift_owner: object | None = None
        self.initial_drift_owner_token: int | None = None
        self.initial_drift_revision: int | None = None
        self.last_drift_revision: int | None = None
        self.drift_owner_unchanged = True
        self.drift_revision_monotonic = True
        self.envelope_matches_owner = False
        self.drift = Counter()
        self.drift_maximum_delta_norm_mps = 0.0
        self.drift_deltas_finite = True
        self.drift_velocity_only = True
        self.drift_decision_digests: list[str] = []
        self.consumed_observations: set[str] = set()
        self.drift_duplicate_or_replay = 0
        self.uwb_position_only = True
        self.bias_initial: np.ndarray | None = None
        self.bias_unchanged_by_drift = True
        self.last_consumed_native200_time_s: float | None = None
        self.pending_admission_identities: list[dict[str, Any]] = []
        self.next_native_stream_identities: list[dict[str, Any]] = []
        self.observer_failures: list[str] = []
        coordinator = owners.coordinator
        initializer = getattr(coordinator, "_initializer", None)
        original_install = getattr(initializer, "commit_first_group", None)
        if callable(original_install):
            def observed_install(*args: object, **kwargs: object) -> object:
                installation = original_install(*args, **kwargs)
                self._timed_hook(
                    self._install_hooks_on,
                    installation.a._composition,
                    installation.b._composition,
                )
                self.bootstrap_hook_installs += 1
                return installation
            initializer.commit_first_group = observed_install

    def _timed_hook(self, callback: object, *args: object) -> object:
        started = time.monotonic()
        try:
            return callback(*args)
        finally:
            self.hook_observer_wall_s += time.monotonic() - started

    @property
    def consume_minus_hook_wall_s(self) -> float:
        return max(0.0, self.core_consume_wall_s - self.hook_observer_wall_s)

    def _source_region(self, ticket: object) -> object:
        """Map one authenticated record identity to its immutable source slice."""

        if not self.source_regions:
            raise RuntimeError("Phase-C observer lacks source-region inventory")
        raw = tuple(ticket.raw_identity)
        if len(raw) != 4:
            raise ValueError("Phase-C record lacks exact raw identity")
        start, stop = int(raw[1]), int(raw[2])
        index = bisect.bisect_right(self.source_region_starts, start) - 1
        if index < 0 or index >= len(self.source_regions):
            raise ValueError("Phase-C raw record is outside source inventory")
        region = self.source_regions[index]
        if not (region.start_offset <= start < stop <= region.stop_offset):
            raise ValueError("Phase-C raw record crosses source-region ownership")
        return region

    def _capture_checkpoint(self, events: int) -> None:
        while (
            self.next_checkpoint_index < len(CHECKPOINT_EVENT_THRESHOLDS)
            and events >= CHECKPOINT_EVENT_THRESHOLDS[self.next_checkpoint_index]
        ):
            threshold = CHECKPOINT_EVENT_THRESHOLDS[self.next_checkpoint_index]
            coordinator = self.owners.coordinator
            self.checkpoints.append({
                "threshold_events": threshold,
                "observed_events": events,
                "records": self.records,
                "wall_s": time.monotonic() - self.started,
                "core_consume_wall_s": self.core_consume_wall_s,
                "hook_observer_wall_s": self.hook_observer_wall_s,
                "consume_minus_hook_wall_s": self.consume_minus_hook_wall_s,
                "observer_self_wall_s": self.observer_self_wall_s,
                "batched_records": int(coordinator._batched_records),
                "batched_frames": int(coordinator._batched_frames),
                "scalar_frames": int(coordinator._scalar_frames),
                "unrouted_frames": int(coordinator._unrouted_frames),
            })
            self.next_checkpoint_index += 1

    def _record_observer_failure(self, stage: str, error: BaseException) -> None:
        self.observer_failures.append(f"{stage}:{type(error).__name__}:{error}")

    def _capture_state(
        self, name: str, state: RootState, *,
        publication_revision: int | None = None,
        publication_digest: str | None = None,
    ) -> None:
        validation = _state_validation(state)
        self.all_states_valid &= bool(validation["valid"])
        if not validation["valid"] and self.first_state_failure is None:
            self.first_state_failure = {
                "branch": name,
                "time_s": float(state.time_s),
                "publication_revision": publication_revision,
                "publication_digest": publication_digest,
                "reason": validation["reason"],
                "maximum_covariance_skew": validation[
                    "maximum_covariance_skew"
                ],
                "minimum_symmetric_covariance_eigenvalue": validation[
                    "minimum_symmetric_covariance_eigenvalue"
                ],
            }
        position = np.asarray(state.vector[:3], dtype=float)
        speed = float(np.linalg.norm(state.vector[3:6]))
        metrics = self.branch[name]
        metrics["minimum_position_m"] = np.minimum(
            metrics["minimum_position_m"], position,
        )
        metrics["maximum_position_m"] = np.maximum(
            metrics["maximum_position_m"], position,
        )
        metrics["maximum_speed_mps"] = max(metrics["maximum_speed_mps"], speed)
        if metrics["initial_position_m"] is None:
            metrics["initial_position_m"] = position.copy()
        metrics["maximum_radius_from_initial_m"] = max(
            metrics["maximum_radius_from_initial_m"],
            float(np.linalg.norm(position - metrics["initial_position_m"])),
        )
        if name == "b":
            self.b_inside_volume &= bool(
                np.all(position >= LOWER_M) and np.all(position <= UPPER_M)
            )
            self.b_speed_bounded &= speed <= MAX_SPEED_MPS

    def _capture_composition_state(
        self, name: str, composition: object,
    ) -> None:
        publication = composition.engine.root.publication_token()
        self._capture_state(
            name, publication.state,
            publication_revision=int(publication.revision),
            publication_digest=str(publication.digest),
        )

    def _check_drift_owner(self) -> None:
        coordinator = self.owners.coordinator
        if coordinator._b is None or self.initial_drift_owner is None:
            return
        current = coordinator._b._composition.consensus_drift
        self.drift_owner_unchanged &= current is self.initial_drift_owner
        if current is not self.initial_drift_owner:
            return
        revision = int(current.revision)
        if self.last_drift_revision is not None:
            self.drift_revision_monotonic &= revision >= self.last_drift_revision
        self.last_drift_revision = revision

    def _observe_admission_effect(self, prepared: object) -> None:
        transaction = prepared.causal_transaction
        if transaction is None or transaction.root_plan is None:
            return
        plan = transaction.root_plan
        self.uwb_position_only &= (
            plan.imu_prediction.vector[3:9].tobytes()
            == plan.measurement_candidate.vector[3:9].tobytes()
            and np.asarray(
                plan.decision.availability_applied_velocity_delta_mps,
            ).tobytes() == np.zeros(3).tobytes()
        )

    def _observe_native_effect(self, prepared: object) -> None:
        result = prepared.drift_plan.result
        if result.consumed_observation_digest is None:
            return
        history_plan = prepared.history_plan
        if history_plan.future_imu is not None:
            imu_candidate = history_plan.future_imu.imu_plan.candidate_state
        else:
            imu_candidate = history_plan.current_imu.candidate_state
        root_plan = history_plan.root_plan
        velocity_plan = getattr(root_plan, "imu_velocity_plan", None)
        if velocity_plan is not None:
            candidate = velocity_plan._candidate_bundle.snapshots[-1].state
        elif hasattr(root_plan, "_candidate_bundle"):
            candidate = root_plan._candidate_bundle.snapshots[-1].state
        else:
            candidate = imu_candidate
        delta = np.asarray(result.velocity_delta_mps)
        self.drift_velocity_only &= (
            imu_candidate.vector[:3].tobytes() == candidate.vector[:3].tobytes()
            and imu_candidate.vector[6:9].tobytes()
            == candidate.vector[6:9].tobytes()
            and imu_candidate.covariance.tobytes() == candidate.covariance.tobytes()
            and np.array_equal(
                candidate.vector[3:6], imu_candidate.vector[3:6] + delta,
            )
        )
        if self.bias_initial is None:
            self.bias_initial = imu_candidate.vector[6:9].copy()
        self.bias_unchanged_by_drift &= (
            imu_candidate.vector[6:9].tobytes()
            == candidate.vector[6:9].tobytes()
        )

    def _observe_drift_commit(
        self, drift_owner: object, plan: object, *,
        before_pending: int, after_pending: int,
    ) -> None:
        result = plan.result
        if result.kind == "ADMISSION":
            if result.accepted:
                self.drift["queued"] += 1
                self._capture_pending_admission_identity(
                    plan._candidate_state.pending[-1],
                )
            return
        if result.kind == "ADMISSION_NATIVE200":
            self.drift["queued"] += 1
            self._capture_pending_admission_identity(
                plan._dependency_plan._candidate_state.pending[-1],
            )
        consumed = result.consumed_observation_digest
        if consumed is None:
            return
        self.drift["consumed"] += 1
        self.drift["accepted" if result.accepted else "rejected"] += 1
        if consumed in self.consumed_observations:
            self.drift_duplicate_or_replay += 1
        self.consumed_observations.add(consumed)
        pending_before_evaluation = (
            plan._dependency_plan._candidate_state.pending
            if result.kind == "ADMISSION_NATIVE200"
            else plan._base_state.pending
        )
        if (
            not pending_before_evaluation
            or pending_before_evaluation[0].digest != consumed
            or after_pending != len(pending_before_evaluation) - 1
        ):
            self.drift_duplicate_or_replay += 1
        availability_s = pending_before_evaluation[0].availability_time_ns * 1e-9
        native_time = drift_owner.snapshot()._state.last_consumed_native200_time_s
        if (
            native_time is None
            or native_time < availability_s - 1e-12
            or (
                self.last_consumed_native200_time_s is not None
                and native_time <= self.last_consumed_native200_time_s + 1e-12
            )
        ):
            self.drift_duplicate_or_replay += 1
        self.last_consumed_native200_time_s = native_time
        velocity_delta = np.asarray(result.velocity_delta_mps, dtype=float)
        finite_delta = bool(np.isfinite(velocity_delta).all())
        self.drift_deltas_finite &= finite_delta
        norm = float(np.linalg.norm(velocity_delta)) if finite_delta else 0.0
        if finite_delta:
            self.drift_maximum_delta_norm_mps = max(
                self.drift_maximum_delta_norm_mps, norm,
            )
        if math.isclose(
            norm, drift_owner.config.maximum_velocity_step_mps,
            rel_tol=0.0, abs_tol=1e-12,
        ):
            self.drift["saturated"] += 1
        if before_pending < 1 and result.kind != "ADMISSION_NATIVE200":
            self.drift_duplicate_or_replay += 1
        digest = hashlib.sha256(json.dumps({
            "kind": result.kind,
            "accepted": result.accepted,
            "reason": result.reason,
            "observation": consumed,
            "velocity_delta_mps": np.asarray(result.velocity_delta_mps).tolist(),
            "plan_digest": plan.digest,
            "native_time_s": native_time,
            "availability_time_s": availability_s,
        }, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        self.drift_decision_digests.append(digest)

    def _capture_pending_admission_identity(self, package: object) -> None:
        observation = package.observation
        self.pending_admission_identities.append({
            "package_digest": package.digest,
            "admission_digest": package.admission_digest,
            "root_plan_digest": package.root_plan_digest,
            "packet_digest": package.packet_digest,
            "epoch_digest": package.epoch_digest,
            "measurement_time_s": float(observation.measurement_time_s),
            "availability_time_ns": int(package.availability_time_ns),
            "source_sequence": int(observation.source_sequence),
            "tag_id": str(observation.tag_id),
            "anchors_used": list(package.anchors_used),
            "trusted_nodes": list(package.trusted_nodes),
        })

    def _capture_next_native_stream_identity(self, prepared: object) -> None:
        consumed = prepared.drift_plan.result.consumed_observation_digest
        if consumed is None:
            return
        native = prepared.history_plan.native
        frame = native.frame
        raw = frame.raw_provenance
        self.next_native_stream_identities.append({
            "consumed_package_digest": consumed,
            "frame_digest": frame.digest,
            "node": frame.node,
            "boot_epoch": int(frame.boot_epoch),
            "source_timer_us": int(frame.source_timer_us),
            "source_global_ns": int(frame.source_global_ns),
            "availability_time_ns": round(
                float(frame.imu_sample.availability_time_s) * 1e9
            ),
            "sample_source_sequence": int(frame.imu_sample.source_sequence),
            "publication_revision": int(frame.publication_revision),
            "pose_publication_digest": frame.pose_publication_digest,
            "raw_identity": [
                int(raw.record_index), int(raw.start_offset),
                int(raw.end_offset), raw.encoded_sha256,
                int(raw.sample_index),
            ],
        })

    def _install_hooks_on(self, a_composition: object, b_composition: object) -> None:
        if self.hooks_installed:
            return
        self.a_drift_absent = a_composition.consensus_drift is None
        drift_owner = b_composition.consensus_drift
        self.b_drift_present = drift_owner is not None
        if drift_owner is None:
            self.hooks_installed = True
            return
        self.stream_owner_digest = drift_owner.stream_owner_digest
        self.initial_drift_owner = drift_owner
        self.initial_drift_owner_token = id(drift_owner)
        self.initial_drift_revision = int(drift_owner.revision)
        self.last_drift_revision = int(drift_owner.revision)
        anchors = np.asarray(b_composition.engine.static.anchors_m, dtype=float)
        self.envelope_matches_owner = bool(
            np.array_equal(LOWER_M, np.min(anchors, axis=0) - 0.75)
            and np.array_equal(UPPER_M, np.max(anchors, axis=0) + 0.75)
        )
        original_drift_commit = drift_owner.commit
        original_native_commit = b_composition.commit_native200
        original_a_admission_commit = a_composition.commit_admission
        original_b_admission_commit = b_composition.commit_admission
        original_a_gap_commit = a_composition.commit_gap
        original_b_gap_commit = b_composition.commit_gap
        original_a_add_imu = a_composition.engine.add_imu
        original_b_add_imu = b_composition.engine.add_imu

        def observed_drift_commit(plan: object) -> None:
            before_pending = int(drift_owner.pending_count)
            original_drift_commit(plan)
            try:
                self._timed_hook(
                    lambda: self._observe_drift_commit(
                        drift_owner, plan, before_pending=before_pending,
                        after_pending=int(drift_owner.pending_count),
                    )
                )
                self._timed_hook(self._check_drift_owner)
            except BaseException as error:
                self._record_observer_failure("DRIFT_COMMIT", error)

        def observed_native_commit(prepared: object) -> None:
            try:
                self._timed_hook(self._observe_native_effect, prepared)
            except BaseException as error:
                self._record_observer_failure("NATIVE_EFFECT", error)
            original_native_commit(prepared)
            try:
                self._timed_hook(
                    self._capture_next_native_stream_identity, prepared,
                )
                self._timed_hook(
                    self._capture_composition_state, "b", b_composition,
                )
                self._timed_hook(self._check_drift_owner)
            except BaseException as error:
                self._record_observer_failure("NATIVE_PUBLICATION", error)

        def observed_a_admission_commit(prepared: object) -> None:
            original_a_admission_commit(prepared)
            self._timed_hook(
                self._capture_composition_state, "a", a_composition,
            )

        def observed_b_admission_commit(prepared: object) -> None:
            try:
                self._timed_hook(self._observe_admission_effect, prepared)
            except BaseException as error:
                self._record_observer_failure("ADMISSION_EFFECT", error)
            original_b_admission_commit(prepared)
            self._timed_hook(
                self._capture_composition_state, "b", b_composition,
            )

        def observed_add_imu(name: str, original: object, composition: object):
            def commit(sample: object) -> bool:
                accepted = original(sample)
                if accepted:
                    self._timed_hook(
                        self._capture_composition_state, name, composition,
                    )
                return accepted
            return commit

        def observed_gap(name: str, original: object, composition: object):
            def commit(prepared: object) -> None:
                original(prepared)
                self._timed_hook(
                    self._capture_composition_state, name, composition,
                )
            return commit

        drift_owner.commit = observed_drift_commit
        b_composition.commit_native200 = observed_native_commit
        a_composition.commit_admission = observed_a_admission_commit
        b_composition.commit_admission = observed_b_admission_commit
        a_composition.engine.add_imu = observed_add_imu(
            "a", original_a_add_imu, a_composition,
        )
        b_composition.engine.add_imu = observed_add_imu(
            "b", original_b_add_imu, b_composition,
        )
        a_composition.commit_gap = observed_gap(
            "a", original_a_gap_commit, a_composition,
        )
        b_composition.commit_gap = observed_gap(
            "b", original_b_gap_commit, b_composition,
        )
        self._capture_composition_state("a", a_composition)
        self._capture_composition_state("b", b_composition)
        self._check_drift_owner()
        self.hooks_installed = True

    def _install_hooks(self) -> None:
        coordinator = self.owners.coordinator
        if self.hooks_installed or coordinator._a is None or coordinator._b is None:
            return
        self._install_hooks_on(
            coordinator._a._composition, coordinator._b._composition,
        )

    def _capture_transactions(self) -> None:
        coordinator = self.owners.coordinator
        total = int(coordinator._ab_transaction_total)
        journal = tuple(coordinator._ab_transaction_journal)
        start = total - len(journal)
        if self.last_ab_total < start or self.last_ab_total > total:
            raise RuntimeError("Phase-C transaction observer lost bounded journal")
        for pair in journal[self.last_ab_total - start:]:
            for name, disposition, counter in (
                ("a", pair.a, self.a_uwb), ("b", pair.b, self.b_uwb),
            ):
                admission = disposition.admission
                if admission is None:
                    counter["no_admission"] += 1
                    continue
                counter["accepted" if admission.prepared_accepted else "rejected"] += 1
                if name == "b" and admission.prepared_accepted:
                    count = len(admission.trusted_partition)
                    self.trusted_partition_histogram[count] += 1
        self.last_ab_total = total

    def _capture_roots(self) -> None:
        coordinator = self.owners.coordinator
        if coordinator._a is None or coordinator._b is None:
            return
        states = {
            "a": coordinator._a._composition.engine.root.current_state,
            "b": coordinator._b._composition.engine.root.current_state,
        }
        self.same_root_time &= states["a"].time_s == states["b"].time_s
        a_frames = coordinator._a._composition.history.frames
        b_frames = coordinator._b._composition.history.frames
        self.same_history_stream &= (
            tuple(frame.digest for frame in a_frames)
            == tuple(frame.digest for frame in b_frames)
        )
        for name, state in states.items():
            position = np.asarray(state.vector[:3], dtype=float)
            metrics = self.branch[name]
            if metrics["previous_record_position_m"] is not None:
                step = float(np.linalg.norm(
                    position - metrics["previous_record_position_m"],
                ))
                metrics["maximum_record_displacement_m"] = max(
                    metrics["maximum_record_displacement_m"], step,
                )
                if step > REPORT_ONLY_STEP_M:
                    self.step_report_only_crossings[name] += 1
            metrics["previous_record_position_m"] = position.copy()
        self._check_drift_owner()

    def __call__(self, ticket: object) -> None:
        call_started = time.monotonic()
        if time.monotonic() - self.started >= INTERNAL_SECONDS:
            raise _Stop("INTERNAL_1500S_DEADLINE")
        coordinator = self.owners.coordinator
        before = int(coordinator._events)
        count = len(tuple(ticket.event_digests))
        if before + count > EVENT_CAP:
            raise _Stop("EVENT_CAP_RECORD_OVERSHOOT")
        source_region = self._source_region(ticket)
        core_started = time.monotonic()
        self.routing_calls += 1
        try:
            self.consume(ticket)
        finally:
            core_elapsed = time.monotonic() - core_started
            self.core_consume_wall_s += core_elapsed
        self.records += 1
        self.regions.add(str(source_region.region_id))
        if source_region.kind == "ACTION":
            self.actions.add(str(source_region.action_id))
        self._install_hooks()
        self._capture_transactions()
        self._capture_roots()
        events = int(coordinator._events)
        self.observer_self_wall_s += time.monotonic() - call_started - core_elapsed
        observer_tail_started = time.monotonic()
        self._capture_checkpoint(events)
        if events in (268_739, EVENT_CAP):
            self.boundaries[events] = {
                "record_ordinal": int(ticket.record_ordinal),
                "raw_identity": list(ticket.raw_identity),
                "event_count": count,
                "end_event_count": events,
                "event_digests_sha256": hashlib.sha256(
                    b"".join(bytes.fromhex(value) for value in ticket.event_digests)
                ).hexdigest(),
            }
        self.observer_self_wall_s += time.monotonic() - observer_tail_started
        if events == EVENT_CAP:
            raise _Stop("EXACT_EVENT_CAP")


def _branch_result(observer: PhaseCPrefixObserver, name: str, state: RootState) -> dict[str, Any]:
    metrics = observer.branch[name]
    return {
        "final_root": _state_summary(state),
        "minimum_position_m": metrics["minimum_position_m"].tolist(),
        "maximum_position_m": metrics["maximum_position_m"].tolist(),
        "maximum_speed_mps": metrics["maximum_speed_mps"],
        "maximum_record_displacement_m": metrics["maximum_record_displacement_m"],
        "record_displacement_over_0_10m_report_only": observer.step_report_only_crossings[name],
        "maximum_radius_from_initial_m": metrics["maximum_radius_from_initial_m"],
        "uwb": dict(observer.a_uwb if name == "a" else observer.b_uwb),
    }


def _early_failure_result(
    *, started: float, failure: BaseException, stop_reason: str,
    source_before: Mapping[str, str], source_after: Mapping[str, str],
    expected_self_sha256: str,
    events: int = 0, records: int = 0,
) -> dict[str, Any]:
    return {
        "schema": SCHEMA,
        "status": "PHASE_C_PREFIX_MECHANISM_FAIL",
        "authorization": "STOP",
        "diagnostic_only": True,
        "product_ready": False,
        "scientific_pass": False,
        "failure": {"type": type(failure).__name__, "message": str(failure)},
        "stop_reason": stop_reason,
        "limits": {
            "event_cap": EVENT_CAP, "internal_seconds": INTERNAL_SECONDS,
            "outer_seconds": OUTER_SECONDS, "rlimit_as_bytes": RLIMIT_AS_BYTES,
            "threads": 1, "maximum_output_bytes": MAX_OUTPUT_BYTES,
            "retry": False,
        },
        "provenance": {
            "session_id": SESSION_ID,
            "expected_self_sha256": expected_self_sha256,
            "observed_self_sha256": _sha(Path(__file__).resolve()),
            "config_source_sha256": EXPECTED[CONSENSUS_DRIFT_CONFIG_SOURCE],
            "source_sha256_before": dict(source_before),
            "source_sha256_after": dict(source_after),
        },
        "boundary": {"events": events, "records": records, "observed": {}},
        "gates": {
            "exact_cap_and_record_boundary": False,
            "no_reconstruction_or_transaction_exception": False,
            "source_hashes_stable": bool(source_before) and source_before == source_after,
            "peak_rss_within_1gib": (
                resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024
                <= RLIMIT_AS_BYTES
            ),
            "result_payload_within_5mib": True,
        },
        "wall_s": time.monotonic() - started,
        "peak_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
    }


def run(*, expected_self_sha256: str) -> dict[str, Any]:
    started = time.monotonic()
    source_before: dict[str, str] = {}
    source_after: dict[str, str] = {}
    owners = None
    observer = None
    failure = None
    stop_reason = "SOURCE_COMPLETED_BEFORE_BOUNDARY"
    previous = signal.getsignal(signal.SIGALRM)

    def deadline(*_args: object) -> None:
        raise _Stop("INTERNAL_1500S_DEADLINE")

    signal.signal(signal.SIGALRM, deadline)
    signal.setitimer(signal.ITIMER_REAL, INTERNAL_SECONDS)
    try:
        observed_self_sha256 = _sha(Path(__file__).resolve())
        if observed_self_sha256 != expected_self_sha256:
            raise RuntimeError("Phase-C harness SHA does not match wrapper binding")
        source_before = _source_hashes()
        owners = build()
        observer = PhaseCPrefixObserver(owners, started)
        owners.coordinator.consume_record_ticket = observer
        owners.coordinator.run(owners.reader)
    except _Stop as stop:
        stop_reason = str(stop)
    except BaseException as error:
        failure = error
        stop_reason = "UNEXPECTED_EXCEPTION"
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0.0)
        signal.signal(signal.SIGALRM, previous)
    try:
        source_after = _source_hashes()
    except BaseException as hash_error:
        if failure is None:
            failure = hash_error
            stop_reason = "SOURCE_HASH_FINALIZATION_FAILED"
    if (
        owners is None or observer is None
        or owners.coordinator._a is None or owners.coordinator._b is None
        or not observer.hooks_installed
    ):
        return _early_failure_result(
            started=started,
            failure=(failure or RuntimeError("prefix ended before branch bootstrap")),
            stop_reason=stop_reason,
            source_before=source_before,
            source_after=source_after,
            expected_self_sha256=expected_self_sha256,
        )
    audit = owners.coordinator.audit()
    drift_owner = owners.coordinator._b._composition.consensus_drift
    if drift_owner is None or drift_owner is not observer.initial_drift_owner:
        return _early_failure_result(
            started=started,
            failure=RuntimeError("B consensus drift owner missing or replaced"),
            stop_reason="B_DRIFT_OWNER_MISSING_OR_REPLACED",
            source_before=source_before,
            source_after=source_after,
            expected_self_sha256=expected_self_sha256,
            events=int(audit.events), records=observer.records,
        )
    publication = owners.coordinator.diagnostic_publication()
    pending = drift_owner.snapshot()._state.pending
    oldest_availability = None if not pending else pending[0].availability_time_ns * 1e-9
    final_native_time = float(publication.b.root_state.time_s)
    exact_boundary_reached = (
        stop_reason == "EXACT_EVENT_CAP"
        and int(audit.events) == EVENT_CAP
        and observer.records == RECORD_COUNT
    )
    pending_boundary_evaluation = (
        None if not exact_boundary_reached
        else oldest_availability is None or oldest_availability > final_native_time
    )
    expected_boundaries = {
        268_739: HISTORICAL_BOUNDARY, EVENT_CAP: FOLLOWING_BOUNDARY,
    }
    boundary_ok = all(
        observer.boundaries.get(events, {}).get("record_ordinal") == expected[0]
        and tuple(observer.boundaries[events]["raw_identity"]) == expected[1]
        and observer.boundaries[events]["event_count"] == (10 if events == EVENT_CAP else 10)
        and observer.boundaries[events]["end_event_count"] == expected[2]
        for events, expected in expected_boundaries.items()
    )
    config = drift_owner.config
    gates = {
        "exact_cap_and_record_boundary": (
            exact_boundary_reached and boundary_ok
        ),
        "action03_reached_without_action_reset": (
            "03_pelvis_hula_circle" in observer.actions
            and drift_owner.stream_owner_digest == observer.stream_owner_digest
            and observer.drift_owner_unchanged
            and observer.drift_revision_monotonic
            and id(drift_owner) == observer.initial_drift_owner_token
        ),
        "a_drift_absent": observer.a_drift_absent,
        "b_drift_present_only": observer.b_drift_present,
        "same_event_time_and_pose_stream": (
            observer.same_root_time and observer.same_history_stream
        ),
        "all_root_states_finite_symmetric_positive_definite": observer.all_states_valid,
        "no_reconstruction_or_transaction_exception": failure is None,
        "observer_remained_noninterfering": not observer.observer_failures,
        "b_inside_anchor_envelope_plus_0_75m": observer.b_inside_volume,
        "anchor_envelope_is_exact_owner_bounds_plus_0_75m": (
            observer.envelope_matches_owner
        ),
        "b_speed_at_most_12mps": observer.b_speed_bounded,
        "at_least_one_accepted_drift_correction": observer.drift["accepted"] >= 1,
        "drift_deltas_finite_and_at_most_0_50mps": (
            observer.drift_deltas_finite
            and math.isfinite(observer.drift_maximum_delta_norm_mps)
            and observer.drift_maximum_delta_norm_mps <= 0.50
        ),
        "drift_transactions_velocity_only": observer.drift_velocity_only,
        "uwb_absolute_updates_position_only": observer.uwb_position_only,
        "trusted_partition_sizes_are_1_to_10": (
            bool(observer.trusted_partition_histogram)
            and all(
                1 <= int(size) <= 10
                for size in observer.trusted_partition_histogram
            )
        ),
        "no_drift_duplicate_or_replay": observer.drift_duplicate_or_replay == 0,
        "pending_is_strictly_future_only": (
            pending_boundary_evaluation is True
        ),
        "bias_disabled_and_unchanged_by_drift": (
            config.acceleration_bias is None and observer.bias_unchanged_by_drift
        ),
        "source_hashes_stable": source_before == source_after,
        "peak_rss_within_1gib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024 <= RLIMIT_AS_BYTES,
        "result_payload_within_5mib": True,
    }
    mechanism = all(gates.values())
    return {
        "schema": SCHEMA,
        "status": "PHASE_C_PREFIX_MECHANISM_PASS" if mechanism else "PHASE_C_PREFIX_MECHANISM_FAIL",
        "authorization": "GO_FULL_CONFIG_WORK" if mechanism else "STOP",
        "diagnostic_only": True,
        "product_ready": False,
        "scientific_pass": False,
        "failure": (
            None if failure is None
            else {"type": type(failure).__name__, "message": str(failure)}
        ),
        "stop_reason": stop_reason,
        "limits": {
            "event_cap": EVENT_CAP, "internal_seconds": INTERNAL_SECONDS,
            "outer_seconds": OUTER_SECONDS, "rlimit_as_bytes": RLIMIT_AS_BYTES,
            "threads": 1, "maximum_output_bytes": MAX_OUTPUT_BYTES,
            "retry": False,
        },
        "provenance": {
            "session_id": SESSION_ID,
            "expected_self_sha256": expected_self_sha256,
            "observed_self_sha256": _sha(Path(__file__).resolve()),
            "config_source_sha256": EXPECTED[CONSENSUS_DRIFT_CONFIG_SOURCE],
            "config": asdict(config),
            "stream_owner_digest": drift_owner.stream_owner_digest,
            "initial_owner_object_token": observer.initial_drift_owner_token,
            "final_owner_object_token": id(drift_owner),
            "initial_owner_revision": observer.initial_drift_revision,
            "final_owner_revision": drift_owner.revision,
            "a_drift_absent": observer.a_drift_absent,
            "b_drift_present": observer.b_drift_present,
            "factory_inputs": {str(path): value for path, value in EXPECTED.items()},
            "source_sha256_before": source_before,
            "source_sha256_after": source_after,
        },
        "boundary": {
            "events": int(audit.events), "records": observer.records,
            "historical_record_counter_explanation": (
                "Prior JSON records_consumed=36548 counted completed records; its last "
                "zero-based record_ordinal was 36547. This prefix adds ordinal 36548."
            ),
            "observed": observer.boundaries,
            "continuous_session_id": SESSION_ID,
            "action_ids_seen": sorted(observer.actions),
            "region_ids_seen": sorted(observer.regions),
            "event_chain_sha256": audit.event_chain_sha256,
        },
        "a": _branch_result(observer, "a", publication.a.root_state),
        "b": _branch_result(observer, "b", publication.b.root_state),
        "drift": {
            **dict(observer.drift),
            "maximum_velocity_delta_norm_mps": observer.drift_maximum_delta_norm_mps,
            "pending_count": drift_owner.pending_count,
            "oldest_pending_availability_time_s": oldest_availability,
            "final_native_frame_time_s": final_native_time,
            "pending_boundary_evaluation": (
                "NOT_EVALUATED_BEFORE_EXACT_BOUNDARY"
                if pending_boundary_evaluation is None
                else pending_boundary_evaluation
            ),
            "duplicate_or_replay_count": observer.drift_duplicate_or_replay,
            "decision_digests": observer.drift_decision_digests,
            "pending_admission_identities": (
                observer.pending_admission_identities
            ),
            "next_native_stream_identities": (
                observer.next_native_stream_identities
            ),
            "bias_enabled": False,
            "observer_failures": observer.observer_failures,
        },
        "state_validity": {
            "contract": (
                "RootState finite + covariance allclose transpose "
                "at atol=1e-10 + Cholesky"
            ),
            "first_failure": observer.first_state_failure,
        },
        "trusted_partition_size_histogram": dict(observer.trusted_partition_histogram),
        "performance": {
            "routing_calls": observer.routing_calls,
            "instrumented_routing_and_consume_wall_s": observer.core_consume_wall_s,
            "observer_hook_wall_s": observer.hook_observer_wall_s,
            "consume_minus_observer_hook_wall_s": (
                observer.consume_minus_hook_wall_s
            ),
            "observer_self_wall_s": observer.observer_self_wall_s,
            "events_per_consume_minus_hook_second": (
                None
                if observer.consume_minus_hook_wall_s <= 0.0
                else int(audit.events) / observer.consume_minus_hook_wall_s
            ),
            "bootstrap_hook_installs": observer.bootstrap_hook_installs,
            "checkpoints": observer.checkpoints,
            "diagnostic_routing_metrics": _jsonable(
                audit.diagnostic_routing_metrics
            ),
        },
        "gates": gates,
        "report_only": {
            "per_record_displacement_sentinel_m": REPORT_ONLY_STEP_M,
            "is_acceptance_gate": False,
        },
        "wall_s": time.monotonic() - started,
        "peak_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
    }


def _write_new(path: Path, value: Mapping[str, Any]) -> None:
    payload = (json.dumps(_jsonable(value), sort_keys=True, separators=(",", ":"),
                          allow_nan=False) + "\n").encode()
    if len(payload) > MAX_OUTPUT_BYTES:
        raise RuntimeError("Phase-C result exceeds 5 MiB")
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC, 0o444)
    try:
        if os.write(descriptor, payload) != len(payload):
            raise RuntimeError("short Phase-C result write")
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--expected-self-sha256", required=True)
    args = parser.parse_args()
    result = run(expected_self_sha256=args.expected_self_sha256)
    _write_new(args.output, result)
    return 0 if result["status"] == "PHASE_C_PREFIX_MECHANISM_PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
