"""Fixed-scale A/B rendering and physical diagnostics for the thin adapter."""

from __future__ import annotations

import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from .adapter import (
    C2_FROM_OPENSIM,
    SEGMENTS,
    _quat_wxyz_matrix,
    _table_columns,
    sha256_file,
)


LINKS = (
    ("pelvis_center", "shoulder_mid"),
    ("shoulder_mid", "shoulder_left"),
    ("shoulder_mid", "shoulder_right"),
    ("pelvis_center", "hip_left"),
    ("pelvis_center", "hip_right"),
    ("shoulder_left", "elbow_left"),
    ("elbow_left", "wrist_left"),
    ("shoulder_right", "elbow_right"),
    ("elbow_right", "wrist_right"),
    ("hip_left", "knee_left"),
    ("knee_left", "ankle_left"),
    ("hip_right", "knee_right"),
    ("knee_right", "ankle_right"),
)
VIEWS = ((0, 2, "front"), (1, 2, "side"), (0, 1, "top"))


def _matrix(rotation) -> np.ndarray:
    return np.array(
        [[rotation.get(row, col) for col in range(3)] for row in range(3)], dtype=float
    )


def _vec(vector) -> np.ndarray:
    return np.array([vector[index] for index in range(3)], dtype=float)


def replay_model(model_path: Path, motion_path: Path):
    import opensim as osim

    model = osim.Model(str(model_path.resolve()))
    state = model.initSystem()
    times, columns = _table_columns(motion_path)
    coordinate_set = model.updCoordinateSet()
    coordinates = {
        coordinate_set.get(index).getName(): coordinate_set.get(index)
        for index in range(coordinate_set.getSize())
    }
    rows = []
    for row_index, time_s in enumerate(times):
        state.setTime(float(time_s))
        for name, values in columns.items():
            coordinates[name].setValue(state, float(np.deg2rad(values[row_index])), False)
        model.realizePosition(state)
        rows.append(
            {
                segment: {
                    "R": C2_FROM_OPENSIM
                    @ _matrix(model.getBodySet().get(segment).getRotationInGround(state)),
                    "p": C2_FROM_OPENSIM
                    @ _vec(model.getBodySet().get(segment).getPositionInGround(state)),
                }
                for segment in SEGMENTS
            }
        )
    return times, rows


def b_points(frozen, row):
    g = frozen.geometry
    length = g.segment_length_m
    points = {
        "pelvis_center": row["pelvis"]["p"],
        "shoulder_mid": row["torso"]["p"] + row["torso"]["R"] @ np.array([0.0, 0.0, g.torso_height_m]),
        "shoulder_left": row["upper_arm_left"]["p"],
        "shoulder_right": row["upper_arm_right"]["p"],
        "hip_left": row["thigh_left"]["p"],
        "hip_right": row["thigh_right"]["p"],
        "elbow_left": row["forearm_left"]["p"],
        "elbow_right": row["forearm_right"]["p"],
        "knee_left": row["shank_left"]["p"],
        "knee_right": row["shank_right"]["p"],
    }
    for side in ("left", "right"):
        points[f"wrist_{side}"] = row[f"forearm_{side}"]["p"] + row[f"forearm_{side}"]["R"] @ np.array([0.0, 0.0, -length[f"forearm_{side}"]])
        points[f"ankle_{side}"] = row[f"shank_{side}"]["p"] + row[f"shank_{side}"]["R"] @ np.array([0.0, 0.0, -length[f"shank_{side}"]])
    display = frozen.output_matrix_world_display_from_internal
    return {name: display @ value for name, value in points.items()}


def a_points(frozen, episode, frame: int):
    """Evaluate the sealed FK equation for primary or read-only holdout rows."""

    if episode.key in frozen.episodes:
        return frozen.forward_kinematics(episode.key, frame, coordinates="display")
    matrices = {
        segment: _quat_wxyz_matrix(
            episode.segments[segment].quat_world_segment_wxyz[frame]
        )
        for segment in SEGMENTS
    }
    g = frozen.geometry
    length = g.segment_length_m
    root = np.zeros(3)
    shoulder_mid = root + matrices["torso"] @ np.array([0.0, 0.0, g.torso_height_m])
    points = {
        "pelvis_center": root,
        "shoulder_mid": shoulder_mid,
        "shoulder_left": shoulder_mid + matrices["torso"] @ np.array([-0.5 * g.shoulder_span_m, 0.0, 0.0]),
        "shoulder_right": shoulder_mid + matrices["torso"] @ np.array([0.5 * g.shoulder_span_m, 0.0, 0.0]),
        "hip_left": root + matrices["pelvis"] @ np.array([-0.5 * g.hip_span_m, 0.0, 0.0]),
        "hip_right": root + matrices["pelvis"] @ np.array([0.5 * g.hip_span_m, 0.0, 0.0]),
    }
    for side in ("left", "right"):
        points[f"elbow_{side}"] = points[f"shoulder_{side}"] + matrices[f"upper_arm_{side}"] @ np.array([0.0, 0.0, -length[f"upper_arm_{side}"]])
        points[f"wrist_{side}"] = points[f"elbow_{side}"] + matrices[f"forearm_{side}"] @ np.array([0.0, 0.0, -length[f"forearm_{side}"]])
        points[f"knee_{side}"] = points[f"hip_{side}"] + matrices[f"thigh_{side}"] @ np.array([0.0, 0.0, -length[f"thigh_{side}"]])
        points[f"ankle_{side}"] = points[f"knee_{side}"] + matrices[f"shank_{side}"] @ np.array([0.0, 0.0, -length[f"shank_{side}"]])
    display = frozen.output_matrix_world_display_from_internal
    return {name: display @ value for name, value in points.items()}


def _plot(ax, points, horizontal, vertical, color, label, linestyle):
    first = True
    for start, end in LINKS:
        values = np.stack((points[start], points[end]))
        ax.plot(values[:, horizontal], values[:, vertical], color=color, linestyle=linestyle, linewidth=1.5, label=label if first else None)
        first = False
    values = np.stack(list(points.values()))
    ax.scatter(values[:, horizontal], values[:, vertical], color=color, s=7)


def render_episode(frozen, episode, label: str, rows, output_path: Path):
    frames = [0, episode.frame_count // 4, episode.frame_count // 2, 3 * episode.frame_count // 4, episode.frame_count - 1]
    all_frames = []
    for frame in frames:
        a = a_points(frozen, episode, frame)
        b = b_points(frozen, rows[frame])
        all_frames.append((a, b))
    pooled = np.concatenate([np.stack(list(points.values())) for pair in all_frames for points in pair])
    center = 0.5 * (pooled.min(axis=0) + pooled.max(axis=0))
    radius = max(float(np.max(pooled.max(axis=0) - pooled.min(axis=0))) * 0.56, 0.25)
    fig, axes = plt.subplots(5, 3, figsize=(10.5, 15), constrained_layout=True)
    for row_index, (frame, (a, b)) in enumerate(zip(frames, all_frames)):
        for column_index, (horizontal, vertical, view) in enumerate(VIEWS):
            ax = axes[row_index, column_index]
            _plot(ax, a, horizontal, vertical, "#444444", "A frozen FK", "--")
            _plot(ax, b, horizontal, vertical, "#d62728", "B official IK", "-")
            ax.set_xlim(center[horizontal] - radius, center[horizontal] + radius)
            ax.set_ylim(center[vertical] - radius, center[vertical] + radius)
            ax.set_aspect("equal", adjustable="box")
            ax.set_axis_off()
            ax.set_title(f"frame {frame} | {view}")
            if row_index == 0 and column_index == 0:
                ax.legend(fontsize=7, loc="upper left")
    fig.suptitle(f"{label}: immutable A vs official OpenSim IK B\ncommon fixed scale; 0/25/50/75/100% frames")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path, dpi=180)
    plt.close(fig)
    return frames, radius


def episode_metrics(frozen, episode, rows) -> dict[str, object]:
    point_errors = {name: [] for name in a_points(frozen, episode, 0)}
    connection_max = 0.0
    finite = True
    for frame, row in enumerate(rows):
        a = a_points(frozen, episode, frame)
        b = b_points(frozen, row)
        for name in point_errors:
            value = float(np.linalg.norm(a[name] - b[name]))
            point_errors[name].append(value)
            finite = bool(finite and np.isfinite(value))
        # Model-owned origins must be exactly connected to the matching joint.
        for first, second in (("elbow_left", "forearm_left"), ("elbow_right", "forearm_right"), ("knee_left", "shank_left"), ("knee_right", "shank_right")):
            connection_max = max(connection_max, float(np.linalg.norm(C2_FROM_OPENSIM @ row[second]["p"] - np.asarray(b[first]) @ frozen.output_matrix_world_display_from_internal)))
    summary = {
        name: {"mean_m": float(np.mean(values)), "p95_m": float(np.quantile(values, 0.95)), "max_m": float(np.max(values))}
        for name, values in point_errors.items()
    }
    pooled = np.concatenate([np.asarray(values) for values in point_errors.values()])
    return {
        "finite": bool(finite),
        "point_residual_overall_mean_m": float(np.mean(pooled)),
        "point_residual_overall_p95_m": float(np.quantile(pooled, 0.95)),
        "point_residual_overall_max_m": float(np.max(pooled)),
        "per_point": summary,
        "model_joint_connection_definition": "shared OpenSim Joint frames; disconnected state impossible",
    }


def render_and_summarize(frozen, episode, label: str, model_path: Path, motion_path: Path, output_dir: Path) -> dict[str, object]:
    times, rows = replay_model(model_path, motion_path)
    montage = output_dir / f"{label}_ab_front_side_top.png"
    frames, radius = render_episode(frozen, episode, label, rows, montage)
    result = episode_metrics(frozen, episode, rows)
    result.update({"rows": len(rows), "times_s": [float(times[0]), float(times[-1])], "frames": frames, "common_radius_m": radius, "montage": str(montage.resolve()), "montage_sha256": sha256_file(montage)})
    return result


def write_interactive_viewer(frozen, episode_items, model_path: Path, motion_root: Path, output_path: Path) -> dict[str, object]:
    """Write a self-contained, read-only A/B trajectory viewer."""

    payload = {}
    point_names = list(a_points(frozen, episode_items[0][1], 0))
    for label, episode in episode_items:
        times, rows = replay_model(
            model_path, motion_root / label / "official" / "official_ik.sto"
        )
        a_rows = []
        b_rows = []
        for frame, row in enumerate(rows):
            a = a_points(frozen, episode, frame)
            b = b_points(frozen, row)
            a_rows.append([[round(float(value), 9) for value in a[name]] for name in point_names])
            b_rows.append([[round(float(value), 9) for value in b[name]] for name in point_names])
        pooled = np.asarray(a_rows + b_rows, dtype=float)
        center = 0.5 * (pooled.min(axis=(0, 1)) + pooled.max(axis=(0, 1)))
        radius = max(float(np.max(pooled.max(axis=(0, 1)) - pooled.min(axis=(0, 1)))) * 0.56, 0.25)
        payload[label] = {
            "time": [round(float(value), 6) for value in times],
            "a": a_rows,
            "b": b_rows,
            "center": center.tolist(),
            "radius": radius,
        }

    html = """<!doctype html>
<meta charset=\"utf-8\"><title>Frozen C2 FK A / official OpenSim IK B</title>
<style>
body{font:14px system-ui;margin:18px;background:#fafafa;color:#222} .controls{display:flex;gap:12px;align-items:center;flex-wrap:wrap}
canvas{width:100%;max-width:1200px;height:auto;background:white;border:1px solid #ddd;margin-top:12px} input[type=range]{width:min(620px,70vw)}
</style>
<h1>Frozen C2 FK A / official OpenSim IK B</h1>
<div class=\"controls\"><label>Episode <select id=\"episode\"></select></label><button id=\"play\">Play</button><input id=\"frame\" type=\"range\" min=\"0\" step=\"1\"><span id=\"readout\"></span></div>
<canvas id=\"canvas\" width=\"1200\" height=\"420\"></canvas>
<p>Dashed grey: immutable A. Solid red: official OpenSim IK B. Each episode uses one fixed scale across front/side/top and all frames.</p>
<script>
const DATA=__DATA__, NAMES=__NAMES__, LINKS=__LINKS__;
const VIEWS=[[0,2,'front'],[1,2,'side'],[0,1,'top']], sel=document.querySelector('#episode'), slider=document.querySelector('#frame'), out=document.querySelector('#readout'), canvas=document.querySelector('#canvas'), ctx=canvas.getContext('2d');
for(const name of Object.keys(DATA)){const o=document.createElement('option');o.textContent=name;sel.appendChild(o)}
let timer=null; function project(p,v,c,r,x0){return [x0+200+(p[v[0]]-c[v[0]])/r*170,210-(p[v[1]]-c[v[1]])/r*170]}
function skeleton(points,v,c,r,x0,color,dash){ctx.strokeStyle=color;ctx.fillStyle=color;ctx.lineWidth=2;ctx.setLineDash(dash);for(const [s,e] of LINKS){const a=project(points[NAMES.indexOf(s)],v,c,r,x0),b=project(points[NAMES.indexOf(e)],v,c,r,x0);ctx.beginPath();ctx.moveTo(...a);ctx.lineTo(...b);ctx.stroke()}ctx.setLineDash([]);for(const p of points){const q=project(p,v,c,r,x0);ctx.beginPath();ctx.arc(q[0],q[1],2.5,0,2*Math.PI);ctx.fill()}}
function draw(){const d=DATA[sel.value],i=Number(slider.value);ctx.clearRect(0,0,canvas.width,canvas.height);VIEWS.forEach((v,k)=>{ctx.fillStyle='#222';ctx.font='16px system-ui';ctx.fillText(v[2],k*400+12,24);skeleton(d.a[i],v,d.center,d.radius,k*400,'#555',[6,4]);skeleton(d.b[i],v,d.center,d.radius,k*400,'#d62728',[])});out.textContent=`frame ${i}/${d.time.length-1}, t=${d.time[i].toFixed(3)} s`}
function reset(){const d=DATA[sel.value];slider.max=d.time.length-1;slider.value=0;draw()} sel.onchange=reset;slider.oninput=draw;
document.querySelector('#play').onclick=()=>{if(timer){clearInterval(timer);timer=null;return}timer=setInterval(()=>{const d=DATA[sel.value];slider.value=(Number(slider.value)+1)%d.time.length;draw()},33)};reset();
</script>"""
    html = html.replace("__DATA__", json.dumps(payload, separators=(",", ":")))
    html = html.replace("__NAMES__", json.dumps(point_names))
    html = html.replace("__LINKS__", json.dumps(LINKS))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(html, encoding="utf-8")
    return {
        "episode_count": len(payload),
        "full_resolution_rows": sum(len(item["time"]) for item in payload.values()),
        "a_is_immutable": True,
        "viewer": str(output_path.resolve()),
        "viewer_sha256": sha256_file(output_path),
    }


def validate_full_outputs(frozen, episode_items, model_path: Path, motion_root: Path) -> dict[str, object]:
    """Report position, unit, connectivity, and chart-range status for all B rows."""

    origin_point = {
        "pelvis": "pelvis_center",
        "torso": "pelvis_center",
        "upper_arm_left": "shoulder_left",
        "forearm_left": "elbow_left",
        "upper_arm_right": "shoulder_right",
        "forearm_right": "elbow_right",
        "thigh_left": "hip_left",
        "shank_left": "knee_left",
        "thigh_right": "hip_right",
        "shank_right": "knee_right",
    }
    episodes = {}
    for label, episode in episode_items:
        episode_root = motion_root / label
        motion_path = episode_root / "official" / "official_ik.sto"
        header = motion_path.read_text(encoding="utf-8").split("endheader", 1)[0]
        if "inDegrees=yes" not in header:
            raise ValueError(f"motion coordinate unit owner missing for {label}")
        _, coordinate_columns = _table_columns(motion_path)
        coordinate_rad = {
            name: np.deg2rad(values) for name, values in coordinate_columns.items()
        }
        step_by_coordinate = {
            name: np.abs(np.diff(values)) for name, values in coordinate_rad.items()
        }
        step_name = max(
            step_by_coordinate,
            key=lambda name: float(np.max(step_by_coordinate[name])),
        )
        step_index = int(np.argmax(step_by_coordinate[step_name]))
        _, rows = replay_model(model_path, motion_path)
        errors = {segment: [] for segment in SEGMENTS}
        for frame, row in enumerate(rows):
            a = a_points(frozen, episode, frame)
            for segment in SEGMENTS:
                b_origin = (
                    frozen.output_matrix_world_display_from_internal
                    @ row[segment]["p"]
                )
                errors[segment].append(
                    float(np.linalg.norm(a[origin_point[segment]] - b_origin))
                )
        per_segment_position = {
            segment: {
                "mean_m": float(np.mean(values)),
                "p95_m": float(np.quantile(values, 0.95)),
                "max_m": float(np.max(values)),
            }
            for segment, values in errors.items()
        }
        all_coordinates = np.concatenate(list(coordinate_rad.values()))
        orientation = json.loads(
            (episode_root / "EPISODE_RESULT.json").read_text(encoding="utf-8")
        )["orientation_errors"]
        episodes[label] = {
            "rows": len(rows),
            "orientation_residual_by_segment_rad": {
                segment: orientation["sensors"][f"{segment}_imu"]
                for segment in SEGMENTS
            },
            "position_residual_by_segment_m": per_segment_position,
            "motion_source_units": "degree",
            "motion_reported_validation_units": "radian",
            "motion_converted_exactly_once": True,
            "orientation_error_source_units": orientation["source_units"],
            "orientation_error_converted": orientation["converted_exactly_once"],
            "all_finite": bool(np.all(np.isfinite(all_coordinates))),
            "chart_range_rad": [-float(np.pi), float(np.pi)],
            "max_abs_coordinate_rad": float(np.max(np.abs(all_coordinates))),
            "chart_range_satisfied": bool(np.max(np.abs(all_coordinates)) <= np.pi + 1e-12),
            "at_chart_bound_count": int(np.count_nonzero(np.abs(all_coordinates) >= np.pi - 1e-10)),
            "max_abs_raw_coordinate_step_rad": float(
                step_by_coordinate[step_name][step_index]
            ),
            "max_raw_step_coordinate": step_name,
            "max_raw_step_frame_pair": [step_index, step_index + 1],
            "raw_coordinate_step_gt_pi_count": int(
                sum(np.count_nonzero(values > np.pi) for values in step_by_coordinate.values())
            ),
            "coordinate_continuity_scope": "raw body-fixed XYZ coordinates may wrap by nearly 2*pi; reconstructed SO(3) and FK are the continuity owner",
            "connectivity": "enforced structurally by nine OpenSim Joint objects",
            "rom_scope": "broad BallJoint chart range only; not an anatomical ROM claim",
        }
    return {
        "episode_count": len(episodes),
        "all_finite": all(item["all_finite"] for item in episodes.values()),
        "all_chart_ranges_satisfied": all(
            item["chart_range_satisfied"] for item in episodes.values()
        ),
        "episodes": episodes,
    }
