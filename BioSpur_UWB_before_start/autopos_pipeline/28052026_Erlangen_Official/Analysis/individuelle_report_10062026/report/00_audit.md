# Phase 0 Data Audit

- Generated: `2026-06-09T22:52:22`
- Data dir: `/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/autopos_pipeline/28052026_Erlangen_Official`
- Capture root: `captures/erlangen_20260528_optitrack`
- Vicon capture root: `opti_captures`
- Workspace name: `28052026_Erlangen_Official`

## Inventory

| type | rows | first_timestamp | last_timestamp | nonzero_rc_rows |
| --- | --- | --- | --- | --- |
| roto | 18 | 2026-05-28T12:49:43+02:00 | 2026-05-28T13:53:38+02:00 | 0 |
| static | 24 | 2026-05-28T11:09:20+02:00 | 2026-05-28T12:40:08+02:00 | 0 |
| sweep | 1 | 2026-05-28T10:59:20+02:00 | 2026-05-28T10:59:20+02:00 | 0 |
| us30 | 1 | 2026-05-28T14:47:03+02:00 | 2026-05-28T14:47:03+02:00 | 0 |
| wand3 | 4 | 2026-05-28T14:01:50+02:00 | 2026-05-28T14:22:26+02:00 | 1 |


| dataset | captures_found | usable_files | rows | source |
| --- | --- | --- | --- | --- |
| sweep | 1 | 1 | 56000 | solver/work/field_dataset_staged/sweep1000/pairs_all.csv |
| static tag | 24 | 24 | 230544 | captures/*/tag_capture_*/tr_all.csv |
| roto tag | 18 | 18 | 345696 | captures/*/tag_capture_*/tr_all.csv |
| Vicon raw full | 43 | 43 |  | opti_captures/full/*.csv |
| Vicon raw static | 24 | 24 |  | opti_captures/static/*.trc |


## Schemas

### Sweep Pairs (`solver/work/field_dataset_staged/sweep1000/pairs_all.csv`)

| column | dtype | non_null | nulls | sample |
| --- | --- | --- | --- | --- |
| a | str | 56000 | 0 | A |
| b | str | 56000 | 0 | B |
| master | str | 56000 | 0 | A |
| dist_mm | int64 | 56000 | 0 | 2889 |
| quality_percent | int64 | 56000 | 0 | 100 |
| raw_mm | int64 | 56000 | 0 | 2889 |
| ok | int64 | 56000 | 0 | 1 |
| fail | int64 | 56000 | 0 | 0 |
| initiator | str | 56000 | 0 | A |
| responder | str | 56000 | 0 | B |
| valid | bool | 56000 | 0 | True |


### Static Tag Ranges (`captures/erlangen_20260528_optitrack/static_ID01_BSF66F_120s_20260528_110629/tag_capture_20260528_110630/tr_all.csv` example)

| column | dtype | non_null | nulls | sample |
| --- | --- | --- | --- | --- |
| capture_kind | str | 230544 | 0 | static |
| capture_id | str | 230544 | 0 | ID01 |
| host_elapsed_s | float64 | 230544 | 0 | 8.6e-05 |
| host_epoch_s | float64 | 230544 | 0 | 1779959236.226124 |
| sweep | int64 | 230544 | 0 | 2833 |
| conn_id | float64 | 0 | 230544 |  |
| peer_name | str | 230544 | 0 | BSF66F |
| tag_id | float64 | 0 | 230544 |  |
| plan | str | 230544 | 0 | f |
| pmode | int64 | 230544 | 0 | 0 |
| anchor_id | int64 | 230544 | 0 | 0 |
| raw_mm | int64 | 230544 | 0 | 1523 |
| range_mm | int64 | 230544 | 0 | 1523 |
| quality_percent | int64 | 230544 | 0 | 100 |
| valid | int64 | 230544 | 0 | 1 |
| status | str | 230544 | 0 | O |
| quality_flag_percent | int64 | 230544 | 0 | 0 |
| first_to_last_us | int64 | 230544 | 0 | 0 |
| frame_us | int64 | 230544 | 0 | 0 |
| poll_count | int64 | 230544 | 0 | 0 |
| tr_version | int64 | 230544 | 0 | 2 |
| rx_mask | float64 | 0 | 230544 |  |
| air_us | float64 | 0 | 230544 |  |
| post_us | float64 | 0 | 230544 |  |
| cycle_us | float64 | 0 | 230544 |  |
| rx_seen | float64 | 0 | 230544 |  |
| imu_valid | int64 | 230544 | 0 | 0 |
| imu_n | int64 | 230544 | 0 | 0 |
| acc_norm_mean_mg | float64 | 0 | 230544 |  |
| acc_norm_std_mg | float64 | 0 | 230544 |  |
| acc_norm_min_mg | float64 | 0 | 230544 |  |
| acc_norm_max_mg | float64 | 0 | 230544 |  |
| imu_skip_count | int64 | 230544 | 0 | 0 |


### Roto Tag Ranges (`captures/erlangen_20260528_optitrack/roto_R01-Static-middle-test_BS2DCE_BSDC91_120s_20260528_124654/tag_capture_20260528_124654/tr_all.csv` example)

| column | dtype | non_null | nulls | sample |
| --- | --- | --- | --- | --- |
| capture_kind | str | 345696 | 0 | roto |
| capture_id | str | 345696 | 0 | R01-Static-middle-test |
| host_elapsed_s | float64 | 345696 | 0 | 0.002222 |
| host_epoch_s | float64 | 345696 | 0 | 1779965258.248267 |
| sweep | int64 | 345696 | 0 | 46 |
| conn_id | float64 | 0 | 345696 |  |
| peer_name | str | 345696 | 0 | BSDC91 |
| tag_id | float64 | 0 | 345696 |  |
| plan | str | 345696 | 0 | f |
| pmode | int64 | 345696 | 0 | 0 |
| anchor_id | int64 | 345696 | 0 | 0 |
| raw_mm | int64 | 345696 | 0 | 1615 |
| range_mm | int64 | 345696 | 0 | 1615 |
| quality_percent | int64 | 345696 | 0 | 100 |
| valid | int64 | 345696 | 0 | 1 |
| status | str | 345696 | 0 | O |
| quality_flag_percent | int64 | 345696 | 0 | 0 |
| first_to_last_us | int64 | 345696 | 0 | 0 |
| frame_us | int64 | 345696 | 0 | 0 |
| poll_count | int64 | 345696 | 0 | 0 |
| tr_version | int64 | 345696 | 0 | 2 |
| rx_mask | float64 | 0 | 345696 |  |
| air_us | float64 | 0 | 345696 |  |
| post_us | float64 | 0 | 345696 |  |
| cycle_us | float64 | 0 | 345696 |  |
| rx_seen | float64 | 0 | 345696 |  |
| imu_valid | int64 | 345696 | 0 | 0 |
| imu_n | int64 | 345696 | 0 | 0 |
| acc_norm_mean_mg | float64 | 0 | 345696 |  |
| acc_norm_std_mg | float64 | 0 | 345696 |  |
| acc_norm_min_mg | float64 | 0 | 345696 |  |
| acc_norm_max_mg | float64 | 0 | 345696 |  |
| imu_skip_count | int64 | 345696 | 0 | 0 |


### Vicon Raw Exports

| path | units | first_line | markers_in_header_sample | frame_rows | first_data_line |
| --- | --- | --- | --- | --- | --- |
| opti_captures/static/ID01.trc | mm | PathFileType | 57 | 957 | 6 |
| opti_captures/full/ID01.csv | mm | ﻿Model Outputs | 19 | 1914 | 6 |
| opti_captures/full/R01.csv | mm | ﻿Model Outputs | 17 | 45988 | 6 |


### Derived Vicon Anchor Table

- Source: `Analysis/official_extra_analysis/FULL/tables/opti_anchor_medians_by_file.csv`
- Rows: `192`
| column | dtype | non_null | nulls | sample |
| --- | --- | --- | --- | --- |
| file_id | str | 192 | 0 | ID01 |
| anchor | str | 192 | 0 | A |
| marker | str | 192 | 0 | Aantenna |
| x_mm | float64 | 192 | 0 | -1074.6176 |
| y_vertical_mm | float64 | 192 | 0 | 248.17326 |
| z_mm | float64 | 192 | 0 | -1624.3124 |
| std_x_mm | float64 | 192 | 0 | 0.2184135078827409 |
| std_y_vertical_mm | float64 | 192 | 0 | 0.0783661603893195 |
| std_z_mm | float64 | 192 | 0 | 0.0755587027306212 |
| std_3d_mm | float64 | 192 | 0 | 0.2440385893217363 |
| n_valid | int64 | 192 | 0 | 957 |


### Derived Static Tag Truth Table

- Source: `Analysis/official_extra_analysis/FULL/tables/tag_ground_truth_correction_summary.csv`
- Rows: `24`
| column | dtype | non_null | nulls | sample |
| --- | --- | --- | --- | --- |
| ID | str | 24 | 0 | ID01 |
| tag_truth_source | str | 24 | 0 | reconstructed_from_relabelled_balls |
| tag_truth_corrected | bool | 24 | 0 | True |
| tag_truth_permutation | str | 24 | 0 | 0,1,4,2,3 |
| motive_iantenna_x_mm | float64 | 24 | 0 | -503.61191 |
| motive_iantenna_y_vertical_mm | float64 | 24 | 0 | 144.63268 |
| motive_iantenna_z_mm | float64 | 24 | 0 | -381.98318 |
| corrected_iantenna_x_mm | float64 | 24 | 0 | -542.215344349341 |
| corrected_iantenna_y_vertical_mm | float64 | 24 | 0 | 182.54280058407932 |
| corrected_iantenna_z_mm | float64 | 24 | 0 | -383.3240609304319 |
| tag_truth_shift_from_motive_mm | float64 | 24 | 0 | 54.12208743140742 |
| motive_icenter_to_iantenna_mm | float64 | 24 | 0 | 39.00085560859404 |
| corrected_icenter_to_iantenna_mm | float64 | 24 | 0 | 38.610478139193695 |
| fingerprint_as_is_max_abs_dev_mm | float64 | 24 | 0 | 29.051378695781 |
| fingerprint_corrected_max_abs_dev_mm | float64 | 24 | 0 | 1.5155498742221385 |
| consensus_reference_id | str | 24 | 0 | ID02 |
| clean_consensus_n | int64 | 24 | 0 | 22 |
| clean_fingerprint_max_spread_mm | float64 | 24 | 0 | 3.010748996927397 |


## Sweep Audit

| rows | valid_rows | directed_links | pairs | range_median_mm | quality_median_percent |
| --- | --- | --- | --- | --- | --- |
| 56000 | 56000 | 56 | 28 | 2877.0 | 100.000 |


Directed valid sample coverage, row = initiator/master, column = responder:

| from\to | A | B | C | D | E | F | G | H |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| A | - | 1000 | 1000 | 1000 | 1000 | 1000 | 1000 | 1000 |
| B | 1000 | - | 1000 | 1000 | 1000 | 1000 | 1000 | 1000 |
| C | 1000 | 1000 | - | 1000 | 1000 | 1000 | 1000 | 1000 |
| D | 1000 | 1000 | 1000 | - | 1000 | 1000 | 1000 | 1000 |
| E | 1000 | 1000 | 1000 | 1000 | - | 1000 | 1000 | 1000 |
| F | 1000 | 1000 | 1000 | 1000 | 1000 | - | 1000 | 1000 |
| G | 1000 | 1000 | 1000 | 1000 | 1000 | 1000 | - | 1000 |
| H | 1000 | 1000 | 1000 | 1000 | 1000 | 1000 | 1000 | - |


Per-pair directed counts:

| pair | dir1_count | dir2_count | median_all_mm | quality_median |
| --- | --- | --- | --- | --- |
| A-B | 1000 | 1000 | 2895.0 | 100.000 |
| A-C | 1000 | 1000 | 3718.0 | 100.000 |
| A-D | 1000 | 1000 | 2429.0 | 100.000 |
| A-E | 1000 | 1000 | 1550.0 | 100.000 |
| A-F | 1000 | 1000 | 3322.0 | 100.000 |
| A-G | 1000 | 1000 | 4020.5 | 100.000 |
| A-H | 1000 | 1000 | 2688.0 | 100.000 |
| B-C | 1000 | 1000 | 2407.0 | 100.000 |
| B-D | 1000 | 1000 | 3670.0 | 100.000 |
| B-E | 1000 | 1000 | 3135.0 | 100.000 |
| B-F | 1000 | 1000 | 1620.0 | 100.000 |
| B-G | 1000 | 1000 | 2745.0 | 100.000 |
| B-H | 1000 | 1000 | 3902.0 | 100.000 |
| C-D | 1000 | 1000 | 2887.0 | 100.000 |
| C-E | 1000 | 1000 | 3885.0 | 100.000 |
| C-F | 1000 | 1000 | 2672.0 | 100.000 |
| C-G | 1000 | 1000 | 1618.0 | 100.000 |
| C-H | 1000 | 1000 | 3347.0 | 100.000 |
| D-E | 1000 | 1000 | 2793.0 | 100.000 |
| D-F | 1000 | 1000 | 3850.0 | 100.000 |
| D-G | 1000 | 1000 | 3160.0 | 100.000 |
| D-H | 1000 | 1000 | 1659.0 | 100.000 |
| E-F | 1000 | 1000 | 2762.0 | 100.000 |
| E-G | 1000 | 1000 | 3711.0 | 100.000 |
| E-H | 1000 | 1000 | 2333.0 | 100.000 |
| F-G | 1000 | 1000 | 2374.0 | 100.000 |
| F-H | 1000 | 1000 | 3660.0 | 100.000 |
| G-H | 1000 | 1000 | 2880.0 | 100.000 |


## Static Tag Audit

| kind | captures | rows | valid_rows | valid_percent | anchors_seen | peers_seen | timestamp_start | timestamp_end | status_counts |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| static | 24 | 230544 | 228265 | 99.011 | 0,1,2,3,4,5,6,7 | BSF66F | 2026-05-28T11:07:16 | 2026-05-28T12:40:03 | O:228265, T:2279 |


Valid sample counts by static position and anchor:

| position | 0 | 1 | 2 | 3 | 4 | 5 | 6 | 7 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| ID01 | 1199 | 1199 | 1197 | 1198 | 1201 | 1192 | 1141 | 1179 |
| ID02 | 1200 | 1201 | 1196 | 1201 | 1201 | 1195 | 1174 | 1187 |
| ID03 | 1200 | 1201 | 1198 | 1201 | 1198 | 1188 | 1156 | 1173 |
| ID04 | 1199 | 1200 | 1198 | 1201 | 1200 | 1178 | 1171 | 1187 |
| ID05 | 1196 | 1197 | 1199 | 1200 | 1200 | 1189 | 1121 | 1193 |
| ID06 | 1198 | 1200 | 1200 | 1200 | 1200 | 1194 | 1152 | 1176 |
| ID07 | 1201 | 1197 | 1200 | 1199 | 1201 | 1193 | 1120 | 1187 |
| ID08 | 1198 | 1201 | 1201 | 1200 | 1201 | 1188 | 1131 | 1171 |
| ID09 | 1194 | 1199 | 1197 | 1200 | 1198 | 1195 | 1161 | 1184 |
| ID10 | 1200 | 1201 | 1201 | 1199 | 1201 | 1173 | 1139 | 1182 |
| ID11 | 1201 | 1199 | 1198 | 1201 | 1201 | 1194 | 1137 | 1179 |
| ID12 | 1199 | 1201 | 1199 | 1201 | 1201 | 1190 | 1136 | 1177 |
| ID13 | 1199 | 1199 | 1198 | 1199 | 1195 | 1192 | 1137 | 1179 |
| ID14 | 1198 | 1199 | 1196 | 1200 | 1199 | 1198 | 1138 | 1182 |
| ID15 | 1199 | 1199 | 1200 | 1201 | 1200 | 1196 | 1158 | 1201 |
| ID16 | 1199 | 1199 | 1199 | 1198 | 1199 | 1176 | 1155 | 1159 |
| ID17 | 1199 | 1198 | 1197 | 1197 | 1198 | 1192 | 1160 | 1159 |
| ID18 | 1192 | 1198 | 1195 | 1199 | 1199 | 1192 | 1154 | 1176 |
| ID19 | 1195 | 1198 | 1200 | 1199 | 1199 | 1190 | 1143 | 1161 |
| ID20 | 1199 | 1198 | 1199 | 1197 | 1201 | 1184 | 1157 | 1170 |
| ID21 | 1198 | 1200 | 1200 | 1201 | 1200 | 1174 | 1150 | 1180 |
| ID22 | 1199 | 1200 | 1196 | 1199 | 1200 | 1194 | 1146 | 1184 |
| ID23 | 1201 | 1200 | 1196 | 1197 | 1199 | 1193 | 1159 | 1188 |
| ID24 | 1197 | 1197 | 1199 | 1201 | 1201 | 1188 | 1146 | 1189 |


Per-position/per-anchor rows are available for Phase 1; first 32 groups:

| capture_id | peer_name | anchor_id | rows | valid_rows | valid_percent |
| --- | --- | --- | --- | --- | --- |
| ID01 | BSF66F | 0 | 1201 | 1199 | 99.833 |
| ID01 | BSF66F | 1 | 1201 | 1199 | 99.833 |
| ID01 | BSF66F | 2 | 1201 | 1197 | 99.667 |
| ID01 | BSF66F | 3 | 1201 | 1198 | 99.750 |
| ID01 | BSF66F | 4 | 1201 | 1201 | 100.000 |
| ID01 | BSF66F | 5 | 1201 | 1192 | 99.251 |
| ID01 | BSF66F | 6 | 1201 | 1141 | 95.004 |
| ID01 | BSF66F | 7 | 1201 | 1179 | 98.168 |
| ID02 | BSF66F | 0 | 1201 | 1200 | 99.917 |
| ID02 | BSF66F | 1 | 1201 | 1201 | 100.000 |
| ID02 | BSF66F | 2 | 1201 | 1196 | 99.584 |
| ID02 | BSF66F | 3 | 1201 | 1201 | 100.000 |
| ID02 | BSF66F | 4 | 1201 | 1201 | 100.000 |
| ID02 | BSF66F | 5 | 1201 | 1195 | 99.500 |
| ID02 | BSF66F | 6 | 1201 | 1174 | 97.752 |
| ID02 | BSF66F | 7 | 1201 | 1187 | 98.834 |
| ID03 | BSF66F | 0 | 1201 | 1200 | 99.917 |
| ID03 | BSF66F | 1 | 1201 | 1201 | 100.000 |
| ID03 | BSF66F | 2 | 1201 | 1198 | 99.750 |
| ID03 | BSF66F | 3 | 1201 | 1201 | 100.000 |
| ID03 | BSF66F | 4 | 1201 | 1198 | 99.750 |
| ID03 | BSF66F | 5 | 1201 | 1188 | 98.918 |
| ID03 | BSF66F | 6 | 1201 | 1156 | 96.253 |
| ID03 | BSF66F | 7 | 1201 | 1173 | 97.669 |
| ID04 | BSF66F | 0 | 1201 | 1199 | 99.833 |
| ID04 | BSF66F | 1 | 1201 | 1200 | 99.917 |
| ID04 | BSF66F | 2 | 1201 | 1198 | 99.750 |
| ID04 | BSF66F | 3 | 1201 | 1201 | 100.000 |
| ID04 | BSF66F | 4 | 1201 | 1200 | 99.917 |
| ID04 | BSF66F | 5 | 1201 | 1178 | 98.085 |
| ID04 | BSF66F | 6 | 1201 | 1171 | 97.502 |
| ID04 | BSF66F | 7 | 1201 | 1187 | 98.834 |


## Roto Audit

| kind | captures | rows | valid_rows | valid_percent | anchors_seen | peers_seen | timestamp_start | timestamp_end | status_counts |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| roto | 18 | 345696 | 343130 | 99.258 | 0,1,2,3,4,5,6,7 | BS2DCE,BSDC91 | 2026-05-28T12:47:38 | 2026-05-28T13:53:33 | O:343130, T:2566 |


Per-capture/per-peer/per-anchor rows, first 48 groups:

| capture_id | peer_name | anchor_id | rows | valid_rows | valid_percent |
| --- | --- | --- | --- | --- | --- |
| R01 | BS2DCE | 0 | 1201 | 1197 | 99.667 |
| R01 | BS2DCE | 1 | 1201 | 1193 | 99.334 |
| R01 | BS2DCE | 2 | 1201 | 1196 | 99.584 |
| R01 | BS2DCE | 3 | 1201 | 1195 | 99.500 |
| R01 | BS2DCE | 4 | 1201 | 1195 | 99.500 |
| R01 | BS2DCE | 5 | 1201 | 1188 | 98.918 |
| R01 | BS2DCE | 6 | 1201 | 1194 | 99.417 |
| R01 | BS2DCE | 7 | 1201 | 1194 | 99.417 |
| R01 | BSDC91 | 0 | 1200 | 1194 | 99.500 |
| R01 | BSDC91 | 1 | 1200 | 1195 | 99.583 |
| R01 | BSDC91 | 2 | 1200 | 1191 | 99.250 |
| R01 | BSDC91 | 3 | 1200 | 1196 | 99.667 |
| R01 | BSDC91 | 4 | 1200 | 1193 | 99.417 |
| R01 | BSDC91 | 5 | 1200 | 1177 | 98.083 |
| R01 | BSDC91 | 6 | 1200 | 1191 | 99.250 |
| R01 | BSDC91 | 7 | 1200 | 1189 | 99.083 |
| R01-Static-middle-test | BS2DCE | 0 | 1200 | 1198 | 99.833 |
| R01-Static-middle-test | BS2DCE | 1 | 1200 | 1191 | 99.250 |
| R01-Static-middle-test | BS2DCE | 2 | 1200 | 1188 | 99.000 |
| R01-Static-middle-test | BS2DCE | 3 | 1200 | 1194 | 99.500 |
| R01-Static-middle-test | BS2DCE | 4 | 1200 | 1193 | 99.417 |
| R01-Static-middle-test | BS2DCE | 5 | 1200 | 1185 | 98.750 |
| R01-Static-middle-test | BS2DCE | 6 | 1200 | 1189 | 99.083 |
| R01-Static-middle-test | BS2DCE | 7 | 1200 | 1189 | 99.083 |
| R01-Static-middle-test | BSDC91 | 0 | 1201 | 1199 | 99.833 |
| R01-Static-middle-test | BSDC91 | 1 | 1201 | 1196 | 99.584 |
| R01-Static-middle-test | BSDC91 | 2 | 1201 | 1192 | 99.251 |
| R01-Static-middle-test | BSDC91 | 3 | 1201 | 1195 | 99.500 |
| R01-Static-middle-test | BSDC91 | 4 | 1201 | 1198 | 99.750 |
| R01-Static-middle-test | BSDC91 | 5 | 1201 | 1188 | 98.918 |
| R01-Static-middle-test | BSDC91 | 6 | 1201 | 1178 | 98.085 |
| R01-Static-middle-test | BSDC91 | 7 | 1201 | 1184 | 98.585 |
| R02 | BS2DCE | 0 | 1201 | 1199 | 99.833 |
| R02 | BS2DCE | 1 | 1201 | 1190 | 99.084 |
| R02 | BS2DCE | 2 | 1201 | 1190 | 99.084 |
| R02 | BS2DCE | 3 | 1201 | 1193 | 99.334 |
| R02 | BS2DCE | 4 | 1201 | 1196 | 99.584 |
| R02 | BS2DCE | 5 | 1201 | 1179 | 98.168 |
| R02 | BS2DCE | 6 | 1201 | 1172 | 97.585 |
| R02 | BS2DCE | 7 | 1201 | 1186 | 98.751 |
| R02 | BSDC91 | 0 | 1200 | 1199 | 99.917 |
| R02 | BSDC91 | 1 | 1200 | 1197 | 99.750 |
| R02 | BSDC91 | 2 | 1200 | 1196 | 99.667 |
| R02 | BSDC91 | 3 | 1200 | 1200 | 100.000 |
| R02 | BSDC91 | 4 | 1200 | 1199 | 99.917 |
| R02 | BSDC91 | 5 | 1200 | 1177 | 98.083 |
| R02 | BSDC91 | 6 | 1200 | 1192 | 99.333 |
| R02 | BSDC91 | 7 | 1200 | 1180 | 98.333 |


## Vicon Truth Availability

Anchor truth medians from derived Vicon table:

| anchor | x_mm | y_vertical_mm | z_mm | files | std_3d_median_mm |
| --- | --- | --- | --- | --- | --- |
| A | -1074.0 | 248.647 | -1624.2 | 24 | 0.184 |
| B | -1320.3 | 270.386 | 1029.3 | 24 | 0.175 |
| C | 851.825 | 222.464 | 1194.2 | 24 | 0.326 |
| D | 1082.7 | 240.933 | -1432.5 | 24 | 0.190 |
| E | -1170.6 | 1648.0 | -1581.5 | 24 | 0.134 |
| F | -1271.6 | 1660.0 | 1099.3 | 24 | 0.695 |
| G | 962.429 | 1624.5 | 1186.9 | 24 | 0.227 |
| H | 1028.0 | 1630.3 | -1552.0 | 24 | 0.091 |


Static tag truth positions found: `24`; corrected/reconstructed positions: `2`.

## Unit Sanity Checks

| dataset | min | median | p95 | max |
| --- | --- | --- | --- | --- |
| sweep dist_mm | 1470.0 | 2877.0 | 3917.0 | 4087.0 |
| static range_mm | 494.000 | 2108.0 | 2839.0 | 3662.0 |
| roto range_mm | 198.000 | 2106.0 | 2622.0 | 4721.0 |


Sweep/Vicon inter-anchor distance ratio over `28` pairs: median `1.0663`, range `1.0295`-`1.1888`. This confirms the sweep numeric field is in millimetres; the ratio is intentionally not used as a correction in Phase 0.

Static tag range/Vicon link-distance ratio over `192` links: median `1.0731`, range `1.0099`-`1.2631`. This is only a coarse unit check; link-level bias modeling belongs to Phase 1.

## Audit Notes / Blockers Before Phase 1

| item | status | note |
| --- | --- | --- |
| Directed sweep | OK | 56 directed links expected for 8 anchors. |
| Static tag positions | OK | 24 static positions expected. |
| Roto captures | OK | Directory includes dynamic captures plus any named static-middle test. |
| CIR/RX power | ABSENT | No CIR or RX-power columns found. quality_percent/quality_flag_percent are present in range logs. |
| Per-sample sweep timestamps | LIMITED | Sweep rows have direction and order via round logs/staged CSV, but no per-sample epoch column. |


Phase 0 stops here. Phase 1 should not run until this audit is reviewed.
