# Revision D R3D / D0-A / D0-B synthetic failure checkpoint

This is an evidence-only checkpoint. It preserves these outcomes without
changing their scientific interpretation:

```text
R3D = PASS_R3D_GAUGE_INVARIANT_BROAD_ACTIVITY
D0-A = D0A_CONTRACTS_FROZEN
D0-B = FAIL_D0B_SYNTHETIC_NULLSPACE
```

The standalone exporter called the frozen D0-B production synthetic evaluator
exactly once at the existing deterministic seed and truth point. It performed
zero optimizer iterations. The old persisted shape, rank, singular extrema,
action Jacobian norms, and three null-direction block-energy summaries all
reproduced within the predeclared `1e-12` absolute/relative tolerance.

## Matrix evidence

`matrix_evidence/D0B_MATRIX_EVIDENCE.npz` contains:

- `J_data`, `J_nondata`, and `J_full` in the exact persisted row order;
- `J_publishable`, `J_nuisance`, and the SVD-profiled `J_eff`;
- empty explicit constraint Jacobian `C` and identity tangent basis `T`, because
  the pelvis yaw convention was removed from the 95-coordinate state rather
  than imposed as a residual constraint;
- complete data, non-data, full, nuisance, and profiled singular spectra;
- all 23 data-only and all 3 data-plus-prior null vectors;
- canonical-sign vector representations and basis-invariant null projectors;
- the frozen synthetic truth point and data/non-data/full residual vectors.

Array semantic hashes bind each array name, dtype, shape, and C-order bytes
independently of NPZ container metadata. The row manifest explicitly labels
all 60 non-data rows `PROTOCOL_PRIOR`; none is called a manifold constraint.

The reload-only verifier imported no BioSpur production module and reproduced:

```text
DATA_ONLY_FULL_RANK = 72
DATA_ONLY_NUISANCE_RANK = 40
DATA_ONLY_PROFILED_PRODUCT_RANK = 32
CONSTRAINT_TANGENT_DIMENSION = 95
DATA_PLUS_PRIOR_RANK = 92
```

This task does not classify or repair the remaining torso/trunk nullspace.

## Original compact evidence

- `r3d0_gauge_audit/`: byte-identical compact R3D-0 frame/gauge audit.
- `r3d_synthetic/`: byte-identical R3D synthetic qualification summaries.
- `r3d_formal_compact/`: byte-identical formal R3D compact evidence.
- `d0a_freeze/`: byte-identical D0-A contracts and freeze binding.
- `matrix_evidence/original_d0b/`: byte-identical historical D0-B directory.
- `terminal_report/`: byte-identical terminal R3D/D0-A/D0-B report.

The formal R3D array payload is intentionally not committed because the compact
JSON evidence and hashes are sufficient for this source-audit checkpoint:

```text
local path = Fusion_Part/logs/v47_ten_node_body_calibration_20260814_093601/analysis_imu_multi_action_revision_d_r3d_formal_20260816/R3D_NODE_AND_ACTION_ARRAYS.npz
size = 13993676 bytes
SHA-256 = 17ed8d90d912b444c245e52e2e3adf06fcb1bd467716ebae3d3fb382bbc82678
committed = false
```

No raw calibration ledger, Q2 cache payload, UWB/T4/Anchor data, operator
measurements, held-out data, or media is included.

```text
REAL_D0_OBJECTIVE = NOT_RUN
REAL_D0_JACOBIAN = NOT_RUN
REAL_D0_SOLVER = NOT_RUN
FREEZE/REPLAY/RENDER = NOT_STARTED
FINAL_STILL/GOLF/BOXING/WALK/UWB = SEALED
```
