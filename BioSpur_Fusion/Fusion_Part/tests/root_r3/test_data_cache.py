import numpy as np
import pytest

from biospur_fusion.root_r3.data import load_imu_cache, save_imu_cache
from biospur_fusion.root_r3.models import ImuSample


def test_imu_cache_round_trip_and_source_binding(tmp_path):
    samples = [
        ImuSample(
            measurement_time_s=1.0 + index * 0.005,
            availability_time_s=1.01 + index * 0.005,
            specific_force_sensor_mps2=np.array([index, 0.0, 9.80665]),
            rotation_world_from_sensor=np.eye(3),
            source_sequence=index,
            m1_valid=index != 1,
            m1_reset=index == 2,
        )
        for index in range(3)
    ]
    path = tmp_path / "imu.npz"
    source_hashes = {"raw": "abc", "m1": "def"}
    save_imu_cache(path, samples, source_hashes)

    loaded = load_imu_cache(path, source_hashes)
    assert len(loaded) == len(samples)
    assert [sample.source_sequence for sample in loaded] == [0, 1, 2]
    assert np.allclose(loaded[2].specific_force_sensor_mps2, samples[2].specific_force_sensor_mps2)
    assert loaded[1].m1_valid is False
    assert loaded[2].m1_reset is True

    with pytest.raises(ValueError, match="source hash mismatch"):
        load_imu_cache(path, {"raw": "changed", "m1": "def"})
