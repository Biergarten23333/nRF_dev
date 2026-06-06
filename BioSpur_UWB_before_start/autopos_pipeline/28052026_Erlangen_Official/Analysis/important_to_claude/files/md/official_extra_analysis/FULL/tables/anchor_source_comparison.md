# Anchor Source Comparison

Surveyed-anchor baseline is solved entirely in the OptiTrack frame: OptiTrack anchor coordinates in, corrected OptiTrack `Iantenna` truth out. Alignment DOF = 0; no Kabsch, no reflection, no scale.

Delay modes:

- `raw_zero_delay`: raw tag-to-anchor ranges, anchor delays = 0, tag delay = 0.
- `autopos_v4io_delay_vector`: the V4-io AutoPos per-anchor delay vector is applied to OptiTrack-truth anchors. This is non-circular with respect to OptiTrack delay, but the delay vector is jointly estimated with the AutoPos layout and is gauge/scale-coupled.
- `inter_anchor_delaycal`: per-anchor endpoint delays fit from raw inter-anchor medians against OptiTrack true anchor distances; tag delay is the median endpoint delay. This uses OptiTrack twice and is a partly circular lower bound.
- `autopos_estimated_delays`: the production AutoPos v4-io line from `tag_accuracy_summary.csv`.

Inter-anchor delaycal diagnostic: median common endpoint bias 93.6 mm; per-anchor LS residual RMS 43.8 mm.

## Headline Comparison

| eval set | anchor source | delay | tag method | median 3D mm | p95 mm | RMS mm | horiz med mm | vert med mm |
| --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: |
| all8 | OptiTrack truth | raw_zero_delay | T4 | 307.3 | 453.4 | 311.3 | 76.7 | 301.6 |
| all8 | OptiTrack truth | autopos_v4io_delay_vector | T4 | 254.9 | 394.6 | 252.2 | 72.2 | 240.0 |
| all8 | OptiTrack truth | inter_anchor_delaycal | T4 | 64.1 | 128.4 | 77.7 | 40.3 | 44.5 |
| all8 | AutoPos v4-io | autopos_estimated_delays | production-output | 74.0 | 282.1 | 139.6 | 42.3 | 65.3 |

## AutoPos Minus Baseline

| eval set | baseline delay | delta median 3D mm | delta p95 mm | delta RMS mm | delta horiz med mm | delta vert med mm |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| all8 | raw_zero_delay | -233.3 | -171.3 | -171.8 | -34.4 | -236.3 |
| all8 | autopos_v4io_delay_vector | -180.9 | -112.4 | -112.6 | -29.9 | -174.6 |
| all8 | inter_anchor_delaycal | 9.9 | 153.7 | 61.9 | 1.9 | 20.9 |

## Worst-Point Resolution

| ID | eval set | surveyed raw 3D mm | surveyed AutoPos-delay 3D mm | surveyed delaycal 3D mm | location | height | facing | tag truth corrected |
| --- | --- | ---: | ---: | ---: | --- | --- | --- | --- |
| ID03 | all8 | 504.5 | 431.9 | 66.1 | edge | high | ABEF | False |
| ID04 | all8 | 460.9 | 408.9 | 111.2 | edge | low | BCGF | False |
| ID05 | all8 | 265.6 | 156.0 | 20.9 | edge | mid | BCGF | True |
| ID06 | all8 | 352.0 | 265.3 | 9.3 | edge | high | BCGF | False |

Interpretation rule: if ID03/ID04/ID06 stay large with OptiTrack-truth anchors, the tail is intrinsic UWB/NLOS/multipath/geometry rather than AutoPos layout error; if they collapse, it was dominated by self-calibration.

Actual result: the production tail points collapse under surveyed anchors plus delaycal (ID03=66.1 mm, ID04=111.2 mm, ID06=9.3 mm). The 270 mm-class AutoPos production tail is therefore mainly layout/self-calibration/frame-lock cost, not an irreducible UWB floor at those positions.
