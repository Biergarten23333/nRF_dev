import hashlib, json
from pathlib import Path

import numpy as np

from v47_c2cc_revalidation_v2 import exact_binomial_interval, sensor_transient_gate, systematic_gate, transient_runs
from v47_q1_eskf import G_MPS2, Q1T4ESKF

ROOT=Path(__file__).resolve().parents[3]
OLD=ROOT/"B306_Part/logs/v47_c2cc_arbitrary_pose_calibration_20260812_201945"
RUN=ROOT/"B306_Part/logs/v47_c2cc_calibration_revalidation_v2_20260812_214846"


def sha(path): return hashlib.sha256(path.read_bytes()).hexdigest()


def initialized():
 f=Q1T4ESKF();f.initialize_from_stationary(np.array([0.,0.,G_MPS2]),np.zeros(3));
 for i in range(400):f.propagate(i*.005,np.array([0.,0.,G_MPS2]),np.zeros(3))
 return f


def test_historical_fail_and_frozen_hash_preserved():
 assert json.loads((OLD/"CALIBRATION_RESULT.json").read_text())["primary_verdict"]=="C2CC_DEVICE_CALIBRATION_FAIL"
 assert sha(OLD/"ACCEL_CALIBRATION_PROFILE.json")=="10895c252adbe23cb26ef1e0824abf460f3b8c03fd04d63508e06242fe63a73c"


def test_protocol_frozen_before_capture_and_no_refit_contract():
 protocol=json.loads((RUN/"REVALIDATION_V2_PROTOCOL.json").read_text())
 assert sha(RUN/"REVALIDATION_V2_PROTOCOL.json")=="d87503c8bcf100c9b823fd1fd08ae6e6b72eb255d03d4f2605c9fdd849e557dd"
 assert protocol["validation_data_use"]=="HELD_OUT_ONLY_NO_REFIT"
 assert protocol["frozen_calibration"]["parameter_changes_allowed"]==0
 assert protocol["operator_tokens"]==["FIXED","STOP"] and len(protocol["pose_order"])==6


def test_read_only_identity_query_occurs_after_live_catchup_drain():
 source=(ROOT/"B306_Part/tools/v47_c2cc_revalidation_v2_capture.py").read_text()
 assert source.index('rec.phase="WARMUP_AND_CDC_DRAIN"') < source.index('for cmd in ("MASTER STATUS","LIST"')


def test_systematic_and_transient_gates_are_independent():
 rng=np.random.default_rng(47);poses=[]
 for axis in np.eye(3):
  for sign in (-1,1):poses.append(sign*axis+rng.normal(0,.001,(6400,3)))
 system,_=systematic_gate(poses,[0,0,0],np.eye(3));assert system["pass"]
 samples=[{"node_us":i*5000,"transient_candidate":i in (10,11,12)} for i in range(38400)]
 audit=[{"transient_candidate":x["transient_candidate"],"accepted":False,"numerical_pass":True} for x in samples]
 transient=sensor_transient_gate(samples,audit);assert not transient["pass"] and not transient["checks"]["no_burst_ge_3"]


def test_isolated_transient_classification_and_exact_count():
 samples=[{"node_us":i*5000,"transient_candidate":i==50} for i in range(1000)]
 runs=transient_runs(samples)
 assert len(runs)==1 and len(runs[0])==1 and runs[0][0]["node_us"]==250000


def test_exact_rate_and_confidence_interval():
 lo,hi=exact_binomial_interval(0,38400);assert lo==0 and hi<1/10000
 lo,hi=exact_binomial_interval(1,10000);assert lo>0 and hi>1/10000


def test_q1_rejects_isolated_and_two_sample_anomaly_without_discontinuity_or_psd_loss():
 f=initialized();q=f.q.copy()
 for _ in range(2):
  f.propagate(f.last_timestamp_s+.005,[0.,1.2*G_MPS2,0.],np.zeros(3))
  d=f.gravity_update_causal([0.,1.2*G_MPS2,0.]);assert not d.accepted and d.reason=="INNOVATION_NIS_REJECTED"
 assert f.gravity_update_rejections==2 and abs(float(np.linalg.norm(f.q))-1)<1e-12
 assert float(np.linalg.eigvalsh(f.P)[0])>0 and abs(float(q@f.q))>.999999


def test_stationary_nominal_accepted_and_sustained_motion_not_integrity_rejected():
 f=initialized();d=f.gravity_update_causal([0.,0.,G_MPS2]);assert d.accepted
 for _ in range(20):
  f.propagate(f.last_timestamp_s+.005,[2.,0.,G_MPS2],[0.,0.,.5])
  d=f.gravity_update_causal([2.,0.,G_MPS2],motion_state="MOVING")
  assert not d.accepted and d.reason=="MOTION_GRAVITY_INELIGIBLE"
 assert f.gravity_motion_ineligible==20 and f.gravity_update_rejections==0


def test_single_transient_and_burst_accounting_and_determinism():
 samples=[{"node_us":i*5000,"transient_candidate":i==100} for i in range(40000)]
 audit=[{"transient_candidate":x["transient_candidate"],"accepted":False,"numerical_pass":True} for x in samples]
 a=sensor_transient_gate(samples,audit);b=sensor_transient_gate(samples,audit)
 assert json.dumps(a,sort_keys=True)==json.dumps(b,sort_keys=True)
 assert a["transient_count"]==1 and a["maximum_consecutive"]==1
 assert a["checks"]["point_rate_below_1_per_10000"]
 assert not a["rate_confidence_exposure_sufficient"]


def test_derivation_has_no_fit_or_validation_leakage_path():
 source=(ROOT/"B306_Part/tools/derive_v47_c2cc_revalidation_v2.py").read_text()
 assert "fit_model" not in source and "fit_and_select" not in source
 assert 'profile["model_selection"]["selected"]' in source


def test_six_pose_flow_accepts_only_fixed_or_stop():
 source=(ROOT/"B306_Part/tools/v47_c2cc_revalidation_v2_capture.py").read_text()
 assert source.count('"POSE ')>=6
 assert 'proto.wait(instruction,("FIXED",)' in source
 assert '"NEXT"' not in source and '"REPEAT"' not in source
