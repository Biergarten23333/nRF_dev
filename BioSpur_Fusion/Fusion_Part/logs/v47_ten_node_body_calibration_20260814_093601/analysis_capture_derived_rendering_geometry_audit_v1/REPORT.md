# Capture-derived rendering geometry audit

**CAPTURE_DERIVED_GEOMETRY_UNOBSERVABLE**

Operator measurements: **SEALED / NOT READ**. Walk and final-still: **SEALED / NOT OPENED**.

This is a non-clinical visualization feasibility result; passing values are immutable graphical rendering lengths, not measured bone lengths or anatomical joint-centre distances.

| Dimension | Tier | Verdict | Value (mm) |
|---|---:|---|---:|
| `rendering_forearm_length_L` | 1 | `FAIL_UNOBSERVABLE` | 357.156 |
| `rendering_forearm_length_R` | 1 | `FAIL_UNOBSERVABLE` | 459.994 |
| `rendering_shank_length_L` | 1 | `FAIL_UNOBSERVABLE` | 510.894 |
| `rendering_shank_length_R` | 1 | `FAIL_UNOBSERVABLE` | 690.581 |
| `C7Proxy_to_PelvisProxy_separation` | 1 | `FAIL_UNOBSERVABLE` | 531.152 |
| `rendering_upper_arm_length_L` | 2 | `FAIL_UNOBSERVABLE` | 700.000 |
| `rendering_upper_arm_length_R` | 2 | `FAIL_UNOBSERVABLE` | 700.000 |
| `graphical_shoulder_width` | 2 | `FAIL_UNOBSERVABLE` | 800.000 |
| `rendering_thigh_length_L` | 2 | `NOT_DIRECTLY_SUPPORTED_BY_NODE_LAYOUT` | — |
| `rendering_thigh_length_R` | 2 | `NOT_DIRECTLY_SUPPORTED_BY_NODE_LAYOUT` | — |
| `graphical_hip_width` | 2 | `NOT_DIRECTLY_SUPPORTED_BY_NODE_LAYOUT` | — |
| `graphical_hip_depth` | 2 | `NOT_DIRECTLY_SUPPORTED_BY_NODE_LAYOUT` | — |

See `AUDIT_RESULT.json` for raw-pair distributions, T4/Q1 accounting, actual Jacobians, profile intervals, all placement correlations, multistart/interleaved/action-removal results, bound hits and model-mismatch residuals.
