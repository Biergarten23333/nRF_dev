"""Fixed-camera renderer for calibration-only IMU preview clips."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np

from .core import EDGES,LANDMARK_INDEX
from .io import dump_json,sha256


def _frame_indices(rows:np.ndarray,source_rate:float,fps:int) -> np.ndarray:
    if not len(rows):return rows
    count=max(2,int(round((len(rows)-1)/source_rate*fps))+1);return rows[np.clip(np.round(np.linspace(0,len(rows)-1,count)).astype(int),0,len(rows)-1)]


def render_calibration(replay_dir:Path,calibration_analysis_dir:Path,gates_path:Path,output:Path) -> dict:
    import matplotlib;matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.animation import FFMpegWriter,PillowWriter
    gates=json.loads(Path(gates_path).read_text());internal=json.loads((Path(calibration_analysis_dir)/"RESULT.json").read_text())
    if not internal["calibration_internal_gates_pass"]:raise RuntimeError("calibration preview gates did not pass")
    with np.load(Path(replay_dir)/"CONTINUOUS_STATE_TIMELINE.npz",allow_pickle=False) as state:
        times=state["time_ns"].copy();skeleton=state["skeleton_m"].copy()
    labels=np.load(Path(replay_dir)/"POSTHOC_ACTION_LABELS.npy",allow_pickle=False);output=Path(output)
    if output.exists():raise ValueError("media output exists")
    output.mkdir(parents=True);r=gates["rendering"];fps=int(r["fps"]);source_rate=float(gates["common_time"]["rate_hz"]);limit=float(r["fixed_axis_limit_m"]);watermark=str(r["watermark"]);manifest=[]
    def render_one(name:str,indices:np.ndarray,make_gif:bool=False):
        fig=plt.figure(figsize=(r["width_px"]/100,r["height_px"]/100),dpi=100);ax=fig.add_subplot(111,projection="3d");ax.set_xlim(-limit,limit);ax.set_ylim(-limit,limit);ax.set_zlim(-1.05,1.35);ax.set_box_aspect((2*limit,2*limit,2.4));ax.view_init(elev=float(r["camera_elevation_deg"]),azim=float(r["camera_azimuth_deg"]));ax.set_xlabel("display X (arbitrary yaw)");ax.set_ylabel("display Y");ax.set_zlabel("gravity-relative Z");fig.text(.5,.015,watermark,ha="center",fontsize=8,color="crimson");artists=[]
        def draw(frame):
            for artist in artists:artist.remove()
            artists.clear();i=int(indices[frame]);sk=skeleton[i];action=str(labels[i]);fig.suptitle(f"{action}  global_time_ns={int(times[i])}  t={(int(times[i])-int(times[indices[0]]))/1e9:.2f}s")
            for a,b in EDGES:
                v=sk[[LANDMARK_INDEX[a],LANDMARK_INDEX[b]]];line,=ax.plot(v[:,0],v[:,1],v[:,2],color="navy",lw=3,ls="--" if a=="C7Proxy" and b.startswith("Shoulder") else "-");artists.append(line)
            artists.append(ax.scatter(sk[:,0],sk[:,1],sk[:,2],color="darkorange",s=22))
            artists.append(ax.text(*sk[LANDMARK_INDEX["HeadProxy"]],"HeadProxy",fontsize=7,color="purple"))
        mp4=output/f"{name}.mp4";writer=FFMpegWriter(fps=fps,codec=str(r["mp4_codec"]),extra_args=["-pix_fmt",str(r["pixel_format"]),"-metadata","creation_time=1970-01-01T00:00:00Z"])
        with writer.saving(fig,str(mp4),100):
            for frame in range(len(indices)):draw(frame);writer.grab_frame()
        gif_path=None
        if make_gif:
            gif_path=output/f"{name}.gif";gif_fps=int(r["gif_fps"]);stride=max(1,int(round(fps/gif_fps)));writer2=PillowWriter(fps=gif_fps)
            with writer2.saving(fig,str(gif_path),60):
                for frame in range(0,len(indices),stride):draw(frame);writer2.grab_frame()
        plt.close(fig);return {"name":name,"mp4":str(mp4.resolve()),"mp4_sha256":sha256(mp4),"gif":str(gif_path.resolve()) if gif_path else None,"gif_sha256":sha256(gif_path) if gif_path else None,"frames":int(len(indices)),"first_global_time_ns":int(times[indices[0]]),"last_global_time_ns":int(times[indices[-1]]),"fixed_camera":True,"fixed_scale":True,"uwb_displayed":False}
    combined=[]
    for action in gates["calibration_actions"]:
        rows=np.flatnonzero(labels==action);indices=_frame_indices(rows,source_rate,fps);manifest.append(render_one(action.upper()+"_IMU_PREVIEW_V0",indices,make_gif=False));combined.append(indices)
    combined_indices=np.concatenate(combined);manifest.append(render_one("CALIBRATION_ACTIONS_COMBINED_IMU_PREVIEW_V0",combined_indices,make_gif=True));complete=all(Path(row["mp4"]).is_file() for row in manifest) and Path(manifest[-1]["gif"]).is_file();verdict="PASS_IMU_RELATIVE_ORIENTATION_PREVIEW_V0" if complete else "FAIL_PREVIEW_CALIBRATION";result={"schema":"biospur-imu-preview-calibration-media-v0","verdict":verdict,"calibration_internal_gates_pass":True,"all_eleven_actions_rendered":len(manifest)==12,"media_complete":complete,"clips":manifest,"golf_swing":"SEALED_NOT_OPENED","boxing":"SEALED_NOT_OPENED","walk":"SEALED_NOT_OPENED","final_still":"SEALED_NOT_OPENED","watermark":watermark};dump_json(output/"CALIBRATION_MEDIA_RESULT.json",result);return result
