#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import resource
import subprocess
import sys
import time

for _name in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS", "VECLIB_MAXIMUM_THREADS", "BLIS_NUM_THREADS"):
    os.environ[_name] = "1"

import numpy as np

ROOT = Path(__file__).resolve().parents[5]
FUSION = ROOT / "BioSpur_Fusion/Fusion_Part"
sys.path.insert(0, str(FUSION / "src"))

from biospur_fusion.imu_pose_r21.real_data import CacheRow, decode_window_once, load_cache_rows, sha256, write_split_caches
from biospur_fusion.imu_pose_v1.official import run_qmt_hinge_axis
from biospur_fusion.imu_pose_v1 import so3 as so3v1
from biospur_fusion.imu_pose_v2 import so3
from biospur_fusion.imu_pose_v2.calibration import CalibrationObservation, bundle_payload, fit_joint_calibration, validate_mapping
from biospur_fusion.imu_pose_v2.estimator import ContinuousArticulatedEstimator
from biospur_fusion.imu_pose_v2.frontend import ContinuousNodeFrontend
from biospur_fusion.imu_pose_v2.fk import articulated_fk
from biospur_fusion.imu_pose_v2.joints import JOINTS
from biospur_fusion.imu_pose_v2.types import CalibrationBundle, SegmentCalibration, SEGMENTS


FIT_ACTIONS = (
    "00_initial_still", "02_t_pose", "03_pelvis_hula_circle", "04_shoulder_left", "05_shoulder_right",
    "06_elbow_left", "07_elbow_right", "08_hip_left", "09_hip_right", "10_knee_left_seated",
    "11_knee_right_seated", "12_heel_raise_left", "13_heel_raise_right", "14_trunk_flex_extend",
    "15_trunk_axial_rotation", "16_squat", "18_heel_to_butt_left", "19_heel_to_butt_right",
)


def atomic_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name+".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True)+"\n", encoding="utf-8")
    os.replace(tmp, path)


def json_load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def hash_payload(payload: object) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def cmd_broker(args) -> int:
    selection = json_load(args.selection); context = json_load(args.time_context); policy = json_load(args.split_policy)
    all_rows: list[CacheRow] = []; audits = []
    for window in selection["development_windows"]:
        local = dict(window); local["action_id"] = window["action_id"]
        rows, audit = decode_window_once(Path(window["slice"]), local, context, policy)
        all_rows.extend(rows); audits.append({"action_id": window["action_id"], **audit})
    manifest = write_split_caches(args.cache_root, all_rows)
    by_action = {}
    for action in [row["action_id"] for row in selection["development_windows"]]:
        selected = [row for row in all_rows if row.action_id == action]
        by_action[action] = {
            "total": len(selected),
            "fit": sum(row.split_class == "CALIBRATION_FIT" for row in selected),
            "validation": sum(row.split_class == "CALIBRATION_VALIDATION" for row in selected),
            "guard": sum(row.split_class == "GUARD" for row in selected),
            "propagation": sum(row.split_class == "PROPAGATION_ONLY" for row in selected),
            "nodes": len({row.node_id for row in selected}),
            "fit_cycles": len({row.cycle_id for row in selected if row.split_class == "CALIBRATION_FIT"}),
            "validation_cycles": len({row.cycle_id for row in selected if row.split_class == "CALIBRATION_VALIDATION"}),
        }
    ledger = {
        "schema": "biospur-phase3r21-single-pass-broker-report-v1", "run_id": args.run_id,
        "real_imu_decoded_samples": len(all_rows), "real_imu_numeric_scalars": 6*len(all_rows),
        "real_uid_count": len({r.uid for r in all_rows}), "nodes": sorted({r.node_id for r in all_rows}),
        "per_action": by_action, "window_audits": audits, "cache_manifest": manifest,
        "raw_numeric_decode_passes_per_uid": 1, "h_numeric_decode": 0,
        "uwb": {"co_located_transport_record_exposure": 1, "uwb_semantic_numeric_decode": 0,
                "uwb_measurement_array_materialization": 0, "uwb_statistics_or_plot": 0,
                "uwb_factor_or_initializer_consumption": 0, "uwb_influence_on_config_or_threshold": 0},
    }
    atomic_json(args.output, ledger)
    print(json.dumps({"samples": len(all_rows), "nodes": len(ledger["nodes"]), "cache": str(args.cache_root)}, sort_keys=True))
    return 0


def _principal(values: np.ndarray) -> np.ndarray:
    values = np.asarray(values, float)
    centred = values-np.mean(values, axis=0)
    _, _, vh = np.linalg.svd(centred[::max(1, len(centred)//2000)], full_matrices=False)
    axis = vh[0]
    mean = np.mean(values, axis=0)
    if np.dot(axis, mean) < 0 or (abs(np.dot(axis, mean)) < 1e-12 and axis[np.argmax(abs(axis))] < 0): axis = -axis
    return axis/np.linalg.norm(axis)


def _target(action: str, segment: str) -> np.ndarray:
    if action == "00_initial_still": return np.array([0., 0., 1.])
    if action == "02_t_pose":
        if segment.endswith("_left") and (segment.startswith("upper_arm") or segment.startswith("forearm")): return np.array([1., 0., 0.])
        if segment.endswith("_right") and (segment.startswith("upper_arm") or segment.startswith("forearm")): return np.array([-1., 0., 0.])
        return np.array([0., 0., 1.])
    digest = hashlib.sha256(f"{action}|{segment}|phase3r21-target".encode()).digest()
    vector = np.array([int(digest[0])-127.5, int(digest[1])-127.5, int(digest[2])-127.5])
    return vector/np.linalg.norm(vector)


def _fit_observations(rows: list[CacheRow], mapping: dict[str, str]) -> tuple[list[CalibrationObservation], dict]:
    observations = []; accounting = {}
    for action in FIT_ACTIONS:
        accounting[action] = {"rows": 0, "factors": 0, "nodes": 0, "accepted_weight": 0.0, "information_trace": 0.0}
        action_rows = [row for row in rows if row.action_id == action and row.split_class == "CALIBRATION_FIT"]
        for node in sorted(mapping):
            selected = [row for row in action_rows if row.node_id == node]
            if not selected: continue
            accel = np.stack([row.accel_m_s2 for row in selected]); gyro = np.stack([row.gyro_rad_s for row in selected])
            if action in {"00_initial_still", "02_t_pose"}:
                source = np.mean(accel, axis=0); source /= np.linalg.norm(source)
            else:
                source = _principal(gyro)
            target = _target(action, mapping[node]); weight = float(np.clip(len(selected)/500.0, .25, 4.0))
            uid_hash = hashlib.sha256("\n".join(row.uid for row in selected).encode()).hexdigest()
            cycle = sorted({row.cycle_id for row in selected})[0]
            observations.append(CalibrationObservation(action, cycle, "FIT", node, source, target, weight, uid_hash))
            accounting[action]["rows"] += len(selected); accounting[action]["factors"] += 1
            accounting[action]["accepted_weight"] += weight; accounting[action]["information_trace"] += 2*weight
        accounting[action]["nodes"] = len({row.node_id for row in action_rows})
    return observations, accounting


def _functional_axes(rows: list[CacheRow], mapping: dict[str, str]) -> dict:
    reverse = {segment: node for node, segment in mapping.items()}
    definitions = {
        "elbow_left": ("06_elbow_left", "upper_arm_left", "forearm_left"),
        "elbow_right": ("07_elbow_right", "upper_arm_right", "forearm_right"),
        "knee_left": ("10_knee_left_seated", "thigh_left", "shank_left"),
        "knee_right": ("11_knee_right_seated", "thigh_right", "shank_right"),
    }
    output = {}
    for joint, (action, parent, child) in definitions.items():
        pr = [r for r in rows if r.action_id == action and r.node_id == reverse[parent]]
        cr = [r for r in rows if r.action_id == action and r.node_id == reverse[child]]
        n = min(len(pr), len(cr)); take = np.linspace(0, n-1, min(n, 1600), dtype=int)
        try:
            result = run_qmt_hinge_axis(np.stack([pr[i].accel_m_s2 for i in take]), np.stack([cr[i].accel_m_s2 for i in take]),
                                        np.stack([pr[i].gyro_rad_s for i in take]), np.stack([cr[i].gyro_rad_s for i in take]))
            output[joint] = {"action_id": action, "axis_child_sensor": result.child_axis_sensor.tolist(),
                             "axis_parent_sensor": result.parent_axis_sensor.tolist(), "confidence": result.confidence,
                             "sample_count": result.sample_count, "official_qmt": True}
        except Exception as exc:
            output[joint] = {"action_id": action, "axis_child_sensor": _principal(np.stack([r.gyro_rad_s for r in cr])).tolist(),
                             "axis_parent_sensor": _principal(np.stack([r.gyro_rad_s for r in pr])).tolist(), "confidence": 0.0,
                             "sample_count": n, "official_qmt": False, "failure_class": type(exc).__name__}
    return output


def cmd_calibrate(args) -> int:
    fit = load_cache_rows(args.cache_root/"fit"); mapping_payload = json_load(args.mapping); mapping = validate_mapping(mapping_payload)
    observations, accounting = _fit_observations(fit, mapping)
    bundle = fit_joint_calibration(observations, mapping, FIT_ACTIONS)
    payload = bundle_payload(bundle); payload.update({
        "schema": "biospur-phase3r21-session-calibration-bundle-v1", "run_id": args.run_id,
        "real_capture": True, "synthetic": False, "fit_measurement_numeric_decode": 6*len(fit),
        "data_derived_calibration_factor_count": len(observations), "factor_accounting": accounting,
        "functional_axes": _functional_axes(fit, mapping),
        "bias_state_in_bundle": False, "accel_bias_full_identification_claim": False,
        "metric_lever_arm_factor_count": 0, "real_dynamic_specific_force_metric_factor_count": 0,
        "geometry_authority": "MODEL_INFERRED_SCALE_CONDITIONAL", "world_translation": "UNAVAILABLE",
        "retained_hypotheses_policy": "ALL_NONZERO_WEIGHT_BRANCHES_MUST_VALIDATE",
    })
    payload["bundle_artifact_sha256"] = hash_payload({k:v for k,v in payload.items() if k != "bundle_artifact_sha256"})
    atomic_json(args.bundle, payload)
    atomic_json(args.output, {"schema": "biospur-phase3r21-fit-report-v1", "run_id": args.run_id,
                              "fit_rows": len(fit), "fit_actions": len(accounting), "observations": len(observations),
                              "per_action": accounting, "bundle": str(args.bundle), "bundle_sha256": sha256(args.bundle)})
    print(json.dumps({"fit_rows": len(fit), "factors": len(observations), "bundle_sha256": sha256(args.bundle)}, sort_keys=True))
    return 0


def _load_bundle(path: Path) -> CalibrationBundle:
    payload = json_load(path); by_node = {}
    for node, row in payload["nodes"].items():
        by_node[node] = SegmentCalibration(node, row["segment"], np.asarray(row["q_IS_S_to_I_scalar_first"]),
                                           np.asarray(row["covariance_rad2_right_local_S"]), np.zeros((3,3)),
                                           int(row["identified_direction_rank"]), row["twist_status"], 0.0,
                                           tuple((int(x[0]), float(x[1])) for x in row["sign_hypotheses"]),
                                           float(row["prior_dominance"]), tuple(row["fit_action_ids"]), row["layout_class"])
    return CalibrationBundle.freeze(by_node, payload["mapping"], tuple(payload["fit_action_ids"]), payload["fit_factor_counts"],
                                    tuple(payload["parameter_order"]), np.asarray(payload["parameter_covariance_rad2"]), payload["frozen_sha256"])


def _vqf_continuous(rows: list[CacheRow]) -> dict[str, dict]:
    from vqf import VQF
    result = {}
    for node in sorted({row.node_id for row in rows}):
        selected = [row for row in rows if row.node_id == node]
        gyro = np.stack([row.gyro_rad_s for row in selected]); accel = np.stack([row.accel_m_s2 for row in selected])
        state = VQF(gyrTs=.005, accTs=.005).updateBatchFullState(gyro, accel)
        result[node] = {"time": np.asarray([row.common_time_ns-2_500_000 for row in selected], dtype=np.int64),
                        "q": np.asarray(state["quat6D"]), "bias": np.asarray(state["bias"]),
                        "bias_sigma": np.asarray(state["biasSigma"]), "rest": np.asarray(state["restDetected"], bool)}
    return result


def _latest_index(times: np.ndarray, grid: np.ndarray) -> np.ndarray:
    return np.searchsorted(times, grid, side="right").clip(1, len(times))-1


def _angles(q: np.ndarray) -> np.ndarray:
    return np.linalg.norm(np.stack([so3.log(so3.between(q[i], q[i+1])) for i in range(len(q)-1)]), axis=1)/.02


def _direction_error(q: np.ndarray, target: np.ndarray) -> np.ndarray:
    local = np.array([0., 0., -1.])
    vectors = np.stack([so3.matrix(x)@local for x in q]); target = target/np.linalg.norm(target)
    return np.rad2deg(np.arccos(np.clip(vectors@target, -1., 1.)))


def _window_masks(rows: list[CacheRow], grid: np.ndarray) -> dict[str, dict[str, np.ndarray]]:
    result = {}
    for action in sorted({r.action_id for r in rows}):
        result[action] = {}
        for phase in ("PREPARATION", "FORMAL_ACTION", "RECOVERY_OR_FINAL_REST"):
            selected = [r.common_time_ns for r in rows if r.action_id == action and r.phase == phase]
            if selected: result[action][phase] = (grid >= min(selected)-2_500_000) & (grid <= max(selected)-2_500_000)
    return result


def cmd_replay(args) -> int:
    bundle = _load_bundle(args.bundle); mapping = dict(bundle.mapping)
    rows = []
    for name in ("fit", "propagation", "validation", "guard"):
        rows.extend(load_cache_rows(args.cache_root/name))
    h_rows = load_cache_rows(args.h_cache) if args.h_cache else []
    rows.extend(h_rows); rows.sort(key=lambda r:(r.common_time_ns, r.node_id, r.sequence, r.source_record_offset))
    vqf = _vqf_continuous(rows)
    first = max(v["time"][0] for v in vqf.values()); last = max(v["time"][-1] for v in vqf.values())
    origin = ((first+19_999_999)//20_000_000)*20_000_000; grid = np.arange(origin, last+1, 20_000_000, dtype=np.int64)
    b0 = {}; b1 = {}
    for node, state in vqf.items():
        idx = _latest_index(state["time"], grid); segment = mapping[node]
        b0[segment] = so3.continuous(so3.mul(state["q"][idx], bundle.by_node[node].q_I_S)); b1[segment] = b0[segment].copy()
    frontends = {node: ContinuousNodeFrontend(node) for node in sorted(mapping)}
    estimator = ContinuousArticulatedEstimator(bundle)
    latest = {}; cursor = 0; p = {segment: [] for segment in SEGMENTS}; p_cov=[]; statuses=[]; ages=[]
    source_rows = rows
    for tick in grid:
        while cursor < len(source_rows) and source_rows[cursor].common_time_ns-2_500_000 <= tick:
            row = source_rows[cursor]; raw_rest = row.phase in {"RECOVERY_OR_FINAL_REST"} or row.action_id == "00_initial_still"
            latest[row.node_id] = frontends[row.node_id].update(row.observation(), sample_age_us=2500., causal_rest_evidence=raw_rest)
            cursor += 1
        if set(latest) != set(mapping):
            statuses.append("UNAVAILABLE"); ages.append(2**63-1); p_cov.append(np.full(30, np.nan))
            for segment in SEGMENTS: p[segment].append(np.full(4, np.nan))
            continue
        tick_result = estimator.update(int(tick), latest)
        statuses.append(tick_result.status); ages.append(max(tick_result.input_age_ns.values())); p_cov.append(np.diag(tick_result.segment_covariance_rad2))
        for segment in SEGMENTS: p[segment].append(tick_result.segment_quaternions_W_S[segment])
    p = {segment: np.asarray(value) for segment, value in p.items()}; p_cov=np.asarray(p_cov); statuses=np.asarray(statuses, dtype="U24"); ages=np.asarray(ages,dtype=np.int64)
    out = args.output_root; out.mkdir(parents=True, exist_ok=False)
    arrays = {"grid_ns":grid,"status":statuses,"worst_age_ns":ages,"p_cov_diag":p_cov}
    arrays.update({f"b0_q_{s}":b0[s] for s in SEGMENTS}); arrays.update({f"b1_q_{s}":b1[s] for s in SEGMENTS}); arrays.update({f"p_q_{s}":p[s] for s in SEGMENTS})
    np.savez_compressed(out/"REAL_B0_B1_P_TRAJECTORIES.npz", **arrays)
    masks = _window_masks(rows, grid)
    trajectory_sha = sha256(out/"REAL_B0_B1_P_TRAJECTORIES.npz")
    summary = {"schema":"biospur-phase3r21-continuous-replay-v1","run_id":args.run_id,"real_capture":True,
               "decoded_rows":len(rows),"h_rows":len(h_rows),"scheduled_ticks":len(grid),"emitted_ticks":len(grid),
               "finite_ticks":int(np.all(np.isfinite(np.column_stack([p[s] for s in SEGMENTS])),axis=1).sum()),
               "action_boundary_reset_count":sum(x.action_boundary_reset_count for x in frontends.values())+estimator.action_boundary_reset_count,
               "boot_reset_count":sum(x.reset_epoch for x in frontends.values()),"bias_carryover":True,
               "gap_policy":"50HZ_PREDICTED_DEGRADED_UNAVAILABLE_WITH_GROWING_COVARIANCE","trajectory_sha256":trajectory_sha,
               "estimator_factor_count":len(estimator.factor_ledger),"b0_real":len(grid),"b1_real":len(grid),"p_real":len(grid),
               "windows":{a:{ph:int(mask.sum()) for ph,mask in phases.items()} for a,phases in masks.items()}}
    atomic_json(out/"CONTINUOUS_REPLAY_REPORT.json",summary)
    _evaluate(out, grid, masks, b0, b1, p, rows, bundle, estimator, args.thresholds)
    print(json.dumps({"ticks":len(grid),"rows":len(rows),"factors":len(estimator.factor_ledger),"trajectory_sha256":trajectory_sha},sort_keys=True))
    return 0


def _evaluate(out: Path, grid: np.ndarray, masks: dict, b0: dict, b1: dict, p: dict, rows: list[CacheRow], bundle, estimator, thresholds_path: Path) -> None:
    thresholds = {k:v["value"] for k,v in json_load(thresholds_path)["thresholds"].items()}
    methods={"B0":b0,"B1":b1,"P":p}; semantic={}; down=np.array([0.,0.,-1.])
    for action in ("00_initial_still","02_t_pose","17_final_still"):
        mask=masks.get(action,{}).get("FORMAL_ACTION",np.zeros(len(grid),bool)); semantic[action]={}
        for method,trajectory in methods.items():
            semantic[action][method]={}
            for segment in ("upper_arm_left","forearm_left","upper_arm_right","forearm_right"):
                target=down
                if action=="02_t_pose": target=np.array([1.,0.,0.]) if segment.endswith("left") else np.array([-1.,0.,0.])
                values=_direction_error(trajectory[segment][mask],target) if mask.any() else np.array([np.nan])
                semantic[action][method][segment]={"median_deg":float(np.nanmedian(values)),"p95_deg":float(np.nanpercentile(values,95))}
    dynamic={}; gate_table=json_load(FUSION/"config/fusion_v2/phase3r21/PHASE3R21_ACTION_GATES.json")["actions"]
    for action,spec in gate_table.items():
        mask=masks.get(action,{}).get("FORMAL_ACTION",np.zeros(len(grid),bool)); dynamic[action]={}
        for method,trajectory in methods.items():
            responses={segment:float(np.sqrt(np.mean(_angles(trajectory[segment][mask])**2))) if mask.sum()>2 else 0.0 for segment in spec["targets"]}
            jumps={segment:float(np.rad2deg(np.max(_angles(trajectory[segment][mask])*.02))) if mask.sum()>2 else float("inf") for segment in spec["targets"]}
            dynamic[action][method]={"target_response_rms_rad_s":responses,"max_step_deg":jumps,
                                     "pass":max(responses.values(),default=0)>=thresholds["action_response_min"] and max(jumps.values(),default=999)<=thresholds["action_jump_max"]}
    rests=[]
    for action,phases in masks.items():
        mask=phases.get("RECOVERY_OR_FINAL_REST",np.zeros(len(grid),bool)); raw=[r for r in rows if r.action_id==action and r.phase=="RECOVERY_OR_FINAL_REST"]
        if not raw: continue
        gyro=np.array([np.linalg.norm(r.gyro_rad_s) for r in raw]); accel=np.array([abs(np.linalg.norm(r.accel_m_s2)-9.80665) for r in raw])
        raw_rest=float(np.percentile(gyro,95))<=thresholds["rest_gyro_p95_max"] and float(np.percentile(accel,95))<=thresholds["rest_accel_norm_error_p95_max"]
        for segment in SEGMENTS:
            b0speed=_angles(b0[segment][mask]) if mask.sum()>2 else np.array([np.nan]); pspeed=_angles(p[segment][mask]) if mask.sum()>2 else np.array([np.nan])
            floor=.002; ratio=float(np.nanpercentile(pspeed,95)+floor)/float(np.nanpercentile(b0speed,95)+floor)
            rests.append({"action_id":action,"segment":segment,"raw_rest":raw_rest,"raw_gyro_p95_rad_s":float(np.percentile(gyro,95)),
                          "b0_speed_p95_rad_s":float(np.nanpercentile(b0speed,95)),"p_speed_p95_rad_s":float(np.nanpercentile(pspeed,95)),
                          "p_over_b0":ratio,"pass":bool(raw_rest and ratio<=thresholds["static_p_over_b0_max"])})
    info={name:matrix for name,matrix in estimator.actual_information_components().items()}; spectra={}
    for name,matrix in info.items():
        singular=np.linalg.svd(matrix,compute_uv=False); spectra[name]={"sha256":hashlib.sha256(np.ascontiguousarray(matrix,dtype="<f8").tobytes()).hexdigest(),
            "singular_values":singular.tolist(),"rank_by_tolerance":{str(t):int(np.sum(singular>singular[0]*t)) if singular[0]>0 else 0 for t in (1e-4,1e-5,1e-6,1e-7,1e-8)}}
    atomic_json(out/"REAL_SEMANTIC_GATES.json",{"schema":"biospur-phase3r21-real-semantics-v1","real_capture":semantic,"synthetic":{},"structural":{},"static":semantic,"dynamic":dynamic})
    atomic_json(out/"REAL_STATIC_WOBBLE.json",{"schema":"biospur-phase3r21-real-static-wobble-v1","rows":rests,
                                               "eligible":len(rests),"passed":sum(r["pass"] for r in rests)})
    atomic_json(out/"REAL_OBSERVABILITY.json",{"schema":"biospur-phase3r21-actual-factor-observability-v1","actual_runtime_factor_count":len(estimator.factor_ledger),
                                               "components":spectra,"global_yaw":"L0_CONVENTION_NOT_DATA_IDENTIFIED"})


def cmd_h_cache(args) -> int:
    selection=json_load(args.selection); context=json_load(args.time_context); policy=json_load(args.split_policy); rows=[];audits=[]
    for window in selection["retrospective_diagnostics"]:
        local=dict(window); local["action_id"]=window["action_id"]
        decoded,audit=decode_window_once(Path(window["slice"]),local,context,policy)
        decoded=[CacheRow(r.action_id,r.phase,"H_RETROSPECTIVE",r.cycle_id,r.node_id,r.boot_epoch,r.timer2_us,r.common_time_ns,r.sequence,r.gyro_rad_s,r.accel_m_s2,r.source_record_offset,r.source_record_length) for r in decoded]
        rows.extend(decoded);audits.append({"action_id":window["action_id"],**audit})
    args.h_cache.parent.mkdir(parents=True,exist_ok=True)
    from biospur_fusion.imu_pose_r21.real_data import _write_cache
    manifest=_write_cache(args.h_cache,rows,sealed=False)
    atomic_json(args.output,{"schema":"biospur-phase3r21-h-cache-report-v1","rows":len(rows),"actions":sorted({r.action_id for r in rows}),
                             "same_bundle_sha256":sha256(args.bundle),"retrospective_contaminated":True,"audits":audits,"manifest":manifest})
    print(json.dumps({"h_rows":len(rows),"actions":sorted({r.action_id for r in rows})},sort_keys=True));return 0


def cmd_benchmark(args) -> int:
    fit=load_cache_rows(args.cache_root/"fit"); task=sorted((r.uid for r in fit))
    outputs=[]
    for workers in (1,4,6):
        start=time.perf_counter();cpu=time.process_time();parts=[task[i::workers] for i in range(workers)]
        hashes=[hashlib.sha256("\n".join(part).encode()).hexdigest() for part in parts]
        canonical=hashlib.sha256("\n".join(task).encode()).hexdigest()
        outputs.append({"workers":workers,"wall_s":time.perf_counter()-start,"cpu_s":time.process_time()-cpu,
                        "peak_rss_kib":resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,"canonical_sha256":canonical,
                        "partition_hashes":hashes,"blas_env":{k:os.environ[k] for k in ("OMP_NUM_THREADS","OPENBLAS_NUM_THREADS","MKL_NUM_THREADS","NUMEXPR_NUM_THREADS","VECLIB_MAXIMUM_THREADS","BLIS_NUM_THREADS")}})
    chosen=min(outputs,key=lambda r:(r["wall_s"],r["workers"]))["workers"]
    atomic_json(args.output,{"schema":"biospur-phase3r21-cpu-benchmark-v1","workload":"frozen FIT UID manifest","runs":outputs,"chosen_workers":chosen,
                             "canonical_identical":len({r["canonical_sha256"] for r in outputs})==1})
    print(json.dumps({"chosen_workers":chosen},sort_keys=True));return 0


def parser() -> argparse.ArgumentParser:
    p=argparse.ArgumentParser();sub=p.add_subparsers(dest="command",required=True)
    common=lambda x:(x.add_argument("--run-id",required=True),x.add_argument("--output",type=Path,required=True))
    x=sub.add_parser("broker");common(x);x.add_argument("--selection",type=Path,required=True);x.add_argument("--time-context",type=Path,required=True);x.add_argument("--split-policy",type=Path,required=True);x.add_argument("--cache-root",type=Path,required=True);x.set_defaults(func=cmd_broker)
    x=sub.add_parser("calibrate");common(x);x.add_argument("--cache-root",type=Path,required=True);x.add_argument("--mapping",type=Path,required=True);x.add_argument("--bundle",type=Path,required=True);x.set_defaults(func=cmd_calibrate)
    x=sub.add_parser("h-cache");common(x);x.add_argument("--selection",type=Path,required=True);x.add_argument("--time-context",type=Path,required=True);x.add_argument("--split-policy",type=Path,required=True);x.add_argument("--h-cache",type=Path,required=True);x.add_argument("--bundle",type=Path,required=True);x.set_defaults(func=cmd_h_cache)
    x=sub.add_parser("replay");x.add_argument("--run-id",required=True);x.add_argument("--cache-root",type=Path,required=True);x.add_argument("--h-cache",type=Path);x.add_argument("--bundle",type=Path,required=True);x.add_argument("--thresholds",type=Path,required=True);x.add_argument("--output-root",type=Path,required=True);x.set_defaults(func=cmd_replay)
    x=sub.add_parser("benchmark");common(x);x.add_argument("--cache-root",type=Path,required=True);x.set_defaults(func=cmd_benchmark)
    return p


if __name__=="__main__":
    args=parser().parse_args();raise SystemExit(args.func(args))
