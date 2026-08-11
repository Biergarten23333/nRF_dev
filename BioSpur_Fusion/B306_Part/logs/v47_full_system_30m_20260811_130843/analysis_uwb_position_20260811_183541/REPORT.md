# v47 pure-UWB position replay

Verdict: `UWB_POSITION_REPLAY_PASS` using canonical `UWB_TAG_T4`.

The replay consumed all 149,999 formal UWB sweeps from all ten Fusion PCBs.
Every input produced a position; accounting is closed per node. IMU, ZUPT,
Fusion, table-position constraints, and Tag-driven geometry refitting were not
used. UWB_TAG_U5 ran as a comparison on the identical input and matched T4 in
all 149,999 positions because the optional U5 RF/sigma inputs are absent.

## T0+1–484 s static platform

Coordinates are millimetres in the deployment's relative V4-io frame. Scatter
is internal repeatability, not absolute accuracy.

| Node | median XYZ (mm) | RMS scatter | P95 scatter | solution rate |
|---|---:|---:|---:|---:|
| BSF3C79 | 2825.4, 1356.6, 1109.1 | 71.3 | 123.7 | 100% |
| BSFC2CC | 3311.8, 992.2, 1223.8 | 56.5 | 98.5 | 100% |
| BSF44AD | 2875.1, 1109.4, 1166.6 | 69.1 | 125.2 | 100% |
| BSF6C53 | 925.9, 134.9, 685.6 | 169.2 | 302.9 | 100% |
| BSF8BC4 | 2944.0, 762.9, 940.0 | 47.7 | 81.8 | 100% |
| BSF1120 | 3426.8, 293.8, 805.0 | 52.7 | 86.5 | 100% |
| BSF31CC | 3229.7, 1178.4, 1043.2 | 60.3 | 102.8 | 100% |
| BSFAA61 | 3375.8, 503.8, 1314.2 | 162.4 | 313.9 | 100% |
| BSFB165 | 3052.9, 1489.6, 1084.5 | 63.1 | 104.1 | 100% |
| BSFEC35 | 2966.9, 399.0, 948.6 | 49.2 | 81.7 | 100% |

BSF6C53 is retained. Its larger scatter is consistent with its exceptional
antenna-to-metal geometry but is not excluded or corrected.

## Reposition platforms

BSFC2CC changed by `(301.6, -686.7, -377.6)` mm, norm 839.7 mm, from the
pre-move platform to T0+506–535 s. BSFAA61 changed by `(-6.0, -265.9,
-502.7)` mm, norm 568.8 mm. Without external ground truth these are platform
changes in the relative frame, not absolute displacement accuracy.

All 38 table-common-mode vibration events were retained. Across the resulting
380 node/event rows, the median of per-event median UWB position response was
73.6 mm; the 95th percentile was 210.8 mm. See the CSV for every event and
competing RF effects.

The frozen solver API does not define condition, GDOP, VDOP, or covariance, so
those per-sweep columns are intentionally empty rather than fabricated.

