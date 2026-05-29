# Task 1 Layout Absolute Comparison
Primary OptiTrack truth uses median antenna marker positions from: ID01, ID02, ID03, ID04, ID05

All headline accuracy values use reflection-allowed rigid alignment with no scale.
Similarity scale/RMS are diagnostic only and must not be used as absolute-accuracy claims.

G marker warning: Gshort/Glong/marker-fingerprint is treated as suspect. This script writes `opti_anchor_marker_fingerprint.csv`, does not reconstruct G from the jig, and reports all headline layout numbers both all8 and noG.

Headline sanity: v4-io all8 rigid RMS 104.9 mm (horizontal 86.1 mm, vertical 59.9 mm); similarity scale 0.960 diagnostic only.

No-G headline: v4-io noG rigid RMS 104.4 mm (horizontal 84.4 mm, vertical 61.4 mm).

## Summary

| version | eval_set | n_anchors | shape_rms_mm | proper_rotation_rms_3d_mm | reflection_allowed_rms_3d_mm | reflection_allowed_horizontal_rms_mm | reflection_allowed_vertical_rms_mm | similarity_scale | similarity_rms_3d_mm |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| v1-old | all8 | 8 | 137.648 | 1439.002 | 106.932 | 94.678 | 49.705 | 0.957 | 65.933 |
| v1-old | noG | 7 | 137.447 | 1339.383 | 98.775 | 90.240 | 40.166 | 0.956 | 50.787 |
| v1-old | lower | 4 | 176.785 | 120.099 | 114.114 | 111.488 | 24.344 | 0.942 | 44.178 |
| v1-old | upper | 4 | 112.606 | 73.833 | 73.833 | 73.374 | 8.216 | 0.964 | 35.893 |
| v2 | all8 | 8 | 185.844 | 1490.398 | 135.358 | 110.020 | 78.851 | 0.941 | 65.875 |
| v2 | noG | 7 | 193.246 | 1382.663 | 137.467 | 108.564 | 84.328 | 0.938 | 64.098 |
| v2 | lower | 4 | 217.608 | 137.741 | 135.077 | 134.854 | 7.752 | 0.929 | 29.479 |
| v2 | upper | 4 | 119.340 | 78.540 | 78.540 | 76.273 | 18.734 | 0.962 | 36.075 |
| v3-full | all8 | 8 | 149.180 | 1432.101 | 144.390 | 126.893 | 68.895 | 0.966 | 128.556 |
| v3-full | noG | 7 | 161.908 | 1325.869 | 151.621 | 134.749 | 69.509 | 0.963 | 133.635 |
| v3-full | lower | 4 | 236.256 | 181.334 | 177.739 | 176.560 | 20.431 | 0.941 | 141.532 |
| v3-full | upper | 4 | 47.538 | 49.009 | 30.394 | 29.846 | 5.746 | 0.984 | 10.601 |
| v3-lite | all8 | 8 | 185.723 | 1490.865 | 135.438 | 109.824 | 79.260 | 0.941 | 66.240 |
| v3-lite | noG | 7 | 192.972 | 1382.932 | 137.516 | 108.252 | 84.806 | 0.938 | 64.673 |
| v3-lite | lower | 4 | 217.051 | 137.546 | 134.787 | 134.528 | 8.355 | 0.929 | 29.746 |
| v3-lite | upper | 4 | 119.074 | 78.551 | 78.551 | 76.237 | 18.924 | 0.962 | 36.665 |
| v4-io | all8 | 8 | 130.551 | 1467.913 | 104.940 | 86.137 | 59.940 | 0.960 | 71.190 |
| v4-io | noG | 7 | 133.354 | 1362.898 | 104.408 | 84.422 | 61.432 | 0.960 | 70.149 |
| v4-io | lower | 4 | 157.951 | 106.810 | 105.302 | 105.266 | 2.727 | 0.951 | 57.840 |
| v4-io | upper | 4 | 76.733 | 52.179 | 52.179 | 51.596 | 7.779 | 0.977 | 31.110 |
