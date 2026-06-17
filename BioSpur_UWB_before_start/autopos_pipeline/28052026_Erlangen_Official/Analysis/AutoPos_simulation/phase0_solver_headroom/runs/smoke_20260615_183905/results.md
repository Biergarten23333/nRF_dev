# Phase 0 Solver Headroom Results

Run directory: `autopos_pipeline/28052026_Erlangen_Official/Analysis/AutoPos_simulation/phase0_solver_headroom/runs/smoke_20260615_183905`

## Production Invocation

```bash
/usr/bin/python3 /home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/SS-TWR/alt-SS-TWR/broadcast/scripts/prepare_autopos_v3_box.py --pairs-csv /home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/<run_dir>/<case>/rep_0000/pairs_all.csv --out-dir /home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/<run_dir>/<case>/rep_0000/solver --verbose 0
```

## Reference pairs_all.csv schema

- Source: `/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/autopos_pipeline/28052026_Erlangen_Official/solver/work/field_dataset_staged/sweep1000/pairs_all.csv`
- Columns: `a, b, master, dist_mm, quality_percent, raw_mm, ok, fail`
- Directed convention: `master -> other endpoint using columns a,b,master`
- Rows: `56000`, directed links: `56`

## Required Readouts

1. BASELINE sanity: rigid total RMS median = `7.573 mm`; pass_lt_5mm = `False`.
2. E3 validation: similarity scale median = `0.939916`; expected near `1.04`; flag = `True`.
3. COMBINED_REAL vs real Erlangen: combined median rigid total RMS = `131.060 mm`; real target RMS = `105.420 mm`; ratio = `1.243`.
4. Verdict: E3 rigid total RMS = `129.965 mm`; E4_REAL rigid total RMS = `24.279 mm`; E3/E4 ratio = `5.353`.
5. E2 readout: breaking_k = `None`; curve = `{1: 14.644006415451276, 2: 20.152221318400816, 4: 8.916426632588413, 8: 39.51103849478692}`.

## Aggregate Case Summary

| Case | n | Rigid total RMS median mm | Similarity total RMS median mm | Similarity scale median | Scale contrib total RMS median mm |
|---|---:|---:|---:|---:|---:|
| BASELINE | 1 | 7.573 | 7.573 | 0.999971 | 0.000 |
| COMBINED_REAL | 1 | 131.060 | 53.694 | 0.939818 | 77.366 |
| E1 | 1 | 7.473 | 7.472 | 0.999918 | 0.002 |
| E2_K1 | 1 | 14.644 | 14.596 | 0.999364 | 0.048 |
| E2_K2 | 1 | 20.152 | 20.143 | 1.000321 | 0.009 |
| E2_K4 | 1 | 8.916 | 8.644 | 0.998830 | 0.272 |
| E2_K8 | 1 | 39.511 | 39.207 | 0.997388 | 0.304 |
| E3 | 1 | 129.965 | 51.434 | 0.939916 | 78.531 |
| E4_REAL | 1 | 24.279 | 24.270 | 0.999641 | 0.009 |
| E4_SWEEP_X1 | 1 | 24.279 | 24.270 | 0.999641 | 0.009 |
| E4_SWEEP_X2 | 1 | 47.473 | 47.450 | 0.999212 | 0.023 |
| E4_SWEEP_X4 | 1 | 95.035 | 94.931 | 0.997624 | 0.104 |
| E4_SWEEP_X8 | 1 | 194.604 | 193.870 | 0.990991 | 0.734 |
