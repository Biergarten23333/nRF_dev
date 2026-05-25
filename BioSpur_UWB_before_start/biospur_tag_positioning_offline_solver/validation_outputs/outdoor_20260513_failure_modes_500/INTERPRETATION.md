# Interpretation: 500-Repeat Outdoor Failure-Mode Monte Carlo

Run:

- Dataset: `autopos_pipeline/outdoor_20260513`
- Layout: `FULL-COMPARE-1000/v4-io/layout.json`
- Methods: `T1`, `T2`, `T3`
- Repeats: `500`
- Conditions: MC1 random keep-k, MC2 anchor-specific dropout, MC3 burst dropout, MC4 persistent positive NLOS bias

## Main Result

T3 is useful for dynamic/dropout robustness, especially when runtime anchor redundancy is reduced. It is not a replacement for explicit NLOS detection. Under persistent positive range bias, T3 can improve static repeatability slightly but makes roto turn-center repeatability worse, which means the current T3 temporal/residual memory can preserve smooth motion while still carrying a biased geometry.

## Static Repeatability

Metric: median per-static-capture 3D repeatability, in mm.

| Condition | T1 | T2 | T3 | T3 vs best T1/T2 |
| --- | ---: | ---: | ---: | ---: |
| MC1 keep-8 | 50.3 | 51.8 | 48.6 | +3.5% |
| MC1 keep-7 | 55.4 | 55.9 | 53.2 | +4.0% |
| MC1 keep-6 | 82.5 | 83.5 | 73.8 | +10.5% |
| MC1 keep-5 | 109.4 | 114.0 | 98.0 | +10.4% |
| MC1 keep-4 | 168.1 | 176.1 | 129.3 | +23.1% |
| MC2 H weak | 56.2 | 56.4 | 53.1 | +5.4% |
| MC2 B/E/H weak | 61.0 | 61.2 | 56.0 | +8.2% |
| MC2 upper-tail weak | 60.3 | 60.7 | 55.4 | +8.1% |
| MC3 H burst 1s | 50.7 | 50.9 | 49.3 | +2.7% |
| MC4 NLOS +200mm | 52.9 | 53.0 | 51.4 | +2.7% |
| MC4 NLOS +300mm | 55.8 | 55.8 | 53.1 | +4.9% |

Static repeatability alone under-reports persistent NLOS damage, because a constant biased anchor can shift the whole static point cloud without greatly increasing within-capture scatter. The residual RMS does expose the injected bias: static residual median rises from about 55 mm in clean keep-8 to 66 mm, 87 mm, and 114 mm for +100, +200, and +300 mm NLOS injection.

## Roto Robustness

Metric: median per-track turn-center RMS, in mm.

| Condition | T1 | T2 | T3 | T3 vs best T1/T2 |
| --- | ---: | ---: | ---: | ---: |
| MC1 keep-8 | 22.0 | 22.1 | 22.6 | -2.8% |
| MC1 keep-7 | 31.5 | 32.3 | 29.8 | +5.3% |
| MC1 keep-6 | 52.9 | 54.2 | 42.0 | +20.5% |
| MC1 keep-5 | 91.2 | 92.7 | 58.7 | +35.6% |
| MC1 keep-4 | 139.1 | 142.6 | 82.7 | +40.5% |
| MC2 H weak | 25.1 | 25.3 | 24.1 | +4.2% |
| MC2 B/E/H weak | 29.1 | 29.4 | 26.4 | +9.3% |
| MC2 upper-tail weak | 31.1 | 31.5 | 27.4 | +11.7% |
| MC3 H burst 1s | 19.7 | 19.7 | 20.2 | -2.7% |
| MC4 NLOS +100mm | 20.3 | 20.6 | 21.8 | -7.3% |
| MC4 NLOS +200mm | 21.9 | 22.2 | 24.6 | -12.7% |
| MC4 NLOS +300mm | 24.3 | 24.6 | 28.2 | -15.8% |

T3 gives large gains when anchor count is low: under keep-4, roto turn-center RMS drops from 139.1 mm to 82.7 mm. Under keep-5 it drops from 91.2 mm to 58.7 mm. This is the strongest evidence that T3 is useful for low-redundancy dynamic tracking.

The NLOS result is different. T3 makes roto center repeatability worse under persistent positive bias, even though circle thickness is slightly lower. That means it can smooth a biased trajectory rather than correct the bias. A separate anchor health / NLOS positive-bias detector is needed.

## Practical Conclusion

Keep T3 as the dynamic-stable solver candidate. It improves low-anchor-count and weak-anchor runtime conditions without hard anchor rejection. Do not advertise it as NLOS-robust yet.

Next algorithm work should add an explicit NLOS/anchor-health layer, for example:

- compare residual sign persistence per anchor;
- detect repeated positive residuals rather than only residual magnitude;
- use frame-to-frame anchor health memory separately from the motion prior;
- report an NLOS warning instead of silently smoothing through biased ranges.

## Generated Files

- `mc_summary_by_condition.csv`: compact 500-repeat summary by dataset, condition, and method.
- `mc_condition_repeat_summary.csv`: one row per repeat, condition, method, and dataset.
- `mc_static_capture_detail.csv`: per-static-capture detail rows.
- `mc_roto_track_detail.csv`: per-roto-track detail rows.
- `figures/mc_static_d3_by_condition.png`
- `figures/mc_static_z_by_condition.png`
- `figures/mc_roto_center_by_condition.png`
- `figures/mc_roto_thickness_by_condition.png`
