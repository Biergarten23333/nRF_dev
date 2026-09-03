from __future__ import annotations

from pathlib import Path

import opensim as osim

from biospur_fusion.c2_3a_kinematics import load_frozen_c2_3a
from biospur_fusion.c2_3b_official_opensense.adapter import (
    BODY_BY_SEGMENT,
    IMU_FRAME_BY_SEGMENT,
    configure_opensim_log,
)
from biospur_fusion.c2_3b_official_opensense.validate import (
    summarize_official_orientation_errors,
    validate_orientation_replay,
)


WORKSPACE = Path(__file__).resolve().parents[1]
EVIDENCE = WORKSPACE / "logs/c2_3b_official_opensense_20260902_094743"
PILOT = EVIDENCE / "real_pilot_00_02"


def test_source_has_no_forbidden_solver_dependency():
    source = WORKSPACE / "src/biospur_fusion/c2_3b_official_opensense"
    text = "\n".join(path.read_text() for path in sorted(source.glob("*.py")))
    assert "scipy.optimize" not in text
    assert "least_squares" not in text
    assert "c2_3b_imu_ik" not in text


def test_official_input_table_replays_frozen_values():
    frozen = load_frozen_c2_3a(workspace=WORKSPACE)
    for capture_label, episode_key in (
        ("00_initial_still", "00"),
        ("02_t_pose", "01"),
    ):
        result = validate_orientation_replay(
            frozen.episodes[episode_key],
            PILOT / capture_label / "input/frozen_orientations.sto",
        )
        assert result["passed"]
        assert result["row_count"] == 701
        assert result["max_quaternion_abs_error"] <= 1e-15


def test_configured_model_frame_and_forearm_ownership(tmp_path):
    configure_opensim_log(tmp_path / "opensim.log")
    model = osim.Model(str(PILOT / "model/c2_official_configured.osim"))
    state = model.initSystem()
    for segment, body_name in BODY_BY_SEGMENT.items():
        frame = osim.PhysicalOffsetFrame.safeDownCast(
            model.findComponent(IMU_FRAME_BY_SEGMENT[segment])
        )
        assert frame is not None
        assert frame.getParentFrame().getName() == body_name
        translation = frame.get_translation()
        orientation = frame.get_orientation()
        assert [translation[index] for index in range(3)] == [0.0, 0.0, 0.0]
        assert [orientation[index] for index in range(3)] == [0.0, 0.0, 0.0]
    for name in ("pro_sup_l", "pro_sup_r"):
        coordinate = model.getCoordinateSet().get(name)
        assert coordinate.getLocked(state)
        assert coordinate.getValue(state) == 0.0


def test_official_outputs_have_complete_native_errors():
    expected = set(IMU_FRAME_BY_SEGMENT.values())
    for capture_label in ("00_initial_still", "02_t_pose"):
        result = summarize_official_orientation_errors(
            PILOT
            / capture_label
            / "official_output/official_ik.sto_orientationErrors.sto"
        )
        assert result["rows"] == 701
        assert result["all_finite"]
        assert set(result["sensors"]) == expected
