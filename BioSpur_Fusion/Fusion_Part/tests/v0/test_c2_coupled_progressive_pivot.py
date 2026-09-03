from __future__ import annotations

from pathlib import Path

import numpy as np

from biospur_fusion.c2_coupled_progressive.contracts import load_effective_config
from biospur_fusion.c2_coupled_progressive.estimator import SEGMENTS
from biospur_fusion.c2_coupled_progressive.renderer import (
    DisplayModel,
    _segment_distance_3d,
    joints_for_frame,
    physical_qa,
)
from biospur_fusion.c2_coupled_progressive.synthetic import run_qualification


def test_c2_coupled_progressive_pivot_qualification_passes() -> None:
    result = run_qualification()
    assert result["status"] == "PASS"


def test_c2_coupled_progressive_declares_attempt_001_diagnostic() -> None:
    path = Path("logs/c2_coupled_progressive_20260831_082131/development/ATTEMPT_001_CLASS_A_DIAGNOSTIC.json")
    text = path.read_text(encoding="utf-8")
    assert "SEALED_AS_DIAGNOSTIC_NOT_A_SCIENTIFIC_CANDIDATE" in text
    assert "A7_VIEWER_REBASE_RERUN_AND_FALLBACK" in text


def test_real_run_006_twisted_lower_body_is_a_negative_regression() -> None:
    """The image rejected by the user must never pass the geometry gate again."""
    path = Path(
        "logs/c2_coupled_progressive_20260831_082131/REAL_RUN_006/"
        "C2_COUPLED_PROGRESSIVE_OUTPUTS.npz"
    )
    with np.load(path, allow_pickle=False) as archive:
        trajectory = {"trajectory": {"17": {}}}
        for segment in SEGMENTS:
            base = f"trajectory/17/{segment}"
            trajectory["trajectory"]["17"][segment] = {
                "time_root_s": archive[f"{base}/time_root_s"],
                "quat_world_segment_wxyz": archive[f"{base}/quat_world_segment_wxyz"],
                "mask": archive[f"{base}/mask"],
            }
    joints = joints_for_frame(
        trajectory,
        "17",
        350,
        DisplayModel("middle_proxy", 0.425, 0.23),
        load_effective_config(),
    )
    qa = physical_qa(joints)
    assert qa["pass"] is False
    assert qa["gross_axial_twist_over_90deg"] is True


def test_crossing_gate_uses_three_dimensional_distance_not_projection() -> None:
    crossing = _segment_distance_3d(
        np.array([-1.0, -1.0, 0.0]),
        np.array([1.0, 1.0, 0.0]),
        np.array([-1.0, 1.0, 0.0]),
        np.array([1.0, -1.0, 0.0]),
    )
    depth_separated_projection = _segment_distance_3d(
        np.array([-1.0, -1.0, 0.0]),
        np.array([1.0, 1.0, 0.0]),
        np.array([-1.0, 1.0, 0.1]),
        np.array([1.0, -1.0, 0.1]),
    )
    assert crossing < 1e-12
    assert np.isclose(depth_separated_projection, 0.1)


def test_physical_gate_left_right_is_invariant_to_global_yaw() -> None:
    joints = {
        "pelvis_center": np.array([0.0, 0.0, 0.0]),
        "shoulder_mid": np.array([0.0, 0.0, 0.45]),
        "shoulder_left": np.array([-0.2, 0.0, 0.45]),
        "shoulder_right": np.array([0.2, 0.0, 0.45]),
        "hip_left": np.array([-0.12, 0.0, 0.0]),
        "hip_right": np.array([0.12, 0.0, 0.0]),
        "elbow_left": np.array([-0.2, 0.0, 0.15]),
        "wrist_left": np.array([-0.2, 0.0, -0.1]),
        "elbow_right": np.array([0.2, 0.0, 0.15]),
        "wrist_right": np.array([0.2, 0.0, -0.1]),
        "knee_left": np.array([-0.12, 0.08, -0.48]),
        "ankle_left": np.array([-0.12, 0.10, -0.91]),
        "knee_right": np.array([0.12, 0.08, -0.48]),
        "ankle_right": np.array([0.12, 0.10, -0.91]),
    }
    yaw = np.array([[0.0, -1.0, 0.0], [1.0, 0.0, 0.0], [0.0, 0.0, 1.0]])
    rotated = {name: yaw @ point for name, point in joints.items()}
    assert physical_qa(joints, standing=True)["pass"] is True
    assert physical_qa(rotated, standing=True)["pass"] is True
