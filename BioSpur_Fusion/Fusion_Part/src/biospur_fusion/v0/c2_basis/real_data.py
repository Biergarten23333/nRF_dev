"""Metadata-sealed, bounded C2 raw accelerometer/gyroscope ingest.

The historical access artifacts are metadata authorities only.  They let this
module reconstruct the exact accepted C2 byte/time brackets without scanning
the acquisition plan or opening any Hxx action.  Payload access is impossible
until a fresh immutable basis preselection is supplied and verified.
"""
from __future__ import annotations

from dataclasses import asdict
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np

from biospur_fusion.v0.contracts import dump_json, sha256_file
from biospur_fusion.v0.dual_capture import load_capture2_calibration_episode
from biospur_fusion.v0.episode import segment_five_phase_episode
from biospur_fusion.v0.raw6_heading import Raw6Episode

from .contracts import CAPTURE_ID, CAPTURE_REL, C2_IDENTITY, EPISODE_SELECTION
from .raw_frontend import (
    ChronologicalOrientationFrontend,
    StillnessCalibration,
    estimate_stillness,
    raw_episode_from_rows,
)


FROZEN_SELECTION_REL = Path(
    "logs/pure_imu_v0_raw6_edge_global_20260828T050124Z/METADATA_PRESELECTION.json"
)
FROZEN_SELECTION_SHA256 = "f32bd72c02cf4333efd21c7e8faf9546a8474312d511dcf92950d5b8240d4581"
FROZEN_ACCESS_PRESELECTION_REL = Path(
    "logs/pure_imu_v0_raw6_bounded_access_20260828T060355Z/METADATA_PRESELECTION.json"
)
FROZEN_ACCESS_PRESELECTION_SHA256 = "a204ec85a9129351f54d21a365b680072bb9b10a3ffc49a0172be9b383d5b742"
FROZEN_ACCESS_DIR_REL = Path(
    "logs/pure_imu_v0_raw6_bounded_access_20260828T060355Z/CAPTURE2_ACTION_ACCESS"
)


def _canonical_hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode()).hexdigest()


def _require_immutable(path: Path) -> None:
    if path.stat().st_mode & 0o222:
        raise RuntimeError(f"metadata authority is writable: {path}")


def metadata_authorities(root: Path) -> dict[str, Any]:
    """Read only previously sealed C2 metadata; never touch capture payload."""

    root = Path(root).resolve()
    selection_path = root / FROZEN_SELECTION_REL
    access_preselection_path = root / FROZEN_ACCESS_PRESELECTION_REL
    for path, expected in (
        (selection_path, FROZEN_SELECTION_SHA256),
        (access_preselection_path, FROZEN_ACCESS_PRESELECTION_SHA256),
    ):
        _require_immutable(path)
        if sha256_file(path) != expected:
            raise RuntimeError(f"frozen metadata authority hash changed: {path}")
    selection = json.loads(selection_path.read_text(encoding="utf-8"))
    embedded = selection.pop("selection_sha256")
    if _canonical_hash(selection) != embedded:
        raise RuntimeError("frozen selection canonical hash changed")
    selection["selection_sha256"] = embedded
    capture = selection["captures"]["CAPTURE2"]
    if capture["capture_id"] != CAPTURE_ID or capture["capture_local_identity"] != C2_IDENTITY:
        raise RuntimeError("frozen C2 selection identity changed")
    selected = tuple(capture["selected_actions"])
    observed = tuple((row["action"], int(row["attempt"])) for row in selected)
    if observed != EPISODE_SELECTION:
        raise RuntimeError("frozen C2 chronological selection changed")

    access_rows: dict[str, Mapping[str, Any]] = {}
    access_hashes: dict[str, str] = {}
    raw_hashes: set[str] = set()
    for action, _ in EPISODE_SELECTION:
        path = root / FROZEN_ACCESS_DIR_REL / f"{action}.json"
        _require_immutable(path)
        row = json.loads(path.read_text(encoding="utf-8"))
        if row.get("capture_id") != CAPTURE_ID or row.get("action") != action:
            raise RuntimeError(f"{action}: historical C2 access authority changed")
        if row.get("complete_container_hash_recomputed") is not False:
            raise RuntimeError(f"{action}: historical access unexpectedly hashed the container")
        if row.get("hxx_payload_opened") is not False:
            raise RuntimeError(f"{action}: historical access reports Hxx payload use")
        if row.get("uwb_spatial_payload_consumed") is not False:
            raise RuntimeError(f"{action}: historical access reports UWB spatial use")
        raw_hashes.add(str(row["sealed_container_sha256_imported"]))
        access_rows[action] = row
        access_hashes[action] = sha256_file(path)
    if len(raw_hashes) != 1:
        raise RuntimeError("historical C2 access authorities disagree on raw container")
    return {
        "selection": selection,
        "selected_actions": selected,
        "historical_access": access_rows,
        "selection_path": selection_path,
        "selection_sha256": FROZEN_SELECTION_SHA256,
        "access_preselection_path": access_preselection_path,
        "access_preselection_sha256": FROZEN_ACCESS_PRESELECTION_SHA256,
        "historical_access_hashes": access_hashes,
        "sealed_raw_container_sha256_imported_not_recomputed": next(iter(raw_hashes)),
        "payload_opened": False,
        "payload_hashed": False,
    }


def _reconstruct_spec(root: Path, authorities: Mapping[str, Any]) -> dict[str, Any]:
    first = authorities["historical_access"][EPISODE_SELECTION[0][0]]
    seed = first["timing_safe_ceiling_seed_authority"]
    raw_path = Path(first["raw_path"]).resolve()
    expected_raw = (Path(root).resolve() / CAPTURE_REL / "system/fusion_continuous/fusion_host_raw.cobs.bin").resolve()
    if raw_path != expected_raw:
        raise RuntimeError("historical C2 raw path changed")
    return {
        "capture_id": CAPTURE_ID,
        "capture_root": str((Path(root).resolve() / CAPTURE_REL).resolve()),
        "raw_container": str(raw_path),
        "raw_sha256": first["sealed_container_sha256_imported"],
        "identity": dict(C2_IDENTITY),
        "timing_safe_ceiling_seed_source": {
            "path": seed["path"],
            "sha256": seed["sha256"],
            "field": seed["field"],
            "semantics": seed["semantics"],
        },
    }


def _reconstruct_action(
    selected: Mapping[str, Any], historical: Mapping[str, Any],
) -> dict[str, Any]:
    complete = dict(selected["complete_episode_bounds"])
    formal = dict(selected["formal_action_bounds"])
    boundary = historical["boundary_authority"]
    for key in (
        "start_byte_inclusive", "stop_byte_exclusive",
        "start_host_monotonic_ns", "stop_host_monotonic_ns_exclusive",
    ):
        if historical["episode_bounds"][key] != complete[key]:
            raise RuntimeError(f"{selected['action']}: historical complete bound changed: {key}")
        if historical["formal_action_bounds"][key] != formal[key]:
            raise RuntimeError(f"{selected['action']}: historical formal bound changed: {key}")
    return {
        "action": selected["action"],
        "operator_attempt_id": int(selected["attempt"]),
        "episode_bounds": complete,
        "formal_action_bounds": formal,
        "read_bracket": dict(historical["read_bracket"]),
        "event_source": boundary["event_source"],
        "event_source_sha256": boundary["event_source_sha256"],
        "manifest": boundary["manifest"],
        "manifest_sha256": boundary["manifest_sha256"],
        "forbidden_hxx_timing_intervals_ns": [
            list(row) for row in historical["timing_access"][
                "forbidden_golf_boxing_timing_intervals_ns"
            ]
        ],
        "forbidden_hxx_raw_byte_ranges": [
            list(row) for row in historical["forbidden_hxx_raw_byte_ranges"]
        ],
    }


def verify_fresh_seal(root: Path, seal_path: Path, expected_sha256: str | None = None) -> dict[str, Any]:
    root = Path(root).resolve()
    seal_path = Path(seal_path).resolve()
    _require_immutable(seal_path)
    observed_hash = sha256_file(seal_path)
    if expected_sha256 is not None and observed_hash != expected_sha256:
        raise RuntimeError("fresh C2 basis preselection hash mismatch")
    seal = json.loads(seal_path.read_text(encoding="utf-8"))
    if seal.get("schema") != "biospur-c2-basis-metadata-preselection-v1":
        raise RuntimeError("fresh C2 basis preselection schema changed")
    if seal.get("record_status") != "SEALED_BEFORE_ANY_NEW_PAYLOAD_OPEN_OR_HASH":
        raise RuntimeError("fresh C2 basis preselection was not sealed before payload")
    if seal.get("capture_id") != CAPTURE_ID or seal.get("scope") != "CAPTURE2_ONLY":
        raise RuntimeError("fresh preselection is not the independent C2 scope")
    if seal["payload_boundary"].get("new_payload_opened_before_seal") is not False:
        raise RuntimeError("fresh preselection admits pre-seal payload access")
    for relative, expected in seal["source_files_sha256"].items():
        path = root / relative
        if sha256_file(path) != expected:
            raise RuntimeError(f"sealed source/config changed after preselection: {relative}")
    return {**seal, "path": str(seal_path), "sha256": observed_hash}


def _stillness_json(calibration: StillnessCalibration) -> dict[str, Any]:
    return {
        "source_action": calibration.source_action,
        "information_scope": list(calibration.information_scope),
        "forbidden_unique_claims": list(calibration.forbidden_unique_claims),
        "nodes": {
            node: {
                key: value.tolist() if isinstance(value, np.ndarray) else value
                for key, value in asdict(row).items()
            }
            for node, row in calibration.by_node.items()
        },
    }


def audit_c2_raw6_decode_scope(access: Mapping[str, Any]) -> dict[str, Any]:
    """Validate the exact bounded-loader schema, without truthiness coercion."""

    decode = access.get("decode")
    if not isinstance(decode, Mapping):
        decode = {}
    raw_access = decode.get("raw_access")
    if not isinstance(raw_access, Mapping):
        raw_access = {}
    gates = {
        "only_ten_node_imu_payload_decoded": (
            decode.get("decoded_payload_classes") == ["TEN_NODE_IMU"]
        ),
        "uwb_spatial_field_list_present_and_empty": (
            decode.get("uwb_spatial_fields_decoded") == []
        ),
        "range_values_not_consumed": decode.get("range_values_consumed") is False,
        "anchor_geometry_not_consumed": decode.get("anchor_geometry_consumed") is False,
        "uwb_spatial_payload_not_consumed": (
            access.get("uwb_spatial_payload_consumed") is False
        ),
        "hxx_payload_not_opened": access.get("hxx_payload_opened") is False,
        "forbidden_byte_interval_not_touched": (
            raw_access.get("forbidden_interval_bytes_touched") is False
        ),
        "complete_container_not_scanned": (
            raw_access.get("complete_container_scan_attempted") is False
        ),
        "bounded_slice_hash_verified": raw_access.get("slice_sha256_verified") is True,
    }
    return {
        "schema": "biospur-c2-raw6-decode-scope-audit-v1",
        "gates": gates,
        "named_conflicts": [name for name, passed in gates.items() if not passed],
        "uwb_transport_envelopes_skipped_without_payload_decode": decode.get(
            "uwb_transport_envelopes_skipped_without_payload_decode"
        ),
        "pass": all(gates.values()),
    }


def load_c2_episodes(
    root: Path,
    run_dir: Path,
    seal_path: Path,
    config: Mapping[str, Any],
    *,
    expected_seal_sha256: str | None = None,
) -> tuple[tuple[Raw6Episode, ...], StillnessCalibration, dict[str, Any]]:
    """Open only the 19 sealed C2 episode slices after seal verification."""

    root = Path(root).resolve()
    run_dir = Path(run_dir).resolve()
    seal = verify_fresh_seal(root, seal_path, expected_seal_sha256)
    authorities = metadata_authorities(root)
    selected = authorities["selected_actions"]
    spec = _reconstruct_spec(root, authorities)
    access_dir = run_dir / "C2_ACTION_ACCESS"
    access_dir.mkdir(parents=True, exist_ok=False)
    episodes: list[Raw6Episode] = []
    stillness: StillnessCalibration | None = None
    orientation_frontend: ChronologicalOrientationFrontend | None = None
    bindings = []
    for index, selected_row in enumerate(selected):
        action_name = str(selected_row["action"])
        print(f"STAGE C2 bounded raw load {index + 1:02d}/19 {action_name} begin", flush=True)
        historical = authorities["historical_access"][action_name]
        action = _reconstruct_action(selected_row, historical)
        rows, access = load_capture2_calibration_episode(root, spec, action)
        formal = access["formal_action_bounds"]
        diagnostic = segment_five_phase_episode(
            rows,
            action=action_name,
            action_kind=(
                "STATIONARY_REFERENCE"
                if action_name in {"00_initial_still", "17_final_still"}
                else "MOVEMENT_OR_POSE"
            ),
            formal_start_global_ns=int(formal["start_global_time_ns"]),
            formal_stop_global_ns_exclusive=int(formal["stop_global_time_ns_exclusive"]),
            contract=config["episode_segmentation"],
            boundary_authority=access["boundary_authority"],
        )
        if diagnostic["EPISODE_COMPLETENESS"] != "PASS":
            raise RuntimeError(f"{action_name}: five-phase episode failed: {diagnostic['failures']}")
        if index == 0:
            if action_name != "00_initial_still":
                raise RuntimeError("initial still is not the first persistent-profile episode")
            stillness = estimate_stillness(rows, source_action=action_name)
            orientation_frontend = ChronologicalOrientationFrontend(
                stillness,
                rate_hz=int(config["sampling"]["working_rate_hz"]),
                capture_id="CAPTURE2",
            )
        if stillness is None:
            raise RuntimeError("capture-wide stillness state was not initialized")
        if orientation_frontend is None:
            raise RuntimeError("capture-level orientation frontend was not initialized")
        scope_audit = audit_c2_raw6_decode_scope(access)
        artifact = access_dir / f"{action_name}.json"
        if artifact.exists():
            raise FileExistsError(f"refusing to overwrite access evidence: {artifact}")
        dump_json(artifact, {
            **access,
            "five_phase_episode_diagnostic": diagnostic,
            "c2_raw6_decode_scope_audit": scope_audit,
        })
        artifact.chmod(0o444)
        if not scope_audit["pass"]:
            raise RuntimeError(
                f"{action_name}: forbidden payload class entered C2 ingest: "
                f"{scope_audit['named_conflicts']}"
            )
        episode = raw_episode_from_rows(
            action=action_name,
            rows_by_node=rows,
            identity=C2_IDENTITY,
            diagnostic=diagnostic,
            stillness=stillness,
            orientation_frontend=orientation_frontend,
            rate_hz=int(config["sampling"]["working_rate_hz"]),
        )
        decode = access["decode"]
        nodes = decode["nodes"]
        bindings.append({
            "action": action_name,
            "attempt": int(selected_row["attempt"]),
            "persistent_profile_partition": "CUMULATIVE_PROFILE",
            "access_artifact": str(artifact),
            "access_artifact_sha256": sha256_file(artifact),
            "bounded_slice_sha256": access["read_bracket"]["slice_sha256"],
            "node_payload_sha256": {
                node: row["payload_sha256"] for node, row in nodes.items()
            },
            "five_phase_status": diagnostic["EPISODE_COMPLETENESS"],
            "phase_counts": episode.audit["phase_counts"],
        })
        episodes.append(episode)
        print(f"STAGE C2 bounded raw load {index + 1:02d}/19 {action_name} complete", flush=True)
    observed_order = tuple((episode.action, EPISODE_SELECTION[index][1]) for index, episode in enumerate(episodes))
    if observed_order != EPISODE_SELECTION:
        raise RuntimeError("executed C2 episode order differs from immutable preselection")
    audit = {
        "schema": "biospur-c2-basis-bounded-raw-access-v1",
        "capture_id": CAPTURE_ID,
        "fresh_preselection": {"path": str(seal_path), "sha256": seal["sha256"]},
        "selected_actions": bindings,
        "single_persistent_profile": True,
        "whole_action_validation_partition": False,
        "held_out_strategy": config["sampling"]["held_out_strategy"],
        "full_raw_container_hash_recomputed": False,
        "hxx_payload_opened": False,
        "capture1_payload_opened": False,
        "capture3_payload_opened": False,
        "golf_boxing_payload_opened": False,
        "uwb_spatial_payload_consumed": False,
        "prior_quaternion_or_shared_ik_opened": False,
        "direct_fields": list(config["input_contract"]["fields"]),
        "stillness": _stillness_json(stillness),
        "orientation_frontend": orientation_frontend.audit(),
    }
    return tuple(episodes), stillness, audit
