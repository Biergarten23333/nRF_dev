#!/usr/bin/env python3
"""Assemble an OFF/ON large-shadow comparison from saved publications only.

Neither estimator is rerun. The common pose, anchors, clock, initial root and
120 ms publication mechanism must match exactly. Movement statistics are not
ground-truth errors, drift estimates, or an accuracy validation.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np

from tools.report_c2_motion_excursion import metrics


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REGIONS = ROOT / "logs/c2_full_continuous_ab_20260912/POSE_WORLD.json"
LABELS = ["IMU+UWB · 大遮挡关闭", "IMU+UWB · 大遮挡启用"]
NOTE = "同一 120 ms 发布机制；仅比较天线方向 + 佩戴背向大遮挡先验，不含小遮挡。数值为运动幅度/稳定性诊断，无真值时不能解释为绝对精度或漂移误差。"
SHARED = ("time_s", "joints_relative", "anchors_world_m", "joint_names")
RAW_EVIDENCE = ("source_range_mm", "source_strobe_us", "source_frame_us",
                "source_sequence", "source_sweep", "source_t_round_us",
                "source_valid_mask", "uwb_node", "uwb_time_s")


def _sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _load(path, *, raw_estimator=False):
    with np.load(path, allow_pickle=False) as data:
        values = {key: np.asarray(data[key]) for key in (*SHARED, "roots_b")}
        for key in ("node_names", "anchor_names", "joints_relative_b", "roots_b_posterior", "root_state_b", *RAW_EVIDENCE):
            if key in data:
                values[key] = np.asarray(data[key])
    metadata = json.loads((path.parent / "RESULT.json" if raw_estimator else path.with_suffix(".json")).read_text())
    if raw_estimator:
        if "roots_b_posterior" not in values or not np.array_equal(values["roots_b"], values["roots_b_posterior"]):
            raise ValueError("raw estimator comparison requires roots_b == roots_b_posterior")
        if "root_state_b" in values:
            state = values["root_state_b"]
            if state.ndim != 2 or state.shape[1] < 3 or not np.array_equal(values["roots_b"], state[:, :3]):
                raise ValueError("raw estimator root_state_b position mismatch")
    else:
        for key in ("release_period_s", "maximum_release_delay_s"):
            if metadata.get(key) != .12:
                raise ValueError(f"{path.name}: publication {key} is not 120 ms")
    n = len(values["time_s"])
    if (values["time_s"].shape != (n,) or n < 2
            or np.any(np.diff(values["time_s"]) <= 0)
            or values["roots_b"].shape != (n, 3)
            or values["joints_relative"].shape != (n, len(values["joint_names"]), 3)
            or values["anchors_world_m"].shape != (8, 3)):
        raise ValueError(f"invalid publication array shapes/clock: {path}")
    for key in ("time_s", "roots_b", "joints_relative", "anchors_world_m"):
        if not np.isfinite(values[key]).all():
            raise ValueError(f"nonfinite publication {key}: {path}")
    if "joints_relative_b" in values:
        if values["joints_relative_b"].shape != values["joints_relative"].shape:
            raise ValueError(f"invalid joints_relative_b shape: {path}")
        if not np.isfinite(values["joints_relative_b"]).all():
            raise ValueError(f"nonfinite publication joints_relative_b: {path}")
    return values, metadata


def movement_summary(times, off_root, on_root, regions):
    """Use the established excursion owner; retain even uncovered regions."""
    result = {"scope": "SAVED_PUBLICATION_EXCURSION_NOT_TRUTH_ERROR",
              "full": {"off": metrics(times, off_root), "on": metrics(times, on_root)},
              "regions": []}
    for region in regions:
        mask = (times >= region["start_ns"] * 1e-9) & (times < region["stop_ns"] * 1e-9)
        row = {"id": region["region_id"], "action": region.get("action_id"),
               "kind": region["kind"], "samples": int(np.count_nonzero(mask))}
        if row["samples"] >= 2:
            row.update(status="AVAILABLE", off=metrics(times[mask], off_root[mask]),
                       on=metrics(times[mask], on_root[mask]))
        else:
            row.update(status="INSUFFICIENT_PUBLICATION_SAMPLES", off=None, on=None)
        result["regions"].append(row)
    return result


def build(off_path: Path, on_path: Path, output: Path, *,
          regions_path: Path = DEFAULT_REGIONS, html_path: Path | None = None,
          back_cut: bool = False, joint_feedback: bool = False,
          geometry_repair: bool = False, persistent_error: bool = False,
          published: bool = False, input_safety: bool = False):
    off_path, on_path, output = Path(off_path), Path(on_path), Path(output)
    regions_path = Path(regions_path)
    summary_path = output.with_suffix(".json")
    targets = [output, summary_path]
    if html_path is not None:
        html_path = Path(html_path)
        targets.extend([html_path, html_path.with_suffix(".audit.json")])
    if len({p.resolve() for p in targets}) != len(targets) or any(p.exists() for p in targets):
        raise ValueError("comparison outputs must be distinct new paths")
    if output.suffix != ".npz":
        raise ValueError("comparison output must have .npz suffix")
    if sum((back_cut, joint_feedback, geometry_repair, persistent_error, input_safety)) > 1:
        raise ValueError("comparison modes are mutually exclusive")
    estimator_comparison = persistent_error or input_safety
    if published and not estimator_comparison:
        raise ValueError("--published is only valid with an explicit estimator comparison")
    off, off_meta = _load(off_path, raw_estimator=estimator_comparison and not published)
    on, on_meta = _load(on_path, raw_estimator=estimator_comparison and not published)
    shared_keys = tuple(key for key in SHARED if not geometry_repair or key != "joints_relative")
    for key in (*shared_keys, "node_names", "anchor_names"):
        if (key in off) != (key in on):
            raise ValueError(f"OFF/ON shared field presence mismatch: {key}")
        if key in off and not np.array_equal(off[key], on[key]):
            raise ValueError(f"OFF/ON shared field mismatch: {key}")
    if not geometry_repair and not np.array_equal(off["roots_b"][0], on["roots_b"][0]):
        raise ValueError("OFF/ON initial root mismatch")
    if geometry_repair or estimator_comparison:
        raw_bindings = {}
        for key in RAW_EVIDENCE:
            if key not in off or key not in on:
                raise ValueError(f"comparison requires raw evidence array: {key}")
            if not np.array_equal(off[key], on[key]):
                raise ValueError(f"OFF/ON raw capture mismatch: {key}")
            value = np.ascontiguousarray(off[key])
            digest = hashlib.sha256(json.dumps([value.dtype.str, list(value.shape)]).encode())
            digest.update(value.view(np.uint8))
            raw_bindings[key] = digest.hexdigest()
        if "node_names" not in off:
            raise ValueError("comparison requires identical node_names")
    if joint_feedback and "joints_relative_b" not in on:
        raise ValueError("joint-feedback comparison requires ON joints_relative_b")
    if not joint_feedback and not geometry_repair and "joints_relative_b" in on and not np.array_equal(on["joints_relative_b"], on["joints_relative"]):
        raise ValueError("distinct ON pose requires explicit joint-feedback comparison")
    regions_document = json.loads(regions_path.read_text())
    regions = regions_document["regions"] if isinstance(regions_document, dict) else regions_document
    labels = ["IMU+UWB · 不剔除背面", "IMU+UWB · 剔除背面"] if back_cut else LABELS
    note = NOTE if not back_cut else (
        "同一 120 ms 发布机制；右侧剔除朝天线背面的测距（cos<0），其余方向权重保持1。"
        "路径不足沿用求解器拒绝机制，不补回背面路径。无小遮挡。运动幅度不是真值误差。")
    summary = {"role": "LARGE_SHADOW_OFF_ON_SAVED_PUBLICATION_COMPARISON",
               "comparison_labels": labels, "comparison_note": note,
               "hard_back_cut": back_cut,
               "common_pose_clock_anchors_initial_root_exact": True,
               "estimator_or_pose_rerun": False, "small_occlusion_included": False,
               "movement_is_not_absolute_accuracy_or_truth_drift": True,
               "publication_release_period_s": .12,
               "provenance": {"off": {"path": str(off_path.resolve()), "sha256": _sha256(off_path),
                                        "publication_metadata": off_meta},
                              "on": {"path": str(on_path.resolve()), "sha256": _sha256(on_path),
                                       "publication_metadata": on_meta},
                              "regions": {"path": str(regions_path.resolve()),
                                          "sha256": _sha256(regions_path)}},
               "motion": movement_summary(off["time_s"], off["roots_b"], on["roots_b"], regions)}
    if joint_feedback:
        summary.update(
            role="SHARED_ROOT_VS_RECURRENT_IMU_FK_RAW_FEEDBACK_COMPARISON",
            comparison_labels=["IMU+UWB · 原共同 root", "IMU+FK · 循环原始测距反馈"],
            comparison_note="同一 120 ms 发布机制与连续时钟；比较原共同 root 输出和循环 IMU+FK 原始测距反馈。运动统计不代表真值精度。",
            viewer_policy="右侧为 root 9 维 + segment 30 维状态反馈，外部 native 200 Hz hinge 基准增量持续驱动；无接触约束，不是十节点各自 bias 重标定。左右使用各自输入的相对骨架。",
            common_base_pose_clock_anchors_initial_root_exact=True,
            common_pose_clock_anchors_initial_root_exact=False,
            relative_pose_a_b_exactly_equal=bool(np.array_equal(off["joints_relative"], on["joints_relative_b"])),
        )
    if geometry_repair:
        summary.update(
            role="COORDINATE_AND_TAG_GEOMETRY_REPAIR_DIAGNOSTIC_COMPARISON",
            comparison_labels=["修复前", "坐标与标签几何修复后"],
            comparison_note="同一原始捕获、连续时钟、A–H 锚点和 120 ms 发布机制；几何修复会改变相对骨架及初始 root，不强制重合。运动幅度不是真值误差。",
            viewer_policy="左右分别显示修复前后保存的骨架与 root；同步相机、时间与固定锚点体积，不对齐起点、不重定位骨架。仅为坐标与标签几何修复诊断，不代表物理精度验证。",
            common_pose_clock_anchors_initial_root_exact=False,
            common_clock_anchors_nodes_raw_capture_exact=True,
            identical_raw_payload_array_sha256=raw_bindings,
            initial_root_exactly_equal=bool(np.array_equal(off["roots_b"][0], on["roots_b"][0])),
            initial_root_difference_m=(on["roots_b"][0] - off["roots_b"][0]).tolist(),
            relative_pose_a_b_exactly_equal=bool(np.array_equal(
                off.get("joints_relative_b", off["joints_relative"]),
                on.get("joints_relative_b", on["joints_relative"]))),
        )
    if persistent_error:
        summary.update(
            role="PERSISTENT_NODE_ERROR_FUSION_DIAGNOSTIC_COMPARISON",
            comparison_labels=["原融合（逐次拉动）", "持续误差建模后的融合"],
            comparison_note="左右均为 IMU+UWB 原始估计器后验：同一原始测量、骨架与初始 root，不使用 120 ms 发布平滑，直接比较持续节点误差建模的输出。运动与抖动统计不是物理精度验证。",
            viewer_policy="右侧为 root 9 维 + 30 维持续节点误差状态；不是关节姿态更新，也不是十节点 IMU bias 标定。求解保持 native 200 Hz；显示仅取前序原样本，不修改结果。",
            common_clock_anchors_nodes_raw_capture_exact=True,
            identical_raw_payload_array_sha256=raw_bindings,
            scientific_acceptance=False,
            publication_release_period_s=None,
            raw_estimator_posterior_comparison=True,
        )
        if published:
            summary.update(
                comparison_labels=["原融合（逐次拉动）· 主输出", "持续误差建模后的融合 · 主输出"],
                comparison_note="主输出（同一120ms发布）：左右均为 IMU+UWB，复用相同且未调参的 120 ms 发布机制，对应此前页面的输出层级。不是原始后验比较；需结合单独的原始后验对比检查抖动。无新增显示平滑，不代表物理精度验证。",
                viewer_policy="右侧为 root 9 维 + 30 维持续节点误差状态；不是关节姿态更新，也不是十节点 IMU bias 标定。两侧使用既有同一 120 ms 发布输出；native 200 Hz 输入仅取前序样本显示。",
                publication_release_period_s=.12,
                raw_estimator_posterior_comparison=False,
                matched_120ms_publication_comparison=True,
            )
    if input_safety:
        summary.update(
            role="ROOT9_RANGE_ADMISSION_IMU_GAP_INPUT_SAFETY_COMPARISON",
            comparison_labels=["原融合", "测距接纳＋IMU断档处理"],
            comparison_note=("同一120ms发布输出；" if published else "原始估计器后验，不使用发布平滑；") + "左右均比较旧/新 IMU+UWB 融合 root，不是纯 IMU 对比。保持 root 9 维、十节点原始测距、相同 200 Hz 与 FK/IK；不增加 39 维状态，不改变 Q。IMU 分支也会令过期输入失效，但本页不显示该分支。统计不代表物理精度验证。",
            viewer_policy="仅比较测距接纳与 IMU 断档输入处理；root 9 维，十节点原始 UWB，相同 native 200 Hz 与 FK/IK。无新 39 维状态、无 Q 调整；显示仅取前序样本，不新增平滑。",
            common_clock_anchors_nodes_raw_capture_exact=True,
            identical_raw_payload_array_sha256=raw_bindings,
            scientific_acceptance=False,
            publication_release_period_s=.12 if published else None,
            raw_estimator_posterior_comparison=not published,
            matched_120ms_publication_comparison=published,
            compared_outputs="OLD_FUSED_ROOT_VS_NEW_FUSED_ROOT_NOT_PURE_IMU",
        )
    output.parent.mkdir(parents=True, exist_ok=True)
    shared = {key: off[key] for key in (*SHARED, "node_names", "anchor_names") if key in off}
    if joint_feedback:
        shared["joints_relative_b"] = on["joints_relative_b"]
    if geometry_repair:
        shared["joints_relative"] = off.get("joints_relative_b", off["joints_relative"])
        shared["joints_relative_b"] = on.get("joints_relative_b", on["joints_relative"])
    np.savez_compressed(output, roots_a=off["roots_b"], roots_b=on["roots_b"], **shared)
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
    if html_path is not None:
        from tools.build_c2_full_continuous_ab_viewer import build as build_viewer
        build_viewer(output, regions_path, html_path, summary_path=summary_path)
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--off", type=Path, required=True)
    parser.add_argument("--on", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--regions", type=Path, default=DEFAULT_REGIONS)
    parser.add_argument("--html", type=Path)
    parser.add_argument("--back-cut", action="store_true")
    parser.add_argument("--joint-feedback", action="store_true")
    parser.add_argument("--geometry-repair", action="store_true")
    parser.add_argument("--persistent-error", action="store_true")
    parser.add_argument("--published", action="store_true")
    parser.add_argument("--input-safety", action="store_true")
    args = parser.parse_args()
    report = build(args.off, args.on, args.output, regions_path=args.regions, html_path=args.html,
                   back_cut=args.back_cut, joint_feedback=args.joint_feedback,
                   geometry_repair=args.geometry_repair, persistent_error=args.persistent_error,
                   published=args.published, input_safety=args.input_safety)
    print(json.dumps({"output": str(args.output), "region_count": len(report["motion"]["regions"]),
                      "full_motion": report["motion"]["full"]}, ensure_ascii=False))
