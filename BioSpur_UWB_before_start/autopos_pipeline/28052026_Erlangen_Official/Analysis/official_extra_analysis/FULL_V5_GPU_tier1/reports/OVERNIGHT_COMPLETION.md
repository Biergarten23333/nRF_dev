# GPU Tier 1 - Overnight Run Summary

Date: 2026-06-18T00:00:17

Machine: i7-8700K + 2x GTX 1080 Ti (dual-GPU parallel)

## Phase A

| Task | GPU | Status | Runtime min | Key Finding |
|---|---|---|---|---|
| Task 1 (Multi-room MC) | cuda:0 | OK | 10.46 | P(V5<V4)=1.00 |
| Task 5 (Solver search) | cuda:1 | OK | 0.63 | best 82.7 mm |

Phase A wall time: 10.51 min

## Phase B

| Task | GPU | Status | Runtime min | Key Finding |
|---|---|---|---|---|
| Task 3 (Shapley) | cuda:0 | OK | 0.65 | D=1242.9, F=1229.4 |
| Task 4 (AA vs AT) | cuda:1 | OK | 0.63 | mean asymmetry -4.7 mm |

Phase B wall time: 0.70 min

## Phase C

| Task | GPU | Status | Runtime min | Key Finding |
|---|---|---|---|---|
| Task 2 (Fisher) | cuda:0 | OK | 0.62 | weakest eig 1.000e-06 |
| Task 6 (NLOS detector) | cuda:1 | OK | 0.82 | torch_mlp PR-AUC=0.952 |

Phase C wall time: 0.87 min

Total wall time: 12.08 min

Tasks succeeded: 6/6

Tasks failed: 0/6

| Task | Mean CPU % | Mean GPU % | Max GPU % | Peak VRAM MB |
|---|---:|---:|---:|---:|
| Task 1 (Multi-room MC) | 33.1 | 19.4 | 26.0 | 336.0 |
| Task 2 (Fisher) | 40.0 | 0.0 | 0.0 | 288.0 |
| Task 3 (Shapley) | 40.5 | 0.9 | 17.0 | 372.0 |
| Task 4 (AA vs AT) | 40.3 | 0.2 | 4.0 | 196.0 |
| Task 5 (Solver search) | 42.3 | 1.2 | 21.0 | 196.0 |
| Task 6 (NLOS detector) | 33.1 | 2.3 | 10.0 | 204.0 |
