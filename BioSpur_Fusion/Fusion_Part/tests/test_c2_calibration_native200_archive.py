import numpy as np

from biospur_fusion.c2_coupled_progressive.calibration_native200_archive import (
    ACTION_KEYS,
    CalibrationNative200Archive,
)
from biospur_fusion.c2_coupled_progressive.contracts import EPISODES


def test_archive_exposes_exact_acquired_protocol_inventory() -> None:
    archive = CalibrationNative200Archive.from_sealed_archives()

    assert tuple(archive.actions) == EPISODES
    assert len(archive.actions) == 19
    assert "01_neutral_sway" not in archive.actions
    assert ACTION_KEYS["00_initial_still"] == "00"
    assert ACTION_KEYS["02_t_pose"] == "01"
    assert ACTION_KEYS["19_heel_to_butt_right"] == "18"


def test_every_action_is_native_200_inside_spans_and_read_only() -> None:
    archive = CalibrationNative200Archive.from_sealed_archives()

    for source in archive.actions.values():
        same_span = source.span[1:] == source.span[:-1]
        np.testing.assert_array_equal(np.diff(source.time_us)[same_span], 5000)
        assert np.all(np.diff(source.time_us)[~same_span] > 5000)
        assert source.time_us.flags.writeable is False
        assert source.calibrated_acc_mps2.flags.writeable is False
        for segment in source.trajectory.values():
            assert segment["quat_world_segment_wxyz"].flags.writeable is False
            assert segment["mask"].flags.writeable is False
            assert np.all(segment["mask"])
