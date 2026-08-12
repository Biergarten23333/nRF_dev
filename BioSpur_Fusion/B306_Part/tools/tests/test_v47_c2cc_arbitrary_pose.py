import unittest
import numpy as np

from v47_c2cc_arbitrary_pose import (PREREGISTERED, StableDwell, apply_calibration,
    coverage_metrics, distinct_direction, farthest_direction, fit_and_select, fit_model,
    heldout_metrics, parse_imu_samples, pose_token_transition, stability_metrics)
from v47_c2cc_arbitrary_pose_capture import token_disposition


class ArbitraryPoseTests(unittest.TestCase):
    def test_exact_token_state_machine(self):
        self.assertEqual(token_disposition("FIXED",("FIXED",)),"ACCEPT")
        self.assertEqual(token_disposition("fixed",("FIXED",)),"REJECT")
        self.assertEqual(token_disposition("STOP",("FIXED",)),"STOP")
        self.assertEqual(pose_token_transition("WAIT_FIXED","NEXT"),"WAIT_FIXED")
        self.assertEqual(pose_token_transition("WAIT_FIXED","FIXED"),"COLLECTING")

    def test_interrupted_stability_resets(self):
        d=StableDwell(15);self.assertEqual(d.update(0,True),0);self.assertEqual(d.update(10,True),10)
        self.assertEqual(d.update(11,False),0);self.assertEqual(d.update(20,True),0);self.assertEqual(d.update(35,True),15)
    def test_units_and_axis_order(self):
        f={"base_us":"100","seq":"65535","temp_raw":"2500","samples":"0,2048,-4096,1024,16,-32,0;5000,0,0,2048,0,0,0"}
        x=parse_imu_samples(f,1.0)
        self.assertEqual(x[0]["accel_g"],[1,-2,.5]);self.assertEqual(x[0]["seq"],65535);self.assertEqual(x[1]["seq"],0)

    def test_stable_and_unstable(self):
        rng=np.random.default_rng(1);a=np.c_[rng.normal(0,.001,200),rng.normal(0,.001,200),rng.normal(1,.001,200)];w=rng.normal(0,.02,(200,3))
        s=[{"accel_g":x,"gyro_dps":y} for x,y in zip(a,w)];self.assertTrue(stability_metrics(s)["stable"])
        s[0]["gyro_dps"]=[10,0,0];self.assertFalse(stability_metrics(s)["stable"])

    def test_duplicate_and_coverage(self):
        ok,angle=distinct_direction([1,.01,0],[[1,0,0]]);self.assertFalse(ok);self.assertLess(angle,PREREGISTERED["minimum_pairwise_direction_deg"])
        c=coverage_metrics([[1,0,0],[-1,0,0],[0,1,0],[0,-1,0],[0,0,1],[0,0,-1]])
        self.assertGreater(c["direction_covariance_min_eigenvalue"],.3)
        target,clear=farthest_direction([[1,0,0],[-1,0,0],[0,1,0],[0,-1,0]])
        self.assertGreater(abs(target[2]),.99);self.assertGreater(clear,89)

    def test_degenerate_coverage(self):
        c=coverage_metrics([[1,0,0],[.99,.01,0],[.98,.02,0],[.97,.03,0]])
        self.assertLess(c["direction_covariance_min_eigenvalue"],1e-6)

    def test_models_recover_and_heldout_no_refit(self):
        rng=np.random.default_rng(2);d=rng.normal(size=(24,3));d/=np.linalg.norm(d,axis=1)[:,None]
        C=np.array([[1.05,.02,0],[.02,.96,.01],[0,.01,1.02]]);b=np.array([.03,-.02,.01]);raw=(np.linalg.inv(C)@d.T).T+b
        poses=[raw[i:i+1]+rng.normal(0,.0005,(80,3)) for i in range(18)]
        result=fit_and_select(poses);self.assertIn(result["selected_model"],("DIAGONAL_SCALE","FULL_SPD"))
        frozen=dict(result["selected"]);v=heldout_metrics([raw[i:i+1].repeat(80,0) for i in range(18,22)],frozen)
        self.assertTrue(v["pass"]);self.assertEqual(frozen,result["selected"])

    def test_diagonal_scale_calibration_and_determinism(self):
        rng=np.random.default_rng(12);d=rng.normal(size=(900,3));d/=np.linalg.norm(d,axis=1)[:,None]
        scale=np.diag([1.06,.95,1.02]);bias=np.array([.02,-.03,.01]);a=(np.linalg.inv(scale)@d.T).T+bias
        one=fit_model(a,"DIAGONAL_SCALE");two=fit_model(a,"DIAGONAL_SCALE")
        self.assertEqual(one,two);self.assertTrue(np.allclose(one["correction_matrix"],scale,atol=1e-4));self.assertTrue(np.allclose(one["bias_g"],bias,atol=1e-4))

    def test_bias_model(self):
        rng=np.random.default_rng(4);d=rng.normal(size=(1000,3));d/=np.linalg.norm(d,axis=1)[:,None];b=np.array([.04,-.03,.02]);f=fit_model(d+b,"BIAS_ONLY")
        self.assertLess(np.linalg.norm(np.asarray(f["bias_g"])-b),1e-4);self.assertLess(np.std(np.linalg.norm(apply_calibration(d+b,f),axis=1)),1e-6)

    def test_full_spd_and_robust_outlier(self):
        rng=np.random.default_rng(8);d=rng.normal(size=(1200,3));d/=np.linalg.norm(d,axis=1)[:,None]
        C=np.array([[1.08,.03,-.01],[.03,.94,.02],[-.01,.02,1.03]]);b=np.array([.02,-.01,.03]);a=(np.linalg.inv(C)@d.T).T+b;a[0]=[4,-4,4]
        f=fit_model(a,"FULL_SPD");self.assertTrue(np.all(np.linalg.eigvalsh(f["correction_matrix"])>0));self.assertLess(np.linalg.norm(np.asarray(f["bias_g"])-b),.02)


if __name__=="__main__":unittest.main()
