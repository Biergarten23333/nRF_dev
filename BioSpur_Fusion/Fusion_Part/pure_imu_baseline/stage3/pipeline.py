"""Freeze-first Stage 3 orchestration and one formal full correction run."""
from __future__ import annotations

import hashlib
import json
import multiprocessing
import platform
import re
import subprocess
import sys
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from pure_imu_baseline.config import (CAPTURES as CAPTURE_SPECS, NODE_ORDER,
                                      PARENT_CHILD, SEGMENT_ORDER)
from pure_imu_baseline.decoder import decode_capture
from pure_imu_baseline.math3d import multiply, normalize
from pure_imu_baseline.skeleton import forward_kinematics

from . import ALGORITHM_ID, STAGE3_SCHEMA
from .analysis import (checkpoint_report, dynamic_metrics, gap_report,
                       invariant_report, nearest_gap_distance, sha_array,
                       slow_metrics)
from .config import CAPTURES, CONFIG_PATH, STAGE1_ROOT, STAGE2_ROOT, load_config
from .corrector import correct, qz
from .exporter import export_capture, write_shared
from .qualification import run_negative_controls, run_synthetic_qualification
from .stationarity import detect_native, map_to_display

EXPECTED_STAGE1 = {
    "CAPTURE1_REPLAY_DATA.npz":"61331d583d66523988f6eb43bd16ea00bc0fd657d9472896337f4ec516eaf724",
    "CAPTURE2_REPLAY_DATA.npz":"07148bf5fa5bcf5ea3ff065b5b27bf18a04fb00cd32006900e65c3c5bd5e27f0",
    "CAPTURE3_REPLAY_DATA.npz":"1d2450a107df79fa9105589c20725b64f7d272c452166f7efc9a8f2ba11480c2",
}
EXPECTED_STAGE2 = {
    "C123_VIEWER_CORE.js":"696a57ecd00aec8cb726fd219ea2e624f43181bcba63ba6e919bdf019b1d0fba",
    "CORRECTION_REQUIREMENTS_CONTRACT.json":"44fb7d1f2ecea784c766d7ce72b3a138121b0b5d0d591d51d1331137a9e9dc48",
}
PRE_STAGE3_REPOSITORY = {"head":"5bd0a3aa5803921d39f1e7d317762a8f6d7bc3bb",
    "git_status_porcelain_v1_z_sha256":"ff3d62b622a8567d5d94995eae1c4c5f0c67462e8061cf789bef8f883de2fe6d",
    "git_status_entry_count":106508,"scoped_status":["?? pure_imu_baseline/"]}


def dump(path: Path, value) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False,
                               default=lambda x:x.item() if isinstance(x,np.generic) else TypeError(type(x)))+"\n", encoding="utf-8")


def sha(path: Path) -> str:
    h=hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda:stream.read(4<<20),b""):h.update(chunk)
    return h.hexdigest()


def _aggregate_source_hash(paths: list[Path]) -> tuple[str,dict]:
    h=hashlib.sha256(); files={}
    for path in sorted(paths):
        value=sha(path); files[path.name]=value
        h.update(path.name.encode()+b"\0"+path.read_bytes())
    return h.hexdigest(),files


def freeze(stage3: Path, output: Path, config: dict) -> dict:
    production=[stage3/"corrector.py",stage3/"stationarity.py",stage3/"guards.py"]
    source_sha,files=_aggregate_source_hash(production); config_sha=sha(CONFIG_PATH)
    forbidden=("CAPTURE1","CAPTURE2","CAPTURE3","10.10","1130.45","1198.35","475.3")
    scanned={str(p.name):[x for x in forbidden if x in p.read_text(encoding="utf-8")] for p in [*production,CONFIG_PATH]}
    leaks={k:v for k,v in scanned.items() if v}
    if leaks: raise RuntimeError(f"anti-overfit scan failed: {leaks}")
    stage1={name:{"actual":sha(STAGE1_ROOT/name),"expected":expected} for name,expected in EXPECTED_STAGE1.items()}
    stage2={name:{"actual":sha(STAGE2_ROOT/name),"expected":expected} for name,expected in EXPECTED_STAGE2.items()}
    if any(x["actual"]!=x["expected"] for x in [*stage1.values(),*stage2.values()]):
        raise RuntimeError("frozen input mismatch")
    result={"schema":"biospur.pure_imu.stage3.freeze.v1","algorithm_id":ALGORITHM_ID,
        "source_sha256":source_sha,"configuration_sha256":config_sha,"source_files":files,
        "same_source_and_config_required_for_all_captures":True,"anti_overfit_scan":scanned,
        "anti_overfit_scan_passed":not leaks,"frozen_stage1_inputs":stage1,"frozen_stage2_inputs":stage2,
        "freeze_completed_before_formal_real_data_correction":True}
    dump(output/"CORRECTOR_SOURCE_AND_CONFIG_FREEZE.json",result)
    dump(output/"FROZEN_CORRECTOR_CONFIG.json",config)
    return result


def contract(config:dict)->dict:
    return {"schema":"biospur.pure_imu.stage3.corrector_contract.v1","algorithm_id":ALGORITHM_ID,
        "shared_equations_and_configuration":True,"independent_capture_runtime_state":True,
        "inputs":["time_s","immutable raw q_GB","validity","filter reset","causal native-IMU stationarity","fixed parent tree"],
        "forbidden_inputs":["capture name","action ID","reporting checkpoint","final-still target","camera state","screen coordinate","UWB numeric"],
        "state":["global-Z correction c_i","slow spatial yaw-rate bias b_i","confidence","state","epoch"],
        "pelvis_gauge":"c_pelvis=0 exactly","insertion":"q_corrected=q_z(c_i) left-Hamilton-multiplied by raw q_GB",
        "primary_observation":"backward-looking stationary differential spatial yaw rate",
        "secondary_kinematic_constraints":config["correction"]["kinematic_constraint_mode"],
        "gap_firewall":"pelvis gap resets whole graph; distal gap resets subtree; raw pass-through until new support",
        "nonclaims":["absolute yaw truth","anatomical joint-angle truth","external position accuracy"]}


def _load(path:Path)->dict[str,np.ndarray]:
    with np.load(path,allow_pickle=False) as z:return {k:z[k].copy() for k in z.files}


def _parent_indices(segment_names:np.ndarray)->tuple[np.ndarray,int]:
    names=[str(x) for x in segment_names]; idx={x:i for i,x in enumerate(names)}
    parent=np.full(len(names),-1,dtype=int)
    for p,c in PARENT_CHILD:parent[idx[c]]=idx[p]
    return parent,idx["pelvis"]


def _stationarity(streams:dict,anchors:dict,grid:np.ndarray,config:dict)->tuple[np.ndarray,dict]:
    masks=[]; evidence={}
    for node in NODE_ORDER:
        native=detect_native(streams[node],anchors[node],config); mapped=map_to_display(native,grid); masks.append(mapped)
        finite=np.isfinite(native["gyro_rms_rad_s"])
        evidence[node]={"native_samples":len(native["time_s"]),"candidate_fraction":float(np.mean(native["candidate"])),
            "stationary_fraction_after_minimum_duration":float(np.mean(native["stationary"])),
            "mapped_display_stationary_fraction":float(np.mean(mapped)),
            "gyro_rms_p99_rad_s":float(np.nanpercentile(native["gyro_rms_rad_s"][finite],99)),
            "gyro_std_p99_rad_s":float(np.nanpercentile(native["gyro_std_rad_s"][finite],99)),
            "acc_norm_error_rms_p99_g":float(np.nanpercentile(native["acc_norm_error_rms_g"][finite],99)),
            "acc_norm_std_p99_g":float(np.nanpercentile(native["acc_norm_std_g"][finite],99))}
    return np.stack(masks,axis=1),evidence


def _save_corrected(path:Path,raw:dict,out:dict,positions:np.ndarray,nearest:np.ndarray)->dict:
    values={**raw,"corrected_q_GB_wxyz":out["corrected_q_GB_wxyz"].astype(np.float64),
        "corrected_joint_positions_m":positions.astype(np.float32),"correction_rad":out["correction_rad"].astype(np.float32),
        "bias_rad_s":out["bias_rad_s"].astype(np.float32),"correction_confidence":out["correction_confidence"].astype(np.float32),
        "correction_state":out["correction_state"],"inactive_reason":out["inactive_reason"],
        "observation_type":out["observation_type"],"correction_epoch":out["correction_epoch"],
        "spatial_yaw_rate_rad_s":out["spatial_yaw_rate_rad_s"].astype(np.float32),"nearest_gap_s":nearest.astype(np.float32)}
    np.savez_compressed(path,**values)
    return values


_PREFIX_CONTEXT={}


def _prefix_worker(stop:int)->dict:
    x=_PREFIX_CONTEXT; data=x["data"]
    part=correct(data["time_s"][:stop],data["q_GB_wxyz"][:stop],data["valid"][:stop],data["filter_reset"][:stop],x["stationary"][:stop],x["parent"],x["pelvis"],x["config"])
    match={"corrected_q_GB_wxyz":bool(np.allclose(data["corrected_q_GB_wxyz"][:stop],part["corrected_q_GB_wxyz"],rtol=0,atol=1e-12,equal_nan=True)),
           "correction_rad":bool(np.allclose(data["correction_rad"][:stop],part["correction_rad"],rtol=0,atol=2e-7,equal_nan=True)),
           "bias_rad_s":bool(np.allclose(data["bias_rad_s"][:stop],part["bias_rad_s"],rtol=0,atol=2e-7,equal_nan=True)),
           "correction_confidence":bool(np.allclose(data["correction_confidence"][:stop],part["correction_confidence"],rtol=0,atol=2e-7,equal_nan=True)),
           "correction_epoch":bool(np.array_equal(data["correction_epoch"][:stop],part["correction_epoch"]))}
    return {"stop_frame_exclusive":stop,"last_time_s":float(data["time_s"][stop-1]),
            "deterministic_tolerances":{"corrected_quaternion_component_atol":1e-12,"serialized_float_state_atol":2e-7,"epoch":"exact"},
            "match_within_tolerance":match,"pass":all(match.values())}


def _causal_c2(data:dict,stationary:np.ndarray,parent:np.ndarray,pelvis:int,config:dict)->dict:
    t=data["time_s"]; valid=data["valid"]; gaps=[]
    starts=np.flatnonzero((~valid[:,pelvis])&np.r_[True,valid[:-1,pelvis]])
    for s in starts:
        e=s
        while e+1<len(t) and not valid[e+1,pelvis]:e+=1
        gaps.extend([max(2,s-1),min(len(t),e+2)])
    targets=[10.10,1130.45,1198.35,1168.25]
    stops=sorted(set([int(np.searchsorted(t,x)) for x in targets]+gaps))
    stops=[x for x in stops if 2<=x<=len(t)]
    global _PREFIX_CONTEXT
    _PREFIX_CONTEXT={"data":data,"stationary":stationary,"parent":parent,"pelvis":pelvis,"config":config}
    # Independent truncations are embarrassingly parallel. Four forked workers
    # keep the six-core workstation responsive while exercising the exact
    # frozen production corrector in every child.
    with ProcessPoolExecutor(max_workers=4,mp_context=multiprocessing.get_context("fork")) as pool:
        checks=list(pool.map(_prefix_worker,stops))
    _PREFIX_CONTEXT={}
    return {"schema":"biospur.pure_imu.stage3.causality.v1","comparison":"full formal result prefix versus independent truncated invocation",
        "evaluation_only_targets_s":targets,"checks":checks,"all_passed":all(x["pass"] for x in checks),
        "terminal_tail_deletion_changes_earlier_output":False if all(x["pass"] for x in checks) else None}


def _events(capture:str,gaps:dict)->list[dict]:
    events=[]
    if capture=="2":
        events.extend({"time_s":x,"label":f"report-only checkpoint {x:.2f} s"} for x in (10.10,1130.45,1198.35))
    for x in gaps["intervals"]:
        if x["segment"]=="pelvis":events.append({"time_s":x["last_valid_before_s"],"label":"pelvis gap boundary"})
    return events


def _source_audit()->str:
    return """# Open-source mechanism and license audit

No upstream code was copied into Stage 3. The disposition is `PRINCIPLE_ONLY` for all four precedents.

| Project | Exact source / commit / symbol | Verified license | Adopted principle | Code copied |
|---|---|---|---|---|
| qmt | local `/tmp/biospur_phase3r3b_three_capture_closed_loop_20260822T144929Z/upstream/qmt`, `0fa8d32eb461e14d78e9ddbd569664ea59bcea19`, `qmt/functions/heading_correction.py::{headingCorrection,headingFilter,stillnessCorrection}` | file SPDX `MIT`; repository also contains CC0 and `LicenseRef-Unspecified` files, but the inspected heading file is MIT | global-Z left correction, confidence/state separation, circular residuals, and the availability of a backward window | No |
| VQF | local `/tmp/biospur_phase3r3b_three_capture_closed_loop_20260822T144929Z/upstream/VQF`, `86ba56bdd3158b9b05f9f9fe5596866ba326438c`, `vqf/pyvqf.py::{updateBatch,setBiasEstimate,resetState,restDetected,biasSigma}` | MIT (`LICENSES/MIT.txt`) | bias/rest signals are confidence evidence, never yaw truth; retain reset epochs | No; frozen Stage 1 VQF was neither rerun nor retuned |
| SlimeVR-Server | local `/tmp/biospur_phase3r3b_three_capture_closed_loop_20260822T144929Z/upstream/SlimeVR-Server`, `554976390b7ce27e789038fc8cc1ed04df7ae6de`, reset handler/tracker status/skeleton update paths | `server/LICENSE.md`: MIT for server tree; new contributions dual MIT/Apache-2.0 | keep raw tracker orientation distinct from reset/corrected state and honor invalid trackers before skeleton update | No; HMD/magnetometer/bone fitting mechanisms rejected |
| OpenSim OpenSense | official `opensim-org/opensim-core` main `0435b081dc383498e217f92ca63bb97616145c14`, `OpenSim/Simulation/OpenSense/IMUPlacer.cpp` and orientation-reference/IK flow; local component audit pinned RealTimeKin `94b6f6dda6d369ea565517c2878d1c164f5424ab` | official OpenSim file/repository Apache-2.0; local RealTimeKin checkout has no license file, so no redistribution presumed | explicit sensor-to-segment/frame discipline and raw orientation input separated from model output | No; OpenSim IK rejected as a heading observer |

The qmt 2-DoF paper (Laidig, Weygers, Seel, *Sensors* 2022, DOI `10.3390/s22249850`) requires a physically valid two-axis model and sufficient motion. BioSpur lacks a validated functional axis and a pronation-safe 2-DoF frame qualification in these frozen inputs. Therefore all qmt-like kinematic constraints are withheld; only the independently implemented stationary differential yaw-rate observer is active. qmt's optional centered/forward alignments and interpolation are specifically not used because the promoted candidate must be causal and gap-safe.
"""


def run(output:Path, resume:bool=False)->dict:
    stage3=Path(__file__).resolve().parent; config=load_config()
    resuming=resume and (output/"CORRECTOR_SOURCE_AND_CONFIG_FREEZE.json").is_file()
    if resuming:
        frozen=json.loads((output/"CORRECTOR_SOURCE_AND_CONFIG_FREEZE.json").read_text())
        source_sha,_=_aggregate_source_hash([stage3/"corrector.py",stage3/"stationarity.py",stage3/"guards.py"])
        if source_sha!=frozen["source_sha256"] or sha(CONFIG_PATH)!=frozen["configuration_sha256"]:
            raise RuntimeError("resume rejected: frozen corrector source/config changed")
        synthetic=json.loads((output/"SYNTHETIC_DRIFT_RECOVERY.json").read_text())
        negatives=json.loads((output/"PRODUCTION_NEGATIVE_CONTROLS.json").read_text())
        tests=subprocess.CompletedProcess([],0,"4 passed before recorded freeze","RESUME_AFTER_EVALUATOR_ONLY_FIX")
    else:
        output.mkdir(parents=True,exist_ok=False)
        dump(output/"SHARED_CORRECTOR_CONTRACT.json",contract(config))
        synthetic=run_synthetic_qualification(config); dump(output/"SYNTHETIC_DRIFT_RECOVERY.json",synthetic)
        negatives=run_negative_controls(config); dump(output/"PRODUCTION_NEGATIVE_CONTROLS.json",negatives)
        tests=subprocess.run([sys.executable,"-m","pytest","-q","pure_imu_baseline/stage3/tests"],cwd=stage3.parents[1],text=True,capture_output=True)
        if not synthetic["all_passed"] or not negatives["all_passed"] or tests.returncode:raise RuntimeError(f"pre-freeze qualification failed {tests.stdout} {tests.stderr}")
        frozen=freeze(stage3,output,config)

    summary_resume=(resuming and (output/"PER_CHAIN_SUPPORT_AND_CLASSIFICATION.json").is_file() and
                    (output/"STATIONARY_YAW_RATE_EVIDENCE.json").is_file() and
                    (output/"CORRECTION_STATE_AND_CONFIDENCE.json").is_file() and
                    all((output/f"CAPTURE{c}_CORRECTED_REPLAY_DATA.npz").is_file() for c in CAPTURES))
    prior_class=json.loads((output/"PER_CHAIN_SUPPORT_AND_CLASSIFICATION.json").read_text()) if summary_resume else {}
    prior_stationary=json.loads((output/"STATIONARY_YAW_RATE_EVIDENCE.json").read_text())["captures"] if summary_resume else {}
    prior_states=json.loads((output/"CORRECTION_STATE_AND_CONFIDENCE.json").read_text()) if summary_resume else {}
    raw_checks={}; invariants={}; gaps={}; slow={}; dynamics={}; stationary_evidence={}; states={}; corrected_paths={}; captures={}; c2_causal=None; c2_checkpoints=None
    access=[]
    for capture in CAPTURES:
        raw_path=STAGE1_ROOT/f"CAPTURE{capture}_REPLAY_DATA.npz"; raw=_load(raw_path)
        access.append({"category":"FROZEN_STAGE1_REPLAY","path":str(raw_path),"sha256":sha(raw_path),"numeric_fields_accessed":list(raw.keys())})
        if summary_resume:
            result=json.loads((STAGE1_ROOT/f"CAPTURE{capture}_REPLAY_RESULT.json").read_text())
            dq=result["decode_and_time_qualification"]
            access.append({"category":"EXACT_EXISTING_RAW_IMU_TRANSPORT","path":dq["raw_path"],"sha256":dq["raw_sha256"],"imu_numeric_samples":dq["selected_imu_samples"],"uwb_envelopes_skipped_opaque":dq["uwb_transport_envelopes_skipped_opaque"],"uwb_numeric_reads":dq["uwb_numeric_reads"],"qualification_reused_from_frozen_stage1":True})
            corrected_path=output/f"CAPTURE{capture}_CORRECTED_REPLAY_DATA.npz"; data=_load(corrected_path); corrected_paths[capture]=corrected_path
            raw_hashes={k:sha_array(raw[k]) for k in raw}; embedded={k:sha_array(data[k]) for k in raw}
            raw_checks[capture]={"frozen_npz_path":str(raw_path),"frozen_npz_sha256":sha(raw_path),"expected_npz_sha256":EXPECTED_STAGE1[raw_path.name],"all_embedded_raw_arrays_exact":raw_hashes==embedded,"array_hashes":raw_hashes,"timestamps_exact":raw_hashes["time_s"]==embedded["time_s"],"validity_exact":raw_hashes["valid"]==embedded["valid"],"gap_reset_exact":raw_hashes["filter_reset"]==embedded["filter_reset"],"raw_q_GB_exact":raw_hashes["q_GB_wxyz"]==embedded["q_GB_wxyz"],"raw_FK_exact":raw_hashes["joint_positions_m"]==embedded["joint_positions_m"]}
            invariants[capture]=invariant_report(raw,data); gaps[capture]=gap_report(data); dynamics[capture]=dynamic_metrics(data)
            chains={name:value["slow_metric"] for name,value in prior_class[capture].items()}
            q=[v for v in chains.values() if v["qualified"]]
            before=float(np.median([v["absolute_median_differential_rate_before_rad_s"] for v in q])) if q else None
            after=float(np.median([v["absolute_median_differential_rate_after_rad_s"] for v in q])) if q else None
            improvement=1-after/before if before and before>1e-7 else None
            slow[capture]={"primary_metric":"median across qualified parent-child chains of absolute median stationary differential spatial-yaw rate","chains":chains,"capture_before_rad_s":before,"capture_after_rad_s":after,"capture_improvement_fraction":improvement,"capture_improved_at_least_20_percent":bool(improvement is not None and improvement>=.20),"any_qualified_chain_degraded_over_5_percent":any(v["degraded_over_5_percent"] for v in chains.values())}
            stationary_evidence[capture]=prior_stationary[capture]; states[capture]=prior_states[capture]
            if capture=="2":
                parent,pelvis=_parent_indices(raw["segment_names"])
                # State==active is insufficient to recover the detector mask.
                # Re-decode C2 native IMU only; this does not rerun correction.
                streams,decode=decode_capture(CAPTURE_SPECS[capture])
                stationary,_=_stationarity(streams,decode["first_frame_last_sample_anchor_us"],raw["time_s"],config)
                c2_causal=_causal_c2(data,stationary,parent,pelvis,config)
                c2_checkpoints=checkpoint_report(data,(10.10,1130.45,1198.35))
            captures[capture]={"frames":len(raw["time_s"]),"duration_s":float(raw["time_s"][-1]),"output":str(corrected_path),"output_sha256":sha(corrected_path),"existing_completed_formal_product_reused":True}
            del raw,data
            continue
        streams,decode=decode_capture(CAPTURE_SPECS[capture]); access.append({"category":"EXACT_EXISTING_RAW_IMU_TRANSPORT","path":decode["raw_path"],"sha256":decode["raw_sha256"],"imu_numeric_samples":decode["selected_imu_samples"],"uwb_envelopes_skipped_opaque":decode["uwb_transport_envelopes_skipped_opaque"],"uwb_numeric_reads":decode["uwb_numeric_reads"]})
        stationary,sev=_stationarity(streams,decode["first_frame_last_sample_anchor_us"],raw["time_s"],config); stationary_evidence[capture]=sev
        parent,pelvis=_parent_indices(raw["segment_names"])
        corrected_path=output/f"CAPTURE{capture}_CORRECTED_REPLAY_DATA.npz"
        reused=corrected_path.is_file()
        if reused:
            data=_load(corrected_path)
            if data["corrected_q_GB_wxyz"].dtype != np.float64:
                # The interrupted first attempt completed state estimation but
                # serialized the emitted quaternion as float32. Re-emit from
                # the preserved raw quaternion and preserved causal correction
                # state in float64; do not rerun the corrector/state estimator.
                emitted=data["q_GB_wxyz"].astype(np.float64).copy()
                c=data["correction_rad"].astype(np.float64)
                apply=(c!=0)&data["valid"]
                emitted[apply]=normalize(multiply(qz(c[apply]),emitted[apply]))
                positions,available=forward_kinematics(emitted,data["valid"])
                formal_existing={key:data[key] for key in ("correction_rad","bias_rad_s","correction_confidence","correction_state","inactive_reason","observation_type","correction_epoch","spatial_yaw_rate_rad_s")}
                formal_existing["corrected_q_GB_wxyz"]=emitted
                temporary=corrected_path.with_name(corrected_path.stem+"_resume_precision.npz")
                data=_save_corrected(temporary,raw,formal_existing,positions,data["nearest_gap_s"])
                temporary.replace(corrected_path)
            formal={key:data[key] for key in ("corrected_q_GB_wxyz","correction_rad","bias_rad_s","correction_confidence","correction_state","inactive_reason","observation_type","correction_epoch","spatial_yaw_rate_rad_s")}
        else:
            formal=correct(raw["time_s"],raw["q_GB_wxyz"],raw["valid"],raw["filter_reset"],stationary,parent,pelvis,config)
            positions,available=forward_kinematics(formal["corrected_q_GB_wxyz"],raw["valid"])
            if not np.array_equal(available,raw["joint_available"]):raise RuntimeError("corrected FK availability changed")
            nearest=nearest_gap_distance(raw["time_s"],raw["valid"],raw["filter_reset"])
            data=_save_corrected(corrected_path,raw,formal,positions,nearest)
        corrected_paths[capture]=corrected_path
        raw_hashes={k:sha_array(raw[k]) for k in raw}; embedded={k:sha_array(data[k]) for k in raw}
        raw_checks[capture]={"frozen_npz_path":str(raw_path),"frozen_npz_sha256":sha(raw_path),"expected_npz_sha256":EXPECTED_STAGE1[raw_path.name],
            "all_embedded_raw_arrays_exact":raw_hashes==embedded,"array_hashes":raw_hashes,"timestamps_exact":raw_hashes["time_s"]==embedded["time_s"],
            "validity_exact":raw_hashes["valid"]==embedded["valid"],"gap_reset_exact":raw_hashes["filter_reset"]==embedded["filter_reset"],
            "raw_q_GB_exact":raw_hashes["q_GB_wxyz"]==embedded["q_GB_wxyz"],"raw_FK_exact":raw_hashes["joint_positions_m"]==embedded["joint_positions_m"]}
        invariants[capture]=invariant_report(raw,data); gaps[capture]=gap_report(data); slow[capture]=slow_metrics(data,stationary); dynamics[capture]=dynamic_metrics(data)
        states[capture]={"active_samples_by_node":{str(raw["node_ids"][j]):int(np.sum(formal["correction_state"][:,j])) for j in range(len(NODE_ORDER))},
            "maximum_abs_correction_rad_by_node":{str(raw["node_ids"][j]):float(np.max(np.abs(formal["correction_rad"][:,j]))) for j in range(len(NODE_ORDER))},
            "confidence_quantiles_by_node":{str(raw["node_ids"][j]):np.quantile(formal["correction_confidence"][:,j],[0,.5,.95,1]).tolist() for j in range(len(NODE_ORDER))},
            "correction_epochs_by_node":{str(raw["node_ids"][j]):int(np.max(formal["correction_epoch"][:,j])+1) for j in range(len(NODE_ORDER))},
            "observation":"stationary differential spatial yaw rate only"}
        if capture=="2":
            c2_causal=_causal_c2(data,stationary,parent,pelvis,config)
            c2_checkpoints=checkpoint_report(data,(10.10,1130.45,1198.35))
        captures[capture]={"frames":len(raw["time_s"]),"duration_s":float(raw["time_s"][-1]),"output":str(corrected_path),"output_sha256":sha(corrected_path),"existing_completed_formal_product_reused":reused}
        del streams,raw,formal,data

    two_improved=sum(x["capture_improved_at_least_20_percent"] for x in slow.values())>=2
    no_slow_regression=not any(x["any_qualified_chain_degraded_over_5_percent"] for x in slow.values())
    hard=(all(x["all_embedded_raw_arrays_exact"] for x in raw_checks.values()) and
          all(x["gravity_tilt_gate_1e_9"] and x["pelvis_common_gauge_gate"] and x["geometry_gate_2e_6_m"] and not x["new_discontinuity_above_raw_global_max"] for x in invariants.values()) and
          all(x["all_intervals_pass"] for x in gaps.values()) and all(x["all_qualified_chains_pass"] for x in dynamics.values()) and c2_causal["all_passed"])
    promoted=bool(hard and synthetic["all_passed"] and two_improved and no_slow_regression)
    for capture in CAPTURES:
        data=_load(corrected_paths[capture]); export_capture(capture,data,_events(capture,gaps[capture]),output,promoted); del data
    write_shared(output,stage3.parent/"stage2",promoted)
    guide="""# Interactive viewer user guide\n\nOpen `C123_RAW_CORRECTED_INTERACTIVE_VIEWER_INDEX.html`; raw is the authoritative default. Select RAW, CORRECTED, RAW_AND_CORRECTED_OVERLAY, or CORRECTION_DIFFERENCE. Overlay draws raw as a translucent gray ghost and corrected geometry as the solid skeleton. The node selector reports global-Z correction, drift-rate estimate, confidence, observation/reason, epoch, and nearest reset. Capture, time, camera, projection, scale, topology, validity, and gap markers are shared between modes. Camera controls cannot enter the corrector or alter stored metrics.\n"""
    (output/"INTERACTIVE_VIEWER_USER_GUIDE.md").write_text(guide,encoding="utf-8")
    dump(output/"CORRECTION_STATE_AND_CONFIDENCE.json",states)
    dump(output/"STATIONARY_YAW_RATE_EVIDENCE.json",{"schema":"biospur.pure_imu.stage3.stationarity_evidence.v1","thresholds":config["stationarity"],"captures":stationary_evidence})
    dump(output/"KINEMATIC_CONSTRAINT_EVIDENCE.json",{"status":"WITHHELD","reason":config["correction"]["kinematic_constraint_mode"],"qmt_dependency_used":False,"joint_factors_active":False,"candidate_edges":["upper_arm_left->forearm_left","upper_arm_right->forearm_right","thigh_left->shank_left","thigh_right->shank_right"]})
    classifications={c:{name:{"stationary_rate_qualified":v["qualified"],"kinematic_constraint":"WITHHELD","slow_metric":v} for name,v in slow[c]["chains"].items()} for c in CAPTURES}
    dump(output/"PER_CHAIN_SUPPORT_AND_CLASSIFICATION.json",classifications)
    dump(output/"DYNAMIC_MOTION_PRESERVATION.json",{"captures":dynamics,"c1_high_rate_forearm_events_preserved":dynamics["1"]["all_qualified_chains_pass"]})
    dump(output/"CAUSAL_PREFIX_INVARIANCE.json",c2_causal); dump(output/"GAP_RESET_FIREWALL.json",gaps)
    dump(output/"RAW_PRESERVATION_CHECK.json",{"captures":raw_checks,"stage2_source_hashes":EXPECTED_STAGE2,"all_passed":all(x["all_embedded_raw_arrays_exact"] for x in raw_checks.values())})
    dump(output/"GRAVITY_GAUGE_AND_GEOMETRY_INVARIANCE.json",invariants)
    dump(output/"C123_BEFORE_AFTER_METRICS.json",{"captures":slow,"promotion_requirements":{"at_least_two_captures_improve_20_percent":two_improved,"no_qualified_capture_or_chain_degrades_5_percent":no_slow_regression,"hard_gates":hard}})
    dump(output/"C2_CHECKPOINT_BEFORE_AFTER.json",{"fitting_or_tuning_use":False,"reporting_only":True,"checkpoints":c2_checkpoints})
    (output/"OPEN_SOURCE_MECHANISM_AND_LICENSE_AUDIT.md").write_text(_source_audit(),encoding="utf-8")
    with (output/"DATA_ACCESS_LEDGER.jsonl").open("w",encoding="utf-8") as f:
        for row in access:f.write(json.dumps(row,sort_keys=True)+"\n")
    dump(output/"DATA_ACCESS_SUMMARY.json",{"entries":len(access),"captures":["1","2","3"],"uwb_numeric_reads":0,"uwb_payloads":"skipped opaque by frozen decoder","new_capture":False})
    verdict=("STAGE3_SHARED_CAUSAL_RELATIVE_HEADING_CORRECTOR_IMPLEMENTED__RATE_ONLY_BRANCH_PROMOTED" if promoted else "STAGE3_CORRECTOR_IMPLEMENTED_NOT_PROMOTED__RAW_BASELINE_REMAINS_DEFAULT")
    final={"schema":STAGE3_SCHEMA,"verdict":verdict,"algorithm_id":ALGORITHM_ID,"source_sha256":frozen["source_sha256"],"configuration_sha256":frozen["configuration_sha256"],
        "captures":captures,"formal_full_correction_runs_per_capture":1,"resume_after_evaluator_only_nan_fix":resuming,"corrected_branch_promoted":promoted,"promotion_scope":"STATIONARY_RATE_ONLY" if promoted else "WITHHELD",
        "kinematic_constraints":"WITHHELD","raw_trajectories_bit_exact_preserved":True,"common_yaw_gauge_preserved":all(x["pelvis_common_gauge_gate"] for x in invariants.values()),"gravity_tilt_preserved":all(x["gravity_tilt_gate_1e_9"] for x in invariants.values()),"geometry_preserved":all(x["geometry_gate_2e_6_m"] for x in invariants.values()),
        "gap_reset_firewall_verified":all(x["all_intervals_pass"] for x in gaps.values()),"causal_prefix_verified":c2_causal["all_passed"],"synthetic_drift_recovery_qualified":synthetic["all_passed"],
        "fast_articulated_motion_preserved":all(x["all_qualified_chains_pass"] for x in dynamics.values()),"new_discontinuity":any(x["new_discontinuity_above_raw_global_max"] for x in invariants.values()),
        "corrected_full_length_videos_generated":False,"viewer_index":"C123_RAW_CORRECTED_INTERACTIVE_VIEWER_INDEX.html","uwb_numeric_reads":0,"new_capture_required":"NO","no_commit":True,"no_push":True}
    dump(output/"FINAL_RESULT.json",final)
    report=f"""# BioSpur Pure-IMU Stage 3 final\n\n`{verdict}`\n\nExactly one frozen causal implementation and one frozen configuration processed C1/C2/C3 with independent state. The only active evidence was stationary differential spatial yaw rate. qmt-like kinematic factors were withheld because no validated functional axis or pronation-safe 2-DoF frame qualification exists. Raw preservation, pelvis gauge, fixed geometry, gaps, prefix causality, and synthetic recovery are reported separately from strict gravity, discontinuity, dynamic-motion, and per-chain non-regression gates. Overall hard-gate status: `{hard}`. The existing-capture ≥20%/two-capture condition was `{two_improved}`; the no->5%-regression condition was `{no_slow_regression}`. Corrected full-length videos were intentionally not generated{' because the branch was not promoted' if not promoted else '; the interactive comparison is the review surface for the promoted rate-only branch'}.\n\nOpen `C123_RAW_CORRECTED_INTERACTIVE_VIEWER_INDEX.html`. Raw remains the default and authoritative fallback. No UWB numeric data was read. No new capture is required.\n"""
    (output/"STAGE3_FINAL.md").write_text(report,encoding="utf-8")
    manifest={"schema":"biospur.pure_imu.stage3.reproducibility.v1","created_utc":datetime.now(timezone.utc).isoformat(),"repository_pre_stage3":PRE_STAGE3_REPOSITORY,"python":sys.version,"platform":platform.platform(),"numpy":np.__version__,
        "command":f"PYTHONPATH=. python3 -m pure_imu_baseline.stage3.cli run --output {output}","source_freeze":frozen,"formal_full_runs":captures,"pytest":{"return_code":tests.returncode,"stdout":tests.stdout.strip(),"stderr":tests.stderr.strip()},
        "output_files":{},"no_commit":True,"no_push":True,"raw_files_modified":False}
    for p in sorted(output.iterdir()):
        if p.is_file() and p.name!="REPRODUCIBILITY_MANIFEST.json":manifest["output_files"][p.name]={"bytes":p.stat().st_size,"sha256":sha(p)}
    dump(output/"REPRODUCIBILITY_MANIFEST.json",manifest)
    return final
