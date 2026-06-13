# BioSpur GR Dataset Collection and ML Plan

This document defines the first complete data collection and machine learning
plan for the BioSpur Gesture Recognition system.

The immediate target is not full 20-DOF hand reconstruction. The first target is
simple and measurable:

- Which fingers moved?
- Is each measured finger open or flexed?
- Can EMG predict the mechanical glove's finger bend signals?

## 1. System Architecture

Keep the EMG and mechanical glove as two independent PC-facing data sources.
Do not merge the mechanical glove into the B120 firmware path for the first
dataset version.

```text
GR module --BLE--> B120 GR-Master --USB CDC--> PC
Mechanical glove ESP32 -----------USB serial--> PC

PC:
  collect EMG stream
  collect mechanical glove stream
  add host_time_ns to every received record
  write raw session files
  align streams offline
  generate labels and ML windows
```

Reasoning:

- B120 already handles BLE central, EMG receive, USB forwarding, and GR module
  OTA. That path should remain focused and stable.
- The mechanical glove data rate is small, but merging it into B120 creates
  unnecessary coupling between EMG streaming, glove acquisition, USB output, and
  OTA state.
- PC-side synchronization is simpler to inspect and debug.

## 2. Repository and Dataset Paths

Keep code in this repository. Keep large raw and processed datasets outside git.

Repository code path:

```text
/home/zekaixiao/Documents/nRF_dev/BioSpur_Gesture_Recognition/
  dataset_pipeline/
    acquire/
    preprocess/
    train/
    configs/
```

Formal dataset root:

```text
/mnt/DatenBankHDD/datasets/BioSpur_GR/
```

Recommended dataset layout:

```text
/mnt/DatenBankHDD/datasets/BioSpur_GR/
  raw/
    subject_zkx/
      2026-06-12_s001/
        manifest.json
        emg_raw.jsonl
        glove_raw.csv
        events.jsonl
        collector.log
  processed/
    v001/
      subject_zkx/
        2026-06-12_s001/
          aligned.parquet
          windows.npz
      split.json
      normalization.json
  models/
    v001_baseline/
    v001_tcn/
  reports/
    v001/
```

The existing repository `captures/` directory is only for bring-up and quick
debug logs. It should not be used as the formal dataset root.

## 3. Device Roles and Ports

### EMG Path

- GR module: nRF52840/B306 BLE peripheral, advertised name like `GRXXXX`.
- B120: `BioSpur-GR` / `GR-Master` BLE central and USB CDC bridge.
- B120 stable USB CDC by-id path:

```text
/dev/serial/by-id/usb-BioSpur-GR_BioSpur-GR_51D4A5716A4C5551-if00
```

Current EMG output:

- ADS1298 channel count: 8 possible channels.
- Current verified channel mask: `0xFF` when all 8 are enabled in firmware.
- Current sample rate: 1000 SPS.
- Current BLE/USB frame bundle: 4 EMG samples per frame.

Not all electrodes need to be physically attached in early datasets. The
session manifest must record which channels were actually connected to the body.

### Mechanical Glove Path

- Device: ACEBOTT ESP32-WROOM-32E mechanical glove.
- USB serial adapter: CH340.
- Normal port: `/dev/ttyUSB0`.
- Normal output rate: 100 Hz.

Current glove CSV fields:

```text
t_us,thumb_adc,index_adc,middle_adc,ring_adc,pinky_adc,
ax_ms2,ay_ms2,az_ms2,gx_rads,gy_rads,gz_rads,temp_c
```

The PC collector must prepend `host_time_ns` to each received glove line.

## 4. Raw File Formats

All raw files are append-only. Never overwrite a completed raw session.

### `manifest.json`

One manifest per session. It records hardware, subject, electrode placement,
glove status, firmware versions, and known problems.

Example:

```json
{
  "dataset_version": "v001",
  "session_id": "2026-06-12_s001",
  "subject_id": "subject_zkx",
  "created_utc": "2026-06-12T13:30:00Z",
  "emg": {
    "enabled": true,
    "sample_rate_sps": 1000,
    "channels_enabled_mask": "0xFF",
    "channels_physically_attached": [1, 2, 3, 4],
    "device_name": "GRAE2E",
    "device_id": "0x2EAE",
    "bridge": "BioSpur-GR",
    "notes": "Only channels 1-4 attached in this session."
  },
  "glove": {
    "enabled": true,
    "sample_rate_hz": 100,
    "port_hint": "/dev/ttyUSB0",
    "signals": ["thumb", "index", "middle", "ring", "pinky", "imu"]
  },
  "collection": {
    "collector": "dataset_pipeline/acquire/collect_session.py",
    "sync_method": "pc_host_time_ns_first_pass"
  },
  "notes": ""
}
```

### `emg_raw.jsonl`

One JSON record per received B120 line or decoded EMG frame.

Minimum raw record:

```json
{
  "host_time_ns": 1781270565423066129,
  "source": "b120",
  "line": "RECV_HEX mcu_ms=17386 type=A seq=112 dev=0x2EAE ts=177952 len=122 samples=4 mask=0xFF rate=1000 data=..."
}
```

Preferred decoded record:

```json
{
  "host_time_ns": 1781270565423066129,
  "source": "b120",
  "type": "A",
  "seq": 112,
  "device_id": "0x2EAE",
  "device_ts": 177952,
  "samples": 4,
  "channel_mask": "0xFF",
  "sample_rate_sps": 1000,
  "data_hex": "AA417000AE2E..."
}
```

### `glove_raw.csv`

The PC collector writes one CSV line per glove serial sample:

```text
host_time_ns,t_us,thumb_adc,index_adc,middle_adc,ring_adc,pinky_adc,ax_ms2,ay_ms2,az_ms2,gx_rads,gy_rads,gz_rads,temp_c
```

### `events.jsonl`

The collector records task prompts and keyboard/manual markers.

Example:

```json
{"host_time_ns": 1781270600000000000, "event": "session_start"}
{"host_time_ns": 1781270610000000000, "event": "trial_start", "trial": 12, "action": "index_flex"}
{"host_time_ns": 1781270612000000000, "event": "phase", "trial": 12, "phase": "rest_pre"}
{"host_time_ns": 1781270614000000000, "event": "phase", "trial": 12, "phase": "move"}
{"host_time_ns": 1781270616000000000, "event": "phase", "trial": 12, "phase": "hold"}
{"host_time_ns": 1781270618000000000, "event": "phase", "trial": 12, "phase": "release"}
{"host_time_ns": 1781270620000000000, "event": "trial_end", "trial": 12, "action": "index_flex"}
{"host_time_ns": 1781270700000000000, "event": "session_end"}
```

## 5. Collection Protocol

Start with simple isolated finger tasks. Do not begin with complex gestures.

Initial action set:

```text
rest
open_hand
fist
thumb_flex
index_flex
middle_flex
ring_flex
pinky_flex
thumb_extend
index_extend
middle_extend
ring_extend
pinky_extend
```

Recommended trial timing:

```text
2 s rest_pre
2 s move
2 s hold
2 s release
2 s rest_post
```

Recommended first dataset volume:

- 20 to 30 trials per isolated finger action.
- 10 to 20 trials for `open_hand` and `fist`.
- Multiple short sessions are better than one very long session, because
  electrode contact and glove mounting will drift over time.

Early session policy:

- It is acceptable if not all 8 EMG channels are physically attached.
- Record the attached channels in `manifest.json`.
- Do not hide bad channels in raw data. Keep them and mark them in metadata.
- If EMG loses power, end the session and mark the session as invalid or partial.

## 6. Synchronization Plan

First-pass synchronization uses PC `host_time_ns`.

Every incoming EMG line, glove line, and event marker gets a host timestamp at
the moment the PC collector receives it.

For v001 this is enough to start:

```text
EMG host_time_ns timeline
Glove host_time_ns timeline
Event host_time_ns timeline
```

Second-pass synchronization should improve timing:

- Use EMG `seq`, `samples`, and `sample_rate_sps` to expand each EMG frame into
  individual 1000 Hz sample times.
- Use glove `t_us` and PC `host_time_ns` to fit a linear clock model for the
  ESP32 glove clock.
- Interpolate glove bend values onto the EMG time base.

The final aligned file should use one common time axis.

## 7. Processed Dataset

The aligned dataset should contain:

```text
time_ns
emg_ch1 ... emg_ch8
thumb_bend index_bend middle_bend ring_bend pinky_bend
thumb_motion index_motion middle_motion ring_motion pinky_motion
thumb_state index_state middle_state ring_state pinky_state
trial_id
action
phase
```

Glove ADC values should be calibrated per session:

```text
bend_norm = (adc - open_adc) / (closed_adc - open_adc)
```

Clamp to `[0.0, 1.0]` after calibration.

Finger motion labels can be derived from glove bend velocity:

```text
motion = abs(d(bend_norm)/dt) > threshold
```

Finger state labels can be derived from bend value:

```text
state_flexed = bend_norm > threshold
```

Keep thresholds in the dataset config, not hardcoded.

## 8. ML Windowing

First EMG-only model input:

```text
X_emg shape: [8, 200]
window length: 200 ms
stride: 20 ms or 50 ms
sample rate: 1000 Hz
```

Optional later input:

```text
X_emg: [8, 200]
X_imu: GR module IMU window, if enabled later
```

Do not use mechanical glove as an input for the main EMG model. Use the glove as
the label source. A separate analysis model can use glove as input for debugging,
but the target model should learn from EMG.

Initial labels:

```text
y_motion: [thumb, index, middle, ring, pinky]  # multi-label 0/1
y_state:  [thumb, index, middle, ring, pinky]  # multi-label 0/1
y_bend:   [thumb, index, middle, ring, pinky]  # continuous 0.0..1.0
```

## 9. Training Plan

### Stage 1: Baseline Features

Extract simple EMG features per channel per window:

- RMS
- MAV
- waveform length
- zero crossing count
- slope sign changes
- variance

Train:

- Logistic Regression or Linear SVM for finger motion/state.
- Random Forest for quick nonlinear baseline.
- Ridge regression or Random Forest regression for bend value.

This stage is useful because it is fast, inspectable, and exposes data problems.

### Stage 2: Small Neural Network

Train a small 1D CNN or TCN on raw EMG windows:

```text
input:  [batch, 8, 200]
output: y_motion logits [batch, 5]
        y_state logits  [batch, 5]
        y_bend          [batch, 5]
```

Recommended losses:

```text
BCEWithLogitsLoss for y_motion
BCEWithLogitsLoss for y_state
SmoothL1Loss or MSELoss for y_bend
```

The first neural model should stay small. The goal is to prove the signal and
labels are usable before trying larger networks.

### Stage 3: Real-Time Inference Candidate

Only after the PC model works:

- Reduce model size.
- Quantize if needed.
- Test latency with streaming windows.
- Decide whether inference belongs on PC, GR module, or B120.

For early research, inference should run on PC.

## 10. Train/Test Split Rules

Do not randomly split individual windows across train and test. That leaks
near-identical neighboring windows into both sets and makes accuracy look better
than it is.

Use session-level splits:

```text
train: sessions 1..N
val: held-out session
test: later held-out session or different day
```

For a more realistic test, use different electrode mounting sessions as the test
set.

## 11. Evaluation Metrics

For `y_motion` and `y_state`:

- per-finger precision
- per-finger recall
- per-finger F1
- macro F1
- confusion between adjacent fingers

For `y_bend`:

- MAE per finger
- RMSE per finger
- correlation per finger

Also report:

- window latency
- dropped EMG frame count
- glove missing sample count
- number of physically attached EMG channels

## 12. Implementation Order

1. Create `dataset_pipeline/` skeleton.
2. Implement `acquire/collect_session.py`.
3. Collect one short valid session with EMG powered and glove streaming.
4. Implement `preprocess/align_session.py`.
5. Implement `preprocess/build_windows.py`.
6. Train baseline feature model.
7. Inspect failure cases.
8. Train small 1D CNN/TCN.
9. Increase dataset size only after the pipeline is stable.

## 13. First Collector Requirements

The first `collect_session.py` should:

- Identify B120 by the stable by-id path.
- Identify mechanical glove by CH340 `/dev/ttyUSB0` or VID/PID.
- Add `host_time_ns` to all received records.
- Send `status` and `rx` to B120 at session start.
- Save B120 raw lines to `emg_raw.jsonl`.
- Save glove CSV with prepended `host_time_ns`.
- Save keyboard/prompt events to `events.jsonl`.
- Write a complete `manifest.json`.
- Cleanly send `disconnect` to B120 on exit.

The first version does not need a GUI.

## 14. Practical Notes

- If the GR module has no power, the collector should still write a session log
  but mark EMG as missing.
- If only some EMG electrodes are attached, record that in the manifest and keep
  all raw channels.
- If the glove is awkward to wear, collect many short sessions rather than one
  long session.
- Keep raw logs even when a session is imperfect. Mark the session quality in
  metadata instead of deleting data.
- Do not use B120 for mechanical glove acquisition in v001.
