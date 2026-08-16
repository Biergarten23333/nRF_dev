from __future__ import annotations
import csv,gzip,json,hashlib
from pathlib import Path
import numpy as np

ROOT=Path(__file__).resolve().parents[2]; CAP=ROOT/"logs/v47_ten_node_body_calibration_20260814_093601"; OLD=CAP/"analysis_body_fusion_v2"; OUT=ROOT/"logs/fusion_v1_reference_20260816_130000"
G=9.80665; ACC=2048.0; GYRO=16.384
A=1000001.0719517616; B=-156496335359.1971
def global_ns(mono): return int(round((A*mono+B)*1000))
def robust(x):
 x=np.asarray(x,float); med=float(np.median(x)); mad=float(1.4826*np.median(np.abs(x-med))); return med,mad
def main():
 actions=[]
 with (CAP/"analysis_body_calibration_v1/run_a/ACTION_LEDGER_RECONSTRUCTED.csv").open() as f:
  for r in csv.DictReader(f):
   if r["selected"]!="True": continue
   role="held_out" if r["action"] in {"golf_swing","boxing"} else "validation" if r["action"] in {"walk","final_still"} else "development"
   actions.append({"action":r["action"],"attempt":int(r["attempt"]),"role":role,"operator_start_monotonic_s":float(r["action_start_monotonic"]),"operator_stop_monotonic_s":float(r["operator_stop_upper_monotonic"]),"start_global_time_ns":global_ns(float(r["action_start_monotonic"])),"stop_global_time_ns":global_ns(float(r["operator_stop_upper_monotonic"])),"refinement":"operator bracket retained; signal refinement deferred except low-motion interior"})
 (OUT/"ACTION_INTERVALS_REFINED.json").write_text(json.dumps({"schema":"fusion-v1-action-intervals-v1","annotation_bridge_only":True,"held_out_not_opened":True,"actions":actions},indent=2)+"\n")
 still=next(x for x in actions if x["action"]=="initial_still" and x["attempt"]==2); lo,hi=still["start_global_time_ns"],still["stop_global_time_ns"]
 imu_stats={}; pair_rows=[]
 with np.load(OLD/"TIME_EVENT_LEDGER.npz",allow_pickle=False) as z:
  for key in sorted(k for k in z.files if k.startswith("imu_")):
   node=key[4:]; x=z[key]; accepted=x["status"]==1; s=accepted&(x["global_time_ns"]>=lo)&(x["global_time_ns"]<=hi)
   acc=x["acc_raw"][s].astype(float)/ACC*G; gyro=np.deg2rad(x["gyro_raw"][s].astype(float)/GYRO)
   times=x["global_time_ns"][accepted]; dt=np.diff(times)/1e6; gaps=dt[dt>7.5]
   bias=gyro.mean(0); noise=gyro.std(0,ddof=1); anorm=np.linalg.norm(acc,axis=1)
   ac=[]
   for j in range(3):
    y=gyro[:,j]-gyro[:,j].mean(); ac.append(float(np.dot(y[:-1],y[1:])/np.dot(y,y)) if len(y)>1 and np.dot(y,y)>0 else 0)
   imu_stats[node]={"static_samples":int(s.sum()),"gyro_bias_rad_s":bias.tolist(),"gyro_noise_rad_s":noise.tolist(),"gyro_lag1_correlation":ac,"accel_mean_mps2":acc.mean(0).tolist(),"accel_noise_mps2":acc.std(0,ddof=1).tolist(),"gravity_norm_mean_mps2":float(anorm.mean()),"gravity_norm_bias_mps2":float(anorm.mean()-G),"gravity_norm_robust_sigma_mps2":robust(anorm)[1],"accepted_samples":int(accepted.sum()),"median_cadence_ms":float(np.median(dt)),"gap_count_gt_7_5ms":int(len(gaps)),"max_gap_ms":float(gaps.max(initial=0))}
  for key in sorted(k for k in z.files if k.startswith("uwb_")):
   node=key[4:]; x=z[key]; sweep=(x["status"]==1)&(x["global_time_ns"]>=lo)&(x["global_time_ns"]<=hi)
   for slot in range(8):
    aid=x["anchor_id"][:,slot]; valid=sweep&((x["valid_mask"]&(1<<slot))!=0)&(x["range_mm"][:,slot]>0)&(x["range_mm"][:,slot]<65535)
    values=x["range_mm"][valid,slot].astype(float)/1000; med,mad=robust(values) if len(values) else (float('nan'),float('nan'))
    d=np.diff(values); jumps=int(np.count_nonzero(np.abs(d)>max(.5,5*mad))) if len(d) else 0
    pair_rows.append({"node":node,"anchor_id":slot,"static_candidate_sweeps":int(sweep.sum()),"valid_static_ranges":int(valid.sum()),"availability":float(valid.sum()/max(1,sweep.sum())),"range_median_m":med,"range_robust_sigma_m":mad,"jump_count":jumps,"lag1_correlation":float(np.corrcoef(values[:-1],values[1:])[0,1]) if len(values)>2 and np.std(values)>0 else 0.0})
 with gzip.open(OUT/"UWB_PAIR_STATISTICS.csv.gz","wt",newline="") as f:
  w=csv.DictWriter(f,fieldnames=list(pair_rows[0])); w.writeheader(); w.writerows(pair_rows)
 (OUT/"IMU_STATISTICS.json").write_text(json.dumps(imu_stats,indent=2)+"\n")
 (OUT/"IMU_SEMANTICS_AND_SCALE_REPORT.md").write_text("# IMU semantics and scale\n\nThe fitted JY61P six-axis register window is `0x34..0x39`, sampled on B306 TIMER2. Existing firmware/validated frontend semantics give acceleration 2048 LSB/g (±16 g) and gyro 16.384 LSB/(deg/s) (±2000 deg/s), converted with g=9.80665 m/s². The new characterization reused those hardware constants and checked all ten nodes against the real initial-low-motion interval. Per-node gravity norms, gyro bias/noise, cadence, correlation, and gaps are in `IMU_STATISTICS.json`. Board axes remain sensor-frame axes; no old sensor-to-segment rotation is accepted. Axis signs require the clean-slate functional-motion calibration and are not inferred from attractive rendering.\n")
 spreads=[r["range_robust_sigma_m"] for r in pair_rows if np.isfinite(r["range_robust_sigma_m"])]
 (OUT/"SENSOR_CHARACTERIZATION.md").write_text(f"# Sensor characterization\n\nHeld-out golf and boxing were not opened. Initial-low-motion statistics use the operator bracket mapped through the validated annotation bridge. All ten IMUs use node-specific statistics; no shared covariance is assumed. Across {len(pair_rows)} node-anchor pairs, static robust range spread spans {min(spreads):.4f}--{max(spreads):.4f} m. Pair-specific rows preserve availability, median, robust sigma, jumps and lag-1 correlation in `UWB_PAIR_STATISTICS.csv.gz`. These are descriptive initial statistics, not residual-to-ground-truth bias estimates. Sustained bias, orientation dependence, vertical weakness and recovery require the clean articulated prediction/T4 comparison and remain unresolved.\n")
 print(json.dumps({"nodes":len(imu_stats),"pairs":len(pair_rows),"spread_min":min(spreads),"spread_max":max(spreads)},indent=2))
if __name__=="__main__": main()
