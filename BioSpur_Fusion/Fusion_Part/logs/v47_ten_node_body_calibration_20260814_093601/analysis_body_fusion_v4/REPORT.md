# Ten-node body Fusion V4 measurement-conditioned centerline

Top-level verdict: `BLOCKED_ANTHROPOMETRY_INPUT_INCOMPLETE`.

`FULL_SEGMENT_POSE_CALIBRATION` remains `FAIL_AXIAL_TWIST_GAUGES_UNRESOLVED`. `STICK_FIGURE_CENTERLINE_CALIBRATION` is `NOT_RUN_ANTHROPOMETRY_INCOMPLETE`.

V4 uses an explicit quotient state: eight limb axial-twist coordinates are absent from the centerline optimizer. Physical invariance gates were frozen at 1e-4 rad and 0.1 mm before this run. The repository contains no measured subject anthropometry, shoe condition, or sensor-to-landmark offsets, so calibration stopped before opening calibration payloads. No dimensions were imported from V3 or fitted from this capture. V3 hashes all verify and V3 was not modified. The held-out ledger was not opened and no GIF was generated.
