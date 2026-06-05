# Phase4 L2 Single-I FULL Seed0 Launch Note

Status: COMPLETED_CURRENT_BACKEND_ONLY
Started UTC: 2026-06-05T10:37:11+00:00
Run ID: phase4_L2_singleI_FULL_seed0_1080ti_20260605T103711Z
PID: 765332

User-requested contract:
- Phase4 output path only
- L restricted to L2 / MPU6050-like / JY61P-like
- single-I mode = I0-I8, no multi-filter stacks
- seeds = 1 only, seed0/S00
- no 5-seed run
- no bootstrap-only run

Launch command:

```bash
python3 scripts/run_phase3_full_confirmation.py \
  --run-id phase4_L2_singleI_FULL_seed0_1080ti_20260605T103711Z \
  --phase-name phase4_L2_singleI_FULL_seed0 \
  --output-subdir phase4_algorithm_factory \
  --seeds 1 \
  --l-ids L2 \
  --i-mode single \
  --workers 10
```

Important naming note: this uses the reusable current implementation backend from `run_phase3_full_confirmation.py`, but output and manifest are Phase4. It is not a 5-seed run.

Live files:
- `logs/driver.pid`
- `logs/driver.log`
- `stage0_readiness_and_matrix_manifest/matrix_manifest.json`
- final ranking will be written to `tables/phase3_final_ranking.csv` by the reused backend.

Runtime check:
- Restarted with `setsid` after the first plain-background launch detached incorrectly.
- Confirmed 10 worker processes running under PID 765332.

Completion:
- End state: process exited normally.
- Rows actually computed by this reused backend: 100.
- Wall time: 165.44 s.
- This was NOT the true 2.5-6 h Phase4 declared FULL factory. It only covered the
  currently implemented A0/U4/P0,P2/R2,R4/L2/I0-I8/T2,T3,T5,T6,T8,T11+B0
  backend.
- Do not use this run as the final FULL answer.
