from types import SimpleNamespace
import numpy as np
from scipy.spatial.transform import Rotation
from tools.build_c2_continuous_pose import batch_fk
from biospur_fusion.c2_uwb_calibration.articulated_range import SEGMENTS, corrected_proxy_points


def test_batch_fk_matches_existing_geometry():
    rng = np.random.default_rng(8)
    rotations = {s:Rotation.from_rotvec(rng.normal(size=(5,3))).as_matrix() for s in SEGMENTS}
    geometry = SimpleNamespace(torso_height_m=.5, shoulder_span_m=.4, hip_span_m=.25,
        segment_length_m={s:.35 for s in SEGMENTS})
    actual = batch_fk(rotations, geometry)
    for i in range(5):
        expected = corrected_proxy_points({s:r[i] for s,r in rotations.items()},
                                         {s:np.zeros(3) for s in SEGMENTS}, geometry)
        for name in expected:
            np.testing.assert_allclose(actual[name][i], expected[name], atol=1e-14)
