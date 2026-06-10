# Phase 2.11 C-core Sweep-Transfer Check

- Generated: `2026-06-10T10:18:24`
- Ground-truth terminology: `Vicon`
- Scope: one C-core T4 consistency check for the individual-report ladder table; no production files were modified.

## Result
Both rows use the V-B calibrated layout, C-core T4, mean session estimator, and anchor-only 3D rigid/reflection registration. `vb_sweep_plus_loo_tag_delta` is the C-core counterpart of the earlier Phase 2 simplified-WLS V-B transfer row: sweep-fitted anchor Delta_i terms plus a leave-one-position-out Delta_tag term, with no tag-side proportional rho.

| mode | positions | median_3d_mm | p95_3d_mm | rmse_3d_mm | median_horizontal_mm | median_vertical_mm |
| --- | --- | --- | --- | --- | --- | --- |
| vb_sweep_anchor_delta_only | 24 | 100.375 | 211.880 | 135.884 | 44.731 | 90.660 |
| vb_sweep_plus_loo_tag_delta | 24 | 224.228 | 397.270 | 263.741 | 80.636 | 215.977 |


STOP: Phase 2.11 complete. Use this only to avoid mixing WLS and C-core rows in the standalone report draft.
