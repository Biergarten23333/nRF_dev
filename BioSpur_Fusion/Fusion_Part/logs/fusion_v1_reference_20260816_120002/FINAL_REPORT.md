# Fusion v1 session report

## Verdict

**REFERENCE_ESTIMATOR_INVALID**

This verdict means no reference estimator has yet earned scientific validity.
Stage A raw lineage and model-independent primitives are working, but the
common-clock mapping remains unaccepted and no nonlinear estimator has been
fit. Calling the work partially validated would overstate the evidence.

## Repository

Branch `feature/b306-bringup`; starting HEAD
`412233adcb0a5a8551f2a5d1085c79b8c2c26ae5`. The parent worktree was already
heavily dirty. All changes from this session are new paths under
`Fusion_Part/fusion_v1` and `Fusion_Part/logs/fusion_v1_reference_*`. Historical
estimators, configs, evidence, and logs were not modified. No commit or push
was performed.

## Data

One 1392.378 s post-T0 capture is available. The independently decoded raw
stream contains 1,234,999 complete records, one CRC failure, and 129 incomplete
EOF bytes. The canonical table contains 7,295,015 IMU samples and 2,271,712
individual UWB ranges from all ten nodes; 474,585 invalid ranges remain visible.
The action-level split is frozen in `fusion_v1/config/data_split.json`. There is
no independent recording-level held-out dataset.

## Sensor characterization

Only cadence and availability are established. Median IMU cadence is 5 ms on
all nodes, with node-dependent gaps. Per-node IMU noise/bias and per-pair UWB
heavy-tail, sustained-bias, vertical, range, and orientation findings are not
yet measured and therefore are not reported.

## Human model

The proposed pelvis-rooted ten-segment topology and FK primitive exist. Segment
lengths are capture constants; joint centres, sensor extrinsics, functional
axes, and contacts are designed as uncertain/soft quantities. No subject
geometry has been estimated, and no old calibration product was used.

## Estimator

No fitted estimator exists. SciPy is selected provisionally because installed
NumPy/SciPy provide maintained sparse optimization while JAX and GTSAM are not
installed. Cauchy weighting and pair-health hysteresis primitives are tested,
but their scientific scales are deliberately unfitted. `common_time_us` is
blank pending the TDMA clock gate.

## Validation

Nine software tests pass. Static jitter, outlier/bias injection, dropout,
vertical weakness, non-rigidity, leave-one-sensor/anchor, timing perturbation,
and T4 comparisons have not been run. No animation or scientific metrics are
claimed.

## Artifacts

- Audit: `ARCHITECTURE_AND_DATA_AUDIT.md`
- Machine counts: `STAGE_A_MACHINE_AUDIT.json`
- Canonical table: `CANONICAL_OBSERVATIONS.csv.gz`
- Configuration: `../../fusion_v1/config/reference_v1.json`
- Split: `../../fusion_v1/config/data_split.json`
- Tests: `TEST_RESULTS.md`
- Reproducible audit command: documented in `../../fusion_v1/README.md`

No metrics tables, plots, or animations are listed because none meet the
scientific prerequisites yet.

