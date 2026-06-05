# Phase4 GPU+CPU Full Factory Dispatch

Status: RUNNING
Started UTC: 2026-06-05T11:31:45+00:00
Run ID: phase4_GPUCPU_FULLFACTORY_2x1080ti_20260605T113145Z

Resource contract:
- use both GPUs: cuda:0 and cuda:1
- workers_per_device = 4, total dispatch workers = 8
- max wall time = 21600 s / 6 h
- chunk_size = 2 rows
- full tracks and full frames per row
- monitor resource samples every 15 s

Launcher:

```bash
python3 scripts/run_phase4_nightly_bootstrap.py \
  --run-id phase4_GPUCPU_FULLFACTORY_2x1080ti_20260605T113145Z \
  --devices cuda:0 cuda:1 \
  --workers-per-device 4 \
  --chunk-size 2 \
  --partial-max-tracks 0 \
  --partial-max-frames 0 \
  --max-wall-time 21600 \
  --chunk-timeout-s 7200 \
  --monitor-interval 15
```
