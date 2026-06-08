# 5090D Export Bundle

This folder is a minimal runnable export for re-running Phase 4 on a remote
5090D machine.

## Contents

```text
EXPORT_5090D/
  IMU-Fusion-Simulation/
    configs/
    scripts/
    cache/
    docs/plans/reports needed by scripts
    no historical runs/
  official_extra_analysis/
    FULL_US/roto_absolute/tables/roto_abs_samples_v4io_T4.csv
    FULL_US/roto_absolute/tables/roto_time_offsets_v4io_T4.csv
    FULL_AutoPos_one_baseline_scale_correction_US/roto_absolute/tables/roto_abs_samples_v4io_T4.csv
    FULL_AutoPos_align_to_Vicon_US/roto_absolute/tables/roto_abs_samples_v4io_T4.csv
    FULL_AutoPos_scale_to_vicon_US/roto_absolute/tables/roto_abs_samples_v4io_T4.csv
```

Raw capture folders are not included and are not required for the current
Phase 4 rerun. The Phase 4 scripts consume the aligned official ROTO sample
tables above.

## Remote Layout

Keep `official_extra_analysis` as a sibling of `IMU-Fusion-Simulation`.
The scripts derive paths relative to their own location:

```text
SIM_ROOT      = EXPORT_5090D/IMU-Fusion-Simulation
ANALYSIS_ROOT = EXPORT_5090D
EXTRA_ROOT    = EXPORT_5090D/official_extra_analysis
```

## Basic Environment

Install or provide:

```text
python3
numpy
pandas
pyyaml
matplotlib
torch with CUDA, if using GPU pilot/backends
tmux
rsync
```

## Smoke Check

```bash
cd EXPORT_5090D/IMU-Fusion-Simulation
python3 scripts/run_phase4_l2_singleI_full_factory.py --help
```

## Example Phase 4 TRUEFULL Runs

Run one seed for one sensor:

```bash
cd EXPORT_5090D/IMU-Fusion-Simulation
python3 scripts/run_phase4_l2_singleI_full_factory.py \
  --sensor-id L20 \
  --seed-id S00 \
  --workers 12 \
  --run-id phase4_L20_TRUEFULL_S00_5090D
```

Run L2/L16/L20 and S00-S04 manually:

```bash
cd EXPORT_5090D/IMU-Fusion-Simulation
for L in L2 L16 L20; do
  for S in S00 S01 S02 S03 S04; do
    python3 scripts/run_phase4_l2_singleI_full_factory.py \
      --sensor-id "$L" \
      --seed-id "$S" \
      --workers 12 \
      --run-id "phase4_${L}_TRUEFULL_${S}_5090D"
  done
done
```

For long runs, start them inside `tmux` on the remote machine.

