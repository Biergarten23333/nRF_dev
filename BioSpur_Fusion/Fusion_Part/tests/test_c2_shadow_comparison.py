import json

import numpy as np
import pytest

from tools.build_c2_shadow_comparison import RAW_EVIDENCE, build


def _fixture(tmp_path, mismatch=None):
    t = 100 + np.arange(101) * .02
    anchors = np.arange(24).reshape(8, 3).astype(float)
    values = dict(time_s=t, roots_b=np.zeros((101, 3)),
                  joints_relative=np.zeros((101, 2, 3)), joint_names=np.array(["pelvis", "head"]),
                  anchors_world_m=anchors, node_names=np.array(["BSFC2CC"]))
    off, on = tmp_path / "off.npz", tmp_path / "on.npz"
    np.savez(off, **values)
    values["roots_b"][1:, 0] = np.linspace(.01, 1, 100)
    if mismatch == "initial_root":
        values["roots_b"][0, 0] = .001
    elif mismatch in ("time_s", "joints_relative", "anchors_world_m"):
        values[mismatch] = values[mismatch].copy()
        values[mismatch].flat[-1] += 1e-8
    elif mismatch == "joint_names":
        values["joint_names"] = np.array(["pelvis", "neck"])
    np.savez(on, **values)
    for path in (off, on):
        path.with_suffix(".json").write_text(json.dumps(
            dict(release_period_s=.12, maximum_release_delay_s=.12)))
    regions = tmp_path / "regions.json"
    regions.write_text(json.dumps({"regions": [
        dict(region_id="00", action_id="00", kind="ACTION", start_ns=int(100e9), stop_ns=int(101e9)),
        dict(region_id="gap", action_id=None, kind="INTER_ACTION_GAP", start_ns=int(101e9), stop_ns=int(102e9)),
        dict(region_id="uncovered", action_id="19", kind="ACTION", start_ns=int(105e9), stop_ns=int(106e9))]}))
    return off, on, regions


def test_mapping_and_all_region_metrics_preserve_sources(tmp_path):
    off, on, regions = _fixture(tmp_path)
    before = {p: p.read_bytes() for p in (off, on)}
    output = tmp_path / "comparison.npz"
    result = build(off, on, output, regions_path=regions)
    with np.load(output) as assembled, np.load(off) as a, np.load(on) as b:
        np.testing.assert_array_equal(assembled["roots_a"], a["roots_b"])
        np.testing.assert_array_equal(assembled["roots_b"], b["roots_b"])
        np.testing.assert_array_equal(assembled["time_s"], a["time_s"])
        assert "root_state_b" not in assembled
    assert result["comparison_labels"] == ["IMU+UWB · 大遮挡关闭", "IMU+UWB · 大遮挡启用"]
    assert len(result["motion"]["regions"]) == 3
    assert result["motion"]["regions"][2]["status"] == "INSUFFICIENT_PUBLICATION_SAMPLES"
    assert result["motion"]["full"]["off"]["maximum_excursion_from_initial_mean_m"] == 0
    assert all(path.read_bytes() == raw for path, raw in before.items())


@pytest.mark.parametrize("mismatch", ["initial_root", "time_s", "joints_relative", "anchors_world_m", "joint_names"])
def test_shared_input_mismatch_rejected_before_any_output(tmp_path, mismatch):
    off, on, regions = _fixture(tmp_path, mismatch)
    output = tmp_path / "comparison.npz"
    with pytest.raises(ValueError, match="mismatch"):
        build(off, on, output, regions_path=regions)
    assert not output.exists()


def test_publication_mechanism_mismatch_rejected(tmp_path):
    off, on, regions = _fixture(tmp_path)
    on.with_suffix(".json").write_text(json.dumps(dict(release_period_s=.1, maximum_release_delay_s=.1)))
    with pytest.raises(ValueError, match="120 ms"):
        build(off, on, tmp_path / "comparison.npz", regions_path=regions)


def test_joint_feedback_preserves_derived_b_pose_and_base_assertion(tmp_path):
    off, on, regions = _fixture(tmp_path)
    with np.load(on) as data:
        values = {key: data[key] for key in data.files}
    values["joints_relative_b"] = values["joints_relative"] + .125
    np.savez(on, **values)
    output = tmp_path / "comparison.npz"
    result = build(off, on, output, regions_path=regions, joint_feedback=True)
    with np.load(output) as saved:
        np.testing.assert_array_equal(saved["joints_relative_b"], values["joints_relative_b"])
        np.testing.assert_array_equal(saved["joints_relative"], values["joints_relative"])
    assert not result["relative_pose_a_b_exactly_equal"]
    assert "root 9" in result["viewer_policy"] and "segment 30" in result["viewer_policy"]
    assert "无接触" in result["viewer_policy"]
    values["joints_relative"][3, 0, 0] += .01
    np.savez(on, **values)
    with pytest.raises(ValueError, match="shared field mismatch: joints_relative"):
        build(off, on, tmp_path / "mismatch.npz", regions_path=regions, joint_feedback=True)


def test_joint_feedback_requires_actual_separate_b_pose(tmp_path):
    off, on, regions = _fixture(tmp_path)
    with pytest.raises(ValueError, match="requires ON joints_relative_b"):
        build(off, on, tmp_path / "comparison.npz", regions_path=regions, joint_feedback=True)


def _geometry_fixture(tmp_path):
    off, on, regions = _fixture(tmp_path)
    for path in (off, on):
        with np.load(path) as data:
            arrays = {key: data[key] for key in data.files}
        arrays.update({key: np.arange(5) for key in RAW_EVIDENCE})
        arrays["uwb_node"] = np.array(["BSFC2CC"] * 5)
        np.savez(path, **arrays)
    with np.load(on) as data:
        values = {key: data[key] for key in data.files}
    values["roots_b"] += .4
    values["joints_relative"] += .125
    np.savez(on, **values)
    return off, on, regions, values


def test_geometry_repair_explicitly_preserves_different_roots_and_poses(tmp_path):
    off, on, regions, values = _geometry_fixture(tmp_path)
    result = build(off, on, tmp_path / "comparison.npz", regions_path=regions, geometry_repair=True)
    with np.load(tmp_path / "comparison.npz") as saved, np.load(off) as previous:
        np.testing.assert_array_equal(saved["joints_relative"], previous["joints_relative"])
        np.testing.assert_array_equal(saved["joints_relative_b"], values["joints_relative"])
        np.testing.assert_array_equal(saved["roots_b"], values["roots_b"])
    assert result["comparison_labels"] == ["修复前", "坐标与标签几何修复后"]
    assert not result["initial_root_exactly_equal"]
    assert result["common_clock_anchors_nodes_raw_capture_exact"]
    with pytest.raises(ValueError, match="mismatch"):
        build(off, on, tmp_path / "strict.npz", regions_path=regions)


@pytest.mark.parametrize("field", ["time_s", "anchors_world_m", "node_names", "capture"])
def test_geometry_repair_does_not_relax_world_clock_or_capture_checks(tmp_path, field):
    off, on, regions, values = _geometry_fixture(tmp_path)
    if field == "capture":
        values["source_range_mm"][0] += 1
        np.savez(on, **values)
    else:
        values[field] = values[field].copy()
        values[field].flat[-1] = "BSF9999" if field == "node_names" else values[field].flat[-1] + .001
        np.savez(on, **values)
    with pytest.raises(ValueError, match="mismatch"):
        build(off, on, tmp_path / "bad.npz", regions_path=regions, geometry_repair=True)


def test_geometry_mode_is_exclusive_and_requires_capture_provenance(tmp_path):
    off, on, regions = _fixture(tmp_path)
    with pytest.raises(ValueError, match="mutually exclusive"):
        build(off, on, tmp_path / "bad.npz", regions_path=regions, geometry_repair=True, back_cut=True)
    with pytest.raises(ValueError, match="raw evidence array"):
        build(off, on, tmp_path / "missing.npz", regions_path=regions, geometry_repair=True)


def _persistent_fixture(tmp_path):
    off, on, regions, values = _geometry_fixture(tmp_path)
    with np.load(off) as baseline:
        values["joints_relative"] = baseline["joints_relative"]
        values["roots_b"][0] = baseline["roots_b"][0]
    np.savez(on, **values)
    for path in (off, on):
        with np.load(path) as data:
            arrays = {key: data[key] for key in data.files}
        arrays["roots_b_posterior"] = arrays["roots_b"].copy()
        arrays["root_state_b"] = np.c_[arrays["roots_b"], np.zeros((len(arrays["roots_b"]), 6))]
        np.savez(path, **arrays)
    (tmp_path / "RESULT.json").write_text(json.dumps({"role": "RAW_ESTIMATOR_DIAGNOSTIC"}))
    with np.load(on) as data:
        values = {key: data[key] for key in data.files}
    return off, on, regions, values


def test_persistent_error_mode_retains_shared_pose_and_explicit_scope(tmp_path):
    off, on, regions, values = _persistent_fixture(tmp_path)
    output = tmp_path / "comparison.npz"
    report = build(off, on, output, regions_path=regions, persistent_error=True)
    assert report["comparison_labels"] == ["原融合（逐次拉动）", "持续误差建模后的融合"]
    assert report["common_pose_clock_anchors_initial_root_exact"]
    assert "root 9" in report["viewer_policy"] and "30 维持续节点误差" in report["viewer_policy"]
    assert not report["scientific_acceptance"]
    assert report["raw_estimator_posterior_comparison"]
    assert report["publication_release_period_s"] is None
    assert len(report["identical_raw_payload_array_sha256"]) == 9
    with np.load(output) as saved:
        assert "joints_relative_b" not in saved
        np.testing.assert_array_equal(saved["roots_b"], values["roots_b"])


@pytest.mark.parametrize("field", ["initial", "pose", "raw"])
def test_persistent_error_keeps_strict_input_assertions(tmp_path, field):
    off, on, regions, values = _persistent_fixture(tmp_path)
    if field == "initial":
        values["roots_b"][0, 0] += .01
        values["roots_b_posterior"][0, 0] += .01
        values["root_state_b"][0, 0] += .01
    elif field == "pose":
        values["joints_relative"][3, 0, 0] += .01
    else:
        values["source_sequence"][0] += 1
    np.savez(on, **values)
    with pytest.raises(ValueError, match="mismatch"):
        build(off, on, tmp_path / "bad.npz", regions_path=regions, persistent_error=True)


@pytest.mark.parametrize("other", ["geometry_repair", "joint_feedback", "back_cut"])
def test_persistent_error_is_exclusive(tmp_path, other):
    off, on, regions, _ = _persistent_fixture(tmp_path)
    with pytest.raises(ValueError, match="mutually exclusive"):
        build(off, on, tmp_path / "bad.npz", regions_path=regions,
              persistent_error=True, **{other: True})


@pytest.mark.parametrize("key", ["roots_b_posterior", "root_state_b"])
def test_persistent_error_rejects_smoothed_or_inconsistent_root(tmp_path, key):
    off, on, regions, values = _persistent_fixture(tmp_path)
    values[key][5, 0] += .001
    np.savez(on, **values)
    with pytest.raises(ValueError, match="raw estimator"):
        build(off, on, tmp_path / "bad.npz", regions_path=regions, persistent_error=True)


def test_published_persistent_comparison_uses_existing_120ms_output(tmp_path):
    off, on, regions, values = _persistent_fixture(tmp_path)
    values["roots_b"][5, 0] += .02  # publication can differ from stored posterior
    np.savez(on, **values)
    result = build(off, on, tmp_path / "published.npz", regions_path=regions,
                   persistent_error=True, published=True)
    assert result["matched_120ms_publication_comparison"]
    assert not result["raw_estimator_posterior_comparison"]
    assert result["publication_release_period_s"] == .12
    assert "主输出（同一120ms发布）" in result["comparison_note"]
    with np.load(tmp_path / "published.npz") as saved:
        np.testing.assert_array_equal(saved["roots_b"], values["roots_b"])
    with pytest.raises(ValueError, match="raw estimator"):
        build(off, on, tmp_path / "raw.npz", regions_path=regions, persistent_error=True)


def test_published_flag_rejects_wrong_mode_and_mismatched_release(tmp_path):
    off, on, regions, _ = _persistent_fixture(tmp_path)
    with pytest.raises(ValueError, match="only valid"):
        build(off, on, tmp_path / "wrong.npz", regions_path=regions, published=True)
    meta = json.loads(on.with_suffix(".json").read_text())
    meta["release_period_s"] = .1
    on.with_suffix(".json").write_text(json.dumps(meta))
    with pytest.raises(ValueError, match="120 ms"):
        build(off, on, tmp_path / "wrong.npz", regions_path=regions,
              persistent_error=True, published=True)


@pytest.mark.parametrize("published", [False, True])
def test_input_safety_is_root9_fused_comparison_with_shared_loader(tmp_path, published):
    off, on, regions, values = _persistent_fixture(tmp_path)
    output = tmp_path / "safety.npz"
    report = build(off, on, output, regions_path=regions, input_safety=True, published=published)
    assert report["comparison_labels"] == ["原融合", "测距接纳＋IMU断档处理"]
    assert "root 9" in report["viewer_policy"] and "无 Q 调整" in report["viewer_policy"]
    assert report["raw_estimator_posterior_comparison"] is not published
    assert report["matched_120ms_publication_comparison"] is published
    assert report["compared_outputs"] == "OLD_FUSED_ROOT_VS_NEW_FUSED_ROOT_NOT_PURE_IMU"
    with np.load(output) as saved:
        np.testing.assert_array_equal(saved["roots_b"], values["roots_b"])


def test_input_safety_preserves_initial_pose_raw_and_posterior_checks(tmp_path):
    off, on, regions, values = _persistent_fixture(tmp_path)
    values["source_range_mm"][0] += 1
    np.savez(on, **values)
    with pytest.raises(ValueError, match="raw capture mismatch"):
        build(off, on, tmp_path / "bad.npz", regions_path=regions, input_safety=True)
    with pytest.raises(ValueError, match="mutually exclusive"):
        build(off, on, tmp_path / "bad.npz", regions_path=regions,
              input_safety=True, persistent_error=True)


def test_back_cut_labels_are_explicit(tmp_path):
    off, on, regions = _fixture(tmp_path)
    result = build(off, on, tmp_path / "cut.npz", regions_path=regions, back_cut=True)
    assert result["comparison_labels"] == ["IMU+UWB · 不剔除背面", "IMU+UWB · 剔除背面"]
    assert result["hard_back_cut"]
    assert "cos<0" in result["comparison_note"]
