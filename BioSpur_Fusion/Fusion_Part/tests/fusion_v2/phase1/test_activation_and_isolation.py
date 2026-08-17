import sys
from pathlib import Path
import numpy as np
ROOT=Path(__file__).resolve().parents[5];sys.path.insert(0,str(ROOT/"BioSpur_Fusion/Fusion_Part/src"))
from biospur_fusion.imu_frontend_v2.filter import FrontendConfig,ImuFrontend,G
from biospur_fusion.imu_frontend_v2.so3 import angle_between
def replay(cfg=FrontendConfig(),bg=None,ba=None,age=(0,5000),gap=False):
 f=ImuFrontend("BSF31CC",1,cfg);f.bg=np.zeros(3) if bg is None else np.asarray(bg,float);f.ba=np.zeros(3) if ba is None else np.asarray(ba,float);t=0
 for i in range(300):
  t+=100000 if gap and i==150 else 5000;f.step((0.15*np.sin(i/20),0,G),(0.05,0.1,0.2),t,t*1000,age)
 return f
def test_gravity_ablation_changes_orientation_and_uncertainty():
 a=replay();b=replay(FrontendConfig(gravity_stride=10**9));assert angle_between(a.q,b.q)>1e-5 and abs(np.trace(a.P)-np.trace(b.P))>1e-6
def test_gyro_bias_perturbation_changes_orientation():assert angle_between(replay().q,replay(bg=(.02,0,0)).q)>1e-3
def test_ba_nuisance_perturbation_changes_gravity_path():assert angle_between(replay().q,replay(ba=(.5,0,0)).q)>1e-5
def test_sample_age_envelope_changes_bound_not_gaussian_covariance():
 a=replay(age=(0,0));b=replay(age=(0,5000));assert b.timing_sensitivity_rad>a.timing_sensitivity_rad and np.array_equal(a.P,b.P)
def test_gap_path_changes_uncertainty_without_reset():
 a=replay();b=replay(gap=True);assert b.gap_events==1 and np.trace(b.P)>np.trace(a.P) and abs(np.linalg.norm(b.q)-1)<1e-12
def test_state_dimensions_and_cross_covariance():
 f=replay();assert f.P.shape==(9,9) and np.any(abs(f.P[:3,3:6])>0)
def test_ten_independent_yaw_identifiers():
 ids={ImuFrontend(f"BSF{i:04X}",0).summary()["yaw_gauge_id"] for i in range(10)};assert len(ids)==10
def test_forbidden_inputs_not_in_production_api():
 import inspect,biospur_fusion.imu_frontend_v2.runner as r
 from biospur_fusion.phase1.ingress import ImuObservation
 assert set(inspect.signature(r.run_partition).parameters)=={"input_contract_path","partition","config"}
 assert not set(ImuObservation.__dataclass_fields__) & {"q1","t4","uwb_range","historical_pose","anatomical_role"}
