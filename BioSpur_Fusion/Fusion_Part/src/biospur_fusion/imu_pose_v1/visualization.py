from __future__ import annotations

from pathlib import Path
from typing import Mapping
import numpy as np


EDGES = (("pelvis","chest"),("chest","neck"),("chest","shoulder_left"),("shoulder_left","elbow_left"),
         ("elbow_left","wrist_left"),("chest","shoulder_right"),("shoulder_right","elbow_right"),
         ("elbow_right","wrist_right"),("pelvis","hip_left"),("hip_left","knee_left"),
         ("knee_left","ankle_left"),("pelvis","hip_right"),("hip_right","knee_right"),
         ("knee_right","ankle_right"))


def render_triptych(path: Path, time_s: np.ndarray, trajectories: Mapping[str, tuple[Mapping[str,np.ndarray], ...]],
                     status: Mapping[str, tuple[str, ...]], fps: int = 20, max_frames: int = 400) -> dict:
    import imageio.v2 as imageio
    import matplotlib.pyplot as plt
    names = tuple(trajectories)
    if len(names) != 3:
        raise ValueError("B0/B1/P triptych required")
    indices = np.linspace(0, len(time_s)-1, min(max_frames, len(time_s)), dtype=int)
    frames=[]
    for idx in indices:
        fig = plt.figure(figsize=(12,4), dpi=90)
        for col,name in enumerate(names,1):
            ax=fig.add_subplot(1,3,col,projection="3d"); pts=trajectories[name][idx]
            for a,b in EDGES:
                xyz=np.vstack((pts[a],pts[b])); ax.plot(xyz[:,0],xyz[:,1],xyz[:,2],"o-",lw=2)
            ax.set(xlim=(-.8,.8),ylim=(-.8,.8),zlim=(-.9,.9),xlabel="L0 X",ylabel="L0 Y",zlabel="L0 Z")
            ax.set_title(f"{name}  t={time_s[idx]-time_s[0]:.2f}s\n{status[name][idx]}")
            ax.view_init(15,-70)
        fig.tight_layout(); fig.canvas.draw()
        rgba=np.asarray(fig.canvas.buffer_rgba()); frames.append(rgba[...,:3].copy()); plt.close(fig)
    Path(path).parent.mkdir(parents=True,exist_ok=True)
    imageio.mimsave(path,frames,duration=int(round(1000/fps)),loop=0)
    return {"path":str(Path(path).resolve()),"frames":len(frames),"fps":fps,"duration_s":len(frames)/fps,
            "methods":list(names),"fixed_normalized_geometry":True}
