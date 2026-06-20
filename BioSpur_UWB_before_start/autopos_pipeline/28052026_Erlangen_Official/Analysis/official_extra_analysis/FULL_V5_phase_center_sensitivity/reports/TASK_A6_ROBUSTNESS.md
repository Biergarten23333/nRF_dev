# Task A6 - Robustness Summary

| conclusion | baseline_value | flip_threshold_mm | robustness_label |
| --- | --- | --- | --- |
| V5 Sim3 scale > 0.99 | 1.010 | >10 | robust |
| V4+LOO beats V5+LOO | V4-V5=-23.0 mm | >10 | robust |
| Vicon oracle rank/worst status | rank=2, worst=False | 2.0 | fragile |
| D_tag LOO approximately 49.6mm | 49.028 mm; sensitivity 0.190 mm/mm | not binary | stable |
| D_tag per-height spread V5 < V4 | 7.4 < 11.8 mm from prior mechanism audit | not directly flipped by global phase-center sweep | not directly tested here; use A4 as caveat |
| Cancellation valley exists | max tested operating-point valley-distance shift 11.37 | does not depend on absolute phase-center offset | invariant mechanism |

## Recommended Paper Wording

The static V4/V5 ranking and the V5 metric scale conclusion are robust to tested global phase-center shifts up to 10 mm. Manufacturing-level independent phase-center variation primarily broadens metric distributions rather than changing the qualitative ranking at plausible sigma values. The paper should state that phase-center uncertainty is a residual systematic, but not the driver of the main scale-delay conclusions in this campaign.
