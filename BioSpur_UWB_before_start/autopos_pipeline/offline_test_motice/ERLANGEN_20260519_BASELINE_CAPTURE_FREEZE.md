# Erlangen 2026-05-19 Baseline Capture Freeze

This file records the latest working Erlangen baseline commands and the validated
capture outputs. Keep the names exactly as written here so the files can be found
quickly during the OptiTrack session.

## 0. Session Root

```text
/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/autopos_pipeline/offline_test_motice/erlangen_20260519_110221
```

Broadcast timing baseline:

```text
tail900 start5
A 1200 us, B 2200 us, C 3200 us, D 4200 us, E 5200 us, F 6100 us, G 7000 us, H 7900 us
```

Master ports used:

```bash
export BIOSPUR_ANCHOR_PORT="/dev/serial/by-id/usb-BioSpur_BioSpur_BLE_Control_87EA2F4A526C5A02-if00"
export BIOSPUR_TAG_PORT="/dev/serial/by-id/usb-Master_Tag_BioSpur_BLE_Control_6918E0384172A49F-if00"
export BIOSPUR_ANCHOR_SNR="960148546"
export BIOSPUR_TAG_SNR="1050070698"
export H_UUID="B1E487C2B1FD740D1442206A1857DFA1"
```

Important: current capture is TR-only. Do not use old command logic that separates
`static`, `roto`, and `motion` profiles. Use `--targets ... --tr-hz 10`.

## 1. AutoPos Sweep 1000 + Prewarm 10

Validated output:

```text
autopos_sweep1000_prewarm10_us30/sweep1000
```

Summary:

```text
summary.json: success true
order: ABCDEFGH
requested formal SW sets per round: 1000
device SW sets per round: 1010
prewarm setting: 10
final responder restore: success true, sent=8 ready=8/8
```

Command:

```bash
cd /home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/SS-TWR/alt-SS-TWR/broadcast

export SESSION_ROOT="/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/autopos_pipeline/offline_test_motice/erlangen_20260519_110221"

out="$SESSION_ROOT/autopos_sweep1000_prewarm10_us30"
mkdir -p "$out"

python3 scripts/run_autopos_sweep_loop.py \
  --port "$BIOSPUR_ANCHOR_PORT" \
  --order ABCDEFGH \
  --sw-sets 1000 \
  --prewarm-sw-sets 10 \
  --round-retries 1 \
  --out-dir "$out/sweep1000" \
  --verbose 1 2>&1 | tee "$out/sweep1000.console.log"
```

Note: this folder name includes `us30`, but no `ultrasound_H.csv` was found in
this session folder. If the H ultrasound value needs to be archived for the
OptiTrack run, repeat a standalone US30 capture and save it under an explicit
folder name such as:

```text
H_US30_after_sweep1000_YYYYMMDD_HHMMSS/ultrasound_H.csv
```

## 2. BSF66F 120 s Capture

Use this successful folder:

```text
BSF66F_120s_20260519_113311
```

Do not use this failed old-profile folder:

```text
BSF66F_static_120s_20260519_111929
```

The failed old folder only failed because the old command passed deprecated
`--profiles/--static-hz` arguments to the lower-level capture script.

Validated files:

```text
BSF66F_120s_20260519_113311/tag_capture_20260519_113401/raw.log
BSF66F_120s_20260519_113311/tag_capture_20260519_113401/tr_all.csv
BSF66F_120s_20260519_113311/tag_capture_20260519_113401/summary.json
```

Result:

```text
success: true
target: BSF66F
duration: 120 s
TR rows: 9608
valid TR rows: 9540
sweeps_total: 1201
>=7 anchors: 1201 / 1201 = 100.00%
8/8 anchors: 1133 / 1201 = 94.34%
valid count distribution: 7:68, 8:1133
US residual in raw log: none
```

Command:

```bash
out="$SESSION_ROOT/BSF66F_120s"
mkdir -p "$out"

python3 scripts/run_dual_master_tdma_capture.py \
  --anchor-port "$BIOSPUR_ANCHOR_PORT" \
  --anchor-snr "$BIOSPUR_ANCHOR_SNR" \
  --tag-port "$BIOSPUR_TAG_PORT" \
  --tag-snr "$BIOSPUR_TAG_SNR" \
  --duration 120 \
  --targets "BSF66F" \
  --tr-hz 10 \
  --out-dir "$out" 2>&1 | tee "$out/console.log"
```

## 3. Roto 2-Tag 120 s Capture

Use this successful folder:

```text
roto_BS2DCE_BSDC91_120s_20260519_114009
```

Validated files:

```text
roto_BS2DCE_BSDC91_120s_20260519_114009/tag_capture_20260519_114057/raw.log
roto_BS2DCE_BSDC91_120s_20260519_114009/tag_capture_20260519_114057/tr_all.csv
roto_BS2DCE_BSDC91_120s_20260519_114009/tag_capture_20260519_114057/summary.json
```

Result:

```text
success: true
targets: BS2DCE, BSDC91
duration: 120 s
TR rows: 19200
valid TR rows: 19045
sweeps_total: 2400
>=7 anchors: 2398 / 2400 = 99.92%
8/8 anchors: 2247 / 2400 = 93.63%
valid count distribution: 6:2, 7:151, 8:2247
US residual in raw log: none
```

Per Tag:

```text
BS2DCE: sweeps 1201, >=7 99.92%, 8/8 94.09%, distribution 6:1, 7:70, 8:1130
BSDC91: sweeps 1199, >=7 99.92%, 8/8 93.16%, distribution 6:1, 7:81, 8:1117
```

Command:

```bash
out="$SESSION_ROOT/roto_BS2DCE_BSDC91_120s"
mkdir -p "$out"

python3 scripts/run_dual_master_tdma_capture.py \
  --anchor-port "$BIOSPUR_ANCHOR_PORT" \
  --anchor-snr "$BIOSPUR_ANCHOR_SNR" \
  --tag-port "$BIOSPUR_TAG_PORT" \
  --tag-snr "$BIOSPUR_TAG_SNR" \
  --duration 120 \
  --targets "BS2DCE,BSDC91" \
  --tr-hz 10 \
  --out-dir "$out" 2>&1 | tee "$out/console.log"
```

## 4. Wand 3-Tag 120 s Capture

Use this successful folder:

```text
wand3_BS9336_BS955A_BSCCF4_120s_20260519_114436
```

Validated files:

```text
wand3_BS9336_BS955A_BSCCF4_120s_20260519_114436/tag_capture_20260519_114525/raw.log
wand3_BS9336_BS955A_BSCCF4_120s_20260519_114436/tag_capture_20260519_114525/tr_all.csv
wand3_BS9336_BS955A_BSCCF4_120s_20260519_114436/tag_capture_20260519_114525/summary.json
```

Result:

```text
success: true
targets: BS9336, BS955A, BSCCF4
duration: 120 s
TR rows: 28816
valid TR rows: 28616
sweeps_total: 3602
>=7 anchors: 3599 / 3602 = 99.92%
8/8 anchors: 3405 / 3602 = 94.53%
valid count distribution: 6:3, 7:194, 8:3405
US residual in raw log: none
```

Per Tag:

```text
BS9336: sweeps 1200, >=7 99.92%, 8/8 96.83%, distribution 6:1, 7:37, 8:1162
BS955A: sweeps 1201, >=7 100.00%, 8/8 94.00%, distribution 7:72, 8:1129
BSCCF4: sweeps 1201, >=7 99.83%, 8/8 92.76%, distribution 6:2, 7:85, 8:1114
```

Command:

```bash
out="$SESSION_ROOT/wand3_BS9336_BS955A_BSCCF4_120s"
mkdir -p "$out"

python3 scripts/run_dual_master_tdma_capture.py \
  --anchor-port "$BIOSPUR_ANCHOR_PORT" \
  --anchor-snr "$BIOSPUR_ANCHOR_SNR" \
  --tag-port "$BIOSPUR_TAG_PORT" \
  --tag-snr "$BIOSPUR_TAG_SNR" \
  --duration 120 \
  --targets "BS9336,BS955A,BSCCF4" \
  --tr-hz 10 \
  --out-dir "$out" 2>&1 | tee "$out/console.log"
```

## 5. Quick Check Commands

Show all summaries:

```bash
find "$SESSION_ROOT" -name summary.json -print -exec cat {} \;
```

Check for ultrasound contamination in normal Tag captures:

```bash
rg -n "US;|USON|USOFF|DIST;|ULTRASOUND" \
  "$SESSION_ROOT/BSF66F_120s_20260519_113311" \
  "$SESSION_ROOT/roto_BS2DCE_BSDC91_120s_20260519_114009" \
  "$SESSION_ROOT/wand3_BS9336_BS955A_BSCCF4_120s_20260519_114436"
```

Expected result:

```text
no matches
```

