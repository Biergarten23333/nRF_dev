"""Rendering contract tests: no solver ownership, clock or gauge changes."""
import base64
import json
from pathlib import Path

import numpy as np
import pytest

from tools.build_c2_full_continuous_ab_viewer import LINES, build


def _inputs(tmp_path: Path, *, nonfinite: bool = False):
    names = sorted({name for a, b, _ in LINES for name in (a, b)})
    origin = 234836.221471621
    times = origin + np.arange(1001) * .005
    roots = np.zeros((len(times), 3))
    if nonfinite:
        roots[7, 0] = np.nan
    source = tmp_path / "CONTINUOUS_AB.npz"
    np.savez(source, time_s=times, roots_a=roots, roots_b=np.ones_like(roots),
             joints_relative=np.zeros((len(times), len(names), 3)),
             joint_names=names, node_names=[f"BSF{i:04X}" for i in range(10)],
             anchors_world_m=np.array([[x, y, z] for z in (0, 2)
                                      for x, y in ((0, 0), (2, 0), (2, 2), (0, 2))]))
    regions = [{"kind": "ACTION", "action_id": "00_initial_still",
                "start_ns": int(origin * 1e9), "stop_ns": int((origin + 1) * 1e9)},
               {"kind": "INTER_ACTION_GAP", "action_id": None,
                "start_ns": int((origin + 1) * 1e9), "stop_ns": int((origin + 3) * 1e9)},
               {"kind": "ACTION", "action_id": "19_final_still",
                "start_ns": int((origin + 3) * 1e9), "stop_ns": int((origin + 5) * 1e9)}]
    actions = tmp_path / "POSE.json"
    actions.write_text(json.dumps({"regions": regions}))
    return source, actions, tmp_path / "viewer.html", origin


def test_actual_solver_schema_and_global_region_clock(tmp_path):
    source, actions, output, origin = _inputs(tmp_path)
    audit = build(source, actions, output)
    assert audit["clock_origin_s"] == origin
    assert audit["native_samples"] == 1001
    assert audit["display_samples"] == 251
    assert audit["origin_a_m"] == [0, 0, 0]
    assert audit["origin_b_m"] == [1, 1, 1]
    assert not audit["viewer_state_correction"]
    assert audit["relative_pose_a_b_exactly_equal"]
    assert not audit["separate_pose_b_supplied"]
    html = output.read_text()
    payload = json.loads(html.split("const DATA=", 1)[1].split(";\nfunction unpack", 1)[0])
    assert [a["id"] for a in payload["actions"]] == ["00_initial_still", "19_final_still"]
    assert payload["actions"][0]["start_s"] == pytest.approx(0, abs=1e-8)
    assert payload["actions"][1]["start_s"] == pytest.approx(3, abs=1e-8)
    assert payload["audit"]["anchor_volume_edges"] == [
        "AB", "BC", "CD", "DA", "EF", "FG", "GH", "HE", "AE", "BF", "CG", "DH"]
    assert "ctx.setLineDash([7,5])" in html
    assert "加宽显示框内" in html and "在 A–H 体积内" not in html
    assert 'id="trail"' in html
    assert "drawTrail(data,project);" in html
    assert "?N-1:frameIndex" in html


def test_nonfinite_native_sample_is_rejected_before_display_subsampling(tmp_path):
    source, actions, output, _ = _inputs(tmp_path, nonfinite=True)
    with pytest.raises(ValueError, match="nonfinite"):
        build(source, actions, output)
    assert not output.exists()


def test_fusion_comparison_labels(tmp_path):
    source, actions, output, _ = _inputs(tmp_path)
    summary = tmp_path / "summary.json"
    labels = ["大遮挡关闭", "大遮挡启用"]
    summary.write_text(json.dumps({"comparison_labels": labels}))
    assert build(source, actions, output, summary_path=summary)["comparison_labels"] == labels
    summary.write_text(json.dumps({"comparison_labels": ["one"]}))
    with pytest.raises(ValueError, match="comparison_labels"):
        build(source, actions, output, summary_path=summary)


def test_distinct_b_pose_is_packed_and_drawn_with_policy_override(tmp_path):
    source, actions, output, _ = _inputs(tmp_path)
    with np.load(source) as original:
        data = {key: original[key] for key in original.files}
    data["joints_relative_b"] = data["joints_relative"] + .25
    np.savez(source, **data)
    summary = tmp_path / "summary.json"
    policy = "原始 UWB 分别更新根位置与关节姿态，离线诊断。"
    summary.write_text(json.dumps({"viewer_policy": policy}))
    audit = build(source, actions, output, summary_path=summary)
    assert audit["separate_pose_b_supplied"]
    assert not audit["relative_pose_a_b_exactly_equal"]
    assert audit["viewer_policy"] == policy
    assert "非逐关节" not in audit["diagnostic_scope"]
    html = output.read_text()
    payload = json.loads(html.split("const DATA=", 1)[1].split(";\nfunction unpack", 1)[0])
    assert np.all(np.frombuffer(base64.b64decode(payload["pose_b"]), dtype="<f4") == .25)
    assert np.all(np.frombuffer(base64.b64decode(payload["pose"]), dtype="<f4") == 0)
    assert "panel(A,POSE," in html and "panel(B,POSE_B," in html
    assert "=>pose[(frameIndex*J+j)*3+k]+r[k]" in html


@pytest.mark.parametrize("invalid", ["nonfinite", "shape"])
def test_invalid_b_pose_is_rejected_before_display_selection(tmp_path, invalid):
    source, actions, output, _ = _inputs(tmp_path)
    with np.load(source) as original:
        data = {key: original[key] for key in original.files}
    b_pose = data["joints_relative"].copy()
    if invalid == "nonfinite":
        b_pose[7, 0, 0] = np.nan  # not a selected display frame
    else:
        b_pose = b_pose[:-1]
    data["joints_relative_b"] = b_pose
    np.savez(source, **data)
    with pytest.raises(ValueError, match="nonfinite|native pose shape"):
        build(source, actions, output)
    assert not output.exists()
