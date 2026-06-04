# FULL 4-Way Big Comparison, US30/FGH Height-Gauged Rerun

Generated from the US rerun outputs. This report is the US counterpart of the original FULL 4-way summary; the cross-comparison lives in `../../FULL_NO_US_VS_US/reports/FULL_NO_US_VS_US_COMPARISON.md`.

## One-Screen Headline

- Legacy anchor-locked production static `v4-io`: **74.0 / 282.1 mm**, RMSE **139.6 mm**. This old metric is unchanged by a pure gauge transform.
- US raw replay median-estimator `v4-io/T4`: **69.8 / 173.8 mm**, RMSE **108.9 mm**; `T3`: **69.1 / 173.0 mm**.
- US height-preserving v4-io static diagnostic: **72.5 / 284.8 mm**, RMSE **139.1 mm**. This permits only horizontal 2D rigid alignment plus vertical shift, not full 3D pitch/roll.
- US height-preserving v4-io anchor diagnostic: **98.4 / 162.1 mm**, RMSE **109.7 mm**.
- Static one-baseline E-H + delaycal `v4-io/T4`: **58.1 / 130.2 mm** under the legacy 4-way aggregate metric.
- Original FULL_US ROTO `v4-io/T4`: **105.8 / 231.8 mm** track-median 3D P50/P95.
- ROTO filtered replay: F4 **86.3 / 158.2 mm**, F5 **83.3 / 148.6 mm**.
- ROTO pseudo-IMU oracle: PI1 **66.1 / 97.5 mm**, PI4 **58.7 / 81.5 mm**.
- No-US vs US legacy rows with effectively zero delta: **8/10**. That is expected for metrics that allow full 3D anchor/capture alignment.

## Interpretation

- The US30/FGH rerun has been executed through the same core 4-way static/ROTO matrices plus filtered replay, pseudo-IMU replay, dynamic diagnostics, resilience audit, and reporting checklist.
- Legacy anchor-locked metrics are mostly invariant because a full 3D alignment can absorb the coordinate-gauge change. Treat them as sanity checks, not as proof that US has no deployment effect.
- The new height-preserving table is the deployment-gauge diagnostic: it preserves the US vertical gauge and avoids using arbitrary 3D pitch/roll to erase Z differences.

## Output Files
- `tables/static_4way_accuracy_summary.csv`
- `tables/roto_4way_accuracy_summary.csv`
- `reports/STATIC_4WAY_COMPARISON.md`
- `reports/ROTO_4WAY_COMPARISON.md`
- `roto_filtered/tables/roto_filtered_summary.csv`
- `roto_pseudo_imu/tables/roto_pseudo_imu_summary.csv`
- `resilience_gap_audit/reports/RESILIENCE_GAP_AUDIT.md`
- `reporting_checklist/reports/REPORTING_CHECKLIST_AUDIT.md`
- `../FULL_NO_US_VS_US/reports/FULL_NO_US_VS_US_COMPARISON.md`
