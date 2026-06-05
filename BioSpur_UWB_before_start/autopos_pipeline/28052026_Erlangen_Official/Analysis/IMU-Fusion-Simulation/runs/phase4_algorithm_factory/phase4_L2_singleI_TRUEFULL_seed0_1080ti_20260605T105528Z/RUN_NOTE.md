# Phase4 L2 Single-I TRUEFULL Seed0

Status: COMPLETED_CPU_BACKEND
Started UTC: 2026-06-05T10:55:28+00:00
Run ID: phase4_L2_singleI_TRUEFULL_seed0_1080ti_20260605T105528Z

Contract:
- L = L2 only
- I = I0-I8 single-I only
- seed = S00 only
- declared manifest rows = 5292
- no 5-seed run
- no previous 100-row backend shortcut
- output path is phase4_algorithm_factory
- completed rows = 1377 runnable rows
- declared manifest rows = 5292
- excluded rows = 3915
- elapsed = 1973.07 s, about 32.9 min
- GPU usage = none; this was CPU backend and is not the final GPU+CPU 2.5-6 h factory

Launcher:

```bash
python3 scripts/run_phase4_l2_singleI_full_factory.py --run-id phase4_L2_singleI_TRUEFULL_seed0_1080ti_20260605T105528Z --workers 4
```
