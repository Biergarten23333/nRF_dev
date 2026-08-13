from __future__ import annotations

import json
import hashlib
import sys
from pathlib import Path

import numpy as np
import pytest


TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS))

from current_room_autopos_sw100 import guarded_payload, parse_latest_mstat  # noqa: E402
from v47_uwb_position_replay import (  # noqa: E402
    load_solver,
    validate_anchor_slot_identity,
    validate_delay_ownership,
)

DEPLOYMENT = TOOLS.parent / "deployments/current_room_autopos_20260811_183541"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_command_guard_accepts_only_formal_sw100_surface():
    guarded_payload(
        b"anchor role all matrix\nautopos round A 100\nautopos apply\n"
        b"anchor role all responder\n"
    )
    for command in (b"anchor reset all\n", b"autopos map A DEADBEEF\n", b"ota start\n"):
        with pytest.raises(RuntimeError):
            guarded_payload(command)


def test_mstat_parser_uses_latest_peer_state():
    text = (
        "MSTAT peer=0 name=BS1234 conn=1 ready=0\n"
        "MSTAT peer=0 name=BS1234 conn=1 ready=1\n"
        "MSTAT peer=1 name=BS5678 conn=1 ready=1\n"
    )
    parsed = parse_latest_mstat(text)
    assert parsed[0]["ready"] is True
    assert parsed[1]["name"] == "BS5678"


def test_capture_anchor_slots_are_exact_a_through_h():
    validate_anchor_slot_identity(range(8))
    with pytest.raises(ValueError, match="slot identity mismatch"):
        validate_anchor_slot_identity([0, 1, 2, 3, 4, 5, 7, 6])
    with pytest.raises(ValueError, match="slot identity mismatch"):
        validate_anchor_slot_identity([0, 1, 2, 3, 4, 5, 6])


def test_delay_double_application_fails_closed():
    validate_delay_ownership(
        transport_applies_v4_delay=False,
        solver_applies_v4_delay=True,
    )
    with pytest.raises(ValueError, match="double-applied"):
        validate_delay_ownership(
            transport_applies_v4_delay=True,
            solver_applies_v4_delay=True,
        )
    with pytest.raises(ValueError, match="must own"):
        validate_delay_ownership(
            transport_applies_v4_delay=False,
            solver_applies_v4_delay=False,
        )


def test_v4io_integration_qualification_and_determinism():
    result = json.loads((DEPLOYMENT / "V4IO_QUALIFICATION.json").read_text(encoding="utf-8"))
    assert result["verdict"] == "V4IO_LAYOUT_PASS"
    assert all(result["gates"].values())
    assert result["metrics"]["deterministic_max_absolute_delta"] == 0.0
    assert result["mirror_selection"]["selected"] == "reflected_init"


def test_frame_binding_uses_capture_bound_canonical_geometry_not_intermediate_mirror():
    import derive_v47_c2cc_frame_binding as derive
    import v47_c2cc_frame_binding_capture as capture

    binding = json.loads((DEPLOYMENT / "CAPTURE_BOUND_GEOMETRY_MANIFEST.json").read_text())
    canonical = (DEPLOYMENT / binding["layout"]["path"]).resolve()
    intermediate = (DEPLOYMENT / "V4IO/anchor_layout.json").resolve()
    assert _sha256(canonical) == binding["layout"]["sha256"]
    assert _sha256(intermediate) != binding["layout"]["sha256"]
    assert derive.LAYOUT.resolve() == canonical
    assert capture.LAYOUT.resolve() == canonical


@pytest.mark.parametrize("label", ["UWB_TAG_T4", "UWB_TAG_U5"])
def test_real_tag_solver_package_recovers_exact_synthetic_point(label):
    models, layout_io, c_solver = load_solver(label)
    layout = layout_io.load_layout_json(DEPLOYMENT / "V4IO_LAYOUT.json")
    expected = np.asarray([2000.0, 1500.0, 800.0])
    observations = []
    for anchor_id, anchor in sorted(layout.anchors.items()):
        anchor_xyz = np.asarray([anchor.x_mm, anchor.y_mm, anchor.z_mm])
        measured = float(np.linalg.norm(expected - anchor_xyz) + anchor.d_anchor_mm)
        observations.append(
            models.Observation(
                anchor_id=anchor_id,
                range_mm=measured,
                quality_percent=100.0,
                status="O",
            )
        )
    frame = models.Frame(
        tag="TEST", sweep=1, host_elapsed_s=0.0, host_epoch_s=0.0,
        observations=tuple(observations), imu=None,
    )
    result = c_solver.TagPositionSolver(
        layout, models.SolverConfig(method="T4")
    ).solve_frame(frame)
    assert result is not None
    assert result.status == "ok"
    assert result.anchors_used == 8
    assert np.linalg.norm(np.asarray([result.x_mm, result.y_mm, result.z_mm]) - expected) < 1e-6
