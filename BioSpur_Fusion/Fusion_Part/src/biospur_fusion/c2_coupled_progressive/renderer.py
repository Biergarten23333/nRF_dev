"""Renderer for estimator-owned C2 corrected trajectory streams.

This module never runs QMT, never recalculates heading, and never creates a
viewer-local yaw gauge. It consumes the trajectory dictionary produced by
``estimator.build_corrected_trajectory``.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.font_manager import FontProperties
import numpy as np

from .contracts import load_effective_config
from .estimator import SEGMENTS, PosteriorState
from .math_utils import array_binding, qmt_wxyz_to_rotation, semantic_sha
from .output_coordinates import apply_output_coordinate_convention


SKELETON_LINES = [
    ("pelvis_center", "shoulder_mid"),
    ("shoulder_left", "shoulder_right"),
    ("hip_left", "hip_right"),
    ("shoulder_left", "elbow_left"),
    ("elbow_left", "wrist_left"),
    ("shoulder_right", "elbow_right"),
    ("elbow_right", "wrist_right"),
    ("hip_left", "knee_left"),
    ("knee_left", "ankle_left"),
    ("hip_right", "knee_right"),
    ("knee_right", "ankle_right"),
]

_CJK_FONT_PATH = Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc")


def _cjk_font(size: float) -> FontProperties | None:
    if not _CJK_FONT_PATH.is_file():
        return None
    return FontProperties(fname=str(_CJK_FONT_PATH), size=size)


@dataclass(frozen=True)
class DisplayModel:
    name: str
    torso_height_m: float
    hip_span_m: float


def display_models(config: dict[str, Any] | None = None) -> list[DisplayModel]:
    cfg = load_effective_config() if config is None else config
    torso_values = [float(v) for v in cfg["proxy_geometry"]["torso_display_geometry"]["models_m"]]
    # Display-only hip span sensitivity. It is not calibrated and is not
    # derived from the external 0.335 m trochanter landmark span.
    hip_values = [0.18, 0.23, 0.28]
    return [
        DisplayModel(name, torso, hip)
        for name, torso, hip in zip(("short_narrow", "middle_proxy", "tall_wide"), torso_values, hip_values)
    ]


def _segment_matrix(trajectory: dict[str, Any], episode_key: str, segment: str, frame: int) -> np.ndarray:
    quat = trajectory["trajectory"][episode_key][segment]["quat_world_segment_wxyz"][frame]
    return qmt_wxyz_to_rotation(quat).as_matrix()


def _frame_mask(trajectory: dict[str, Any], episode_key: str) -> np.ndarray:
    masks = [
        np.asarray(trajectory["trajectory"][episode_key][segment]["mask"], dtype=bool)
        for segment in SEGMENTS
    ]
    return np.logical_and.reduce(masks)


def joints_for_frame(
    trajectory: dict[str, Any],
    episode_key: str,
    frame: int,
    model: DisplayModel,
    config: dict[str, Any],
    *,
    apply_output_coordinates: bool = True,
) -> dict[str, np.ndarray]:
    geom = config["proxy_geometry"]
    length = {
        "upper_arm_left": float(geom["upper_arm_left_m"]["nominal"]),
        "upper_arm_right": float(geom["upper_arm_right_m"]["nominal"]),
        "forearm_left": float(geom["forearm_left_m"]["nominal"]),
        "forearm_right": float(geom["forearm_right_m"]["nominal"]),
        "thigh_left": float(geom["thigh_left_m"]["nominal"]),
        "thigh_right": float(geom["thigh_right_m"]["nominal"]),
        "shank_left": float(geom["shank_left_m"]["nominal"]),
        "shank_right": float(geom["shank_right_m"]["nominal"]),
        "shoulder_span": float(geom["acromion_proxy_span_m"]["nominal"]),
    }
    root = np.zeros(3, dtype=float)
    pelvis = _segment_matrix(trajectory, episode_key, "pelvis", frame)
    torso = _segment_matrix(trajectory, episode_key, "torso", frame)
    shoulder_mid = root + torso @ np.array([0.0, 0.0, model.torso_height_m])
    shoulder_left = shoulder_mid + torso @ np.array([-0.5 * length["shoulder_span"], 0.0, 0.0])
    shoulder_right = shoulder_mid + torso @ np.array([0.5 * length["shoulder_span"], 0.0, 0.0])
    hip_left = root + pelvis @ np.array([-0.5 * model.hip_span_m, 0.0, 0.0])
    hip_right = root + pelvis @ np.array([0.5 * model.hip_span_m, 0.0, 0.0])
    elbow_left = shoulder_left + _segment_matrix(trajectory, episode_key, "upper_arm_left", frame) @ np.array([0.0, 0.0, -length["upper_arm_left"]])
    wrist_left = elbow_left + _segment_matrix(trajectory, episode_key, "forearm_left", frame) @ np.array([0.0, 0.0, -length["forearm_left"]])
    elbow_right = shoulder_right + _segment_matrix(trajectory, episode_key, "upper_arm_right", frame) @ np.array([0.0, 0.0, -length["upper_arm_right"]])
    wrist_right = elbow_right + _segment_matrix(trajectory, episode_key, "forearm_right", frame) @ np.array([0.0, 0.0, -length["forearm_right"]])
    knee_left = hip_left + _segment_matrix(trajectory, episode_key, "thigh_left", frame) @ np.array([0.0, 0.0, -length["thigh_left"]])
    ankle_left = knee_left + _segment_matrix(trajectory, episode_key, "shank_left", frame) @ np.array([0.0, 0.0, -length["shank_left"]])
    knee_right = hip_right + _segment_matrix(trajectory, episode_key, "thigh_right", frame) @ np.array([0.0, 0.0, -length["thigh_right"]])
    ankle_right = knee_right + _segment_matrix(trajectory, episode_key, "shank_right", frame) @ np.array([0.0, 0.0, -length["shank_right"]])
    joints = {
        "pelvis_center": root,
        "shoulder_mid": shoulder_mid,
        "shoulder_left": shoulder_left,
        "shoulder_right": shoulder_right,
        "hip_left": hip_left,
        "hip_right": hip_right,
        "elbow_left": elbow_left,
        "wrist_left": wrist_left,
        "elbow_right": elbow_right,
        "wrist_right": wrist_right,
        "knee_left": knee_left,
        "ankle_left": ankle_left,
        "knee_right": knee_right,
        "ankle_right": ankle_right,
    }
    if not apply_output_coordinates:
        return joints
    return apply_output_coordinate_convention(trajectory, joints)


def _horizontal_alignment(a: np.ndarray, b: np.ndarray) -> float:
    ah = np.asarray(a[:2], dtype=float)
    bh = np.asarray(b[:2], dtype=float)
    denom = float(np.linalg.norm(ah) * np.linalg.norm(bh))
    return float(np.dot(ah, bh) / denom) if denom > 1e-9 else -1.0


def _segment_distance_3d(
    first_start: np.ndarray,
    first_stop: np.ndarray,
    second_start: np.ndarray,
    second_stop: np.ndarray,
) -> float:
    """Shortest distance between two finite 3-D line segments.

    A front/top projection may show a crossing while the limbs are separated
    in depth.  The physical gate must use the actual 3-D FK coordinates, not
    a viewer projection.  This is the standard clamped closest-points
    calculation and introduces no anatomical or action-pose target.
    """
    p1 = np.asarray(first_start, dtype=float)
    q1 = np.asarray(first_stop, dtype=float)
    p2 = np.asarray(second_start, dtype=float)
    q2 = np.asarray(second_stop, dtype=float)
    d1 = q1 - p1
    d2 = q2 - p2
    r = p1 - p2
    a = float(np.dot(d1, d1))
    e = float(np.dot(d2, d2))
    eps = 1e-12
    if a <= eps and e <= eps:
        return float(np.linalg.norm(p1 - p2))
    if a <= eps:
        s = 0.0
        t = float(np.clip(np.dot(d2, r) / e, 0.0, 1.0))
    else:
        c = float(np.dot(d1, r))
        if e <= eps:
            t = 0.0
            s = float(np.clip(-c / a, 0.0, 1.0))
        else:
            b = float(np.dot(d1, d2))
            f = float(np.dot(d2, r))
            denominator = a * e - b * b
            s = float(np.clip((b * f - c * e) / denominator, 0.0, 1.0)) if denominator > eps else 0.0
            t = (b * s + f) / e
            if t < 0.0:
                t = 0.0
                s = float(np.clip(-c / a, 0.0, 1.0))
            elif t > 1.0:
                t = 1.0
                s = float(np.clip((b - c) / a, 0.0, 1.0))
    return float(np.linalg.norm((p1 + s * d1) - (p2 + t * d2)))


def _contralateral_leg_distance(joints: dict[str, np.ndarray]) -> float:
    left = (("hip_left", "knee_left"), ("knee_left", "ankle_left"))
    right = (("hip_right", "knee_right"), ("knee_right", "ankle_right"))
    return float(min(
        _segment_distance_3d(joints[a], joints[b], joints[c], joints[d])
        for a, b in left
        for c, d in right
    ))


def physical_qa(
    joints: dict[str, np.ndarray],
    *,
    standing: bool = False,
) -> dict[str, Any]:
    finite = bool(all(np.all(np.isfinite(value)) for value in joints.values()))
    lengths = [float(np.linalg.norm(joints[b] - joints[a])) for a, b in SKELETON_LINES]
    left_order = bool(joints["shoulder_left"][2] > joints["hip_left"][2] > joints["knee_left"][2] > joints["ankle_left"][2])
    right_order = bool(joints["shoulder_right"][2] > joints["hip_right"][2] > joints["knee_right"][2] > joints["ankle_right"][2])
    # Left/right and front/back are anatomical relations, not fixed world-axis
    # relations.  The old gate used world X/Y and falsely rejected a valid
    # body whenever the subject naturally turned during an action.
    hip_axis = joints["hip_right"] - joints["hip_left"]
    shoulder_axis = joints["shoulder_right"] - joints["shoulder_left"]
    body_right = hip_axis.copy()
    body_right[2] = 0.0
    if np.linalg.norm(body_right) < 1e-9:
        body_right = shoulder_axis.copy()
        body_right[2] = 0.0
    body_right /= max(float(np.linalg.norm(body_right)), 1e-9)
    body_forward = np.cross(np.array([0.0, 0.0, 1.0]), body_right)
    origin = joints["pelvis_center"]

    def lateral(name: str) -> float:
        return float(np.dot(joints[name] - origin, body_right))

    left_right = bool(
        lateral("shoulder_left") < lateral("shoulder_right")
        and lateral("hip_left") < lateral("hip_right")
        and lateral("knee_left") < lateral("knee_right")
        and lateral("ankle_left") < lateral("ankle_right")
    )
    knee_forward = np.array([
        np.dot(joints["knee_left"] - joints["hip_left"], body_forward),
        np.dot(joints["knee_right"] - joints["hip_right"], body_forward),
    ])
    sagittal_deadband_m = 0.03
    knee_split = bool(
        (knee_forward[0] > sagittal_deadband_m and knee_forward[1] < -sagittal_deadband_m)
        or (knee_forward[1] > sagittal_deadband_m and knee_forward[0] < -sagittal_deadband_m)
    )
    collapse = bool(min(lengths) < 0.05)
    contralateral_leg_distance = _contralateral_leg_distance(joints)
    # Five millimetres is a numerical intersection guard, not a human body
    # thickness model.  Near but depth-separated limbs remain valid.
    leg_segment_intersection_3d = bool(contralateral_leg_distance < 0.005)
    torso_axis = joints["shoulder_mid"] - joints["pelvis_center"]
    torso_up_dot = float(torso_axis[2] / max(np.linalg.norm(torso_axis), 1e-9))
    shoulder_hip_horizontal_alignment = _horizontal_alignment(shoulder_axis, hip_axis)
    torso_pelvis_twist_deg = float(np.degrees(np.arccos(np.clip(
        shoulder_hip_horizontal_alignment, -1.0, 1.0
    ))))
    gross_axial_twist = bool(shoulder_hip_horizontal_alignment < 0.0)
    shoulder_vertical_fraction = float(abs(shoulder_axis[2]) / max(np.linalg.norm(shoulder_axis), 1e-9))
    hip_vertical_fraction = float(abs(hip_axis[2]) / max(np.linalg.norm(hip_axis), 1e-9))
    lateral_frames_feasible = bool(
        shoulder_vertical_fraction < 0.55 and hip_vertical_fraction < 0.55
    )
    points = np.vstack(list(joints.values()))
    body_vertical_extent = float(np.max(points[:, 2]) - np.min(points[:, 2]))
    height_threshold_m = 0.65 if standing else 0.35
    gross_height_collapse = bool(body_vertical_extent < height_threshold_m)
    torso_feasible = bool(torso_up_dot > 0.25)
    vertical_order_required = standing
    vertical_order_pass = bool(
        (left_order and right_order) if vertical_order_required else True
    )
    passed = bool(
        finite
        and vertical_order_pass
        and left_right
        and not knee_split
        and not leg_segment_intersection_3d
        and not collapse
        and not gross_height_collapse
        and not gross_axial_twist
        and lateral_frames_feasible
        and torso_feasible
    )
    return {
        "pass": passed,
        "finite": finite,
        "standing_vertical_order_required": vertical_order_required,
        "shoulders_above_hips_above_knees_above_ankles_left": left_order,
        "shoulders_above_hips_above_knees_above_ankles_right": right_order,
        "left_right_order": left_right,
        "left_right_and_forward_frame": "PELVIS_LATERAL_BODY_FRAME",
        "front_back_knee_split": knee_split,
        "contralateral_leg_min_distance_m": contralateral_leg_distance,
        "leg_segment_intersection_3d": leg_segment_intersection_3d,
        "leg_segment_intersection_threshold_m": 0.005,
        "knee_sagittal_deadband_m": sagittal_deadband_m,
        "collapse": collapse,
        "gross_height_collapse": gross_height_collapse,
        "body_vertical_extent_m": body_vertical_extent,
        "body_vertical_extent_threshold_m": height_threshold_m,
        "torso_world_up_dot": torso_up_dot,
        "torso_feasible": torso_feasible,
        "shoulder_hip_horizontal_alignment": shoulder_hip_horizontal_alignment,
        "torso_pelvis_twist_deg": torso_pelvis_twist_deg,
        "gross_axial_twist_over_90deg": gross_axial_twist,
        "shoulder_axis_vertical_fraction": shoulder_vertical_fraction,
        "hip_axis_vertical_fraction": hip_vertical_fraction,
        "lateral_frames_feasible": lateral_frames_feasible,
        "connected": finite,
        "minimum_segment_length_m": float(min(lengths)),
        "knee_forward_offsets_m": knee_forward.tolist(),
    }


def _trajectory_motion_score(
    trajectory: dict[str, Any],
    episode_key: str,
) -> np.ndarray:
    per_segment: list[np.ndarray] = []
    for segment in SEGMENTS:
        quat = np.asarray(
            trajectory["trajectory"][episode_key][segment]["quat_world_segment_wxyz"],
            dtype=float,
        )
        dot = np.abs(np.sum(quat[1:] * quat[:-1], axis=1))
        angle = 2.0 * np.arccos(np.clip(dot, 0.0, 1.0))
        angle[~np.isfinite(angle)] = 0.0
        per_segment.append(np.r_[0.0, angle])
    score = np.sum(np.vstack(per_segment), axis=0)
    # One-second-ish smoothing at the 20 Hz renderer grid suppresses a single
    # noisy derivative sample without using an action label or desired pose.
    kernel = np.ones(21, dtype=float) / 21.0
    return np.convolve(score, kernel, mode="same")


def select_render_candidates(
    trajectory: dict[str, Any],
    state: PosteriorState,
    config: dict[str, Any],
    selections: dict[str, int],
) -> list[dict[str, Any]]:
    models = display_models(config)
    rows: list[dict[str, Any]] = []
    for label, episode_index in selections.items():
        episode_key = f"{episode_index:02d}"
        common = _frame_mask(trajectory, episode_key)
        valid_indices = np.flatnonzero(common)
        if len(valid_indices) == 0:
            rows.append({
                "label": label,
                "episode_index": episode_index,
                "status": "NO_FULL_BODY_COMMON_TRAJECTORY_FRAME",
                "renderable": False,
            })
            continue
        motion_score = _trajectory_motion_score(trajectory, episode_key)
        if label == "standing":
            # Inspect an early stable frame after every segment has a valid
            # capture-wide correction, not a hand-picked posture.
            frame = int(valid_indices[min(len(valid_indices) - 1, max(0, len(valid_indices) // 10))])
            selection_quantile = 0.10
        else:
            valid_score = motion_score[valid_indices]
            finite_score = np.isfinite(valid_score)
            if np.any(finite_score):
                threshold = float(np.quantile(valid_score[finite_score], 0.85))
                eligible = valid_indices[finite_score & (valid_score >= threshold)]
                frame = int(eligible[0])
            else:
                frame = int(valid_indices[len(valid_indices) // 2])
            selection_quantile = 0.85
        for model in models:
            joints = joints_for_frame(trajectory, episode_key, frame, model, config)
            qa = physical_qa(joints, standing=(label == "standing"))
            rows.append({
                "label": label,
                "episode_index": episode_index,
                "episode_key": episode_key,
                "frame": frame,
                "frame_selection": {
                    "source": "trajectory_quaternion_increment_energy",
                    "result_or_pose_label_used": False,
                    "quantile": selection_quantile,
                    "selected_score": float(motion_score[frame]),
                },
                "display_model": model.__dict__,
                "branch_id": int(np.argmax([row.weight for row in state.branch_candidates])) if state.branch_candidates else None,
                "pre_render_physical_selection": True,
                "renderable": qa["pass"],
                "physical_qa": qa,
                "joints": joints,
            })
    return rows


def _plot_view(ax: Any, joints: dict[str, np.ndarray], dims: tuple[int, int], title: str) -> None:
    for a, b in SKELETON_LINES:
        pa = joints[a]
        pb = joints[b]
        if a.endswith("_left") and b.endswith("_left"):
            color = "#1f77b4"
        elif a.endswith("_right") and b.endswith("_right"):
            color = "#ff7f0e"
        else:
            color = "#171717"
        ax.plot(
            [pa[dims[0]], pb[dims[0]]],
            [pa[dims[1]], pb[dims[1]]],
            color=color,
            linewidth=2.4,
        )
    ax.set_aspect("equal", adjustable="box")
    ax.set_title(title, fontsize=9)
    ax.axis("off")


def render_triptych(
    joints: dict[str, np.ndarray],
    path: Path,
    title: str,
    *,
    caption_zh: str | None = None,
) -> None:
    figure_height = 4.8 if caption_zh else 4.0
    fig, axes = plt.subplots(1, 3, figsize=(9.0, figure_height), dpi=130)
    _plot_view(axes[0], joints, (0, 2), "front")
    _plot_view(axes[1], joints, (1, 2), "side")
    _plot_view(axes[2], joints, (0, 1), "top")
    points = np.vstack(list(joints.values()))
    for ax, dims in zip(axes, ((0, 2), (1, 2), (0, 1)), strict=True):
        xs = points[:, dims[0]]
        ys = points[:, dims[1]]
        cx = 0.5 * float(xs.min() + xs.max())
        cy = 0.5 * float(ys.min() + ys.max())
        span = 0.58 * max(float(xs.max() - xs.min()), float(ys.max() - ys.min()), 1.0)
        ax.set_xlim(cx - span, cx + span)
        ax.set_ylim(cy - span, cy + span)
    title_font = _cjk_font(10.0)
    fig.suptitle(
        f"{title}  |  左侧=蓝色，右侧=橙色",
        fontsize=10,
        fontproperties=title_font,
    )
    if caption_zh:
        caption_font = _cjk_font(8.5)
        fig.text(
            0.5,
            0.025,
            caption_zh,
            ha="center",
            va="bottom",
            wrap=True,
            fontproperties=caption_font,
        )
        fig.tight_layout(rect=(0.0, 0.15, 1.0, 0.92))
    else:
        fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)


def render_selected_episodes(
    trajectory: dict[str, Any],
    state: PosteriorState,
    output_dir: Path,
    *,
    selections: dict[str, int] | None = None,
) -> dict[str, Any]:
    start = time.perf_counter()
    config = load_effective_config()
    output_dir.mkdir(parents=True, exist_ok=True)
    selection_map = selections or {"standing": 0, "upper": 6, "lower": 17}
    candidates = select_render_candidates(trajectory, state, config, selection_map)
    pngs: list[str] = []
    diagnostic_pngs: list[str] = []
    qa_rows: list[dict[str, Any]] = []
    bindings: dict[str, Any] = {}
    for candidate in candidates:
        if not candidate.get("renderable", False):
            row = {k: v for k, v in candidate.items() if k != "joints"}
            if "joints" in candidate:
                model_name = candidate["display_model"]["name"]
                png = output_dir / f"diagnostic_invalid_{candidate['label']}_{candidate['episode_key']}_{model_name}_front_side_top.png"
                render_triptych(candidate["joints"], png, f"diagnostic invalid {candidate['label']} episode {candidate['episode_key']} {model_name}")
                coords = np.vstack([candidate["joints"][name] for name in sorted(candidate["joints"])])
                bindings[f"diagnostic_invalid/{candidate['label']}/{candidate['episode_key']}/{model_name}/joints"] = array_binding(coords)
                diagnostic_pngs.append(str(png))
                row["diagnostic_png"] = str(png)
            qa_rows.append(row)
            continue
        model_name = candidate["display_model"]["name"]
        png = output_dir / f"{candidate['label']}_{candidate['episode_key']}_{model_name}_front_side_top.png"
        render_triptych(candidate["joints"], png, f"{candidate['label']} episode {candidate['episode_key']} {model_name}")
        coords = np.vstack([candidate["joints"][name] for name in sorted(candidate["joints"])])
        bindings[f"{candidate['label']}/{candidate['episode_key']}/{model_name}/joints"] = array_binding(coords)
        pngs.append(str(png))
        row = {k: v for k, v in candidate.items() if k != "joints"}
        row["png"] = str(png)
        qa_rows.append(row)
    renderable_labels = {row["label"] for row in qa_rows if "png" in row}
    all_required_labels_renderable = set(selection_map).issubset(renderable_labels)
    html = output_dir / "replay_index.html"
    html.write_text(_html(qa_rows), encoding="utf-8")
    audit = {
        "schema": "biospur-c2-coupled-progressive-direct-fk-render-audit-v2",
        "status": "PASS" if (
            all_required_labels_renderable
            and all(row.get("physical_qa", {}).get("pass", False) for row in qa_rows if row.get("png"))
        ) else "PHYSICAL_QA_FAIL",
        "wall_s": float(time.perf_counter() - start),
        "selection_map": selection_map,
        "selection_basis": "post_fit_visual_QA_only_no_calibration_target_or_action_factor_routing",
        "pngs": pngs,
        "diagnostic_pngs": diagnostic_pngs,
        "html": str(html),
        "qa": qa_rows,
        "trajectory_owner": trajectory["schema"],
        "viewer_qmt_rerun": False,
        "viewer_heading_fallback": False,
        "viewer_yaw_gauge_recomputed": False,
        "single_capture_wide_pelvis_yaw_gauge_rad": trajectory["pelvis_yaw_gauge_rad"],
        "torso_scalar_0_425_used_as_posterior": False,
        "trochanter_0_335_used_as_internal_hip_spacing": False,
        "sensor_origins_drawn_as_joints": False,
        "ik_retarget_rebase_repair_used": False,
        "plain_skeleton_only": True,
        "trajectory_bindings": bindings,
        "semantic_sha256": semantic_sha({"qa": qa_rows, "pngs": pngs, "bindings": bindings}),
    }
    path = output_dir / "DIRECT_FK_RENDER_AUDIT.json"
    path.write_text(json.dumps(audit, indent=2, sort_keys=True), encoding="utf-8")
    return audit


def _html(rows: list[dict[str, Any]]) -> str:
    sections = []
    for row in rows:
        if "png" in row:
            rel = Path(row["png"]).name
            sections.append(
                f"<section><h2>{row['label']} {row['episode_key']} {row['display_model']['name']}</h2>"
                f"<img src='{rel}' alt='{rel}'><pre>{json.dumps(row['physical_qa'], sort_keys=True)}</pre></section>"
            )
        elif "diagnostic_png" in row:
            rel = Path(row["diagnostic_png"]).name
            sections.append(
                f"<section><h2>diagnostic invalid {row['label']} {row['episode_key']} {row['display_model']['name']}</h2>"
                f"<img src='{rel}' alt='{rel}'><pre>{json.dumps(row['physical_qa'], sort_keys=True)}</pre></section>"
            )
        else:
            sections.append(f"<section><h2>{row.get('label')} unavailable</h2><pre>{json.dumps(row, sort_keys=True)}</pre></section>")
    return (
        "<!doctype html><meta charset='utf-8'><title>C2 direct FK replay</title>"
        "<style>body{font-family:Arial,sans-serif;margin:24px;background:white;color:#111}"
        "section{margin-bottom:28px}img{max-width:100%;border:1px solid #ddd}</style>"
        "<h1>C2 direct-FK plain stick figure</h1>"
        + "".join(sections)
    )
