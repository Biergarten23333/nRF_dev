# L2 Single-I Seed0 Current-Implementation Accuracy Run

Started: 2026-06-05T10:24:52Z
Local launch: 2026-06-05T12:24:xx+02:00
Driver PID: 694985
Completed: 2026-06-05T12:27:55+02:00
Completion status: 100 summary rows, 0 failures, elapsed 171.23 s

## Why This Run Exists

The previous `l2_singleI_seed0_nominal_1080ti_20260605T101314Z` run was only a
Phase 4 backend/agreement bootstrap. It finished correctly, but it was not the
requested `single-I FULL` accuracy run.

This run is the immediate correction: use the existing accuracy-scoring kernel
to run the current implemented L2/single-I matrix and produce real P50/P95/dR
leaderboards.

## Exact Command

```bash
python3 scripts/run_phase3_full_confirmation.py \
  --run-id phase4_l2_singleI_seed0_current_impl_1080ti_20260605T102452Z \
  --seeds 1 \
  --workers 10 \
  --l-ids L2 \
  --i-mode single
```

## Exact Scope

This is a current-implementation accuracy run, not the final 5,292-row Phase 4
production FULL. It uses the real scoring/evaluation path for implemented
families:

```text
A = A0 only
U/P = U4/P0, U4/P2
R = R2, R4
L = L2 only
I = I0, I1, I2, I3, I4, I5, I6, I7, I8
T = T1 control, T2, T3, T5, T6, T8, T11 diagnostic
seed = S00
workers = 10 CPU workers
```

Expected rows:

```text
baseline/control = 1
IMU-only = 9
position fusion = 2 U/P * 9 I * 3 T = 54
range fusion = 2 R * 9 I * 2 T = 36
total summary rows = 100
```

Missing from the final Phase 4 production claim:

```text
A1/A2/A3, U1/U2/U3, P1/P3/P4/P5, R0/R1/R3, T4/T7/T9/T10/T12
```

Those are recorded as exclusions in the run manifest/table; they must be
implemented or explicitly handled before a final Phase 4 FULL claim.

## Outputs

```text
Run dir:
runs/phase3_full_confirmation/phase4_l2_singleI_seed0_current_impl_1080ti_20260605T102452Z

Main log:
logs/driver.log

Ranking:
tables/phase3_final_ranking.csv

Summary:
tables/phase3_nominal_summary.csv

Report:
reports/PHASE3_FULL_CONFIRMATION.md
```

## Final Result Snapshot

Top rows by screening score:

```text
#1 X_A0_U4_P2_L2_I2_T2
   P50=103.7 mm, P95=176.3 mm, dR=106.7 mm

#2 X_A0_U4_P0_L2_I2_T2
   P50=104.6 mm, P95=190.1 mm, dR=94.9 mm

#3 X_A0_U4_P0_L2_I0_T5
   P50=104.4 mm, P95=227.6 mm, dR=84.4 mm

#4 B0_A0_U4_P0_T1
   P50=105.8 mm, P95=231.8 mm, dR=80.1 mm
```

Interpretation: in this current-implementation L2/single-I run, the best rows
are position-domain rows. The best row improves P95 versus B0, but it does not
clearly beat B0 on P50 or deltaR. This is not yet the final Phase 4 answer.
