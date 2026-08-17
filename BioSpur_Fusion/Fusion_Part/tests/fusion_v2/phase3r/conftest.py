import numpy as np
import pytest

from biospur_fusion.imu_pose_v1.mapping import FrozenOperatorMapping
from biospur_fusion.imu_pose_v1.synthetic import generate


@pytest.fixture
def synthetic_short():
    return generate(seed=12,duration_s=5.5,noise=True,irregular=True,gaps=False,biases=True,transients=False,outliers=False)


def mapping_for(dataset):
    return FrozenOperatorMapping.from_payload(
        {"mapping":dataset.mapping,"binding_authority":"OPERATOR_RECORDED_POST_CAPTURE"},
        capture_id="Capture_2_with_JOINT_LABEL",session_id="capture_2_with_joint_label",
        donning_id="capture_2_with_joint_label_donning_01")
