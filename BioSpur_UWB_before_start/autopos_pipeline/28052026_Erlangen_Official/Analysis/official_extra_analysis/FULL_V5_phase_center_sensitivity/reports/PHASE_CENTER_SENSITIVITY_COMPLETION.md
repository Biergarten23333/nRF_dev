# Phase Center Sensitivity Completion

| task | status | key_result | elapsed_s |
| --- | --- | --- | --- |
| A1 | OK | ranking does not flip up to 10 mm | 0.381 |
| A2 | OK | V4-over-V5 ranking remains high-probability through sigma=8 mm | 1.332 |
| A3 | OK | anchor perturbations dominate scale/Vicon metrics; tag perturbations dominate D_tag and tag-position error shifts | 0.419 |
| A4 | OK | best median at delta_0=-5 mm, delta_elev=-10 mm | 0.464 |
| A5 | OK | vertical phase-center shifts move the operating point mildly; valley shape remains dominated by scale-D_tag coupling | 0.261 |
| A6 | OK | robustness table complete | 0.005 |
| TOTAL | OK |  | 29.776 |

A2, A3, and A5 use the torch CUDA code path when CUDA is available. On this run the batched kernels were short enough that `nvidia-smi` sampling did not reliably capture live utilization; CUDA availability and device count are recorded in `SCRIPT_VERIFICATION.json`.

## Runtime

| task | status | elapsed_s | mean_cpu_percent | max_cpu_percent | mean_gpu_percent | max_gpu_percent | peak_vram_mb | error |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| A1 | OK | 0.381 | 10.700 | 18.000 | nan | nan | nan |  |
| A2 | OK | 1.332 | 14.050 | 16.800 | nan | nan | nan |  |
| A3 | OK | 0.419 | 16.400 | 23.800 | nan | nan | nan |  |
| A4 | OK | 0.464 | 13.825 | 15.900 | nan | nan | nan |  |
| A5 | OK | 0.261 | 12.400 | 13.800 | nan | nan | nan |  |
| A6 | OK | 0.005 | 20.400 | 20.400 | nan | nan | nan |  |
| TOTAL | OK | 29.776 | 14.629 | 23.800 | nan | nan | nan |  |

## Robustness Summary

| conclusion | baseline_value | flip_threshold_mm | robustness_label |
| --- | --- | --- | --- |
| V5 Sim3 scale > 0.99 | 1.010 | >10 | robust |
| V4+LOO beats V5+LOO | V4-V5=-23.0 mm | >10 | robust |
| Vicon oracle rank/worst status | rank=2, worst=False | 2.0 | fragile |
| D_tag LOO approximately 49.6mm | 49.028 mm; sensitivity 0.190 mm/mm | not binary | stable |
| D_tag per-height spread V5 < V4 | 7.4 < 11.8 mm from prior mechanism audit | not directly flipped by global phase-center sweep | not directly tested here; use A4 as caveat |
| Cancellation valley exists | max tested operating-point valley-distance shift 11.37 | does not depend on absolute phase-center offset | invariant mechanism |
