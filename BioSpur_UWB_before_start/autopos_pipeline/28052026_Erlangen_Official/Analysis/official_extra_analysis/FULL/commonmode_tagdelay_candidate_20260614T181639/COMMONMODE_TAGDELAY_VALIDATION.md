# Common-Mode + Tag-Delay Stand-In Validation


- Common-mode layout: `/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/autopos_pipeline/28052026_Erlangen_Official/solver/outputs/v1_to_v4_io_field_check/v4-io-commonmode/layout.json`
- Validation output: `/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/autopos_pipeline/28052026_Erlangen_Official/Analysis/official_extra_analysis/FULL/commonmode_tagdelay_candidate_20260614T181639`
- `91.153` mm is an ORACLE STAND-IN fixed tag-side delay, not a measured calibration.
- Frozen v4-io layout: `/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/autopos_pipeline/28052026_Erlangen_Official/solver/outputs/v1_to_v4_io_field_check/v4-io/layout.json`

## Static Cases

| fixed_tag_delay_mm | label | median_3d_mm | p95_3d_mm | rmse_3d_mm | horiz_med_mm | vert_med_mm | vertical_slope_mm_per_m | vertical_r2 |
| ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 0.000 | port-faithfulness/check | 109.515 | 223.859 | 140.525 | 45.218 | 89.421 | 196.435 | 0.7693 |
| 80.000 | port-faithfulness/check | 60.066 | 131.977 | 74.871 | 32.153 | 45.800 | 15.865 | 0.0258 |
| 95.000 | port-faithfulness/check | 60.739 | 145.775 | 79.525 | 35.491 | 47.807 | -11.591 | 0.0126 |
| 91.153 | ORACLE STAND-IN | 58.591 | 143.213 | 77.889 | 35.630 | 43.817 | -4.688 | 0.0021 |

## Frozen Recheck

- Frozen v4-io/T4/session-mean static median re-run: `72.689` mm.
- P95/RMSE: `171.497` / `109.845` mm.

## Layout Metrics

- Rigid anchor RMSE: `62.992` mm.
- Rigid anchor median/P95: `59.452` / `93.317` mm.
- Sim(3) scale AutoPos-to-Vicon: `1.009782`.

## Pass/Fail

| criterion | actual | target | delta | pass |
| --- | ---: | ---: | ---: | --- |
| tag_delay=0 median near audited 109.515 | 109.515382 | 109.515000 | +0.000382 | True |
| tag_delay=80 median near 60.1 | 60.065611 | 60.100000 | -0.034389 | True |
| tag_delay=95 median near 60.7 | 60.739397 | 60.700000 | +0.039397 | True |
| 91.153 mm ORACLE STAND-IN median beats frozen and lands 58-61 | 58.591334 | 59.500000 | -0.908666 | True |
| 91.153 mm ORACLE STAND-IN vertical slope collapses | -4.687739 | -5.000000 | +0.312261 | True |
| common-mode Sim(3) scale near 1.0 | 1.009782 | 1.000000 | +0.009782 | True |
| common-mode anchor rigid RMSE near 63 mm | 62.991902 | 63.000000 | -0.008098 | True |
