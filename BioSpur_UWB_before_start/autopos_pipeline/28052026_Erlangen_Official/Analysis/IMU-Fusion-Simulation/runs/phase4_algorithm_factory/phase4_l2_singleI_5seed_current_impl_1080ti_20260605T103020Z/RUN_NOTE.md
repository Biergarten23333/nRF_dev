# Phase4 L2 Single-I 5-Seed Current-Implementation Accuracy Run

Started: 2026-06-05T10:30:20Z
Driver PID: 730453
Stopped: 2026-06-05T12:33:25+02:00
Status: ABORTED_BY_CODEX_AFTER_USER_CORRECTION

## Exact Command

```bash
python3 scripts/run_phase3_full_confirmation.py \
  --run-id phase4_l2_singleI_5seed_current_impl_1080ti_20260605T103020Z \
  --output-subdir phase4_algorithm_factory \
  --phase-name phase4_l2_singleI_current_implementation_accuracy \
  --seeds 5 \
  --workers 10 \
  --l-ids L2 \
  --i-mode single
```

## Exact Scope

This run lives under `runs/phase4_algorithm_factory/` and writes a Phase4
manifest/report. It reuses the existing accuracy-scoring kernel, but the output
is no longer in the Phase3 directory.

This run was stopped because the user explicitly requested `single-I FULL, 1
seed`, not 5 seeds. Do not use this partial run as evidence.

Current implemented matrix:

```text
A = A0 only
U/P = U4/P0, U4/P2
R = R2, R4
L = L2 only
I = I0, I1, I2, I3, I4, I5, I6, I7, I8
T = T1 control, T2, T3, T5, T6, T8, T11 diagnostic
seeds = S00-S04
workers = 10 CPU workers
```

Expected rows:

```text
100 summary rows / seed
500 summary rows total
```

## Important Limitation

This is still the current-implementation accuracy matrix, not the final
5,292-row Phase4 production FULL. The missing final families remain:

```text
A1/A2/A3, U1/U2/U3, P1/P3/P4/P5, R0/R1/R3, T4/T7/T9/T10/T12
```

They must be implemented or explicitly source-excluded before a final Phase4
FULL claim. This run is useful because it gives real multiseed L2/JY61P
leaderboards for the currently implemented solver families.

## Outputs

```text
Run dir:
runs/phase4_algorithm_factory/phase4_l2_singleI_5seed_current_impl_1080ti_20260605T103020Z

Main log:
logs/driver.log

Ranking:
tables/phase3_final_ranking.csv

Report:
reports/PHASE4_CURRENT_IMPLEMENTATION_ACCURACY.md
```
