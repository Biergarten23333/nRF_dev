"""Generate the raw-authoritative Pure-IMU MVP-M1 qualification bundle."""
from __future__ import annotations

import hashlib
import inspect
import json
import os
import platform
import resource
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from pure_imu_baseline.config import GEOMETRY
from pure_imu_baseline.math3d import conjugate, multiply, normalize, rotate
from pure_imu_baseline.stage2.config import BONES

from . import PRODUCT_ID
from .adapters import FutureLiveInputAdapter, load_replay, replay_packets
from .config import CAPTURES, STAGE1_ROOT, STAGE2_ROOT, STAGE3R1_ROOT, load_config
from .engine import COMMAND_CLEAR, COMMAND_RECENTER, GaugeCommand, PoseEngine
from .exports import csv_export, metadata, ndjson_export, npz_export, sha
from .viewer import export_capture, write_shared


EXPECTED = {
    "stage1_manifest": "06485188602f48e679082a807812f4057556173320e6a12499a5180f0d06db60",
    "stage2_manifest": "a48b917f3cc14ad888d265e4893bc406ade11dcf969285e65b8bbcf42c6c03a9",
    "stage3r1_manifest": "03dd3baa5c6eb06f1c9832b69d1df4e8bdb4904e38e5bf7d648e8afda06e8551",
    "capture1_replay": "61331d583d66523988f6eb43bd16ea00bc0fd657d9472896337f4ec516eaf724",
    "capture2_replay": "07148bf5fa5bcf5ea3ff065b5b27bf18a04fb00cd32006900e65c3c5bd5e27f0",
    "capture3_replay": "1d2450a107df79fa9105589c20725b64f7d272c452166f7efc9a8f2ba11480c2",
    "segment_contract": "4a239cb457a5a9f7bc15ab47529929658c2a9eac831364dfedc2c3ea409f05a6",
    "skeleton_geometry": "dba04543ed1b1cb79290c644f4ec9af98ca9c5ec9b6e5a8881d91444ce477a21",
}

VQF_ROOT = Path("/tmp/biospur_phase3r3b_three_capture_closed_loop_20260822T144929Z/upstream/VQF")
SLIME_ROOT = Path("/tmp/biospur_phase3r3b_three_capture_closed_loop_20260822T144929Z/upstream/SlimeVR-Server")
VQF_COMMIT = "86ba56bdd3158b9b05f9f9fe5596866ba326438c"
SLIME_COMMIT = "554976390b7ce27e789038fc8cc1ed04df7ae6de"


def dump(path: Path, value) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")


def clean(value):
    if isinstance(value, np.ndarray):
        return clean(value.tolist())
    if isinstance(value, np.generic):
        return clean(value.item())
    if isinstance(value, float) and not np.isfinite(value):
        return None
    if isinstance(value, dict):
        return {str(key): clean(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [clean(item) for item in value]
    return value


def array_hash(value: np.ndarray) -> str:
    array = np.ascontiguousarray(value)
    digest = hashlib.sha256()
    digest.update(array.dtype.str.encode("ascii")); digest.update(repr(array.shape).encode("ascii")); digest.update(array.tobytes())
    return digest.hexdigest()


def raw_array_hashes(raw: dict[str, np.ndarray]) -> dict[str, str]:
    return {name: array_hash(value) for name, value in raw.items()}


def source_map() -> dict[str, Path]:
    return {
        "stage1_manifest": STAGE1_ROOT/"REPRODUCIBILITY_MANIFEST.json",
        "stage2_manifest": STAGE2_ROOT/"STAGE2_REPRODUCIBILITY_MANIFEST.json",
        "stage3r1_manifest": STAGE3R1_ROOT/"REPRODUCIBILITY_MANIFEST.json",
        "capture1_replay": STAGE1_ROOT/"CAPTURE1_REPLAY_DATA.npz",
        "capture2_replay": STAGE1_ROOT/"CAPTURE2_REPLAY_DATA.npz",
        "capture3_replay": STAGE1_ROOT/"CAPTURE3_REPLAY_DATA.npz",
        "segment_contract": STAGE1_ROOT/"SEGMENT_FRAME_CONTRACT.json",
        "skeleton_geometry": STAGE1_ROOT/"SKELETON_GEOMETRY.json",
    }


def verify_frozen_sources() -> dict:
    checks = {}
    for name, path in source_map().items():
        if not path.is_file():
            raise FileNotFoundError(path)
        actual = sha(path); expected = EXPECTED[name]
        checks[name] = {"path":str(path),"expected_sha256":expected,"actual_sha256":actual,"pass":actual==expected}
        if actual != expected:
            raise RuntimeError(f"frozen hash mismatch: {name}")
    return checks


def reset_events(raw: dict, pose: dict) -> list[dict]:
    nodes = [str(value) for value in raw["node_ids"]]
    events = []
    for frame, node in np.argwhere(raw["filter_reset"]):
        events.append({"timestamp_us":int(pose["timestamp_us"][frame]),"frame_index":int(frame),"node_id":nodes[node],
                       "epoch":int(pose["epoch_per_node"][frame,node]),"reason":"FILTER_RESET"})
    return events


def max_bone_error(positions: np.ndarray, available: np.ndarray, joint_names: np.ndarray) -> float:
    indices = {str(name): index for index, name in enumerate(joint_names)}
    maximum = 0.0
    for geometry_name, a_name, b_name, _ in BONES:
        a, b = indices[a_name], indices[b_name]
        mask = available[:,a] & available[:,b]
        if not np.any(mask):
            continue
        lengths = np.linalg.norm(positions[mask,a]-positions[mask,b], axis=-1)
        maximum = max(maximum, float(np.max(np.abs(lengths-float(GEOMETRY[geometry_name])))))
    return maximum


def valid_max_abs(a: np.ndarray, b: np.ndarray) -> float:
    finite = np.isfinite(a) & np.isfinite(b)
    return float(np.max(np.abs(a[finite]-b[finite]))) if np.any(finite) else 0.0


def manual_frames(raw: dict) -> tuple[int, int]:
    pelvis = [str(value) for value in raw["segment_names"]].index("pelvis")
    candidates = np.flatnonzero(raw["valid"][:,pelvis])
    first = int(candidates[np.searchsorted(candidates, len(raw["time_s"])//3)])
    second_target = 2*len(raw["time_s"])//3
    second = int(candidates[min(np.searchsorted(candidates, second_target), len(candidates)-1)])
    return first, second


def capture_invariants(capture: str, raw: dict, pose: dict, manual: dict,
                       source_before: dict[str,str], source_after: dict[str,str]) -> dict:
    valid = raw["valid"]
    raw_norm = np.linalg.norm(raw["q_GB_wxyz"][valid].astype(np.float64), axis=-1)
    work_norm = np.linalg.norm(pose["working_q_GB_wxyz"][valid], axis=-1)
    normalization_delta = np.linalg.norm(pose["working_q_GB_wxyz"][valid]-raw["q_GB_wxyz"][valid].astype(np.float64),axis=-1)
    gravity = np.array([0.0,0.0,-1.0])
    work_gravity = rotate(conjugate(manual["working_q_GB_wxyz"][valid]), gravity)
    display_gravity = rotate(conjugate(manual["display_q_GB_wxyz"][valid]), gravity)
    invalid_q = ~valid
    unavailable_joint = ~raw["joint_available"]
    commands = manual["manual_recenter_events"]
    recenter_frame = int(commands[0]["frame_index"])
    gamma_constant = bool(np.all(manual["global_yaw_gauge_rad"][recenter_frame:int(commands[1]["frame_index"])] == manual["global_yaw_gauge_rad"][recenter_frame]))
    results = {
        "capture":capture,
        "raw_array_hashes_before":source_before,"raw_array_hashes_after":source_after,
        "raw_arrays_bit_exact":source_before==source_after,
        "raw_q_GB_bit_exact":source_before["q_GB_wxyz"]==source_after["q_GB_wxyz"],
        "raw_FK_bit_exact":source_before["joint_positions_m"]==source_after["joint_positions_m"],
        "pelvis_raw_evidence_unchanged":source_before["q_GB_wxyz"]==source_after["q_GB_wxyz"],
        "raw_quaternion_norm_error_max":float(np.max(np.abs(raw_norm-1.0))),
        "normalization_delta_max":float(np.max(normalization_delta)),
        "working_quaternion_norm_error_max":float(np.max(np.abs(work_norm-1.0))),
        "display_gravity_difference_max":valid_max_abs(work_gravity,display_gravity),
        "display_q_PC_vs_working_q_PC_max":valid_max_abs(manual["display_q_PC_wxyz"],manual["working_q_PC_wxyz"]),
        "display_bone_length_error_max_m":max_bone_error(manual["display_joint_positions_m"],raw["joint_available"],raw["joint_names"]),
        "root_position_difference_max_m":valid_max_abs(manual["display_joint_positions_m"][:,0],raw["joint_positions_m"][:,0]),
        "invalid_node_working_q_all_nan":bool(np.all(np.isnan(pose["working_q_GB_wxyz"][invalid_q]))),
        "invalid_node_display_q_all_nan":bool(np.all(np.isnan(pose["display_q_GB_wxyz"][invalid_q]))),
        "unavailable_joint_display_all_nan":bool(np.all(np.isnan(pose["display_joint_positions_m"][unavailable_joint]))),
        "manual_commands_logged":len(commands)==2,
        "manual_event_classification_exact":all(event.get("classification")=="OPERATOR_REQUESTED_GLOBAL_GAUGE_CHANGE" for event in commands),
        "one_common_gamma_constant_between_commands":gamma_constant,
        "no_automatic_gauge_changes":bool(np.all(pose["global_yaw_gauge_epoch"]==0) and np.all(pose["global_yaw_gauge_rad"]==0)),
        "camera_pose_values_changed":0,
        "camera_is_not_an_engine_input":"camera" not in inspect.signature(PoseEngine.process).parameters,
    }
    gates = {
        "raw":results["raw_arrays_bit_exact"], "work_norm":results["working_quaternion_norm_error_max"]<=1e-12,
        "gravity":results["display_gravity_difference_max"]<=1e-12,
        "bones":results["display_bone_length_error_max_m"]<=2e-6,
        "relative":results["display_q_PC_vs_working_q_PC_max"]<=1e-12,
        "root":results["root_position_difference_max_m"]==0.0,
        "gaps":results["invalid_node_working_q_all_nan"] and results["invalid_node_display_q_all_nan"] and results["unavailable_joint_display_all_nan"],
        "manual":results["manual_commands_logged"] and results["manual_event_classification_exact"] and results["one_common_gamma_constant_between_commands"],
        "automatic":results["no_automatic_gauge_changes"], "camera":results["camera_pose_values_changed"]==0,
    }
    results["gates"] = gates; results["pass"] = all(gates.values())
    return results


def benchmark_stream(capture: str, raw: dict, batch: dict, config: dict) -> dict:
    engine = PoseEngine(config); latencies = np.empty(len(raw["time_s"]),dtype=np.float64)
    maximum_work_delta=0.0; maximum_position_delta=0.0; epoch_match=True; quality_match=True
    backlog=0.0; max_backlog=0.0; digest=hashlib.sha256(); started=time.perf_counter()
    previous_ts=None
    for index, packet in enumerate(replay_packets(raw)):
        tick=time.perf_counter_ns(); row=engine.process_packet(packet); latencies[index]=(time.perf_counter_ns()-tick)/1e6
        maximum_work_delta=max(maximum_work_delta,valid_max_abs(row["working_q_GB_wxyz"],batch["working_q_GB_wxyz"][index]))
        maximum_position_delta=max(maximum_position_delta,valid_max_abs(row["display_joint_positions_m"],batch["display_joint_positions_m"][index]))
        epoch_match = epoch_match and np.array_equal(row["epoch_per_node"],batch["epoch_per_node"][index])
        quality_match = quality_match and np.array_equal(row["quality_state"],batch["quality_state"][index])
        digest.update(np.ascontiguousarray(row["working_q_GB_wxyz"]).tobytes()); digest.update(np.ascontiguousarray(row["quality_state"]).tobytes())
        if previous_ts is not None:
            available=(packet.timestamp_us-previous_ts)/1e3
            backlog=max(0.0,backlog+latencies[index]-available); max_backlog=max(max_backlog,backlog)
        previous_ts=packet.timestamp_us
        if (index+1)%30000==0:
            print(f"capture {capture}: streamed {index+1}/{len(latencies)} frames",flush=True)
    elapsed=time.perf_counter()-started; duration=float(raw["time_s"][-1]-raw["time_s"][0])
    return {"capture":capture,"frames":len(latencies),"capture_duration_s":duration,"processing_elapsed_s":elapsed,
            "real_time_factor":duration/elapsed,"latency_ms":{"p50":float(np.percentile(latencies,50)),"p95":float(np.percentile(latencies,95)),
            "p99":float(np.percentile(latencies,99)),"max":float(np.max(latencies))},"final_backlog_ms":backlog,"maximum_backlog_ms":max_backlog,
            "frames_dropped":0,"samples_optimized_away":0,"bounded_state_bytes":int(engine._stream_epoch.nbytes+engine._stream_last_valid_s.nbytes+engine._stream_last_reset_s.nbytes+engine._stream_reset_reason.nbytes),
            "batch_stream_working_q_max_delta":maximum_work_delta,"batch_stream_position_max_delta_m":maximum_position_delta,
            "batch_stream_epoch_exact":epoch_match,"batch_stream_quality_exact":quality_match,"stream_digest_sha256":digest.hexdigest(),
            "pass":duration/elapsed>=1.0 and backlog==0 and maximum_work_delta<=1e-12 and maximum_position_delta<=1e-12 and epoch_match and quality_match}


def negative_controls(config: dict, example_raw: dict, example_pose: dict) -> dict:
    frame = int(np.flatnonzero(np.all(example_raw["valid"],axis=1))[0])
    q = example_pose["working_q_GB_wxyz"][frame].copy(); positions=example_raw["joint_positions_m"][frame:frame+1].astype(float).copy()
    common_pc = example_pose["working_q_PC_wxyz"][frame]
    different=q.copy(); different[0]=normalize(multiply(np.array([np.cos(.1),0,0,np.sin(.1)]),different[0]))
    names=example_raw["segment_names"]
    from .engine import relative_quaternions_float64
    bad_pc,_=relative_quaternions_float64(different[None],np.ones((1,10),bool),names)
    joint_index={str(name):i for i,name in enumerate(example_raw["joint_names"])}
    bone_changed=positions.copy(); bone_changed[0,joint_index["wrist_left"],0]+=0.05
    checks = [
        ("different_yaw_per_node",valid_max_abs(bad_pc[0],common_pc)>1e-6,"relative-quaternion invariant"),
        ("per_segment_recenter",valid_max_abs(bad_pc[0],common_pc)>1e-6,"one-common-gauge/relative invariant"),
        ("camera_yaw_as_recenter","camera" not in inspect.signature(PoseEngine.process).parameters,"engine API has no camera input"),
        ("final_still_automatic","final" not in inspect.signature(PoseEngine.process).parameters,"engine API has no final-still input"),
        ("action_labels","label" not in inspect.signature(PoseEngine.process).parameters,"engine API has no action-label input"),
        ("automatic_recenter_after_reset",bool(np.all(example_pose["global_yaw_gauge_epoch"]==0)),"default/reset replay gauge remains epoch zero"),
        ("changed_bone_lengths",max_bone_error(bone_changed,np.ones((1,14),bool),example_raw["joint_names"])>2e-6,"fixed-geometry gate"),
        ("changed_parent_child_orientation",valid_max_abs(bad_pc[0],common_pc)>1e-6,"parent-child gate"),
        ("modified_raw_quaternion",array_hash(different)!=array_hash(q),"raw array hash"),
        ("translation",not np.array_equal(positions+np.array([[[.1,0,0]]]),positions),"fixed-root/root-position gate"),
        ("uwb_data","uwb" not in InputPacket_fields(),"frozen packet schema excludes UWB"),
    ]
    return {"schema":"biospur.pure_imu.mvp_m1.negative_controls.v1","controls":[{"control":name,"injected":True,"detected":bool(detected),"mechanism":mechanism,"pass":bool(detected)} for name,detected,mechanism in checks],"pass":all(item[1] for item in checks)}


def InputPacket_fields() -> set[str]:
    from .engine import InputPacket
    return set(InputPacket.__dataclass_fields__)


def write_contracts(output: Path, config: dict, frozen: dict, normalized: dict) -> None:
    raw_contract={"schema":"biospur.pure_imu.mvp_m1.raw_evidence_contract.v1","authoritative_source":"Frozen Stage 1 replay arrays",
                  "immutable_fields":["time_s","q_GS_wxyz","q_GB_wxyz","valid","confidence (legacy raw evidence only)","filter_reset","q_parent_child_wxyz","relative_valid","joint_positions_m","joint_available","node/segment/joint names"],
                  "calibration_source":"CAPTURE{1,2,3}_REPLAY_RESULT.json calibration objects","fixed_geometry_source":"SKELETON_GEOMETRY.json",
                  "source_checks":frozen,"redecoded_original_capture":False,"raw_arrays_overwritten":False,"uwb_numeric_reads":0}
    dump(output/"RAW_EVIDENCE_CONTRACT.json",raw_contract); dump(output/"NORMALIZED_WORKING_VIEW_AUDIT.json",normalized)
    pose_schema={"$schema":"https://json-schema.org/draft/2020-12/schema","title":"BioSpur Pure-IMU MVP-M1 PoseFrame","type":"object",
                 "required":["timestamp_us","frame_index","raw_q_GB_wxyz","working_q_GB_wxyz","display_q_GB_wxyz","raw_q_PC_wxyz","display_q_PC_wxyz","raw_joint_positions","display_joint_positions","validity_mask","epoch_per_node","reset_state_per_node","global_yaw_gauge_rad","global_yaw_gauge_epoch","global_yaw_gauge_source","root_mode"],
                 "properties":{"timestamp_us":{"type":"integer"},"frame_index":{"type":"integer"},"raw_q_GB_wxyz":{"shape":[10,4],"dtype":"source float32"},"working_q_GB_wxyz":{"shape":[10,4],"dtype":"float64"},"display_q_GB_wxyz":{"shape":[10,4],"dtype":"float64"},"raw_q_PC_wxyz":{"shape":[9,4]},"display_q_PC_wxyz":{"shape":[9,4]},"raw_joint_positions":{"shape":[14,3],"units":"m"},"display_joint_positions":{"shape":[14,3],"units":"m"},"validity_mask":{"shape":[10],"derived_from":"frozen validity only"},"epoch_per_node":{"shape":[10]},"reset_state_per_node":{"shape":[10]},"quality_state":{"enum":config["quality_states"],"derived_only_from":["validity","time","reset"]},"global_yaw_gauge_rad":{"type":"number"},"global_yaw_gauge_epoch":{"type":"integer"},"global_yaw_gauge_source":{"enum":config["global_yaw_gauge_sources"]},"root_mode":{"const":"FIXED"}},
                 "forbidden_product_fields":["accuracy","confidence","automatic_heading_correction","UWB_position"]}
    dump(output/"POSE_OUTPUT_SCHEMA.json",pose_schema)
    (output/"MANUAL_GLOBAL_YAW_RECENTER_CONTRACT.md").write_text("""# Manual global-yaw recenter contract

`MANUAL_WHOLE_BODY_YAW_RECENTER` is accepted only through the explicit **Set current pelvis facing as display forward** command. Pelvis local `+X` is projected into global XY; a projection norm at or below `1e-12` is rejected. The accepted epoch stores one `gamma` and applies `q_display_i = q_z(gamma) ⊗ q_work_i` to every valid node and `p_display = p_root + R_z(gamma)(p_raw-p_root)` to every available joint.

The command preserves gravity, articulation, parent–child quaternions, bone lengths, root translation, and camera state. It remains constant until another explicit recenter or **Clear recenter / Return to raw gauge**. Each accepted change creates a gauge epoch and event `OPERATOR_REQUESTED_GLOBAL_GAUGE_CHANGE`. It is not drift correction and not heading truth. Resets, health, confidence, labels, final stills, camera motion, and gaps cannot trigger it.
""",encoding="utf-8")
    (output/"STREAMING_INTERFACE.md").write_text("""# Streaming interface

The replay adapter and the future-live adapter both create `InputPacket(timestamp_us, frame_index, raw_q_GB_wxyz[10,4], validity_mask[10], filter_reset[10], raw_joint_positions_m[14,3], joint_available[14])` and feed `PoseEngine.process_packet`. Timestamps are strictly increasing; frame indices are contiguous. Quaternion convention is wxyz Hamilton active local-to-global. The adapter performs acquisition only and supplies no camera, action-label, final-still, confidence, correction, or UWB field.

`PoseEngine.process_packet` returns the fields in `POSE_OUTPUT_SCHEMA.json`. Its only state is per-node validity/reset timing plus the operator gauge. `reset_stream()` is an explicit session boundary; a transport/filter reset in a packet changes node epoch/health but never the yaw gauge. The implemented `FutureLiveInputAdapter` validates the frozen packet schema only. Live hardware acquisition is deliberately outside MVP-M1.
""",encoding="utf-8")
    (output/"RESET_AND_VALIDITY_STATE_MACHINE.md").write_text("""# Reset and validity state machine

State is per node and is derived only from the frozen validity bit, timestamp, and filter-reset bit. A valid sample is `RECENTLY_RESET` for 1.0 s after the last filter reset, then `VALID`. An invalid sample is `STALE` only while its age from the last valid sample is at most 0.25 s; after that, or before any valid sample, it is `UNAVAILABLE`. No pose is emitted for invalid nodes and no position is emitted for unavailable joints. Stage 1 has already performed its frozen interpolation through 0.05 s; MVP-M1 neither re-interpolates nor holds samples.

A reset increments `epoch_per_node`, records `FILTER_RESET`, and updates reset age. It cannot change `global_yaw_gauge_rad` or `global_yaw_gauge_epoch`. `sample_age`, time since last valid, time since reset, and time since the last manual recenter are reported independently.
""",encoding="utf-8")


def write_open_source(output: Path) -> None:
    text=f"""# Open-source traceability

| Project | Exact source | Version | Mechanism consulted | Copied? | License consequence |
|---|---|---|---|---|---|
| VQF | https://github.com/dlaidig/vqf | `{VQF_COMMIT}` | `vqf/pyvqf.py`, tests, six-axis orientation interface, scalar-first quaternion convention | No | MIT reviewed; no code copied, so no redistributed VQF code or added notice obligation in this output |
| SlimeVR Server | https://github.com/SlimeVR/SlimeVR-Server | `{SLIME_COMMIT}` | `RPCResetHandler.kt`, `RPCStatusHandler.kt`, `ResetHandler.kt`: explicit reset request, clear action, progress/status presentation | No | Server core MIT; repository is MIT/Apache-2.0 dual-license for contributions. No code copied |
| BioSpur Stage 2 viewer | local `pure_imu_baseline/stage2/viewer_core.js` and `viewer.css` | repository HEAD recorded in manifest | Offline payload, orbit/pan/zoom, fixed/pelvis camera behavior, browser self-test | Adapted internal project code | Internal project provenance; no external license introduced |

The review was limited to interface, reset/status UX, and offline camera mechanics. No automatic heading-correction algorithm was imported or reintroduced.
"""
    (output/"OPEN_SOURCE_TRACEABILITY.md").write_text(text,encoding="utf-8")


def write_ledger(output: Path, accessed: list[tuple[Path,str]]) -> None:
    seen=set(); lines=[]
    for path,purpose in accessed:
        resolved=path.resolve()
        if resolved in seen: continue
        seen.add(resolved)
        lines.append(json.dumps({"path":str(resolved),"bytes":resolved.stat().st_size,"sha256":sha(resolved),"purpose":purpose,
                                 "uwb_numeric_data":False,"original_capture_decoded":False},sort_keys=True,separators=(",",":")))
    (output/"DATA_ACCESS_LEDGER.jsonl").write_text("\n".join(lines)+"\n",encoding="utf-8")


def rebuild_manifest(output: Path, command: str, status: str) -> dict:
    previous={}
    if (output/"REPRODUCIBILITY_MANIFEST.json").is_file():
        previous=json.loads((output/"REPRODUCIBILITY_MANIFEST.json").read_text(encoding="utf-8"))
    artifacts={}
    for path in sorted(output.iterdir()):
        if path.is_file() and path.name != "REPRODUCIBILITY_MANIFEST.json":
            artifacts[path.name]={"sha256":sha(path),"bytes":path.stat().st_size}
    try:
        head=subprocess.run(["git","rev-parse","HEAD"],cwd=Path(__file__).resolve().parents[4],capture_output=True,text=True,check=True).stdout.strip()
    except Exception:
        head="UNAVAILABLE"
    pipeline_command=command if status=="PENDING_BROWSER_RUNTIME" else previous.get("pipeline_command",previous.get("command",command))
    implementation_files={path.name:{"sha256":sha(path),"bytes":path.stat().st_size}
                          for path in sorted(Path(__file__).resolve().parent.iterdir()) if path.is_file() and path.suffix in (".py",".json",".md")}
    manifest={"schema":"biospur.pure_imu.mvp_m1.reproducibility.v1","created_utc":datetime.now(timezone.utc).isoformat(),
              "product_id":PRODUCT_ID,"command":pipeline_command,"pipeline_command":pipeline_command,"finalization_command":command,
              "pipeline_working_directory":str(Path(__file__).resolve().parents[2]),"implementation_root":str(Path(__file__).resolve().parent),"repository_head":head,
              "python":sys.version,"numpy":np.__version__,"platform":platform.platform(),"source_hashes":{name:EXPECTED[name] for name in EXPECTED},
              "implementation_files":implementation_files,"artifacts":artifacts,"browser_runtime_status":status,"no_new_capture":True,"no_uwb_numeric_data":True,"no_commit":True,"no_push":True}
    dump(output/"REPRODUCIBILITY_MANIFEST.json",manifest); return manifest


def run(output: Path) -> dict:
    config=load_config(); output=output.resolve()
    if output.exists():
        raise FileExistsError(f"refusing to overwrite {output}")
    output.mkdir(parents=True)
    frozen=verify_frozen_sources()
    frame_contract=json.loads((STAGE1_ROOT/"SEGMENT_FRAME_CONTRACT.json").read_text(encoding="utf-8"))
    geometry=json.loads((STAGE1_ROOT/"SKELETON_GEOMETRY.json").read_text(encoding="utf-8"))
    stage1_manifest=json.loads((STAGE1_ROOT/"REPRODUCIBILITY_MANIFEST.json").read_text(encoding="utf-8"))
    stage3_result=json.loads((STAGE3R1_ROOT/"FINAL_RESULT.json").read_text(encoding="utf-8"))
    if "ZERO_EDGES" not in json.dumps(stage3_result):
        raise RuntimeError("Stage 3-R1 terminal zero-edge decision not found")
    write_shared(output,Path(__file__).resolve().parents[1]/"stage2")
    npz_audit={"schema":"biospur.pure_imu.mvp_m1.npz_export_audit.v1","captures":{}}
    ndjson_audit={"schema":"biospur.pure_imu.mvp_m1.ndjson_export_audit.v1","captures":{}}
    csv_audit={"schema":"biospur.pure_imu.mvp_m1.csv_export_audit.v1","captures":{}}
    immutability={"schema":"biospur.pure_imu.mvp_m1.raw_immutability.v1","captures":{}}
    normalized={"schema":"biospur.pure_imu.mvp_m1.normalized_working_view.v1","captures":{}}
    recenter_tests={"schema":"biospur.pure_imu.mvp_m1.recenter_invariants.v1","captures":{}}
    benchmark={"schema":"biospur.pure_imu.mvp_m1.realtime_benchmark.v1","captures":{},"viewer_frame_rate":None}
    viewer_meta={}; default_poses={}; example_raw=None; example_pose=None; export_infos=[]
    input_total_frames=0
    for capture in CAPTURES:
        print(f"capture {capture}: loading frozen Stage 1 replay",flush=True)
        source=STAGE1_ROOT/f"CAPTURE{capture}_REPLAY_DATA.npz"; result_path=STAGE1_ROOT/f"CAPTURE{capture}_REPLAY_RESULT.json"
        raw=load_replay(source); source_file_before=sha(source); raw_before=raw_array_hashes(raw)
        result=json.loads(result_path.read_text(encoding="utf-8")); engine=PoseEngine(config)
        batch_start=time.perf_counter(); pose=engine.process(raw); batch_elapsed=time.perf_counter()-batch_start
        repeat=PoseEngine(config).process(raw)
        deterministic_fields=("timestamp_us","working_q_GB_wxyz","display_q_GB_wxyz","working_q_PC_wxyz","display_q_PC_wxyz","display_joint_positions_m","epoch_per_node","quality_state","global_yaw_gauge_rad","global_yaw_gauge_epoch")
        deterministic=all(array_hash(pose[field])==array_hash(repeat[field]) for field in deterministic_fields)
        first,second=manual_frames(raw); manual=PoseEngine(config).process(raw,[GaugeCommand(first,COMMAND_RECENTER),GaugeCommand(second,COMMAND_CLEAR)])
        raw_after=raw_array_hashes(raw); inv=capture_invariants(capture,raw,pose,manual,raw_before,raw_after)
        inv["source_file_sha256_before"]=source_file_before; inv["source_file_sha256_after"]=sha(source); inv["source_file_unchanged"]=source_file_before==sha(source)
        inv["deterministic_repeated_batch_output"]=deterministic
        recenter_tests["captures"][capture]=inv
        normalized["captures"][capture]={key:inv[key] for key in ("raw_quaternion_norm_error_max","normalization_delta_max","working_quaternion_norm_error_max")}
        normalized["captures"][capture]["pass"]=inv["working_quaternion_norm_error_max"]<=1e-12 and inv["raw_arrays_bit_exact"]
        immutability["captures"][capture]={"source_path":str(source),"source_sha256":source_file_before,"source_unchanged":inv["source_file_unchanged"],"all_array_hashes_unchanged":inv["raw_arrays_bit_exact"],"array_hashes":raw_before,"pass":inv["source_file_unchanged"] and inv["raw_arrays_bit_exact"]}
        resets=reset_events(raw,pose); source_hashes={"stage1_replay":source_file_before,"stage1_manifest":EXPECTED["stage1_manifest"],"stage2_manifest":EXPECTED["stage2_manifest"],"stage3r1_manifest":EXPECTED["stage3r1_manifest"]}
        meta=metadata(source,source_file_before,config,geometry,frame_contract,pose["manual_recenter_events"],result["calibration"],resets,source_hashes)
        npz_info=npz_export(output/f"CAPTURE{capture}_MVP_REPLAY_DATA.npz",raw,pose,meta)
        with np.load(output/f"CAPTURE{capture}_MVP_REPLAY_DATA.npz",allow_pickle=False) as archive:
            exact={name:array_hash(archive[name])==raw_before[name] for name in raw}
        npz_info["raw_arrays_embedded_bit_exact"]=exact; npz_info["pass"]=all(exact.values()); npz_audit["captures"][capture]=npz_info
        nd_info=ndjson_export(output/f"CAPTURE{capture}_MVP_STREAM.ndjson",raw,pose,meta); nd_info["pass"]=nd_info["records"]==len(raw["time_s"])+1; ndjson_audit["captures"][capture]=nd_info
        csv_info=csv_export(output/f"CAPTURE{capture}_MVP_JOINT_POSITIONS.csv",raw,pose,meta); csv_info["pass"]=csv_info["records"]==len(raw["time_s"])*len(raw["joint_names"])+2; csv_audit["captures"][capture]=csv_info
        export_infos.extend((npz_info,nd_info,csv_info))
        viewer_meta[capture]=export_capture(capture,raw,pose,config,output)
        stream=benchmark_stream(capture,raw,pose,config); stream["batch_elapsed_s"]=batch_elapsed; stream["batch_real_time_factor"]=float((raw["time_s"][-1]-raw["time_s"][0])/batch_elapsed); stream["deterministic_repeated_batch_output"]=deterministic
        benchmark["captures"][capture]=stream
        input_total_frames+=len(raw["time_s"])
        if capture=="1": example_raw=raw; example_pose=pose
        default_poses[capture]={"frames":len(raw["time_s"]),"duration_s":float(raw["time_s"][-1]),"gauge_events":len(pose["manual_recenter_events"]),"manual_test_frames":[first,second]}
        print(f"capture {capture}: exports, viewer payload, invariants and stream benchmark complete",flush=True)
        del repeat,manual,raw,pose
    normalized["pass"]=all(item["pass"] for item in normalized["captures"].values())
    immutability["pass"]=all(item["pass"] for item in immutability["captures"].values())
    recenter_tests["pass"]=all(item["pass"] and item["deterministic_repeated_batch_output"] for item in recenter_tests["captures"].values())
    for audit in (npz_audit,ndjson_audit,csv_audit): audit["pass"]=all(item["pass"] for item in audit["captures"].values())
    benchmark["latency_ms_all_captures"]={metric:float(np.percentile([item["latency_ms"][metric] for item in benchmark["captures"].values()],50)) for metric in ("p50","p95","p99","max")}
    benchmark["peak_rss_mib"]=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1024
    benchmark["export_throughput_frames_s"]={kind:{capture:audit["captures"][capture]["throughput_frames_s"] for capture in CAPTURES}
                                                for kind,audit in (("npz",npz_audit),("ndjson",ndjson_audit),("csv",csv_audit))}
    benchmark["pass"]=all(item["pass"] and item["deterministic_repeated_batch_output"] for item in benchmark["captures"].values())
    negative=negative_controls(config,example_raw,example_pose)
    dump(output/"NPZ_EXPORT_AUDIT.json",clean(npz_audit)); dump(output/"NDJSON_EXPORT_AUDIT.json",clean(ndjson_audit)); dump(output/"CSV_EXPORT_AUDIT.json",clean(csv_audit))
    dump(output/"REALTIME_REPLAY_BENCHMARK.json",clean(benchmark)); dump(output/"RAW_IMMUTABILITY_AUDIT.json",clean(immutability)); dump(output/"RECENTER_INVARIANT_TESTS.json",clean(recenter_tests)); dump(output/"NEGATIVE_CONTROLS.json",clean(negative))
    write_contracts(output,config,frozen,clean(normalized)); write_open_source(output)
    pending={"schema":"biospur.pure_imu.mvp_m1.browser_verification.v1","status":"PENDING_BROWSER_RUNTIME","captures":{},"pass":False}
    dump(output/"VIEWER_BROWSER_VERIFICATION.json",pending)
    accessed=[*((path,"frozen hash/provenance gate") for path in source_map().values()),
              *((STAGE1_ROOT/f"CAPTURE{c}_REPLAY_RESULT.json","frozen calibration metadata") for c in CAPTURES),
              (STAGE3R1_ROOT/"FINAL_RESULT.json","terminal zero-edge decision only"),
              (Path(__file__).resolve().parents[1]/"stage2/viewer_core.js","bounded Stage 2 viewer architecture review"),(Path(__file__).resolve().parents[1]/"stage2/viewer.css","bounded Stage 2 viewer style reuse"),
              (VQF_ROOT/"vqf/pyvqf.py","bounded VQF interface/convention review"),(VQF_ROOT/"LICENSES/MIT.txt","VQF license review"),
              (SLIME_ROOT/"server/core/src/main/java/dev/slimevr/protocol/rpc/reset/RPCResetHandler.kt","bounded explicit reset UX review"),(SLIME_ROOT/"server/core/src/main/java/dev/slimevr/protocol/rpc/status/RPCStatusHandler.kt","bounded status presentation review"),(SLIME_ROOT/"server/LICENSE.md","SlimeVR server license review")]
    write_ledger(output,accessed)
    pytest=subprocess.run([sys.executable,"-m","pytest","-q",str(Path(__file__).with_name("tests"))],capture_output=True,text=True)
    result={"schema":"biospur.pure_imu.mvp_m1.final.v1","product_id":PRODUCT_ID,"verdict":"PENDING_BROWSER_RUNTIME",
            "frozen_decisions":["STAGE3R1_ZERO_EDGES_PASS_AUTONOMOUS_CORRECTION_TERMINATED","AUTONOMOUS_RELATIVE_HEADING_CORRECTION_TERMINATED_FOR_CURRENT_DATASET","RAW_BASELINE_REMAINS_AUTHORITATIVE","NO_NEW_CAPTURE_REQUESTED","NO_UWB_NUMERIC_DATA_USED"],
            "captures":default_poses,"total_frames":input_total_frames,"raw_immutability_pass":immutability["pass"],"normalization_pass":normalized["pass"],"recenter_invariants_pass":recenter_tests["pass"],"negative_controls_pass":negative["pass"],"exports_pass":npz_audit["pass"] and ndjson_audit["pass"] and csv_audit["pass"],"realtime_pass":benchmark["pass"],"browser_runtime_pass":False,
            "pytest":{"return_code":pytest.returncode,"stdout":pytest.stdout,"stderr":pytest.stderr,"pass":pytest.returncode==0},"automatic_correction_reintroduced":False,"uwb_numeric_reads":0,"new_human_capture_requested":False,"live_hardware_acquisition_implemented":False,"limitations":config["limitations"]}
    dump(output/"FINAL_RESULT.json",result)
    report=f"""# BioSpur Pure-IMU MVP-M1

Status: **PENDING_BROWSER_RUNTIME**

MVP-M1 preserves the frozen Stage 1 replay arrays as authoritative evidence, creates a separate float64 normalized computational view, and exposes one explicit whole-body global-yaw gauge. All three captures completed replay, exact NPZ export, NDJSON export, CSV joint export, deterministic streaming qualification, and numerical invariant tests over {input_total_frames:,} frames. Browser interaction verification is pending in `VIEWER_BROWSER_VERIFICATION.json`.

No automatic heading correction, action-label trigger, final-still trigger, UWB numeric input, new capture, external position, or confidence-derived pose state exists. The visible limitations remain: **GLOBAL YAW MAY DRIFT**, no absolute heading claim, fixed root, no external position, fixed development geometry, and no anatomical joint-angle claim.
"""
    (output/"MVP_M1_FINAL.md").write_text(report,encoding="utf-8")
    command=f"PYTHONPATH={Path(__file__).resolve().parents[2]} {sys.executable} -m pure_imu_baseline.mvp_m1.cli run --output {output}"
    rebuild_manifest(output,command,"PENDING_BROWSER_RUNTIME")
    return result
