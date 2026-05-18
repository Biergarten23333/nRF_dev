# Erlangen OptiTrack + AutoPos Field Commands

This file is a copy-paste command checklist for the Erlangen validation experiment.

Goal:

- Capture a fresh AutoPos inter-anchor sweep.
- Capture UWB Tag sessions synchronized/paired with OptiTrack recordings.
- Capture Static Tag, RotoArm, and Calibration Wand sessions.
- Keep every UWB session directory clearly matched to the OptiTrack file name.

Important:

- Do **not** flash `Master_Anchor` or `Master_Tag` during this experiment.
- `Master_Anchor` SNR: `960148546`.
- `Master_Tag` SNR: `1050070698`.
- Current positioning outputs are repeatability/consistency unless paired with OptiTrack ground truth.

## 0. Enter Repo And Create Session Root

Run from the repo root:

```bash
cd /home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start
```

Use a new session root for the full Erlangen experiment:

```bash
export ERLANGEN_DATE="$(date +%Y%m%d_%H%M%S)"
export SESSION_ROOT="$PWD/SS-TWR/alt-SS-TWR/broadcast/logs/erlangen_optitrack_${ERLANGEN_DATE}"
mkdir -p "$SESSION_ROOT"
echo "$SESSION_ROOT"
```

Enter the broadcast SS-TWR working directory:

```bash
cd /home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/SS-TWR/alt-SS-TWR/broadcast
```

## 1. Set Master Ports

If the port environment file exists:

```bash
cd /home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start
source .protec/biospur_ports.env
cd SS-TWR/alt-SS-TWR/broadcast
```

Check the selected ports:

```bash
echo "BIOSPUR_ANCHOR_PORT=$BIOSPUR_ANCHOR_PORT"
echo "BIOSPUR_TAG_PORT=$BIOSPUR_TAG_PORT"
```

Set SNRs:

```bash
export BIOSPUR_ANCHOR_SNR="960148546"
export BIOSPUR_TAG_SNR="1050070698"
```

If ports are not set, inspect available serial devices:

```bash
ls -l /dev/serial/by-id/ | grep -E 'BioSpur|SEGGER|J-Link'
```

Then set ports manually:

```bash
export BIOSPUR_ANCHOR_PORT="/dev/serial/by-id/REPLACE_WITH_MASTER_ANCHOR_BIOSPUR_PORT"
export BIOSPUR_TAG_PORT="/dev/serial/by-id/REPLACE_WITH_MASTER_TAG_BIOSPUR_PORT"
```

Sanity check: anchor and tag ports must be different.

```bash
test "$BIOSPUR_ANCHOR_PORT" != "$BIOSPUR_TAG_PORT" && echo "OK: ports differ"
```

## 2. BLE Scan Before Experiment

Scan anchors and tags:

```bash
python3 scripts/scan_and_map.py --timeout-s 8 --json | tee "$SESSION_ROOT/scan_before.json"
```

Expected:

- Anchors A-H are visible.
- Static Tag is visible.
- RotoArm tags are visible.
- Wand tags are visible.

## 3. AutoPos Inter-Anchor Sweep

This is the anchor-only calibration dataset.

### 3.1 Quick Sweep Test: 100 Sets

Use this first to verify that the setup works.

```bash
out="$SESSION_ROOT/autopos_sweep100"
mkdir -p "$out"

python3 scripts/run_autopos_sweep_loop.py \
  --port "$BIOSPUR_ANCHOR_PORT" \
  --order ABCDEFGH \
  --sw-sets 100 \
  --prewarm-sw-sets 0 \
  --timeout-s 2400 \
  --no-final-responder \
  --out-dir "$out" \
  --verbose 0 2>&1 | tee "$out/console.log"
```

Check files:

```bash
find "$out" -maxdepth 3 -type f | sort | tee "$out/file_list.txt"
```

### 3.2 Formal Sweep: 1000 Sets

Use this as the main AutoPos calibration sweep.

```bash
out="$SESSION_ROOT/autopos_sweep1000"
mkdir -p "$out"

python3 scripts/run_autopos_sweep_loop.py \
  --port "$BIOSPUR_ANCHOR_PORT" \
  --order ABCDEFGH \
  --sw-sets 1000 \
  --prewarm-sw-sets 0 \
  --timeout-s 18000 \
  --no-final-responder \
  --out-dir "$out" \
  --verbose 0 2>&1 | tee "$out/console.log"
```

Check files:

```bash
find "$out" -maxdepth 3 -type f | sort | tee "$out/file_list.txt"
```

## 4. Anchor Runtime Responder Preflight

Run before any Tag capture.

```bash
out="$SESSION_ROOT/anchor_preflight_before_tag"
mkdir -p "$out"

python3 scripts/verify_all_anchor_responder_runtime.py \
  --port "$BIOSPUR_ANCHOR_PORT" \
  --command-timeout-s 30 \
  --retry-count 3 \
  --out-dir "$out" \
  --live-output 2>&1 | tee "$out/console.log"
```

Check summary:

```bash
find "$out" -name summary.json -print -exec cat {} \;
```

Expected:

- `success: true`
- 8/8 anchors in runtime responder mode

## 5. Static Tag Captures

Set the static Tag name first.

```bash
export STATIC_TAG="BSF66F"
```

For each OptiTrack static pose:

1. Start OptiTrack recording.
2. Run one UWB capture command below.
3. Stop OptiTrack recording.
4. Record the OptiTrack file name in `session_notes.csv`.

### Static ID01 Template

Change `static_ID01_center_high_ABEF` to match the real pose.

```bash
export UWB_NAME="static_ID01_center_high_ABEF"
export OPTITRACK_FILE="REPLACE_WITH_OPTITRACK_FILE_NAME"

out="$SESSION_ROOT/$UWB_NAME"
mkdir -p "$out"

python3 scripts/run_dual_master_tdma_capture.py \
  --anchor-port "$BIOSPUR_ANCHOR_PORT" \
  --anchor-snr "$BIOSPUR_ANCHOR_SNR" \
  --tag-port "$BIOSPUR_TAG_PORT" \
  --tag-snr "$BIOSPUR_TAG_SNR" \
  --duration 90 \
  --targets "$STATIC_TAG" \
  --profiles "$STATIC_TAG:static" \
  --static-hz 10 \
  --out-dir "$out" 2>&1 | tee "$out/console.log"

echo "$UWB_NAME,static,$OPTITRACK_FILE,$out,static tag pose" >> "$SESSION_ROOT/session_notes.csv"
```

### Recommended Static Poses

Repeat the template above with these names if possible:

```text
static_ID01_center_high_ABEF
static_ID02_center_mid_ABEF
static_ID03_center_low_ABEF
static_ID04_edge_high_BCGF
static_ID05_edge_mid_BCGF
static_ID06_edge_low_BCGF
static_ID07_edge_low_CDHG
static_ID08_edge_mid_CDHG
static_ID09_edge_high_CDHG
static_ID10_center_low_CDHG
static_ID11_center_mid_ADHE
static_ID12_edge_mid_ADHE
```

Minimum if time is short:

```text
static_ID01_center_high_ABEF
static_ID02_center_mid_ABEF
static_ID03_center_low_ABEF
static_ID04_edge_mid_CDHG
static_ID05_edge_low_CDHG
static_ID06_edge_high_CDHG
```

## 6. RotoArm Captures

Set RotoArm tag names.

```bash
export ROTO_TAG_INNER="BS2DCE"
export ROTO_TAG_OUTER="BSDC91"
export ROTO_TARGETS="$ROTO_TAG_INNER,$ROTO_TAG_OUTER"
export ROTO_PROFILES="$ROTO_TAG_INNER:roto,$ROTO_TAG_OUTER:roto"
```

### RotoArm Template

Change `roto_ID01_horizontal_ABEF` to match the real orientation.

```bash
export UWB_NAME="roto_ID01_horizontal_ABEF"
export OPTITRACK_FILE="REPLACE_WITH_OPTITRACK_FILE_NAME"

out="$SESSION_ROOT/$UWB_NAME"
mkdir -p "$out"

python3 scripts/run_dual_master_tdma_capture.py \
  --anchor-port "$BIOSPUR_ANCHOR_PORT" \
  --anchor-snr "$BIOSPUR_ANCHOR_SNR" \
  --tag-port "$BIOSPUR_TAG_PORT" \
  --tag-snr "$BIOSPUR_TAG_SNR" \
  --duration 180 \
  --targets "$ROTO_TARGETS" \
  --profiles "$ROTO_PROFILES" \
  --roto-hz 10 \
  --out-dir "$out" 2>&1 | tee "$out/console.log"

echo "$UWB_NAME,roto,$OPTITRACK_FILE,$out,rotating arm" >> "$SESSION_ROOT/session_notes.csv"
```

### Recommended RotoArm Sessions

```text
roto_ID01_horizontal_ABEF
roto_ID02_horizontal_BCGF
roto_ID03_horizontal_CDHG
roto_ID04_horizontal_ADHE
roto_ID05_tilted_CDHG
```

Minimum if time is short:

```text
roto_ID01_horizontal_ABEF
roto_ID02_horizontal_CDHG
```

## 7. Calibration Wand Captures

Set Wand tag names.

```bash
export WAND_TAG_A="BS9336"
export WAND_TAG_B="BS955A"
export WAND_TAG_C="BSCCF4"
export WAND_TARGETS="$WAND_TAG_A,$WAND_TAG_B,$WAND_TAG_C"
export WAND_STATIC_PROFILES="$WAND_TAG_A:static,$WAND_TAG_B:static,$WAND_TAG_C:static"
export WAND_MOTION_PROFILES="$WAND_TAG_A:motion,$WAND_TAG_B:motion,$WAND_TAG_C:motion"
```

### Static Wand Template

Change `wand_W01_static_pose1`.

```bash
export UWB_NAME="wand_W01_static_pose1"
export OPTITRACK_FILE="REPLACE_WITH_OPTITRACK_FILE_NAME"

out="$SESSION_ROOT/$UWB_NAME"
mkdir -p "$out"

python3 scripts/run_dual_master_tdma_capture.py \
  --anchor-port "$BIOSPUR_ANCHOR_PORT" \
  --anchor-snr "$BIOSPUR_ANCHOR_SNR" \
  --tag-port "$BIOSPUR_TAG_PORT" \
  --tag-snr "$BIOSPUR_TAG_SNR" \
  --duration 90 \
  --targets "$WAND_TARGETS" \
  --profiles "$WAND_STATIC_PROFILES" \
  --static-hz 10 \
  --out-dir "$out" 2>&1 | tee "$out/console.log"

echo "$UWB_NAME,wand_static,$OPTITRACK_FILE,$out,static rigid wand" >> "$SESSION_ROOT/session_notes.csv"
```

Recommended static Wand sessions:

```text
wand_W01_static_pose1
wand_W02_static_pose2
wand_W03_static_pose3
wand_W04_static_pose4
```

### Dynamic Wand Optional

```bash
export UWB_NAME="wand_W05_dynamic_coverage"
export OPTITRACK_FILE="REPLACE_WITH_OPTITRACK_FILE_NAME"

out="$SESSION_ROOT/$UWB_NAME"
mkdir -p "$out"

python3 scripts/run_dual_master_tdma_capture.py \
  --anchor-port "$BIOSPUR_ANCHOR_PORT" \
  --anchor-snr "$BIOSPUR_ANCHOR_SNR" \
  --tag-port "$BIOSPUR_TAG_PORT" \
  --tag-snr "$BIOSPUR_TAG_SNR" \
  --duration 180 \
  --targets "$WAND_TARGETS" \
  --profiles "$WAND_MOTION_PROFILES" \
  --motion-hz 10 \
  --out-dir "$out" 2>&1 | tee "$out/console.log"

echo "$UWB_NAME,wand_dynamic,$OPTITRACK_FILE,$out,dynamic wand coverage" >> "$SESSION_ROOT/session_notes.csv"
```

## 8. Optional 10-Tag Stress Test

Only run this if time allows. Replace all `BSXXXX` names.

```bash
export STRESS_TARGETS="BSXXXX,BSXXXX,BSXXXX,BSXXXX,BSXXXX,BSXXXX,BSXXXX,BSXXXX,BSXXXX,BSXXXX"
export STRESS_PROFILES="BSXXXX:motion,BSXXXX:motion,BSXXXX:motion,BSXXXX:motion,BSXXXX:motion,BSXXXX:motion,BSXXXX:motion,BSXXXX:motion,BSXXXX:motion,BSXXXX:motion"

out="$SESSION_ROOT/stress_10tags_10hz"
mkdir -p "$out"

python3 scripts/run_dual_master_tdma_capture.py \
  --anchor-port "$BIOSPUR_ANCHOR_PORT" \
  --anchor-snr "$BIOSPUR_ANCHOR_SNR" \
  --tag-port "$BIOSPUR_TAG_PORT" \
  --tag-snr "$BIOSPUR_TAG_SNR" \
  --duration 180 \
  --targets "$STRESS_TARGETS" \
  --profiles "$STRESS_PROFILES" \
  --motion-hz 10 \
  --out-dir "$out" 2>&1 | tee "$out/console.log"

echo "stress_10tags_10hz,stress,NA,$out,10 tags at 10 Hz" >> "$SESSION_ROOT/session_notes.csv"
```

## 9. Check Each Capture Immediately

After every capture:

```bash
find "$out" -maxdepth 4 -type f | sort | tee "$out/file_list.txt"
find "$out" -name summary.json -print -exec cat {} \;
find "$out" -name tr_all.csv -print
```

Expected:

- `summary.json`
- `tr_all.csv`
- `console.log`

If no `tr_all.csv` appears, repeat the capture before moving the physical setup.

## 10. Session Notes File

Initialize once:

```bash
cat > "$SESSION_ROOT/session_notes.csv" <<'EOF'
session,type,optitrack_file,uwb_dir,description
EOF
```

After every capture, append one line. Examples:

```bash
echo "static_ID01_center_high_ABEF,static,optitrack_static_001.csv,$SESSION_ROOT/static_ID01_center_high_ABEF,center high facing ABEF" >> "$SESSION_ROOT/session_notes.csv"
echo "roto_ID01_horizontal_ABEF,roto,optitrack_roto_001.csv,$SESSION_ROOT/roto_ID01_horizontal_ABEF,horizontal roto ABEF" >> "$SESSION_ROOT/session_notes.csv"
echo "wand_W01_static_pose1,wand_static,optitrack_wand_001.csv,$SESSION_ROOT/wand_W01_static_pose1,wand static pose 1" >> "$SESSION_ROOT/session_notes.csv"
```

Show notes:

```bash
column -s, -t "$SESSION_ROOT/session_notes.csv" | less -S
```

## 11. Final Pack-Up

Run final scan:

```bash
python3 scripts/scan_and_map.py --timeout-s 8 --json | tee "$SESSION_ROOT/scan_after.json"
```

Create a file inventory:

```bash
find "$SESSION_ROOT" -type f | sort > "$SESSION_ROOT/all_files.txt"
```

Compress the session:

```bash
cd "$(dirname "$SESSION_ROOT")"
tar -czf "$(basename "$SESSION_ROOT").tar.gz" "$(basename "$SESSION_ROOT")"
ls -lh "$(basename "$SESSION_ROOT").tar.gz"
```

## 12. Minimum Experiment If Time Is Short

If the lab time is limited, collect at least:

```text
1. autopos_sweep1000
2. static_ID01_center_high_ABEF
3. static_ID02_center_mid_ABEF
4. static_ID03_center_low_ABEF
5. static_ID04_edge_mid_CDHG
6. static_ID05_edge_low_CDHG
7. roto_ID01_horizontal_ABEF
8. roto_ID02_horizontal_CDHG
9. wand_W01_static_pose1
10. wand_W02_static_pose2
```

This is enough for a first OptiTrack absolute validation pass.

