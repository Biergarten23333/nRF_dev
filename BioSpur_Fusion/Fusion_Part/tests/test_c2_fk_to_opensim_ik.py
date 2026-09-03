from __future__ import annotations

import ast
from pathlib import Path

from biospur_fusion.c2_fk_to_opensim_ik.adapter import (
    PILOT_EPISODES,
    SEGMENTS,
    orientation_error_summary,
)


WORKSPACE = Path(__file__).resolve().parents[1]


def test_scope_is_exact():
    assert tuple(PILOT_EPISODES) == (
        "00_initial_still",
        "02_t_pose",
        "06_upper_dynamic",
        "10_lower_dynamic",
        "H01_boxing",
        "H02_golf",
    )
    assert len(SEGMENTS) == 10


def test_thin_adapter_has_no_forbidden_solver_or_calibration():
    root = WORKSPACE / "src/biospur_fusion/c2_fk_to_opensim_ik"
    text = "\n".join(path.read_text(encoding="utf-8") for path in root.glob("*.py"))
    forbidden = (
        "scipy.optimize",
        "least_squares",
        "IMUPlacer",
        "jointAxisEst",
        "headingCorrection",
        "c2_3b_multi_action_registration",
        "UWB",
    )
    assert not any(token in text for token in forbidden)


def test_python_sources_parse():
    root = WORKSPACE / "src/biospur_fusion/c2_fk_to_opensim_ik"
    for path in root.glob("*.py"):
        ast.parse(path.read_text(encoding="utf-8"))


def test_direct_solver_reassembles_after_each_measurement_chart_write():
    text = (WORKSPACE / "src/biospur_fusion/c2_fk_to_opensim_ik/adapter.py").read_text(
        encoding="utf-8"
    )
    assert "solver.track(state)" not in text
    assert "solver.assemble(state)" in text


def test_orientation_error_unit_metadata_is_not_inferred_from_motion(tmp_path):
    error_table = tmp_path / "orientation_errors.sto"
    error_table.write_text(
        "name=OrientationErrors\nDataType=double\nendheader\ntime\tpelvis_imu\n0\t0.25\n",
        encoding="utf-8",
    )
    summary = orientation_error_summary(error_table)
    assert summary["source_units"] == "radian"
    assert summary["converted_exactly_once"] is False
    assert summary["overall_max_rad"] == 0.25

    degree_table = tmp_path / "degree_table.sto"
    degree_table.write_text(
        "inDegrees=yes\nDataType=double\nendheader\ntime\tpelvis_rx\n0\t180\n",
        encoding="utf-8",
    )
    converted = orientation_error_summary(degree_table)
    assert converted["converted_exactly_once"] is True
    assert abs(converted["overall_max_rad"] - 3.141592653589793) < 1e-15
