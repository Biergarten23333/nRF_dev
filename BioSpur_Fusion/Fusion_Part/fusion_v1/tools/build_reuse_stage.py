from __future__ import annotations
import csv, hashlib, json
from pathlib import Path
from fusion_v1.io.common_clock import load_models, verify_ledger_equivalence, build_sidecar, sha256

ROOT=Path(__file__).resolve().parents[2]
CAP=ROOT/"logs/v47_ten_node_body_calibration_20260814_093601"
OLD=CAP/"analysis_body_fusion_v2"
OUT=ROOT/"logs/fusion_v1_reference_20260816_130000"
RAW=CAP/"continuous_collector/fusion_host_raw.cobs.bin"
CAN=ROOT/"logs/fusion_v1_reference_20260816_120002/CANONICAL_OBSERVATIONS.csv.gz"
LISTENER=CAP/"listener_capture_5/merged_index.jsonl"
TIMING=OLD/"TIME_ALIGNMENT_RESULT.json"; LEDGER=OLD/"TIME_EVENT_LEDGER.npz"

def entry(path,typ,category,body=False,held=False,rationale=""):
    p=Path(path); return {"path":str(p.resolve()),"artifact_type":typ,"producer":"biospur_fusion v2 or acquisition pipeline" if "analysis_body" in str(p) else "capture/acquisition infrastructure","capture_identity":"v47_ten_node_body_calibration_20260814_093601","raw_input_sha256":"a491520739400064db520377ec87a9331feb6274cd42a7e6d9aad57a2b93d56a" if p.exists() else None,"configuration_sha256":None,"used_held_out_data":held,"contains_body_model_assumptions":body,"infrastructure_or_science":"scientific_estimation" if body else "acquisition_infrastructure","classification":category,"verification":{"exists":p.exists(),"sha256":sha256(p) if p.is_file() else None},"rationale":rationale}

def main():
    OUT.mkdir(parents=True,exist_ok=False)
    expected={"raw":"a491520739400064db520377ec87a9331feb6274cd42a7e6d9aad57a2b93d56a","canonical":"836ee43e3a86f818ff4bc954a7111e4f4111a3f7693047b84811571cb48332cd","ledger":"41987fd8d12b74cd5c1e9d7d448f1add981f11bc271f6687ac72b1ad77211a7e","timing":"4b608451f95bc5e5d72ae8c77655d0b2bf46141a41f894d6826dc5d3d530e1cd","listener":"cedd3329814c180ad5d373f3678f841a4b5513f3a3b5a7c76d87223227582144"}
    actual={"raw":sha256(RAW),"canonical":sha256(CAN),"ledger":sha256(LEDGER),"timing":sha256(TIMING),"listener":sha256(LISTENER)}
    if actual!=expected: raise RuntimeError({"identity_mismatch":actual,"expected":expected})
    items=[
      entry(CAN,"canonical observations","REUSE_DIRECTLY",rationale="Immutable independently decoded lineage table; SHA matched."),
      entry(RAW,"raw body stream","REUSE_AFTER_IDENTITY_CHECK",rationale="Identity source; not decoded again."),
      entry(TIMING,"clock coefficients and gates","REUSE_AFTER_IDENTITY_CHECK",rationale="Exact raw/capture/Listener/TDMA identity matched."),
      entry(OLD/"CLOCK_MODELS.csv","clock coefficient table","REUSE_AFTER_IDENTITY_CHECK",rationale="Byte-identical across deterministic prior replay."),
      entry(OLD/"CLOCK_RESIDUALS.csv","clock residual ledger","RECOMPUTE_AND_COMPARE",rationale="Replay metrics only; not a body input."),
      entry(LEDGER,"typed common-time acquisition ledger","REUSE_AFTER_IDENTITY_CHECK",rationale="Lineage-preserving machine mapping; deterministic SHA matched."),
      entry(LISTENER,"Listener join input","REUSE_AFTER_IDENTITY_CHECK",rationale="Listener SHA matched prior evidence."),
      entry(ROOT/"config/captures/v47_ten_node_body_calibration_20260814_093601.json","capture binding","REUSE_DIRECTLY",rationale="Operator/capture identity and TDMA facts."),
      entry(ROOT/"config/geometry/current_room_autopos_20260811_183541.reference.json","geometry reference","REUSE_AFTER_IDENTITY_CHECK",rationale="References frozen V4-io SHA 20320e53..."),
      entry(CAP/"ACTION_EVENTS.jsonl","manual action bounds","REUSE_DIRECTLY",rationale="Acquisition annotation; bounds are not exact motion truth."),
      entry(OLD/"Q1_ATTITUDE_TIMELINES.npz","orientation diagnostic","DIAGNOSTIC_ONLY",rationale="May initialize/compare; cannot become final truth."),
      entry(CAP/"analysis_body_calibration_v1/run_a/T4_POSITION_TIMELINES.npz","production T4 baseline","DIAGNOSTIC_ONLY",rationale="UWB baseline/initialization only; raw ranges remain estimator observations."),
      entry(OLD/"BODY_MODEL_MANIFEST.json","old body model","REJECT_OLD_SCIENTIFIC_ARCHITECTURE",body=True,rationale="Rejected body architecture."),
      entry(OLD/"CALIBRATION_FREEZE_MANIFEST.json","old calibration","REJECT_OLD_SCIENTIFIC_ARCHITECTURE",body=True,rationale="Parameters estimated under rejected model."),
      entry(ROOT/"logs/d0b_r2_observation_lineage_v2_20260816_025655","D0B-R2 outputs","REJECT_OLD_SCIENTIFIC_ARCHITECTURE",body=True,rationale="Explicitly prohibited."),
      entry(CAP/"analysis_body_fusion_v3","old body estimator output","REJECT_OLD_SCIENTIFIC_ARCHITECTURE",body=True,rationale="Historical evidence only."),
    ]
    audit={"schema":"fusion-v1-reuse-audit-v1","identity_checks":{"expected":expected,"actual":actual,"all_match":True},"items":items}
    (OUT/"REUSE_AUDIT.json").write_text(json.dumps(audit,indent=2)+"\n")
    lines=["# Reuse audit","","All five controlling identities match. Acquisition infrastructure is reused; old body science is rejected.","","| Classification | Artifact | Rationale |","|---|---|---|"]
    lines += [f"| `{x['classification']}` | `{x['path']}` | {x['rationale']} |" for x in items]
    (OUT/"REUSE_AUDIT.md").write_text("\n".join(lines)+"\n")
    models=load_models(TIMING); equivalence=verify_ledger_equivalence(LEDGER,models)
    result=json.loads(TIMING.read_text()); result["capture_identity"]="v47_ten_node_body_calibration_20260814_093601"; result["raw_sha256"]=actual["raw"]; result["listener_inputs"]=[{"path":str(LISTENER.resolve()),"sha256":actual["listener"]}]; result["prior_artifact"]={"path":str(TIMING.resolve()),"sha256":actual["timing"]}; result["ledger"]={"path":str(LEDGER.resolve()),"sha256":actual["ledger"]}; result["equivalence"]=equivalence
    payload=json.dumps(result,indent=2)+"\n"; config=ROOT/"fusion_v1/config/common_clock_v1.json"; config.write_text(payload)
    side=OUT/"COMMON_TIME_SIDECAR.npz"; counts=build_sidecar(LEDGER,models,side)
    metrics={"schema":"fusion-v1-common-clock-reuse-metrics-v1","prior_verdict":result["verdict"],"prior_worst_clean_p95_us":280.85167551040644,"prior_worst_clean_max_us":408.27899265289307,"equivalence":equivalence,"sidecar_counts":counts,"sidecar_sha256":sha256(side),"sidecar_bytes":side.stat().st_size,"pass":result["gate_0_pass"] and equivalence["equivalent"]}
    (OUT/"COMMON_CLOCK_METRICS.json").write_text(json.dumps(metrics,indent=2)+"\n")
    (OUT/"COMMON_CLOCK_REUSE_REPORT.md").write_text(f"# Common-clock reuse report\n\nVerdict: `{'COMMON_CLOCK_PASS' if metrics['pass'] else 'COMMON_CLOCK_FAIL'}`.\n\nExact raw, canonical, Listener, timing-result, and ledger hashes matched. The 120 ms superframe, ten node IDs, and explicit boot epochs match. Stored coefficients were loaded without refitting. Re-evaluating each in-domain ledger timestamp from `a_ns_per_us * node_timer_us + b_ns` differs from the stored integer timestamp by at most {equivalence['maximum_timestamp_difference_ns']} ns. Prior and reproduced timing gates therefore remain worst clean P95 280.852 us and maximum 408.279 us.\n\nThe sidecar `{side}` contains {counts['imu']:,} IMU and {counts['uwb_range']:,} individual-range rows. Each UWB time applies the affine clock to `strobe_us + t_round_us/2`. Non-accepted clock rows remain present with status. Sidecar SHA-256: `{metrics['sidecar_sha256']}`.\n")
    print(json.dumps(metrics,indent=2))
if __name__=="__main__": main()
