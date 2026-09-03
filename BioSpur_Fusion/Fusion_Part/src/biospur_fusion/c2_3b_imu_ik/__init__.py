"""Evidence-gated IMU-only constrained-kinematics diagnostics for C2 3B."""

from .contracts import CENTRAL_PROFILE, PROFILES, SEGMENTS
from .solver import solve_frame

__all__ = ["CENTRAL_PROFILE", "PROFILES", "SEGMENTS", "solve_frame"]
