"""Predeclared Root-R5A numerical contracts, frozen before real comparison."""
from __future__ import annotations

import numpy as np


SCHEMA_VERSION = "biospur.root_r5a.v1"
ROOT_R4_MANIFEST_PAYLOAD_SHA256 = "fb5000d759e35abcc7d568711d4b5e2ef2d3cca6862c76f7b1cb75c3ec35e48e"
ROOT_R4_EXPECTED = {
    "historical_verdict": "BLOCKED_CAPTURE_BOUND_FRAME_NOT_OBSERVABLE",
    "five_block_yaw_range_deg": 57.201,
    "maximum_tag_loo_yaw_change_deg": 28.943,
    "t4_yaw_deg": -111.389,
    "raw_yaw_deg": -130.469,
    "hybrid_yaw_deg": -121.092,
    "maximum_layer_disagreement_deg": 19.080,
    "real_c1_fused_root_executed": False,
    "frame_authorized": False,
}

# Bound declared from the same-node B306 clock, <0.1 ms strobe capture,
# <=0.910 ms common-clock mapping residual, 5 ms IMU cadence, and the 16.667 ms
# frozen-M1 export cadence. Twenty milliseconds rounds outward; it is not tuned
# to a real-C1 alignment optimum.
TIME_OFFSET_BOUND_S = 0.020
TIME_OFFSET_GRID_S = np.asarray([-0.020, -0.010, 0.0, 0.010, 0.020], float)

PROFILE_GRID_STEP_DEG = 1.0
PROFILE_FINE_STEP_DEG = 0.25
RAW_MATCHED_EPOCH_STRIDE = 40
HUBER_DELTA_M = 0.25

# M1 metadata reports initial gyro-bias components up to about 1.10 deg/s.
# The outward diagnostic bound is fixed at 1.25 deg/s. No capture-qualified
# bias random-walk model exists, so three predeclared prior strengths are
# carried as sensitivity cases rather than tuned on real residuals.
DYNAMIC_BIAS_ABS_MAX_DPS = 1.25
DYNAMIC_PRIORS = (
    {"name": "TIGHT", "yaw_process_sigma_deg": 1.0, "bias_rw_sigma_dps": 0.02},
    {"name": "REFERENCE", "yaw_process_sigma_deg": 3.0, "bias_rw_sigma_dps": 0.05},
    {"name": "LOOSE", "yaw_process_sigma_deg": 10.0, "bias_rw_sigma_dps": 0.15},
)

# These are anti-overfit evidence gates, not product tolerances. Practical yaw
# sharpness has no pre-existing frozen product gate and is therefore tri-state.
DYNAMIC_HELD_BLOCK_MEAN_RATIO_MAX = 0.95
DYNAMIC_HELD_BLOCK_MIN_WINS = 4
TIMING_DOMINANCE_MODE_RANGE_REDUCTION_MIN = 0.50
SYNTHETIC_NUMERICAL_CONCENTRATION_WIDTH_MAX_DEG = 20.0

PRIMARY_VERDICTS = (
    "ROOT_R5A_WEAK_UWB_DIRECTIONAL_INFORMATION_DOMINATES",
    "ROOT_R5A_STATIC_FRAME_MODEL_MISMATCH_DYNAMIC_ROOT_YAW_DRIFT_SUPPORTED",
    "ROOT_R5A_PHYSICALLY_BOUNDED_TIMING_ERROR_DOMINATES",
    "ROOT_R5A_SHARP_BUT_INCONSISTENT_MODEL_OR_NUISANCE_MISMATCH",
    "ROOT_R5A_MIXED_OR_INCONCLUSIVE_EXISTING_EVIDENCE",
)

SEALED_DATA_NOT_OPENED = (
    "golf datasets",
    "boxing datasets",
    "external-truth datasets",
    "held-out captures other than already-authorized C1",
    "Vicon and other motion-capture truth",
)

FROZEN_INVARIANTS = (
    "IMU and UWB perform bidirectional correction; neither is unconditional truth.",
    "A fixed initial frame/gauge and time-varying IMU drift are distinct states.",
    "UWB authority is conditioned jointly on motion, UWB quality, geometry, timing, innovation consistency, and observability.",
    "Raw ranges and T4 are dependent representations of shared evidence and are never double-counted.",
)
