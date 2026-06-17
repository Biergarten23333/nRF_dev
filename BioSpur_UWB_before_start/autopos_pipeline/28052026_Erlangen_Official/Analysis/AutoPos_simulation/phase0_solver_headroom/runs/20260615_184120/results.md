# Phase 0 Solver Headroom Results

Run directory: `/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/autopos_pipeline/28052026_Erlangen_Official/Analysis/AutoPos_simulation/phase0_solver_headroom/runs/20260615_184120`

## Production Invocation

```bash
/usr/bin/python3 /home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/SS-TWR/alt-SS-TWR/broadcast/scripts/prepare_autopos_v3_box.py --pairs-csv <run_dir>/<case>/rep_0000/pairs_all.csv --out-dir <run_dir>/<case>/rep_0000/solver --verbose 0
```

## Reference pairs_all.csv schema

- Source: `/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/autopos_pipeline/28052026_Erlangen_Official/solver/work/field_dataset_staged/sweep1000/pairs_all.csv`
- Columns: `a, b, master, dist_mm, quality_percent, raw_mm, ok, fail`
- Directed convention: `master -> other endpoint using columns a,b,master`
- Rows: `56000`, directed links: `56`

## Required Readouts

1. BASELINE sanity: rigid total RMS median = `7.573 mm`; pass_lt_10mm = `True`.
2. E3 validation: solved-to-truth similarity scale median = `0.939916`; layout-scale-vs-truth median = `1.063925`; expected layout scale near `1.04`; flag = `False`.
3. COMBINED_REAL vs real Erlangen: combined median rigid total RMS = `130.972 mm`; real target RMS = `105.420 mm`; ratio = `1.242`.
4. Verdict: E3 rigid total RMS = `129.965 mm`; E4_REAL rigid total RMS = `24.279 mm`; E3/E4 ratio = `5.353`.
5. E2 readout: breaking_k = `None`; curve = `{1: 11.926590352711898, 2: 14.962656265001051, 4: 24.90639204183671, 8: 42.07148984730304}`.

## Aggregate Case Summary

| Case | n | Rigid total RMS median mm | Similarity total RMS median mm | Similarity scale median | Scale contrib total RMS median mm |
|---|---:|---:|---:|---:|---:|
| BASELINE | 1 | 7.573 | 7.573 | 0.999971 | 0.000 |
| COMBINED_REAL | 200 | 130.972 | 53.670 | 0.939851 | 77.316 |
| E1 | 200 | 7.565 | 7.563 | 0.999962 | 0.000 |
| E2_K1 | 200 | 11.927 | 11.858 | 1.000186 | 0.074 |
| E2_K2 | 200 | 14.963 | 14.913 | 0.999953 | 0.088 |
| E2_K4 | 200 | 24.906 | 24.689 | 0.999845 | 0.167 |
| E2_K8 | 200 | 42.071 | 41.548 | 1.000069 | 0.312 |
| E3 | 1 | 129.965 | 51.434 | 0.939916 | 78.531 |
| E4_REAL | 1 | 24.279 | 24.270 | 0.999641 | 0.009 |
| E4_SWEEP_X1 | 1 | 24.279 | 24.270 | 0.999641 | 0.009 |
| E4_SWEEP_X2 | 1 | 47.473 | 47.450 | 0.999212 | 0.023 |
| E4_SWEEP_X4 | 1 | 95.035 | 94.931 | 0.997624 | 0.104 |
| E4_SWEEP_X8 | 1 | 194.604 | 193.870 | 0.990991 | 0.734 |
