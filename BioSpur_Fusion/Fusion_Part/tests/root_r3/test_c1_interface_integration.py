import math

from biospur_fusion.root_r3.data import default_authorized_paths, load_c1_uwb, load_m1
from biospur_fusion.root_r3.metrics import reproduce_historical_0902


def test_authorized_c1_reproduces_historical_scale_without_importing_r2_code():
    paths = default_authorized_paths()
    table = load_c1_uwb(paths, load_m1(paths.m1_npz))
    result = reproduce_historical_0902(table, 211.65011463698465, 219.6501813060022)
    assert result["events"] == 663
    assert math.isclose(result["robust_cross_tag_scale_m"], 0.902283933, abs_tol=5e-10)
