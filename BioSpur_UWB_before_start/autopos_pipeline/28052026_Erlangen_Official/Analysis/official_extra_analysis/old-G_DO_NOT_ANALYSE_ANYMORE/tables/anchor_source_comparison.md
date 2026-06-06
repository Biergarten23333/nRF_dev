# Anchor Source Comparison

Surveyed-anchor baseline is solved entirely in the OptiTrack frame: OptiTrack anchor coordinates in, corrected OptiTrack `Iantenna` truth out. Alignment DOF = 0; no Kabsch, no reflection, no scale.

Delay modes:

- `raw_zero_delay`: raw tag-to-anchor ranges, anchor delays = 0, tag delay = 0.
- `autopos_v4io_delay_vector`: the V4-io AutoPos per-anchor delay vector is applied to OptiTrack-truth anchors. This is non-circular with respect to OptiTrack delay, but the delay vector is jointly estimated with the AutoPos layout and is gauge/scale-coupled.
- `inter_anchor_delaycal`: per-anchor endpoint delays fit from raw inter-anchor medians against OptiTrack true anchor distances; tag delay is the median endpoint delay. This uses OptiTrack twice and is a partly circular lower bound.
- `autopos_estimated_delays`: the production AutoPos v4-io line from `tag_accuracy_summary.csv`.

Inter-anchor delaycal diagnostic: median common endpoint bias 84.3 mm; per-anchor LS residual RMS 51.1 mm.

## Headline Comparison

| eval set | anchor source | delay | tag method | median 3D mm | p95 mm | RMS mm | horiz med mm | vert med mm |
| --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: |
| all8 | OptiTrack truth | raw_zero_delay | T4 | 296.0 | 443.1 | 297.8 | 95.5 | 279.9 |
| all8 | OptiTrack truth | autopos_v4io_delay_vector | T4 | 241.9 | 376.3 | 240.4 | 79.9 | 214.8 |
| all8 | OptiTrack truth | inter_anchor_delaycal | T4 | 58.4 | 134.8 | 74.9 | 37.6 | 39.5 |
| all8 | AutoPos v4-io | autopos_estimated_delays | production-output | 77.4 | 270.3 | 138.3 | 43.8 | 63.1 |
| noG | OptiTrack truth | raw_zero_delay | T4 | 322.1 | 534.1 | 349.7 | 126.2 | 286.2 |
| noG | OptiTrack truth | autopos_v4io_delay_vector | T4 | 255.1 | 471.0 | 287.7 | 114.5 | 206.0 |
| noG | OptiTrack truth | inter_anchor_delaycal | T4 | 78.1 | 198.2 | 118.4 | 50.1 | 64.1 |
| noG | AutoPos v4-io | autopos_estimated_delays | production-output | 81.3 | 278.6 | 141.1 | 46.9 | 63.5 |

## AutoPos Minus Baseline

| eval set | baseline delay | delta median 3D mm | delta p95 mm | delta RMS mm | delta horiz med mm | delta vert med mm |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| all8 | raw_zero_delay | -218.6 | -172.9 | -159.5 | -51.7 | -216.8 |
| all8 | autopos_v4io_delay_vector | -164.5 | -106.0 | -102.2 | -36.1 | -151.7 |
| all8 | inter_anchor_delaycal | 18.9 | 135.4 | 63.4 | 6.3 | 23.6 |
| noG | raw_zero_delay | -240.8 | -255.5 | -208.6 | -79.3 | -222.7 |
| noG | autopos_v4io_delay_vector | -173.8 | -192.4 | -146.6 | -67.6 | -142.5 |
| noG | inter_anchor_delaycal | 3.2 | 80.4 | 22.7 | -3.2 | -0.6 |

## Worst-Point Resolution

| ID | eval set | surveyed raw 3D mm | surveyed AutoPos-delay 3D mm | surveyed delaycal 3D mm | location | height | facing | tag truth corrected |
| --- | --- | ---: | ---: | ---: | --- | --- | --- | --- |
| ID03 | all8 | 499.7 | 432.5 | 78.2 | edge | high | ABEF | False |
| ID04 | all8 | 447.6 | 388.1 | 89.5 | edge | low | BCGF | False |
| ID05 | all8 | 261.1 | 145.2 | 32.6 | edge | mid | BCGF | True |
| ID06 | all8 | 329.0 | 244.5 | 34.6 | edge | high | BCGF | False |

Interpretation rule: if ID03/ID04/ID06 stay large with OptiTrack-truth anchors, the tail is intrinsic UWB/NLOS/multipath/geometry rather than AutoPos layout error; if they collapse, it was dominated by self-calibration.

Actual result: the production tail points collapse under surveyed anchors plus delaycal (ID03=78.2 mm, ID04=89.5 mm, ID06=34.6 mm). The 270 mm-class AutoPos production tail is therefore mainly layout/self-calibration/frame-lock cost, not an irreducible UWB floor at those positions.
