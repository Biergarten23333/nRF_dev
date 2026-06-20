# GPU Full Discovery Pipeline - Overnight Summary

Date: 2026-06-18T00:43:27

Machine: i7-8700K + 2x GTX 1080 Ti (dual-GPU, 9 parallel phases)

| Phase | Task (cuda:0) | Status | Task (cuda:1) | Status | Phase Time |
|---|---|---|---|---|---|
| A | Task 1 (Multi-room MC) | OK | Task 5 (Solver search) | OK | 8.54 min |
| B | Task 3 (Shapley) | OK | Task 4 (AA vs AT) | OK | 0.54 min |
| C | Task 2 (Fisher) | OK | Task 6 (NLOS detector) | OK | 0.69 min |
| D | Task 7 (Learned correction) | OK | Task 8 (Landscape) | OK | 1.92 min |
| E | Task 9 (Layout opt) | OK | Task 10 (Residual decomp) | OK | 1.67 min |
| F | Task 11 (Model tournament) | OK | Task 12 (Bayesian solver) | OK | 0.50 min |
| G | Task 13 (GNN attention) | OK | Task 14 (Observability) | OK | 1.58 min |
| H | Task 15 (Synthetic CIR) | OK | Task 16 (ROTO state) | OK | 0.52 min |
| I | Task 17 (Active design) | OK | - | - | 0.52 min |

Tasks succeeded: 17/17

Tasks failed: 0/17

Total wall time: 19.06 min

GPU-0 peak VRAM: 372.0 MB

GPU-1 peak VRAM: 204.0 MB

| Task | Runtime min | Mean CPU % | Mean GPU % | Max GPU % | Peak VRAM MB | Key finding |
|---|---:|---:|---:|---:|---:|---|
| Task 1 (Multi-room MC) | 8.50 | 21.3 | 23.7 | 28.0 | 336.0 | P(V5<V4)=1.00 |
| Task 2 (Fisher) | 0.50 | 27.6 | 0.0 | 0.0 | 288.0 | weakest eig 1.000e-06 |
| Task 3 (Shapley) | 0.50 | 26.3 | 1.0 | 27.0 | 372.0 | D=1242.9, F=1229.4 |
| Task 4 (AA vs AT) | 0.47 | 25.9 | 0.0 | 0.0 | 148.0 | mean asymmetry -4.7 mm |
| Task 5 (Solver search) | 0.54 | 25.4 | 1.2 | 21.0 | 196.0 | best 82.7 mm |
| Task 6 (NLOS detector) | 0.65 | 25.6 | 3.2 | 16.0 | 204.0 | torch_mlp PR-AUC=0.949 |
| Task 7 (Learned correction) | 2.58 | 22.9 | 10.8 | 17.0 | 344.0 | MLP median 118.0 mm vs scalar 98.5 mm |
| Task 8 (Landscape) | 1.88 | 24.0 | 16.2 | 25.0 | 196.0 | min 70.0 mm at s=0.930, dc=50, D=140 |
| Task 9 (Layout opt) | 1.63 | 24.8 | 14.5 | 25.0 | 344.0 | best optimized median 78.3 mm, mean move 88.0 mm |
| Task 10 (Residual decomp) | 0.65 | 33.3 | 1.4 | 14.0 | 204.0 | M0_global median 75.1 mm |
| Task 11 (Model tournament) | 0.45 | 22.6 | 0.0 | 0.0 | 288.0 | BIC winner M2_student_t |
| Task 12 (Bayesian solver) | 0.47 | 22.6 | 0.0 | 0.0 | 148.0 | V5 95pct coverage 0.33 |
| Task 13 (GNN attention) | 1.54 | 20.3 | 15.5 | 26.0 | 344.0 | attention residual median 121.1 mm |
| Task 14 (Observability) | 0.46 | 22.3 | 0.0 | 0.0 | 148.0 | V5 mean GDOP 1.17 |
| Task 15 (Synthetic CIR) | 0.48 | 19.9 | 0.0 | 0.0 | 288.0 | synthetic NLOS agreement 0.14 |
| Task 16 (ROTO state) | 0.03 | 17.5 | 0.5 | 1.0 | 148.0 | median NLOS dwell 1.8 frames |
| Task 17 (Active design) | 0.48 | 20.5 | 0.1 | 3.0 | 288.0 | top info gain 0.178 |
