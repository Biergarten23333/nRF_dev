import numpy as np
import torch
from types import SimpleNamespace
from test_c2_arm_protocol import fixture
from test_c2_bend_protocol import inputs
from biospur_fusion.c2_five_calibration.protocol_pose import build_bend_protocol
from biospur_fusion.c2_five_calibration.protocol_linearization import protocol_window


def test_window_keeps_information_weights_and_original_pose_row_order():
    arm,pose=fixture(np.linspace(0.,.3,601),sensor_error=.2)
    contracts,actions=inputs();bend=build_bend_protocol(contracts,actions)
    prior=SimpleNamespace(arm_protocol=arm)
    p=torch.ones(601,9,dtype=torch.float64);delta=torch.zeros(601,4,dtype=torch.float64)
    full=arm.energy_for_action('06_elbow_left',delta,pose)+bend.energy_for_action('06_elbow_left',p)
    total=0.
    for lo,hi in [(0,350),(350,601)]:
        local,b=protocol_window(prior,bend,lo,hi)
        total+=local.arm_protocol.energy_for_action('06_elbow_left',delta[lo:hi],pose[lo:hi])+b.energy_for_action('06_elbow_left',p[lo:hi])
    torch.testing.assert_close(total,full,atol=1e-14,rtol=1e-14)
    assert len(arm.rows[0].index)==601
    np.testing.assert_array_equal(bend.rows[0].index,np.arange(300,600))
