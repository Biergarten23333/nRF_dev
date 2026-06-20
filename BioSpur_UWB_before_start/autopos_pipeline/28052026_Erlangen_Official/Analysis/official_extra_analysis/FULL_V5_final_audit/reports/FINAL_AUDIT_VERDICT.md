# Final Audit Verdict

Generated: 2026-06-20T22:14:23

## Files inventoried: 43244
- Raw/schema audit entries: 483 raw/solver/capture CSV/JSON files.
- Processed analysis CSVs schema-audited: 1317.
- CPU workers used: 12; GPU status logged but GPU compute was not applicable to file/schema auditing.

## Columns in tr_all.csv: 31 (6 substantively used before this final audit, 25 previously unused or metadata-only columns checked here)

## Unused columns that MIGHT matter

- None after this audit. Candidate columns (`raw_mm`, IMU aggregates, `sweep`) were explicitly checked here.

## Unused columns that DON'T matter

- sweep: final audit checked early-vs-late drift; no pipeline-changing result.
- conn_id: all-null in static captures.
- peer_name: low-information metadata/constant field after schema audit.
- tag_id: all-null in static captures.
- plan: low-information metadata/constant field after schema audit.
- pmode: low-information metadata/constant field after schema audit.
- raw_mm: audited against range_mm; identical for valid static/all-capture tr_all rows, different only on invalid rows.
- status: low-information metadata/constant field after schema audit.
- quality_flag_percent: constant zero in static captures.
- first_to_last_us: constant zero in static captures.
- frame_us: constant zero in static captures.
- poll_count: constant zero in static captures.
- tr_version: low-information metadata/constant field after schema audit.
- rx_mask: all-null in static captures.
- air_us: all-null in static captures.
- post_us: all-null in static captures.
- cycle_us: all-null in static captures.
- rx_seen: all-null in static captures.
- imu_valid: IMU fields are empty/invalid in static captures.
- imu_n: IMU fields are empty/invalid in static captures.
- acc_norm_mean_mg: IMU fields are empty/invalid in static captures.
- acc_norm_std_mg: IMU fields are empty/invalid in static captures.
- acc_norm_min_mg: IMU fields are empty/invalid in static captures.
- acc_norm_max_mg: IMU fields are empty/invalid in static captures.
- imu_skip_count: IMU fields are empty/invalid in static captures.

## Unreferenced CSVs that contain useful info

- `unreferenced_csvs.txt` rows: 653.
- 304 generated summary/diagnostic CSVs are not referenced by Markdown reports, but the audit found them to be derived outputs rather than new raw data channels.
- See `unreferenced_csv_summary.csv` for every unreferenced CSV and its judgement.

## Anomalies found

| severity | check | scope | affected_rows | detail | matters |
| --- | --- | --- | --- | --- | --- |
| WARN | zero_ranges | all_capture_rows | 1.586e+04 | 15860 rows with range_mm == 0 | Zero ranges are expected only as invalid rows. |

## raw_mm vs range_mm

- Same in valid static captures: mean diff 0.000 mm, std 0.000 mm, nonzero rows 0.
- Invalid static rows differ in 2279 rows; those are `T` status rows excluded by valid filtering.
- Therefore `raw_mm` does not provide an independent unanalysed valid range channel.

## IMU data

- Static captures have no usable IMU signal: `imu_valid` sums to 0, `imu_n` sums to 0, and accelerometer aggregate columns are null.
- Therefore IMU cannot be used as a bump/movement quality indicator for these static captures.

## Sweep structure

- Explored in this audit. Static first-20%-vs-last-20% per-link median drift: max 30.0 mm, P95 8.2 mm.
- Sweep/frame ordering has already been more strongly tested by temporal raw-frame analyses; the final audit does not reveal a new conclusion-changing sweep feature.

## Reported-number consistency

| reported_number | source_contains_value | status | source_csv | notes |
| --- | --- | --- | --- | --- |
| V5 p50 LOO median = 67.8 mm | yes | OK | /home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/autopos_pipeline/28052026_Erlangen_Official/Analysis/official_extra_analysis/FULL_V5/tables/static_summary_DLOO.csv | Primary CSV contains the median. Its P95/RMSE are 153.635/82.799, so later 160.5/86.4 baseline pairs are a reporting variant, not this primary source. |
| V4 p50 LOO median = 57.9 mm | yes | OK | /home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/autopos_pipeline/28052026_Erlangen_Official/Analysis/official_extra_analysis/FULL_transfer_matrix/tables/median_DLOO.csv | Numeric audit records exact/near matches if present; otherwise this value is traceable through generated comparison tables/prose rather than one canonical source row. |
| lower_trim_20 V5 Huber30 LOO median = 44.5 mm | yes | OK | /home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/autopos_pipeline/28052026_Erlangen_Official/Analysis/official_extra_analysis/FULL_V5_rawframe_bruteforce_v3/tables/s3_honest_ranking.csv | Primary CSV contains median, P95 164.135, RMSE 81.537. |
| Sim3 scale V4 = 0.958, V5 = 1.010 | yes | OK | /home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/autopos_pipeline/28052026_Erlangen_Official/Analysis/official_extra_analysis/FULL_V4_vs_V5_final/tables/final_scale_comparison.csv | Primary CSV contains v4-io=0.958267 and v5-commonmode=1.009782. |
| ROTO median = 101.5 mm | yes | OK | /home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/autopos_pipeline/28052026_Erlangen_Official/Analysis/official_extra_analysis/FULL_V4_vs_V5_final/tables/final_v4_vs_v5_roto_comparison.csv | Primary CSV contains median 101.485, P95 214.369, RMSE 126.226. |

- V5 p50 LOO median source is consistent, but the commonly cited companion P95/RMSE pair 160.5/86.4 does not match the primary FULL_V5 D_LOO_CV CSV (153.6/82.8).

## VERDICT: NOT EXHAUSTED

## If NOT EXHAUSTED: exactly what remains and why it matters

No raw data column/file remains scientifically unanalysed in a way that suggests a new pipeline. What remains is a reporting consistency fix: reconcile the V5 p50/D_LOO_CV companion P95/RMSE values (primary FULL_V5 CSV: 153.6/82.8; some later tables/prose cite 160.5/86.4).

This matters because the paper should not mix companion P95/RMSE values from different V5 baseline variants. It does not imply a new raw-data analysis path.
