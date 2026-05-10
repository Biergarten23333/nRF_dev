# V4 Layout Push + 3 Old Tag Capture - 2026-05-08

## Inputs
- V4 solve: `autopos_anchor_sweep_100set_after_anchor_powercycle_20260508_204409/solve_v4_interonly_20260508_204409/anchor_layout_v4_interonly_100set_20260508.json`
- APOS candidate: `autopos_anchor_sweep_100set_after_anchor_powercycle_20260508_204409/solve_v4_interonly_20260508_204409/layout_candidate.json`
- APOS push log: `../apos_push_3old_v4_legacy_reliable_20260508_210124/summary.json`

## APOS Push
- BSF66F: 8/8 APOS_OK, commit OK, status OK
- BS2DCE: 8/8 APOS_OK, commit OK, status OK
- BSDC91: 8/8 APOS_OK, commit OK, status OK

## Capture
- Success: `True`
- Duration: `180.0` s
- Session: `logs/v4_layout_3old_tdma180_20260508_210217/tag_capture_20260508_210308`
- Anchor preflight: `{'sent_count': 8, 'ready_count': 8, 'ready_target': 8}`
- Startup CM probe: `True`, ok anchors `[0, 1, 2, 3, 4, 5, 6, 7]`

## Output Counts
- CM rows: `16258`
- CR rows: `21654`
- CF rows: `5380`
- CS rows: `5384`
- TR rows: `0`
- positions rows: `0`

## Per Tag
| Tag | CM | CF | CS | CR | Latest plan | Latest qf | Latest rms | Latest anchors |
|---|---:|---:|---:|---:|---|---:|---:|---|
| BSF66F | 5396 | 1800 | 1800 | 7200 | cal_static | 98 | 0 | A,B,C,D |
| BS2DCE | 5433 | 1789 | 1791 | 7228 | cal_roto | 98 | 61 | A,C,E,F |
| BSDC91 | 5429 | 1791 | 1793 | 7226 | cal_roto | 96 | 22 | A,C,E,F |

## Note
- This run collected calibration-mode CM/CR/CF/CS frames. It did not produce TR or position rows because the selected static/roto profiles map to cal_static/cal_roto output in the current Tag firmware/capture script.
- The data is valid for range-quality/calibration-frame inspection; if pure TR-only offline-solver input is required, rerun with a TR mode/profile instead of cal_static/cal_roto.
