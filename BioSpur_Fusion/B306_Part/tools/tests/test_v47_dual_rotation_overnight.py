import unittest

import numpy as np

from analyze_v47_dual_rotation_overnight import fit_orbit, sequence_stats


class DualRotationAnalysisTests(unittest.TestCase):
    def test_sequence_stats_separates_reset_boundary(self):
        values=np.array([10,11,14,1,2],dtype=np.uint16)
        local=np.array([100,200,300,10,20],dtype=np.uint64)
        result=sequence_stats(values,local,65536)
        self.assertEqual(result["gaps"],1)
        self.assertEqual(result["missing"],2)
        self.assertEqual(result["reset_boundaries"],1)

    def test_orbit_fit_recovers_unequal_radius_input(self):
        t=np.arange(0,120,.12);angle=.94*t
        xyz=np.c_[1200+600*np.cos(angle),800+600*np.sin(angle),np.full(len(t),500.)]
        fit=fit_orbit(t,xyz)
        self.assertEqual(fit["status"],"OK")
        self.assertLess(abs(fit["radius_mm"]-600),1e-6)
        self.assertLess(abs(fit["angular_rate_rad_s"]-.94),1e-6)

    def test_orbit_fit_fails_closed_on_too_few_points(self):
        fit=fit_orbit(np.arange(10.),np.zeros((10,3)))
        self.assertEqual(fit["status"],"INSUFFICIENT")


if __name__ == "__main__":
    unittest.main()
