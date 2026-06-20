# Task F1 - p30 + inverse-RMS Combination

| label | median_3d_mm | p95_3d_mm | rmse_3d_mm | d_tag_used_mm | notes |
| --- | --- | --- | --- | --- | --- |
| V5_p50_uniform_DLOO | 67.809 | 160.509 | 86.400 | 49.621 | baseline synthetic p50 |
| V5_p30_uniform_DLOO | 47.496 | 135.633 | 75.030 | 49.621 | p30 only |
| V5_p50_invRMS_DLOO | 67.046 | 149.504 | 83.513 | 49.621 | weighting only |
| V5_p30_invRMS_DLOO | 53.638 | 131.617 | 74.522 | 49.621 | p30 plus inverse-rms |
| V4_p30_uniform_DLOO | 61.882 | 114.779 | 75.201 | 49.621 | V4 with V5 LOO tag delay |
| V4_p30_invRMS_DLOO | 62.524 | 138.100 | 80.659 | 49.621 | V4 p30 plus inverse-rms |
| Vicon_p30_invRMS_DLOO | 48.459 | 143.456 | 76.366 | 49.621 | known-anchor p30 plus inverse-rms |
| V5_p30_uniform_Dsweep | 46.772 | 135.691 | 75.115 | 48.000 | diagnostic only; full 24-position in-sample D_tag sweep |
| V5_p30_invRMS_Dsweep | 53.210 | 136.041 | 74.943 | 54.000 | diagnostic only; full 24-position in-sample D_tag sweep |
| V5_p30_uniform_DLOO_recal | 59.842 | 158.761 | 81.403 | 32.986 | deployability caveat: LOO-CV on same 24-position campaign |
| V5_p30_invRMS_DLOO_recal | 56.011 | 143.120 | 79.482 | 32.986 | deployability caveat: LOO-CV on same 24-position campaign |

Best row by median 3D: `V5_p30_uniform_Dsweep` = 46.772 mm.
