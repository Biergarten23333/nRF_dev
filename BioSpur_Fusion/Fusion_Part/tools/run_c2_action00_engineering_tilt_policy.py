#!/usr/bin/env python3
"""One-shot Action00-only engineering VQF tilt-policy artifact runner."""
from __future__ import annotations

import argparse
from dataclasses import fields, is_dataclass
import hashlib
import json
import os
from pathlib import Path
import sys
from typing import Any, Mapping

import numpy as np


sys.dont_write_bytecode = True
WORKSPACE = Path("/mnt/nrf_ssd/nRF_dev/BioSpur_Fusion/Fusion_Part")
BASE = Path("logs/c2_basis_progressive_20260829T102836Z")
SETTINGS = BASE / "P2_PREFIT_ACTIVE_PARAMETER_REGISTRY_AMENDMENT_021_SOURCE_CORRECTION_001.json"
SEAL = BASE / "P2_PREFIT_REGISTRY_SEAL_021_SOURCE_CORRECTION_001.json"
ACTIVATION = BASE / "C2_REAL_DIAGNOSTIC_ACTIVATION_009.json"
SOURCE_DELTA = BASE / "C2_REAL_DIAGNOSTIC_ACTIVATION_009_AUTHORIZED_SOURCE_DELTA_001_RUNTIME_BUGFIX_002.json"
ACTION00_ORIENTATION_AUTHORITY_SHA256 = (
    "1384c5eb47a675b21694d582dcd925c6cf2e7728f65f32bf9a71d8c08b9781b0"
)
INITIAL = BASE / "P1_FRONTEND/P1_INITIAL_STILL_STOCHASTIC_STATE.json"
CLOCK = Path("logs/c2_uwb_beacon_clock_20260903_141552/CLOCK_TABLE_CALIBRATION_ONLY.json")


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _jsonable(value: Any) -> Any:
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_jsonable(item) for item in value]
    if is_dataclass(value):
        return {field.name: _jsonable(getattr(value, field.name)) for field in fields(value)}
    return value


def _write_new(path: Path, value: Any) -> None:
    with path.open("x", encoding="utf-8") as stream:
        json.dump(_jsonable(value), stream, indent=2, sort_keys=True, allow_nan=False)
        stream.write("\n")
    path.chmod(0o444)


def _clock_owner():
    from biospur_fusion.c2_coupled_progressive.continuous_frontend import (
        CONTINUOUS_FRONTEND_SCHEMA, ContinuousClockOwner, NodeClockBinding,
    )
    from biospur_fusion.c2_uwb_root_world.authoritative_articulated_fusion import (
        Native200ClockMappingOwner,
    )
    from biospur_fusion.c2_uwb_root_world.run_calibration import _clock_models

    path = WORKSPACE / CLOCK
    document = json.loads(path.read_text(encoding="utf-8"))
    models = _clock_models(path)
    owner_sha = _sha(path)
    source_sha = str(document["source_sha256"])
    bindings = []
    for node, model in sorted(models.items()):
        mapping = Native200ClockMappingOwner(
            node=node, clock_domain="B306_TIMER2", boot_epoch=model.boot_epoch,
            a_ns_per_us=model.a_ns_per_us, b_ns=model.b_ns,
            clock_owner_sha256=owner_sha,
        )
        bindings.append(NodeClockBinding(
            node, model.boot_epoch, "B306_TIMER2", mapping.digest,
            model.a_ns_per_us, model.b_ns, owner_sha, source_sha,
        ))
    return ContinuousClockOwner(CONTINUOUS_FRONTEND_SCHEMA, tuple(bindings))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pre-run-manifest", type=Path, required=True)
    parser.add_argument("--expected-manifest-sha256-file", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    manifest_path = args.pre_run_manifest.resolve()
    manifest_sha_path = args.expected_manifest_sha256_file.resolve()
    output = args.output.resolve()
    manifest_path.relative_to(WORKSPACE)
    manifest_sha_path.relative_to(WORKSPACE)
    output.relative_to(WORKSPACE)
    expected_manifest_sha = manifest_sha_path.read_text(encoding="ascii").strip()
    if _sha(manifest_path) != expected_manifest_sha:
        raise RuntimeError("PRE_RUN_MANIFEST SHA-256 mismatch")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if (
        manifest.get("schema") != "biospur.c2.action00_engineering_policy.real_prerun.v1"
        or manifest.get("attempt_count_before") != 0
        or manifest.get("scope", {}).get("action_id") != "00_initial_still"
        or manifest.get("scope", {}).get("later_action_payload_open_allowed") is not False
        or output != (WORKSPACE / manifest["output_relative"]).resolve()
        or output.exists()
    ):
        raise RuntimeError("invalid or already-consumed Action00 preregistration")
    for binding in manifest["bound_files"]:
        path = (WORKSPACE / binding["path"]).resolve()
        path.relative_to(WORKSPACE)
        if not path.is_file() or _sha(path) != binding["sha256"]:
            raise RuntimeError(f"prelaunch bound file changed: {binding['path']}")
    from biospur_fusion.c2_coupled_progressive.action00_engineering_reader import (
        ACTION00_START_OFFSET, ACTION00_STOP_OFFSET, Action00EngineeringPolicyReader,
        RAW_RELATIVE, SOURCE_STAT_IDENTITY,
    )
    raw_stat = (WORKSPACE / RAW_RELATIVE).lstat()
    observed_stat = [raw_stat.st_dev, raw_stat.st_ino, raw_stat.st_size, raw_stat.st_mtime_ns]
    if observed_stat != list(SOURCE_STAT_IDENTITY) or observed_stat != manifest["raw"]["stat_identity"]:
        raise RuntimeError("raw stat identity changed before Action00 open")

    output.mkdir(parents=False)
    _write_new(output / "ATTEMPT.json", {
        "attempt": 1, "attempt_count_before": 0,
        "status": "STARTED_AFTER_ALL_PRELAUNCH_BINDINGS_VERIFIED",
        "raw_opened": False,
    })
    try:
        amendment = json.loads((WORKSPACE / SETTINGS).read_text(encoding="utf-8"))
        initial_state = json.loads((WORKSPACE / INITIAL).read_text(encoding="utf-8"))
        from biospur_fusion.v0.c2_progressive.action00_orientation_runtime import (
            Action00OrientationRuntime,
        )
        runtime = Action00OrientationRuntime(
            root=WORKSPACE,
            expected_authority_sha256=ACTION00_ORIENTATION_AUTHORITY_SHA256,
            settings=amendment["effective_settings"],
            initial_stochastic_state=initial_state,
        )
        clocks = _clock_owner()
        reader = Action00EngineeringPolicyReader(root=WORKSPACE, clock_owner=clocks)
        decoded = reader.read()
        oriented = runtime.process(decoded.decoded_action)
        from biospur_fusion.c2_coupled_progressive.authenticated_vqf_tilt_join import (
            AuthenticatedVQFTiltClockJoin,
        )
        from biospur_fusion.c2_coupled_progressive.action00_tilt_trust_policy import (
            Action00TiltPolicyRegistry, ENGINEERING_RESULT_SCHEMA,
        )
        provenance = oriented.vqf_tilt_diagnostic_provenance
        join = AuthenticatedVQFTiltClockJoin(
            clock_owner=clocks, diagnostic_provenance_digest=provenance.digest,
        )
        event_by_identity = {event.event_id: event for event in decoded.continuous_imu_events}
        event_by_row = {
            (event.node_id, event.payload_owner.raw.start_offset,
             event.payload_owner.raw.end_offset, event.payload_owner.raw.sample_index): event
            for event in decoded.continuous_imu_events
        }
        if len(event_by_identity) != len(decoded.continuous_imu_events) or len(event_by_row) != len(decoded.continuous_imu_events):
            raise RuntimeError("preserved Action00 event identities are not unique")
        rows = []
        for node in sorted(oriented.time_us_by_node):
            for index in range(len(oriented.time_us_by_node[node])):
                raw_start = int(oriented.raw_start_offset_by_node[node][index])
                raw_end = int(oriented.raw_end_offset_by_node[node][index])
                raw_sample = int(oriented.raw_sample_index_by_node[node][index])
                event = event_by_row.get((node, raw_start, raw_end, raw_sample))
                if event is None:
                    raise RuntimeError("oriented row does not map to exactly one preserved event")
                if event_by_identity.get(event.event_id) is not event:
                    raise RuntimeError("preserved event identity inventory changed")
                rows.append(join.commit(join.prepare(oriented, event=event, index=index)))
        registry = Action00TiltPolicyRegistry(provenance=provenance, initial_state=initial_state)
        registry.ingest(rows)
        policy = registry.finalize()
        result = {
            "schema": ENGINEERING_RESULT_SCHEMA,
            "status": policy.status,
            "product_ready": policy.product_ready,
            "scientific_pass": policy.scientific_pass,
            "policy": policy,
            "preregistration": registry.preregistration,
            "reader_access": decoded.access_audit,
            "joined_row_count": len(rows),
            "joined_rows_by_node": {node: sum(row.node == node for row in rows)
                                    for node in policy.expected_nodes},
            "source_access_union": [[0, ACTION00_START_OFFSET],
                                    [ACTION00_START_OFFSET, ACTION00_STOP_OFFSET]],
            "action00_orientation_authority_sha256": runtime.authority_digest,
            "later_action_payload_opened": False,
            "pipeline_scope": "ORIENTATION_ACTION00_ONLY_NO_CALIBRATION_STAGE_TRANSITION",
        }
        _write_new(output / "RESULT.json", result)
    except BaseException as exc:
        _write_new(output / "FAILURE.json", {
            "status": "FAILED_NO_RETRY", "exception_type": type(exc).__name__,
            "exception_message": str(exc), "later_action_payload_opened": False,
        })
        raise


if __name__ == "__main__":
    main()
