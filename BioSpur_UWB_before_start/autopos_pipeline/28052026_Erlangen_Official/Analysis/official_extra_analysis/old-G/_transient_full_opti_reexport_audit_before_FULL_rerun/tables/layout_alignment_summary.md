# Task 1 Layout Absolute Comparison
Primary OptiTrack truth uses median antenna marker positions from: ID01, ID02, ID03, ID04, ID05

All headline accuracy values use reflection-allowed rigid alignment with no scale.
Similarity scale/RMS are diagnostic only and must not be used as absolute-accuracy claims.

G marker warning: Gshort/Glong/marker-fingerprint is treated as suspect. This script writes `opti_anchor_marker_fingerprint.csv`, does not reconstruct G from the jig, and reports all headline layout numbers both all8 and noG.

Headline sanity: v4-io all8 rigid RMS 105.4 mm (horizontal 86.8 mm, vertical 59.8 mm); similarity scale 0.958 diagnostic only.

No-G headline: v4-io noG rigid RMS 104.4 mm (horizontal 84.4 mm, vertical 61.4 mm).

## Summary

| version | eval_set | n_anchors | shape_rms_mm | proper_rotation_rms_3d_mm | reflection_allowed_rms_3d_mm | reflection_allowed_horizontal_rms_mm | reflection_allowed_vertical_rms_mm | similarity_scale | similarity_rms_3d_mm |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| v1-old | all8 | 8 | 140.637 | 1425.495 | 101.274 | 93.817 | 38.143 | 0.955 | 50.066 |
| v1-old | noG | 7 | 137.447 | 1339.383 | 98.775 | 90.240 | 40.166 | 0.956 | 50.787 |
| v1-old | lower | 4 | 176.785 | 120.099 | 114.114 | 111.488 | 24.344 | 0.942 | 44.178 |
| v1-old | upper | 4 | 107.681 | 77.164 | 73.677 | 69.111 | 25.535 | 0.965 | 38.409 |
| v2 | all8 | 8 | 192.032 | 1476.766 | 136.454 | 109.015 | 82.070 | 0.938 | 60.318 |
| v2 | noG | 7 | 193.246 | 1382.663 | 137.467 | 108.564 | 84.328 | 0.938 | 64.098 |
| v2 | lower | 4 | 217.608 | 137.741 | 135.077 | 134.854 | 7.752 | 0.929 | 29.479 |
| v2 | upper | 4 | 114.843 | 85.359 | 81.069 | 72.515 | 36.245 | 0.962 | 43.767 |
| v3-full | all8 | 8 | 151.623 | 1418.383 | 143.447 | 127.455 | 65.820 | 0.964 | 125.283 |
| v3-full | noG | 7 | 161.908 | 1325.869 | 151.621 | 134.749 | 69.509 | 0.963 | 133.635 |
| v3-full | lower | 4 | 236.256 | 181.334 | 177.739 | 176.560 | 20.431 | 0.941 | 141.532 |
| v3-full | upper | 4 | 45.939 | 31.786 | 31.786 | 29.356 | 12.188 | 0.985 | 17.035 |
| v3-lite | all8 | 8 | 191.970 | 1477.230 | 136.601 | 108.810 | 82.584 | 0.939 | 60.865 |
| v3-lite | noG | 7 | 192.972 | 1382.932 | 137.516 | 108.252 | 84.806 | 0.938 | 64.673 |
| v3-lite | lower | 4 | 217.051 | 137.546 | 134.787 | 134.528 | 8.355 | 0.929 | 29.746 |
| v3-lite | upper | 4 | 114.554 | 85.404 | 81.097 | 72.458 | 36.423 | 0.963 | 44.277 |
| v4-io | all8 | 8 | 136.307 | 1454.476 | 105.420 | 86.842 | 59.765 | 0.958 | 67.121 |
| v4-io | noG | 7 | 133.354 | 1362.898 | 104.408 | 84.422 | 61.432 | 0.960 | 70.149 |
| v4-io | lower | 4 | 157.951 | 106.810 | 105.302 | 105.266 | 2.727 | 0.951 | 57.840 |
| v4-io | upper | 4 | 72.605 | 59.084 | 54.504 | 48.276 | 25.300 | 0.978 | 36.699 |
