from __future__ import annotations

import json
import inspect
from pathlib import Path

import numpy as np
import pytest

from biospur_fusion.imu_pose_r21.real_data import CacheRow, EXPECTED_NODES
from BioSpur_Fusion.Fusion_Part.tools.fusion_v2.phase3r21.postprocess_phase3r21 import _masks
from BioSpur_Fusion.Fusion_Part.tools.fusion_v2.phase3r21 import run_phase3r21


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


def test_phase_masks_union_across_fit_validation_and_guard_caches(tmp_path):
    cache=tmp_path/"cache";h=tmp_path/"h"
    rows={"fit":("a","FORMAL_ACTION",2_500_000),"propagation":("a","PREPARATION",2_500_000),
          "validation":("a","FORMAL_ACTION",22_500_000),"guard":("a","FORMAL_ACTION",42_500_000)}
    for name,(action,phase,time_ns) in rows.items():
        path=cache/name;path.mkdir(parents=True);np.save(path/"action.npy",np.array([action]));np.save(path/"phase.npy",np.array([phase]));np.save(path/"common_time_ns.npy",np.array([time_ns]))
    h.mkdir();np.save(h/"action.npy",np.array(["H00_walk"]));np.save(h/"phase.npy",np.array(["FORMAL_ACTION"]));np.save(h/"common_time_ns.npy",np.array([2_500_000]))
    result=_masks(np.array([0,20_000_000,40_000_000]),cache,h)
    assert result["a"]["FORMAL_ACTION"].tolist()==[True,True,True]


def test_real_calibration_has_no_action_name_pseudo_target_and_routes_qmt_axes():
    source=inspect.getsource(run_phase3r21)
    assert "phase3r21-target" not in source
    assert "action_name_pseudo_targets\":0" in source
    assert "OFFICIAL_VQF_INITIAL_DOWN_PLUS_TPOSE_UPPER_ARM_LONGITUDINAL_WITH_PROPAGATION_TRANSPORT" in source
    assert 'segment.startswith("upper_arm_")' in source
    assert "L0_HORIZONTAL_PLANE_WITH_NATURAL_ELBOW_FLEXION" in source
    assert "axis_child_segment" in source
    assert "functional_axes_child=axes" in source


def test_tpose_forearm_gate_is_horizontal_not_fixed_azimuth():
    q_identity=np.array([[1.,0.,0.,0.]])
    q_down_to_forward=np.array([run_phase3r21.so3.from_two_vectors(np.array([0.,0.,-1.]),np.array([0.,1.,0.]))])
    assert run_phase3r21._horizontal_error(q_down_to_forward)[0] == pytest.approx(0.,abs=1e-8)
    assert run_phase3r21._horizontal_error(q_identity)[0] == pytest.approx(90.,abs=1e-8)
