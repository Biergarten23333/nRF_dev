# Operator measurements minimum required

This request is generated only from dimensions that did not pass the sealed capture-derived audit.
It does not treat surface measurements as anatomical ground truth. The measurements were not opened or used by this audit.

| Failed visualization dimension | Audit verdict | Matching direct surface chord to collect |
|---|---|---|
| `rendering_forearm_length_L` | `FAIL_UNOBSERVABLE` | `lateral_epicondyle_to_wrist_styloid_midpoint_L` |
| `rendering_forearm_length_R` | `FAIL_UNOBSERVABLE` | `lateral_epicondyle_to_wrist_styloid_midpoint_R` |
| `rendering_shank_length_L` | `FAIL_UNOBSERVABLE` | `lateral_knee_landmark_to_malleolar_midpoint_L` |
| `rendering_shank_length_R` | `FAIL_UNOBSERVABLE` | `lateral_knee_landmark_to_malleolar_midpoint_R` |
| `C7Proxy_to_PelvisProxy_separation` | `FAIL_UNOBSERVABLE` | `C7_to_mid_PSIS` |
| `rendering_upper_arm_length_L` | `FAIL_UNOBSERVABLE` | `acromion_to_lateral_epicondyle_L` |
| `rendering_upper_arm_length_R` | `FAIL_UNOBSERVABLE` | `acromion_to_lateral_epicondyle_R` |
| `graphical_shoulder_width` | `FAIL_UNOBSERVABLE` | `biacromial_breadth` |
| `rendering_thigh_length_L` | `NOT_DIRECTLY_SUPPORTED_BY_NODE_LAYOUT` | `greater_trochanter_to_lateral_knee_landmark_L` |
| `rendering_thigh_length_R` | `NOT_DIRECTLY_SUPPORTED_BY_NODE_LAYOUT` | `greater_trochanter_to_lateral_knee_landmark_R` |
| `graphical_hip_width` | `NOT_DIRECTLY_SUPPORTED_BY_NODE_LAYOUT` | `ASIS_breadth` |
| `graphical_hip_depth` | `NOT_DIRECTLY_SUPPORTED_BY_NODE_LAYOUT` | `pelvis_anterior_posterior_depth` |

Future comparison remains a separate, post-freeze stage. Its predeclared visualization agreement gate is `absolute difference <= max(20 mm, 2 × combined standard uncertainty)`. Estimation failure and external-reference disagreement are distinct outcomes.
