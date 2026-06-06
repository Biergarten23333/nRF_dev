## Additional diagnostics (error structure, delay decomposition, anchor health)

This section adds nine diagnostics on top of the corrected static-tag analysis. Unless stated otherwise, the primary line is production-output `v4-io / all8` with corrected ID01/ID05 tag truth.

### 1. Antenna-delay common/differential decomposition

AutoPos v4-io's common effective delay is 34.4 mm, while the OptiTrack inter-anchor endpoint fit has common term 90.6 mm. The differential patterns only weakly agree (Pearson r=-0.03); the AutoPos delay vector should therefore be described as an effective joint self-calibration delay, not a pure physical antenna-delay measurement.

[delay_common_differential.csv](../tables/delay_common_differential.csv)  
![Delay decomposition](fig/delay_decomposition.png)

### 2. Tag error vs distance-from-array-center

The all8 3D-error slope is 166.5 mm/m (R^2=0.28, p=0.007), which supports a positive scale-propagation component. The signed radial slope is 229.9 mm/m (p=0.000). The moderate R^2 means scale propagation is real but not the whole tail explanation.

[tag_error_vs_center_distance.csv](../tables/tag_error_vs_center_distance.csv)  
![Tag error vs center distance](fig/tag_error_vs_center_distance.png)

### 3. Tag error vector field

The all8 mean error vector is (23.7, -6.2, -8.8) mm in OptiTrack XYZ, with |mean|/RMS-scatter=0.19. Median signed radial error is 63.4 mm, median tangential magnitude is 40.1 mm, and 83% of points are radially outward.

[tag_error_vector_decomposition.csv](../tables/tag_error_vector_decomposition.csv)  
![Tag error vector field](fig/tag_error_vector_field.png)

### 4. Worst-point raw-range residual fingerprint

ID01/ID03/ID04/ID06 show structured residual fingerprints rather than identical common offsets. The table reports raw range residuals against both the production solved point and the OptiTrack truth point, with centered per-ID columns to expose anchor-specific structure.

[worstpoint_range_residuals.csv](../tables/worstpoint_range_residuals.csv)  
![Worst-point residual fingerprint](fig/worstpoint_range_residual_fingerprint.png)

### 5. Per-anchor health / trust score

The lowest heuristic trust anchors are G, D, H. This score combines pair residuals, raw asymmetry, temporal drift, OptiTrack marker status, and delay differential magnitude; it is a triage score, not a formal probability of failure.

[anchor_health_scorecard.csv](../tables/anchor_health_scorecard.csv)  
![Anchor health scorecard](fig/anchor_health_scorecard.png)

### 6. Tag error by height

Height grouping uses bootstrap CIs because each group is small. The output table reports 3D median/p95 and OptiTrack X/Y/Z split for all8/noG.

[tag_error_by_height.csv](../tables/tag_error_by_height.csv)  
![Tag error by height](fig/tag_error_by_height.png)

### 7. Tag error: edge vs center

Edge/center grouping checks whether positions farther from the array centroid are worse. Interpret it together with the distance regression rather than as an independent high-power test.

[tag_error_edge_vs_center.csv](../tables/tag_error_edge_vs_center.csv)  
![Tag error edge vs center](fig/tag_error_edge_vs_center.png)

### 8. Tag error by facing group

Facing groups are exploratory because n is small. The table also includes median VDOP/condition number from the existing grid25 DOP-by-facing table for the same IDs.

[tag_error_by_facing.csv](../tables/tag_error_by_facing.csv)  
![Tag error by facing](fig/tag_error_by_facing.png)

### 9. Single-anchor criticality

Drop-one keep7 results rank the most critical anchors to keep as E, D, A for the combined static/roto degradation score. Compare this with the health score: a low-trust anchor can still be geometrically important.

[single_anchor_criticality.csv](../tables/single_anchor_criticality.csv)  
![Single anchor criticality](fig/single_anchor_criticality.png)

### Synthesis

The extra diagnostics point to a coupled error structure rather than one simple cause. The common delay term is gauge-coupled with layout scale, the distance/radial tests do not reduce the tag tail to pure scale propagation, and the worst-point fingerprints plus single-anchor criticality show anchor-specific structure. The best current reading is: typical-position median accuracy is near the surveyed-delaycal floor, while the production p95 tail comes from layout/self-calibration/frame-lock coupling interacting with a few anchor/link weaknesses, not isotropic measurement noise alone.
