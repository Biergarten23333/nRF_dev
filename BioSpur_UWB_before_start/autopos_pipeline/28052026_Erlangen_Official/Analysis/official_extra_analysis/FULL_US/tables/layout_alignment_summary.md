# Task 1 Layout Absolute Comparison
Primary OptiTrack truth uses median antenna marker positions from: ID01, ID02, ID03, ID04, ID05

Headline deployed UWB+US layout accuracy uses height-preserving alignment: 2D horizontal rigid transform plus one vertical shift, with no pitch/roll and no 3D scale.
Full 3D rigid and Sim(3) similarity alignments are diagnostic only and must not be used as deployed-system absolute-accuracy claims.

Corrected FULL OptiTrack export is treated as authoritative; Anchor G is retained in the canonical all8 headline.

Headline sanity: v4-io all8 height-preserving RMS 135.9 mm (horizontal 85.4 mm, vertical 105.7 mm); similarity scale 0.958 diagnostic only.

## Summary

| version | eval_set | n_anchors | shape_rms_mm | height_preserving_rms_3d_mm | height_preserving_horizontal_rms_mm | height_preserving_vertical_rms_mm | height_preserving_vertical_shift_mm | proper_rotation_rms_3d_mm | reflection_allowed_rms_3d_mm | reflection_allowed_horizontal_rms_mm | reflection_allowed_vertical_rms_mm | similarity_scale | similarity_rms_3d_mm |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| v1-old | all8 | 8 | 140.637 | 118.662 | 93.048 | 73.639 | -15.022 | 1425.495 | 101.274 | 93.817 | 38.143 | 0.955 | 50.066 |
| v2 | all8 | 8 | 192.032 | 181.110 | 109.618 | 144.168 | -15.022 | 1476.766 | 136.454 | 109.015 | 82.070 | 0.938 | 60.318 |
| v3-full | all8 | 8 | 151.623 | 168.758 | 137.952 | 97.204 | -15.022 | 1418.383 | 143.447 | 127.455 | 65.820 | 0.964 | 125.283 |
| v3-lite | all8 | 8 | 191.970 | 181.603 | 109.419 | 144.938 | -15.022 | 1477.230 | 136.601 | 108.810 | 82.584 | 0.939 | 60.865 |
| v4-io | all8 | 8 | 136.307 | 135.884 | 85.433 | 105.667 | -15.022 | 1454.476 | 105.420 | 86.842 | 59.765 | 0.958 | 67.121 |
