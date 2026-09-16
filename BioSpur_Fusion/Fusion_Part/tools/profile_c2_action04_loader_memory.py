#!/usr/bin/env python3
"""Low-overhead RSS milestones for the Action04 loader, never fusion admission."""
from __future__ import annotations

import argparse
from dataclasses import asdict
import hashlib
import json
import math
import os
from pathlib import Path
import resource
import sys
import time
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
ACTION = "04_shoulder_left"
DRY_OUTPUT = ROOT / "logs/c2_action04_loader_memory_dry_revision_008_20260907T173000Z"
RAW_OUTPUT = ROOT / "logs/c2_action04_loader_memory_raw_revision_005_20260907T180000Z"
PROFILE_SEAL = "b9b70948b0ea0200082575856c3322ac54cde4d4fe1e44f22e6a741b49a67323"
REV007_SEAL = "9b50919bf901ca53259dc759b4769d5df21086c579787b54fd3b4400102d3034"
FORMAL_FREEZE = ROOT / "logs/c2_imu_19plus2_formal_freeze_20260901_082039"
FORMAL_SEAL = FORMAL_FREEZE / "FORMAL_FREEZE_SEAL.json"
FORMAL_MANIFEST = FORMAL_FREEZE / "FORMAL_FREEZE_MANIFEST.json"
FORMAL_SEAL_SHA256 = "f41317208851eb3b0037b1463dd45aa3935d258161756f9549203603ef885534"
FORMAL_MANIFEST_SHA256 = "e4edfa682daa6c3002212d8c3a8e0992e0c4cb562938434d4d2f75ad43f87acb"
FORMAL_COLLECTIONS = (
    "accepted_artifacts", "canonical_payload", "capture_metadata",
    "effective_configuration", "implementation",
)
NON_PROMOTED_PARTIALS = (
    ROOT / "logs/c2_action04_loader_memory_dry_revision_003_20260907T151000Z",
    ROOT / "logs/c2_action04_loader_memory_dry_revision_004_20260907T153000Z",
    ROOT / "logs/c2_action04_loader_memory_dry_revision_005_20260907T154500Z",
)
NON_PROMOTED_RAW = ROOT / "logs/c2_action04_loader_memory_raw_revision_004_20260907T170000Z"
EXACT_PRIOR_FULL_RAW_PEAK_KIB = 709_152
EXPECTED_INVENTORY = {
    "imu_metric": 1000, "imu_delivery_context": 6,
    "imu_temporal_closure": 1, "imu_total": 1007,
    "sweeps": 410, "groups": 41, "packets": 41,
}


def _sha(path: Path) -> str:
    owner = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            owner.update(block)
    return owner.hexdigest()


def _rss() -> tuple[int, int]:
    resident = None
    for line in Path("/proc/self/status").read_text().splitlines():
        if line.startswith("VmRSS:"):
            resident = int(line.split()[1])
            break
    if resident is None:
        raise RuntimeError("VmRSS unavailable")
    return resident, int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)


class Milestones:
    def __init__(self, path: Path) -> None:
        self.started_ns = time.monotonic_ns()
        self.observer_ns = 0
        self.count = 0
        self.path = path
        self.fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_APPEND, 0o444)

    def mark(self, name: str, **extra) -> None:
        started = time.monotonic_ns()
        rss, peak = _rss()
        record = {
            "index": self.count, "stage": str(name),
            "wall_since_start_ms": (time.monotonic_ns() - self.started_ns) * 1e-6,
            "rss_kib": rss, "ru_maxrss_kib": peak, **extra,
        }
        payload = (json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n").encode()
        os.write(self.fd, payload)
        self.count += 1
        self.observer_ns += time.monotonic_ns() - started

    def close(self) -> None:
        os.fsync(self.fd)
        os.close(self.fd)


def _atomic_json(path: Path, value) -> None:
    payload = (json.dumps(value, indent=2, sort_keys=True) + "\n").encode()
    temporary = path.with_suffix(path.suffix + ".tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o444)
    try:
        os.write(descriptor, payload)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    os.replace(temporary, path)


def _validate(args, script_sha: str) -> Path:
    output = args.output.resolve()
    expected = DRY_OUTPUT if args.mode == "dry" else RAW_OUTPUT
    if output != expected or output.exists():
        raise RuntimeError("exact fresh loader-profile output required")
    if args.action != ACTION or args.start_s != 0.0 or args.duration_s != 5.0 or args.attempt != 1:
        raise RuntimeError("only one Action04 first-5s loader attempt is allowed")
    if args.mode == "raw" and args.authorized_script_sha256 != script_sha:
        raise RuntimeError("loader profile script hash is not externally authorized")
    if args.mode == "dry" and args.authorized_script_sha256 is not None:
        raise RuntimeError("dry run must not claim raw authorization")
    return output


def _bindings() -> dict[str, str]:
    values = {
        "profile_seal": ROOT / "logs/c2_authoritative_articulated_coordinator_profile_revision_001_20260907T140000Z/SHA256SUMS",
        "rev007_seal": ROOT / "logs/c2_authoritative_articulated_action04_raw_revision_007_20260907T070000Z/SHA256SUMS",
    }
    hashes = {name: _sha(path) for name, path in values.items()}
    if hashes != {"profile_seal": PROFILE_SEAL, "rev007_seal": REV007_SEAL}:
        raise RuntimeError("loader profile predecessor binding mismatch")
    return {str(path.relative_to(ROOT)): hashes[name] for name, path in values.items()}


class InvocationGuards:
    def __init__(self) -> None:
        from biospur_fusion.c2_uwb_calibration import articulated_range
        from biospur_fusion.c2_uwb_root_world import authoritative_articulated_fusion

        self.fusion = authoritative_articulated_fusion
        self.range = articulated_range
        self.engine_class = authoritative_articulated_fusion.AuthoritativeArticulatedFusion
        self.original_admit = self.engine_class.admit
        self.original_fusion_solve = authoritative_articulated_fusion.solve_articulated_ranges
        self.original_range_solve = articulated_range.solve_articulated_ranges
        self.counts = {"engine_admit": 0, "fusion_solver_entry": 0,
                       "range_solver_entry": 0}
        self.overhead_ns = 0

    def __enter__(self):
        started = time.monotonic_ns()
        owner = self

        def admit_guard(*args, **kwargs):
            owner.counts["engine_admit"] += 1
            raise RuntimeError("LOADER_PROFILE_CROSSED_ENGINE_ADMIT_BOUNDARY")

        def fusion_solve_guard(*args, **kwargs):
            owner.counts["fusion_solver_entry"] += 1
            raise RuntimeError("LOADER_PROFILE_CROSSED_FUSION_SOLVER_BOUNDARY")

        def range_solve_guard(*args, **kwargs):
            owner.counts["range_solver_entry"] += 1
            raise RuntimeError("LOADER_PROFILE_CROSSED_RANGE_SOLVER_BOUNDARY")

        self.engine_class.admit = admit_guard
        self.fusion.solve_articulated_ranges = fusion_solve_guard
        self.range.solve_articulated_ranges = range_solve_guard
        self.overhead_ns += time.monotonic_ns() - started
        return self

    def __exit__(self, exc_type, exc, traceback):
        started = time.monotonic_ns()
        self.engine_class.admit = self.original_admit
        self.fusion.solve_articulated_ranges = self.original_fusion_solve
        self.range.solve_articulated_ranges = self.original_range_solve
        self.overhead_ns += time.monotonic_ns() - started
        return False


class InventoryObserver:
    """Read object cardinalities in place; never retain loader-owned objects."""

    def __init__(self) -> None:
        self.value: dict[str, int] | None = None
        self.overhead_ns = 0

    def capture_objects(
        self, *, metric_rows, context_rows, temporal_closure_row,
        imu_rows, groups, packets,
    ) -> dict[str, int]:
        started = time.monotonic_ns()
        closure_count = sum(row is temporal_closure_row for row in imu_rows)
        value = {
            "imu_metric": len(metric_rows),
            "imu_delivery_context": len(context_rows),
            "imu_temporal_closure": closure_count,
            "imu_total": len(imu_rows),
            "sweeps": sum(len(group) for group in groups),
            "groups": len(groups),
            "packets": len(packets),
        }
        self.value = value
        self.overhead_ns += time.monotonic_ns() - started
        return value

    def capture_loader_frame(self) -> dict[str, int]:
        frame = sys._getframe(1)
        runner = str((ROOT / "tools/run_c2_authoritative_articulated_action04.py").resolve())
        while frame is not None:
            if (
                frame.f_code.co_name == "_real_action04_loader"
                and str(Path(frame.f_code.co_filename).resolve()) == runner
            ):
                values = frame.f_locals
                return self.capture_objects(
                    metric_rows=values["metric_rows"],
                    context_rows=values["context_rows"],
                    temporal_closure_row=values["temporal_closure_row"],
                    imu_rows=values["imu_rows"], groups=values["groups"],
                    packets=values["packets"],
                )
            frame = frame.f_back
        raise RuntimeError("production loader frame unavailable to inventory observer")

    def validate(self) -> None:
        if self.value != EXPECTED_INVENTORY:
            raise RuntimeError(f"observed loader inventory mismatch: {self.value}")
        if self.overhead_ns >= 1_000_000:
            raise RuntimeError("loader inventory observer overhead is not negligible")


class AccessAudit:
    def __init__(self) -> None:
        self.enabled = True
        self.entries: list[dict[str, str]] = []
        self.forbidden: list[dict[str, str]] = []
        self.provenance_hash_only: list[dict[str, str]] = []
        self.provenance_bindings = self._load_provenance_bindings()

    @classmethod
    def _load_provenance_bindings(cls) -> dict[str, dict[str, str]]:
        """Load the sealed allow-set before installing the process audit hook.

        This is not a path-name exemption.  A matching path is allowed only at
        the frozen-C2 SHA-256 verifier call boundary; every other open remains
        a compute-input violation.
        """
        seal_sha = _sha(FORMAL_SEAL)
        manifest_sha = _sha(FORMAL_MANIFEST)
        if seal_sha != FORMAL_SEAL_SHA256 or manifest_sha != FORMAL_MANIFEST_SHA256:
            raise RuntimeError("formal frozen-C2 provenance binding mismatch")
        seal = json.loads(FORMAL_SEAL.read_text(encoding="utf-8"))
        if (
            seal.get("status") != "FORMAL_FREEZE_SEALED"
            or seal.get("manifest_sha256") != FORMAL_MANIFEST_SHA256
        ):
            raise RuntimeError("formal frozen-C2 seal identity changed")
        manifest = json.loads(FORMAL_MANIFEST.read_text(encoding="utf-8"))
        result: dict[str, dict[str, str]] = {}
        for collection in FORMAL_COLLECTIONS:
            rows = manifest.get(collection)
            if not isinstance(rows, list):
                raise RuntimeError(f"formal manifest collection missing: {collection}")
            for row in rows:
                relative = row.get("path") if isinstance(row, dict) else None
                expected = row.get("sha256") if isinstance(row, dict) else None
                if not isinstance(relative, str) or not isinstance(expected, str):
                    raise RuntimeError("malformed formal manifest binding")
                if not cls._is_forbidden(relative):
                    continue
                absolute = str((ROOT / relative).resolve())
                result[absolute] = {
                    "expected_sha256": expected,
                    "owning_seal": str(FORMAL_SEAL.relative_to(ROOT)),
                    "owning_seal_sha256": FORMAL_SEAL_SHA256,
                    "manifest": str(FORMAL_MANIFEST.relative_to(ROOT)),
                    "manifest_sha256": FORMAL_MANIFEST_SHA256,
                    "collection": collection,
                }
        if not result:
            raise RuntimeError("formal manifest has no bound HXX provenance members")
        return result

    @staticmethod
    def _is_forbidden(value: str) -> bool:
        normalized = value.lower().replace("-", "_")
        return any(token in normalized for token in (
            "/h01/", "/h02/", "h01_", "h02_", "hxx_",
        ))

    @staticmethod
    def _read_only_open(arguments) -> bool:
        mode = str(arguments[1]) if len(arguments) > 1 else ""
        flags = int(arguments[2]) if len(arguments) > 2 and isinstance(arguments[2], int) else 0
        write_flags = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND
        return not any(token in mode for token in ("w", "a", "x", "+")) and not (flags & write_flags)

    @staticmethod
    def _frozen_digest_stack() -> list[dict[str, str]]:
        rows: list[dict[str, str]] = []
        frame = sys._getframe(2)
        while frame is not None and len(rows) < 16:
            rows.append({
                "function": frame.f_code.co_name,
                "file": str(Path(frame.f_code.co_filename).resolve()),
            })
            frame = frame.f_back
        return rows

    @staticmethod
    def _is_digest_verifier_stack(stack: list[dict[str, str]]) -> bool:
        owner = str((ROOT / "src/biospur_fusion/c2_3a_kinematics/provenance.py").resolve())
        functions = {(row["file"], row["function"]) for row in stack}
        return all((owner, function) in functions for function in (
            "sha256_file", "_require_digest", "verify_frozen_c2",
        ))

    def hook(self, event, arguments) -> None:
        if not self.enabled or event not in {"open", "import"}:
            return
        value = str(arguments[0]) if arguments else ""
        row = {"event": event, "value": value, "classification": "OTHER"}
        self.entries.append(row)
        if self._is_forbidden(value):
            resolved = str(Path(value).resolve()) if event == "open" else value
            binding = self.provenance_bindings.get(resolved)
            stack = self._frozen_digest_stack() if event == "open" else []
            if (
                event == "open"
                and binding is not None
                and self._read_only_open(arguments)
                and self._is_digest_verifier_stack(stack)
            ):
                row.update({
                    "classification": "PROVENANCE_HASH_ONLY",
                    **binding,
                    "call_stack": stack,
                })
                self.provenance_hash_only.append(row)
                return
            row.update({"classification": "COMPUTE_INPUT", "call_stack": stack})
            self.forbidden.append(row)
            raise RuntimeError(f"FORBIDDEN_HXX_COMPUTE_INPUT:{value}")

    def stop(self) -> dict:
        self.enabled = False
        return {
            "event_count": len(self.entries),
            "open_count": sum(row["event"] == "open" for row in self.entries),
            "import_count": sum(row["event"] == "import" for row in self.entries),
            "provenance_hash_only_count": len(self.provenance_hash_only),
            "provenance_hash_only": list(self.provenance_hash_only),
            "bound_hxx_member_count": len(self.provenance_bindings),
            "forbidden": list(self.forbidden),
            "entries": list(self.entries),
        }


def _synthetic_dry(mark: Milestones) -> dict:
    import numpy as np
    import run_c2_authoritative_articulated_action04 as runner
    import audit_c2_articulated_analytic_authoritative as harness
    from biospur_fusion.c2_uwb_calibration.causal_articulated_pose import CausalArticulatedPose
    from biospur_fusion.c2_uwb_root_world.authoritative_articulated_fusion import AuthoritativeArticulatedFusion
    from biospur_fusion.ingest.events import EventStatus, RawByteProvenance, RecordType, TypedEvent

    mark.mark("imports.complete")
    with InvocationGuards() as guards:
        clock_owner, clock_paths = runner._load_sealed_action04_pose_clock()
        interval = runner._load_sealed_action04_interval()
        contact = runner._load_sealed_contact_profile_document()
        _axis_report, hinge_model, model_owner = runner._load_and_fit_sealed_axis_owner()
        geometry, alignment, _model, _owner, data, frames = harness._pose_owner()
        mark.mark(
            "sealed.clock_model_contact_trajectory_owners.complete",
            sealed_owner_count=len(clock_paths) + 3,
        )
        runner_sha = _sha(Path(runner.__file__).resolve())
        runner_request = runner.RawRunRequest(ACTION, 0.0, 5.0, 1, runner_sha)
        runner._validate_raw_request(runner_request)
        clock_document = json.loads(runner.POSE_CLOCK_TABLE.read_text())
        boot = int(clock_document["models"]["BSFC2CC"]["boot_epoch"])
        decode_shaped = [
            SimpleNamespace(
                node_id="BSFC2CC", record_type=RecordType.IMU,
                node_timer_us=int(clock_owner.timer_us[index]), global_time_ns=None,
                boot_epoch=boot, payload={"acc_raw": [2048, 0, 0], "gyro_raw": [0, 0, 0]},
                sequence=index,
            )
            for index in range(900, 1033)
        ]
        sentinel_stages = []
        try:
            runner._real_action04_loader(
                runner_request, _decoded_imu_sentinel=decode_shaped,
                _stage_observer=sentinel_stages.append,
            )
        except RuntimeError as error:
            if str(error) != "CONTROLLED_DECODE_ADAPTER_BOUNDARY_SENTINEL":
                raise
        else:
            raise RuntimeError("runner sentinel crossed the raw-open boundary")
        mark.mark("runner_request_to_raw_boundary_sentinel.complete",
                  runner_sha256=runner_sha, raw_opened=False)

    synthetic_raw = b"".join(
        index.to_bytes(4, "little") + b"\x00" * 12 for index in range(1417)
    )
    synthetic_raw_hash = hashlib.sha256(synthetic_raw).hexdigest()
    mark.mark("synthetic.raw_mapping_read.complete", byte_count=len(synthetic_raw))

    typed = []
    for index in range(1007):
        timer = 5_000 * (index + 1)
        typed.append(TypedEvent(
            "BSFC2CC", 0, RecordType.IMU, index, timer, None, None, index,
            {"acc_raw": [2048, 0, 0], "gyro_raw": [0, 0, 0]}, {},
            EventStatus.DECODED,
            RawByteProvenance(index + 1, 16 * index, 16 * (index + 1),
                              hashlib.sha256(synthetic_raw[index * 16:(index + 1) * 16]).hexdigest()),
        ))
    mark.mark("synthetic.v47_decode_shape.complete", typed_event_count=len(typed))
    canonical = json.dumps([
        {
            "node": row.node_id, "boot": row.boot_epoch, "type": row.record_type.value,
            "sequence": row.sequence, "timer_us": row.node_timer_us,
            "payload": row.payload, "status": row.status.value,
            "raw_sha256": row.raw.encoded_sha256,
        }
        for row in typed
    ], sort_keys=True, separators=(",", ":")).encode()
    typed_hash = hashlib.sha256(canonical).hexdigest()
    mark.mark("typed_event_canonicalization.complete", canonical_sha256=typed_hash)
    acceleration = np.asarray([row.payload["acc_raw"] for row in typed], dtype=np.int16)
    gyroscope = np.asarray([row.payload["gyro_raw"] for row in typed], dtype=np.int16)
    imu_hash = hashlib.sha256(acceleration.tobytes() + gyroscope.tobytes()).hexdigest()
    mark.mark("imu_arrays.complete", imu_rows=len(acceleration), imu_sha256=imu_hash)

    static, template, _ = harness.packets()
    static = harness.replace(
        static,
        clocks={node: harness.replace(owner, last_timer_us=6_000_000)
                for node, owner in static.clocks.items()}, digest="",
    )
    template = harness.replace(
        template,
        b_shadow_owner=harness.replace(template.b_shadow_owner, geometry=geometry, digest=""),
        digest="",
    )
    packets = []
    pose_cache = []
    for group_index, frame in enumerate(frames):
        rotations, points = harness._frame(data, frame, alignment, geometry)
        pose_cache.append((rotations, points))
        packets.append(harness._packet(
            static, template, group_index, 10, rotations, points
        ))
    packet_hash = hashlib.sha256(b"".join(packet.digest.encode() for packet in packets)).hexdigest()
    mark.mark("uwb_sweeps_groups_packets.complete", sweeps=410, groups=41,
              packets=41, packet_inventory_sha256=packet_hash)
    pose_hash = hashlib.sha256(b"".join(
        np.asarray(value).tobytes()
        for rotations, points in pose_cache
        for value in (*rotations.values(), *points.values())
    )).hexdigest()
    mark.mark("pose_owner_cache.complete", cached_frames=41, pose_cache_sha256=pose_hash)
    inventory_observer = InventoryObserver()
    observed_inventory = inventory_observer.capture_objects(
        metric_rows=typed[:1000], context_rows=typed[1000:1006],
        temporal_closure_row=typed[1006], imu_rows=typed,
        groups=[packet.event.payload for packet in packets], packets=packets,
    )
    inventory_observer.validate()
    mark.mark("actual_inventory_observed", observed_inventory=observed_inventory,
              observer_overhead_ns=inventory_observer.overhead_ns)

    projector = harness.partial(harness.project_hinge_corrections, model=hinge_model)
    pose = CausalArticulatedPose(
        action_start_s=0.0, action_stop_s=5.005,
        rotations_at_fraction=lambda fraction: pose_cache[min(40, int(math.floor(fraction * 41)))][0],
        geometry=geometry, hinge_projector=projector,
    )
    sentinel_guard_counts = dict(guards.counts)
    sentinel_guard_overhead_ns = guards.overhead_ns
    with InvocationGuards() as construction_guards:
        engine = AuthoritativeArticulatedFusion(static_owner=static, pose=pose)
        state_before = (engine.root.publication_token().digest, engine.pose.publication_token().digest,
                        engine.robust.revision)
        state_after = (engine.root.publication_token().digest, engine.pose.publication_token().digest,
                       engine.robust.revision)
        if state_before != state_after:
            raise RuntimeError("loader-only dry mutated fusion state")
        mark.mark("engine_construction.complete", root_revision=0, pose_revision=0,
                  robust_revision=0)
        observed_counts = {
            key: sentinel_guard_counts[key] + construction_guards.counts[key]
            for key in sentinel_guard_counts
        }
        if any(observed_counts.values()):
            raise RuntimeError("loader-only dry invoked an admission/solver guard")
        mark.mark("stop_before_admit_solve", observed_guard_counts=observed_counts)
    guard_overhead_ns = sentinel_guard_overhead_ns + construction_guards.overhead_ns
    if guard_overhead_ns >= 1_000_000:
        raise RuntimeError("invocation guard overhead is not negligible")
    return {
        "inventory": observed_inventory,
        "inventory_observer_overhead_ns": inventory_observer.overhead_ns,
        "inventory_hashes": {
            "synthetic_raw": synthetic_raw_hash, "typed_events": typed_hash,
            "imu_arrays": imu_hash, "packets": packet_hash, "pose_cache": pose_hash,
        },
        "owners": {
            "interval": interval, "contact_profile_digest": hashlib.sha256(
                json.dumps(contact, sort_keys=True).encode()).hexdigest(),
            "model_owner": model_owner, "clock_frame_count": len(clock_owner.timer_us),
            "runner_sentinel_stages": sentinel_stages,
        },
        "state_unchanged": state_before == state_after,
        "observed_guard_counts": observed_counts,
        "guard_overhead_ns": guard_overhead_ns,
        "guard_overhead_negligible": guard_overhead_ns < 1_000_000,
        "guard_state_unchanged": state_before == state_after,
    }


class _StopBeforeAdmit(RuntimeError):
    pass


def _raw_loader_only(mark: Milestones, script_sha: str) -> dict:
    import run_c2_authoritative_articulated_action04 as runner

    runner_sha = _sha(Path(runner.__file__).resolve())
    request = runner.RawRunRequest(ACTION, 0.0, 5.0, 1, runner_sha)
    runner._validate_raw_request(request)
    stage_map = {
        "loader.imports.complete": "imports.complete",
        "pregroup.clock_owner.complete": "sealed.clock_owner.complete",
        "pregroup.raw_hash.complete": "raw.mapping_read_hash.complete",
        "pregroup.layout_calibration.complete": "sealed.layout_calibration.complete",
        "pregroup.pose_owner.complete": "sealed.trajectory_pose_owner.complete",
        "pregroup.range_groups.complete": "uwb.sweeps_groups.complete",
        "pregroup.measurement_decode.complete": "v47.decode_typed_events.complete",
        "pregroup.pelvis_vqf.complete": "typed_events_imu_arrays.complete",
        "pregroup.ankle_decode.complete": "contact_ankle_arrays.complete",
        "pregroup.temporal_closure.complete": "imu_inventory_temporal_closure.complete",
        "pregroup.static_packets.complete": "uwb.packets.complete",
        "pregroup.axis_model.complete": "sealed.axis_model.complete",
        "pregroup.articulated_engine.complete": "pose_cache_engine_construction.complete",
    }

    inventory_observer = InventoryObserver()

    def observer(stage):
        if stage in stage_map:
            mark.mark(stage_map[stage], production_stage=stage)
        if stage == "pregroup.articulated_engine.complete":
            observed = inventory_observer.capture_loader_frame()
            inventory_observer.validate()
            mark.mark("actual_inventory_observed", observed_inventory=observed,
                      observer_overhead_ns=inventory_observer.overhead_ns)
            raise _StopBeforeAdmit("LOADER_MEMORY_PROFILE_STOP_BEFORE_ADMIT")

    try:
        with InvocationGuards() as guards:
            runner._real_action04_loader(request, _stage_observer=observer)
    except _StopBeforeAdmit:
        counts = dict(guards.counts)
        if any(counts.values()):
            raise RuntimeError("raw loader-only path invoked admission/solver guard")
        mark.mark("stop_before_admit_solve", observed_guard_counts=counts)
        return {"stopped_before_admit_solve": True,
                "observed_guard_counts": counts,
                "guard_overhead_ns": guards.overhead_ns,
                "guard_overhead_negligible": guards.overhead_ns < 1_000_000,
                "inventory": inventory_observer.value,
                "inventory_observer_overhead_ns": inventory_observer.overhead_ns,
                "inventory_observer_negligible": inventory_observer.overhead_ns < 1_000_000}
    raise RuntimeError("raw loader-only profile crossed the admit boundary")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("dry", "raw"), required=True)
    parser.add_argument("--action", required=True)
    parser.add_argument("--start-s", type=float, required=True)
    parser.add_argument("--duration-s", type=float, required=True)
    parser.add_argument("--attempt", type=int, required=True)
    parser.add_argument("--authorized-script-sha256")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    access = AccessAudit()
    sys.addaudithook(access.hook)
    script_sha = _sha(Path(__file__).resolve())
    output = _validate(args, script_sha)
    bindings = _bindings()
    non_promoted = [
        {
            "path": str(NON_PROMOTED_PARTIALS[0].relative_to(ROOT)),
            "status": "NON_PROMOTED_FIRST_GATE_FAILURE",
            "first_failure": "FORBIDDEN_HXX_ACCESS:H01_boxing_early_front_side_top.png",
        },
        {
            "path": str(NON_PROMOTED_PARTIALS[1].relative_to(ROOT)),
            "status": "NON_PROMOTED_LAUNCH_FAILURE",
            "first_failure": "MISSING_TESTS_PYTHONPATH",
        },
        {
            "path": str(NON_PROMOTED_PARTIALS[2].relative_to(ROOT)),
            "status": "NON_PROMOTED_EVIDENCE_INTERMEDIATE",
            "first_failure": "GUARD_OVERHEAD_NOT_SEPARATELY_RECORDED",
        },
    ]
    output.mkdir(parents=True)
    recorder = Milestones(output / "MILESTONES.jsonl")
    recorder.mark("process.baseline", mode=args.mode)
    started = time.monotonic_ns()
    try:
        detail = _synthetic_dry(recorder) if args.mode == "dry" else _raw_loader_only(recorder, script_sha)
        elapsed_ms = (time.monotonic_ns() - started) * 1e-6
        recorder.mark("loader_profile.complete", elapsed_ms=elapsed_ms)
    finally:
        observer_ms = recorder.observer_ns * 1e-6
        recorder.close()
    access_result = access.stop()
    _atomic_json(output / "ACCESS_AUDIT.json", access_result)
    guard_counts = detail["observed_guard_counts"]
    result = {
        "schema": "biospur.c2.action04-loader-memory-profile.v1",
        "mode": args.mode, "status": "STOPPED_BEFORE_ADMIT_SOLVE",
        "detail": detail, "milestone_count": recorder.count,
        "observer_overhead_ms": observer_ms,
        "observer_overhead_negligible": observer_ms < 10.0,
        "wall_ms": elapsed_ms, "wall_under_120s": elapsed_ms < 120_000.0,
        "rss_kib": _rss()[0], "peak_rss_kib": _rss()[1],
        "predecessor_bindings": bindings, "script_sha256": script_sha,
        "non_promoted_predecessor": non_promoted,
        "non_promoted_raw_revision": {
            "path": str(NON_PROMOTED_RAW.relative_to(ROOT)),
            "status": "NON_PROMOTED_INVENTORY_OVERCLAIM",
            "exact_prior_full_raw_peak_kib": EXACT_PRIOR_FULL_RAW_PEAK_KIB,
        },
        "fusion_state_mutated": False,
        "observed_guard_counts": guard_counts,
        "admit_calls": guard_counts["engine_admit"],
        "solve_calls": guard_counts["fusion_solver_entry"] + guard_counts["range_solver_entry"],
        "access_audit": {key: value for key, value in access_result.items() if key != "entries"},
        "raw_opened": args.mode == "raw",
        "hxx_compute_input_opened": bool(access_result["forbidden"]),
        "hxx_provenance_hash_only_opened": bool(access_result["provenance_hash_only"]),
    }
    _atomic_json(output / "RESULT.json", result)
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
