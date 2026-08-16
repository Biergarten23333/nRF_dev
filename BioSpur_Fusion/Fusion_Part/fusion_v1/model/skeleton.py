"""Minimal ten-segment tree; positions exist only through FK."""
from __future__ import annotations
from dataclasses import dataclass
import numpy as np
from .geometry import compose, transform

@dataclass(frozen=True)
class Edge:
    child: str
    parent: str
    offset_m: tuple[float,float,float]

class Skeleton:
    def __init__(self, edges: list[Edge]):
        self.edges=tuple(edges)
        names={e.child for e in edges}; parents={e.parent for e in edges}
        roots=parents-names
        if len(roots)!=1: raise ValueError("skeleton must have one root")
        self.root=next(iter(roots))

    def forward(self, root_T, relative_rotvecs):
        poses={self.root:np.asarray(root_T,float)}
        pending=list(self.edges)
        while pending:
            progressed=False
            for edge in pending[:]:
                if edge.parent not in poses: continue
                local=transform(relative_rotvecs.get(edge.child,(0,0,0)),edge.offset_m)
                poses[edge.child]=compose(poses[edge.parent],local)
                pending.remove(edge); progressed=True
            if not progressed: raise ValueError("cycle or disconnected skeleton")
        return poses

def ten_segment_topology(lengths):
    L=lengths
    return Skeleton([
      Edge("Torso","Pelvis",(0,0,L["trunk"])),
      Edge("UpperArm_L","Torso",(0,L["shoulder_half_width"],0)),
      Edge("UpperArm_R","Torso",(0,-L["shoulder_half_width"],0)),
      Edge("Forearm_L","UpperArm_L",(0,L["upper_arm_L"],0)),
      Edge("Forearm_R","UpperArm_R",(0,-L["upper_arm_R"],0)),
      Edge("Thigh_L","Pelvis",(0,L["hip_half_width"],0)),
      Edge("Thigh_R","Pelvis",(0,-L["hip_half_width"],0)),
      Edge("Shank_L","Thigh_L",(0,0,-L["thigh_L"])),
      Edge("Shank_R","Thigh_R",(0,0,-L["thigh_R"])),
    ])

