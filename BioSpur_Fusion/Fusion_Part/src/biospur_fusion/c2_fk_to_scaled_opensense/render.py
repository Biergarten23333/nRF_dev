"""Fixed-view A/B pixels for the generic/scaled official OpenSense pilot."""

from __future__ import annotations

from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

LINKS = (("pelvis_center","shoulder_mid"),("shoulder_mid","shoulder_left"),("shoulder_mid","shoulder_right"),("pelvis_center","hip_left"),("pelvis_center","hip_right"),("shoulder_left","elbow_left"),("elbow_left","wrist_left"),("shoulder_right","elbow_right"),("elbow_right","wrist_right"),("hip_left","knee_left"),("knee_left","ankle_left"),("hip_right","knee_right"),("knee_right","ankle_right"))
VIEWS = ((0,2,"front"),(1,2,"side"),(0,1,"top"))

def a_points(episode, geometry, frame):
    from biospur_fusion.c2_fk_to_opensim_ik.adapter import _quat_wxyz_matrix
    r={name:_quat_wxyz_matrix(episode.segments[name].quat_world_segment_wxyz[frame]) for name in episode.segments}
    root=np.zeros(3); shoulder_mid=r["torso"]@np.array([0.,0.,geometry.torso_height_m])
    p={"pelvis_center":root,"shoulder_mid":shoulder_mid}
    p["shoulder_left"]=shoulder_mid+r["torso"]@np.array([-.5*geometry.shoulder_span_m,0.,0.]); p["shoulder_right"]=shoulder_mid+r["torso"]@np.array([.5*geometry.shoulder_span_m,0.,0.])
    p["hip_left"]=r["pelvis"]@np.array([-.5*geometry.hip_span_m,0.,0.]); p["hip_right"]=r["pelvis"]@np.array([.5*geometry.hip_span_m,0.,0.])
    for side in ("left","right"):
        p[f"elbow_{side}"]=p[f"shoulder_{side}"]+r[f"upper_arm_{side}"]@np.array([0.,0.,-geometry.segment_length_m[f"upper_arm_{side}"]])
        p[f"wrist_{side}"]=p[f"elbow_{side}"]+r[f"forearm_{side}"]@np.array([0.,0.,-geometry.segment_length_m[f"forearm_{side}"]])
        p[f"knee_{side}"]=p[f"hip_{side}"]+r[f"thigh_{side}"]@np.array([0.,0.,-geometry.segment_length_m[f"thigh_{side}"]])
        p[f"ankle_{side}"]=p[f"knee_{side}"]+r[f"shank_{side}"]@np.array([0.,0.,-geometry.segment_length_m[f"shank_{side}"]])
    return p

def _pose(ax, points, x, y, color, style, label):
    first=True
    for a,b in LINKS:
        p=np.stack([points[a],points[b]])
        ax.plot(p[:,x],p[:,y],color=color,linestyle=style,linewidth=1.6,label=label if first else None)
        first=False
    p=np.stack(list(points.values())); ax.scatter(p[:,x],p[:,y],color=color,s=7)

def render_episode(frozen, episode, episode_key: str, label: str, b_rows, output: Path) -> dict[str, object]:
    n=episode.frame_count
    frames=[0,n//4,n//2,3*n//4,n-1]
    fig,axes=plt.subplots(5,3,figsize=(11.5,15.5),constrained_layout=True)
    max_change=0.0
    for r,frame in enumerate(frames):
        a=a_points(episode,frozen.geometry,frame)
        b=b_rows[frame]
        max_change=max(max_change,max(float(np.linalg.norm(a[name]-b[name])) for name in a))
        allp=np.concatenate([np.stack(list(a.values())),np.stack(list(b.values()))])
        for c,(x,y,name) in enumerate(VIEWS):
            ax=axes[r,c]; _pose(ax,a,x,y,"#444444","--","A frozen FK"); _pose(ax,b,x,y,"#d62728","-","B official constrained IK")
            v=allp[:,[x,y]]; lo=v.min(0); hi=v.max(0); center=(lo+hi)/2; radius=max(float((hi-lo).max())*.58,.25)
            ax.set_xlim(center[0]-radius,center[0]+radius); ax.set_ylim(center[1]-radius,center[1]+radius)
            ax.set_aspect("equal"); ax.set_axis_off(); ax.set_title(f"frame {frame} | {name}")
            if r==0 and c==0: ax.legend(fontsize=7)
    fig.suptitle(f"{label}: A frozen display FK vs B generic/scaled official OpenSense\nB joint centres are model-derived, not subject anatomical truth")
    output.parent.mkdir(parents=True,exist_ok=True); fig.savefig(output,dpi=170); plt.close(fig)
    return {"frames":frames,"png":str(output.resolve()),"sampled_max_point_change_m":max_change}
