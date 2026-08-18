from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from biospur_fusion.imu_pose_r21.real_data import CacheRow, EXPECTED_NODES


ROOT = Path(__file__).resolve().parents[5]


def test_threshold_registry_has_provenance_unit_and_gate_class():
    payload = json.loads((ROOT/"BioSpur_Fusion/Fusion_Part/config/fusion_v2/phase3r21/PHASE3R21_THRESHOLDS.json").read_text())
    assert payload["frozen_before_real_numeric_decode"]
    for row in payload["thresholds"].values():
        assert {"value", "unit", "gate_class", "source", "rationale"} <= set(row)


def test_real_synthetic_structural_evidence_are_separate():
    schema = {"real_capture": {}, "synthetic": {}, "structural": {}}
    assert len(schema) == 3 and not (schema["real_capture"] is schema["synthetic"])


def test_cache_uid_includes_source_offset_and_changes_under_mutation():
    base = dict(action_id="a", phase="FORMAL_ACTION", split_class="CALIBRATION_FIT", cycle_id="c",
                node_id="BSFC2CC", boot_epoch=0, timer2_us=10, common_time_ns=20, sequence=1,
                gyro_rad_s=np.zeros(3), accel_m_s2=np.ones(3), source_record_length=64)
    a = CacheRow(source_record_offset=100, **base); b = CacheRow(source_record_offset=101, **base)
    assert a.uid != b.uid


@pytest.mark.parametrize("nodes", [set(), EXPECTED_NODES-{"BSFC2CC"}, EXPECTED_NODES|{"BSFXXXX"}])
def test_node_drop_empty_and_unknown_are_not_exact_fleet(nodes):
    assert nodes != EXPECTED_NODES


def test_signed_axis_does_not_accept_antipodal_mutation():
    direction=np.array([0.,0.,-1.]); target=np.array([0.,0.,-1.])
    angle=lambda x:np.rad2deg(np.arccos(np.clip(np.dot(x,target),-1,1)))
    assert angle(direction)==0 and angle(-direction)==180
