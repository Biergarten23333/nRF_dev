# UWB Range Bias Policy For Phase 0/1

Phase 0 freezes existing pure-UWB baselines and does not change range bias
handling.

Phase 1 includes a `T6` raw-range tight-fusion placeholder/prototype gate. Raw
range availability is measured from each `tr_all.csv` / `tr.csv`, but the first
vertical-slice `T6` rows use a solved-position proxy for the state update.

Therefore:

```text
Phase 1 G3 status = PASS_OR_LIMITED_PROTO
Phase 2 requirement = implement full raw-range residuals and explicit range-bias handling
```

Before Phase 2, `R2/R3/R4` must define:

```text
anchor_id
range_bias_mm
range_sigma_mm
quality/downweighting policy
missing-link representation
```

No Phase 1 `T6` result may be claimed as a final deployable raw-range EKF.
