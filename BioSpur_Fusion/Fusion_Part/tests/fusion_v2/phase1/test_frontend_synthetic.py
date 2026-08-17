import json,sys
from pathlib import Path
import numpy as np
import pytest
ROOT=Path(__file__).resolve().parents[5];sys.path.insert(0,str(ROOT/"BioSpur_Fusion/Fusion_Part/src"))
from biospur_fusion.imu_frontend_v2.filter import FrontendConfig,ImuFrontend,G
from biospur_fusion.imu_frontend_v2.so3 import angle_between,q_to_R,qexp,qmul,qnormalize
from biospur_fusion.imu_frontend_v2.timebase import timer32_delta,native_dt_seconds
def feed(f,n=200,gyro=(0,0,0),acc=(0,0,G),dts=None):
 t=1_000_000
 for i in range(n):
  dt=(dts[i] if dts else 0.005);t+=round(dt*1e6);o=f.step(acc,gyro,t,t*1000)
 return o
def test_zero_motion():
 f=ImuFrontend("BSF31CC",1);feed(f);assert angle_between(f.q,[1,0,0,0])<1e-10
@pytest.mark.parametrize("axis",range(3))
def test_constant_rate_signed_axes(axis):
 cfg=FrontendConfig(gravity_stride=10**9);f=ImuFrontend("BSF31CC",1,cfg);w=np.zeros(3);w[axis]=0.4;feed(f,n=200,gyro=w,dts=[0.004+(i%5)*0.0005 for i in range(200)]);elapsed=sum(0.004+(i%5)*0.0005 for i in range(1,200));truth=qexp(w*elapsed);assert angle_between(f.q,truth)<2e-6
def test_piecewise_rate():
 f=ImuFrontend("BSF31CC",1,FrontendConfig(gravity_stride=10**9));t=0
 for w in ((.2,0,0),(0,-.3,0),(0,0,.4)):
  for _ in range(50):t+=5000;f.step((0,0,G),w,t,t*1000)
 assert abs(np.linalg.norm(f.q)-1)<1e-12
def test_irregular_dt_differs_from_fixed():
 f=ImuFrontend("n",0,FrontendConfig(gravity_stride=10**9));d=[.003,.007]*50;feed(f,100,(0,0,1),dts=d);truth=sum(d[1:]);assert abs(2*np.arccos(f.q[0])-truth)<2e-6 and abs(truth-.005*99)>.001
def test_timer_wrap():assert timer32_delta(3,0xfffffffe)==5
def test_nonincreasing_rejected():
 with pytest.raises(ValueError):native_dt_seconds(2,2)
def test_gap_inflates_without_reset():
 f=ImuFrontend("n",0);feed(f,10);q=f.q.copy();tr=np.trace(f.P);f.step((0,0,G),(1,0,0),2_000_000,2_000_000_000);assert f.gap_events==1 and abs(f.q@q)>0.999 and np.trace(f.P)>tr
def test_covariance_psd_symmetric():
 f=ImuFrontend("n",0);feed(f,500);assert np.max(abs(f.P-f.P.T))<1e-10 and np.linalg.eigvalsh(f.P).min()>-1e-9
def test_linear_acceleration_downweights_gravity():
 f=ImuFrontend("n",0);feed(f,10);before=f.gravity_rejected;t=f.last_timer_us
 for _ in range(10):t+=5000;f.step((15,0,G),(0,0,0),t,t*1000)
 assert f.gravity_rejected>before
def test_ba_is_three_vector_and_active():
 f1=ImuFrontend("n",0);f2=ImuFrontend("n",0);f2.ba=np.array([.5,0,0]);feed(f1,20);feed(f2,20);assert f1.ba.shape==(3,) and angle_between(f1.q,f2.q)>1e-6
def test_bias_random_walk_covariance_active():
 f=ImuFrontend("n",0);feed(f,20);assert f.bias_process_updates==19 and np.all(np.diag(f.P)[3:]>0)
def test_sample_age_sensitivity():
 f=ImuFrontend("n",0);feed(f,20,gyro=(2,0,0));assert f.timing_sensitivity_rad>0
def test_yaw_gauge_noncontraction():
 f=ImuFrontend("n",0);before=f.P[2,2];feed(f,100);assert f.P[2,2]>=before-1e-10
def test_q_sign_equivalence():assert angle_between([1,0,0,0],[-1,0,0,0])==0
def test_deterministic_replay():
 a=ImuFrontend("n",0);b=ImuFrontend("n",0);feed(a,200,gyro=(.1,.2,.3));feed(b,200,gyro=(.1,.2,.3));assert np.array_equal(a.q,b.q) and np.array_equal(a.P,b.P)
def test_boot_isolation():
 a=ImuFrontend("n",0);b=ImuFrontend("n",1);feed(a,10);feed(b,10);assert a.boot_epoch!=b.boot_epoch and a.last_timer_us==b.last_timer_us
def test_invalid_input_rejected():
 f=ImuFrontend("n",0);assert not f.step((np.nan,0,0),(0,0,0),1,1000)["valid"]
def test_no_forbidden_fields_in_summary():
 f=ImuFrontend("n",0);feed(f,2);s=json.dumps(f.summary()).lower();assert all(x not in s for x in ('uwb','q1','t4','pelvis','anatomical'))
