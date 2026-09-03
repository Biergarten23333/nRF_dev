"""Direct raw-path fixed-geometry FK viewer and independent audit frames."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np

from biospur_fusion.v0.math3d import rz
from biospur_fusion.v0.raw6_heading import EDGES, Raw6Episode, SEGMENTS

from .functional import FunctionalCandidate
from .geometry import BodyGeometry
from .model import CalibrationState
from .orientation_trajectories import corrected_episode_rotations


POINT_ORDER = (
    "pelvis", "chest", "shoulder_left", "elbow_left", "wrist_left",
    "shoulder_right", "elbow_right", "wrist_right",
    "hip_left", "knee_left", "ankle_left",
    "hip_right", "knee_right", "ankle_right",
)
LINES = (
    ("pelvis", "chest"),
    ("chest", "shoulder_left"), ("shoulder_left", "elbow_left"),
    ("elbow_left", "wrist_left"),
    ("chest", "shoulder_right"), ("shoulder_right", "elbow_right"),
    ("elbow_right", "wrist_right"),
    ("pelvis", "hip_left"), ("hip_left", "knee_left"),
    ("knee_left", "ankle_left"),
    ("pelvis", "hip_right"), ("hip_right", "knee_right"),
    ("knee_right", "ankle_right"),
    ("shoulder_left", "shoulder_right"), ("hip_left", "hip_right"),
)


def fixed_geometry_fk(
    episode: Raw6Episode,
    state_value: np.ndarray,
    functional: FunctionalCandidate,
    geometry: BodyGeometry,
) -> dict[str, Any]:
    """Evaluate FK directly from each episode's raw-derived 6D rotations."""

    state = CalibrationState.from_vector(state_value)
    n = len(episode.time_ns)
    frontend_rotation, trajectory_audit = corrected_episode_rotations(
        episode, functional.hinge_axes,
    )
    corrected_sensor = {
        segment: np.einsum(
            "ij,njk->nik", rz(state.heading(segment)),
            frontend_rotation[segment],
        )
        for segment in SEGMENTS
    }
    body_rotation = {
        segment: np.einsum(
            "nij,jk->nik", corrected_sensor[segment],
            functional.body_from_sensor_by_segment[segment].T,
        )
        for segment in SEGMENTS
    }
    body_points = geometry.edge_points_body(state.axial_offsets_m)
    lever_sensor = {
        edge: (
            functional.body_from_sensor_by_segment[parent].T @ parent_point,
            functional.body_from_sensor_by_segment[child].T @ child_point,
        )
        for (edge, parent, child, _), (parent_point, child_point)
        in zip(EDGES, (body_points[name] for name, _, _, _ in EDGES))
    }
    sensor_origin: dict[str, np.ndarray] = {
        "pelvis": np.zeros((n, 3), dtype=float),
    }
    joints: dict[str, np.ndarray] = {}
    for edge, parent, child, _ in EDGES:
        parent_lever, child_lever = lever_sensor[edge]
        joint = sensor_origin[parent] + np.einsum(
            "nij,j->ni", corrected_sensor[parent], parent_lever,
        )
        joints[edge] = joint
        sensor_origin[child] = joint - np.einsum(
            "nij,j->ni", corrected_sensor[child], child_lever,
        )
    connection = geometry.connection_points_body(state.axial_offsets_m)
    points = {
        "pelvis": sensor_origin["pelvis"],
        "chest": sensor_origin["torso"],
        "shoulder_left": joints["shoulder_left"],
        "elbow_left": joints["elbow_left"],
        "wrist_left": sensor_origin["forearm_left"] + np.einsum(
            "nij,j->ni", body_rotation["forearm_left"], connection["forearm_left"][1],
        ),
        "shoulder_right": joints["shoulder_right"],
        "elbow_right": joints["elbow_right"],
        "wrist_right": sensor_origin["forearm_right"] + np.einsum(
            "nij,j->ni", body_rotation["forearm_right"], connection["forearm_right"][1],
        ),
        "hip_left": joints["hip_left"],
        "knee_left": joints["knee_left"],
        "ankle_left": sensor_origin["shank_left"] + np.einsum(
            "nij,j->ni", body_rotation["shank_left"], connection["shank_left"][1],
        ),
        "hip_right": joints["hip_right"],
        "knee_right": joints["knee_right"],
        "ankle_right": sensor_origin["shank_right"] + np.einsum(
            "nij,j->ni", body_rotation["shank_right"], connection["shank_right"][1],
        ),
    }
    return {
        "time_ns": episode.time_ns,
        "phase": episode.phase,
        "points": points,
        "sensor_origin": sensor_origin,
        "body_rotation": body_rotation,
        "source_contract": {
            "input": "RAW_ACCELEROMETER_GYROSCOPE_VQF6D",
            "fixed_geometry": True,
            "ik_used": False,
            "animation_repair_used": False,
            "post_hoc_pose_rebase_used": False,
            "qmt_quat2corr_deltafilt_trajectory": trajectory_audit,
            "episode_scalar_qmt_heading_used_as_orientation_correction": False,
        },
    }


def _viewer_payload(
    episodes: Sequence[Raw6Episode],
    state: np.ndarray,
    functional: FunctionalCandidate,
    geometry: BodyGeometry,
    *,
    downsample: int = 5,
) -> dict[str, Any]:
    output = {}
    for episode in episodes:
        fk = fixed_geometry_fk(episode, state, functional, geometry)
        take = np.arange(0, len(episode.time_ns), max(1, int(downsample)))
        origin = int(episode.time_ns[0])
        output[episode.action] = {
            "time_s": ((episode.time_ns[take] - origin) * 1e-9).tolist(),
            "phase": episode.phase[take].tolist(),
            "points": {
                name: np.round(values[take], 6).tolist()
                for name, values in fk["points"].items()
            },
        }
    return output


def write_viewer(
    output: Path,
    episodes: Sequence[Raw6Episode],
    state: np.ndarray,
    functional: FunctionalCandidate,
    geometry: BodyGeometry,
    profile_summary: Mapping[str, Any],
) -> Path:
    output = Path(output)
    payload = _viewer_payload(episodes, state, functional, geometry)
    summary_json = json.dumps(profile_summary, sort_keys=True, allow_nan=False)
    data_json = json.dumps(payload, separators=(",", ":"), allow_nan=False)
    point_json = json.dumps(POINT_ORDER)
    line_json = json.dumps(LINES)
    document = f"""<!doctype html>
<html><head><meta charset="utf-8"><title>BioSpur C2 direct FK audit</title>
<style>
body{{font-family:system-ui,sans-serif;background:#10151d;color:#eef;margin:0;padding:16px}}
.controls{{display:flex;gap:14px;align-items:center;flex-wrap:wrap;margin-bottom:12px}}
.views{{display:grid;grid-template-columns:repeat(3,minmax(280px,1fr));gap:10px}}
svg{{background:#f7f9fc;border:1px solid #526071;width:100%;height:520px}}
.bone{{stroke:#16212e;stroke-width:4;stroke-linecap:round}} .joint{{fill:#dd4b39}}
.label{{font-size:10px;fill:#233}} pre{{white-space:pre-wrap;background:#17202b;padding:12px}}
.conflict{{color:#ffadad}} input[type=range]{{width:420px}}
</style></head><body>
<h2>Capture2 direct raw-path fixed-geometry FK</h2>
<div class="controls"><label>Episode <select id="episode"></select></label>
<label>Frame <input id="frame" type="range" min="0" value="0"></label>
<span id="clock"></span></div>
<div class="views"><div><h3>Front (left–up)</h3><svg id="front"></svg></div>
<div><h3>Side (forward–up)</h3><svg id="side"></svg></div>
<div><h3>Top (left–forward)</h3><svg id="top"></svg></div></div>
<h3>Profile / uncertainty / conflicts</h3><pre id="summary"></pre>
<script>
const DATA={data_json}; const SUMMARY={summary_json}; const POINTS={point_json}; const LINES={line_json};
const selector=document.getElementById('episode'), slider=document.getElementById('frame');
for(const name of Object.keys(DATA)){{const o=document.createElement('option');o.value=name;o.textContent=name;selector.appendChild(o)}}
document.getElementById('summary').textContent=JSON.stringify(SUMMARY,null,2);
function project(p,view){{if(view==='front')return[p[1],p[2]];if(view==='side')return[p[0],p[2]];return[p[1],p[0]]}}
function renderOne(id,view,record,index){{const svg=document.getElementById(id);svg.innerHTML='';const raw={{}};for(const n of POINTS)raw[n]=project(record.points[n][index],view);
let xs=Object.values(raw).map(p=>p[0]),ys=Object.values(raw).map(p=>p[1]);let xmin=Math.min(...xs),xmax=Math.max(...xs),ymin=Math.min(...ys),ymax=Math.max(...ys);let span=Math.max(xmax-xmin,ymax-ymin,.2)*1.18,cx=(xmin+xmax)/2,cy=(ymin+ymax)/2;
function s(p){{return[260+(p[0]-cx)*480/span,260-(p[1]-cy)*480/span]}}
for(const pair of LINES){{let a=s(raw[pair[0]]),b=s(raw[pair[1]]);let l=document.createElementNS('http://www.w3.org/2000/svg','line');l.setAttribute('x1',a[0]);l.setAttribute('y1',a[1]);l.setAttribute('x2',b[0]);l.setAttribute('y2',b[1]);l.setAttribute('class','bone');svg.appendChild(l)}}
for(const n of POINTS){{let p=s(raw[n]);let c=document.createElementNS('http://www.w3.org/2000/svg','circle');c.setAttribute('cx',p[0]);c.setAttribute('cy',p[1]);c.setAttribute('r',4);c.setAttribute('class','joint');svg.appendChild(c);let t=document.createElementNS('http://www.w3.org/2000/svg','text');t.setAttribute('x',p[0]+5);t.setAttribute('y',p[1]-5);t.setAttribute('class','label');t.textContent=n;svg.appendChild(t)}}}}
function render(){{const r=DATA[selector.value],i=Number(slider.value);slider.max=r.time_s.length-1;document.getElementById('clock').textContent=`t=${{r.time_s[i].toFixed(2)}} s | ${{r.phase[i]}}`;renderOne('front','front',r,i);renderOne('side','side',r,i);renderOne('top','top',r,i)}}
selector.onchange=()=>{{slider.value=0;render()}};slider.oninput=render;render();
</script></body></html>"""
    output.write_text(document, encoding="utf-8")
    return output


def write_audit_frames(
    output_dir: Path,
    episodes: Sequence[Raw6Episode],
    state: np.ndarray,
    functional: FunctionalCandidate,
    geometry: BodyGeometry,
) -> list[dict[str, Any]]:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=False)
    by_action = {episode.action: episode for episode in episodes}
    requests = (
        ("initial_standing", "00_initial_still", "VERIFIED_PRE_REST"),
        ("upper_body", "04_shoulder_left", "FORMAL_ACTION_OR_HOLD"),
        ("lower_body", "16_squat", "FORMAL_ACTION_OR_HOLD"),
    )
    reports = []
    for label, action, phase in requests:
        episode = by_action[action]
        indices = np.flatnonzero(episode.phase == phase)
        if not len(indices):
            raise RuntimeError(f"{action}: requested viewer phase is absent")
        index = int(indices[len(indices) // 2])
        fk = fixed_geometry_fk(episode, state, functional, geometry)
        points = {name: values[index] for name, values in fk["points"].items()}
        fig, axes = plt.subplots(1, 3, figsize=(15, 5), constrained_layout=True)
        for axis, (title, dims) in zip(axes, (
            ("Front", (1, 2)), ("Side", (0, 2)), ("Top", (1, 0)),
        )):
            for left, right in LINES:
                values = np.vstack((points[left], points[right]))
                axis.plot(values[:, dims[0]], values[:, dims[1]], "k-", lw=3)
            for name, value in points.items():
                axis.plot(value[dims[0]], value[dims[1]], "o", ms=4)
                axis.text(value[dims[0]], value[dims[1]], name, fontsize=6)
            axis.set_title(title); axis.set_aspect("equal", adjustable="datalim"); axis.grid(True, alpha=.25)
        time_s = float((episode.time_ns[index] - episode.time_ns[0]) * 1e-9)
        fig.suptitle(f"C2 direct FK | {action} | {phase} | t={time_s:.2f}s | no IK/rebase/repair")
        path = output_dir / f"{label}_{action}.png"
        fig.savefig(path, dpi=160)
        plt.close(fig)
        reports.append({
            "label": label, "action": action, "phase": phase,
            "frame_index": index, "time_s": time_s, "path": str(path),
            "source_contract": fk["source_contract"],
        })
    return reports
