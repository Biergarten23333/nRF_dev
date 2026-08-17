from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping
import numpy as np

from . import so3
from .mapping import FrozenOperatorMapping


@dataclass(frozen=True)
class SegmentCalibration:
    node_id: str
    segment: str
    q_I_S: np.ndarray
    covariance_rad2: np.ndarray
    twist_status: str
    provenance: tuple[str, ...]
    layout_class: str

    def apply(self, q_WI: np.ndarray) -> np.ndarray:
        # R_WS = R_WI R_IS
        return so3.normalize(so3.mul(q_WI, self.q_I_S))


@dataclass(frozen=True)
class CalibrationBundle:
    by_node: Mapping[str, SegmentCalibration]

    @classmethod
    def from_neutral(cls, mapping: FrozenOperatorMapping, neutral_q_WI: Mapping[str, np.ndarray],
                     neutral_covariance: Mapping[str, np.ndarray] | None = None,
                     *, t_pose_used: bool = False) -> "CalibrationBundle":
        if set(neutral_q_WI) != set(mapping.node_to_segment):
            raise ValueError("neutral calibration must cover all ten nodes")
        rows = {}
        for node, segment in mapping.node_to_segment.items():
            # Desired neutral segment frames are the L0 identity realization.
            q_I_S = so3.inv(neutral_q_WI[node])
            cov = np.eye(3)*np.deg2rad(6.0)**2 if neutral_covariance is None else np.asarray(neutral_covariance[node], float)
            if cov.shape != (3, 3) or np.linalg.eigvalsh(cov)[0] < -1e-12:
                raise ValueError("invalid calibration covariance")
            rows[node] = SegmentCalibration(
                node, segment, q_I_S, cov,
                "T_POSE_CONDITIONED" if t_pose_used else "NEUTRAL_TILT_IDENTIFIED_TWIST_CONDITIONAL",
                ("00_initial_still",)+(('02_t_pose',) if t_pose_used else ()),
                "C2CC_DISTINCT" if node == "BSFC2CC" else "H9",
            )
        return cls(MappingProxyType(rows))

    def apply(self, node_id: str, q_WI: np.ndarray) -> np.ndarray:
        return self.by_node[node_id].apply(q_WI)

    def assert_non_identity_exercised(self) -> bool:
        return any(np.linalg.norm(so3.log(x.q_I_S)) > 1e-3 for x in self.by_node.values())
