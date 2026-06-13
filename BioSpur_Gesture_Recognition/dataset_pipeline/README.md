# BioSpur GR Dataset Pipeline

This directory contains PC-side data collection, synchronization, preprocessing,
and model training code for BioSpur Gesture Recognition.

Large datasets should not be stored in this repository. Use:

```text
/mnt/DatenBankHDD/datasets/BioSpur_GR/
```

Planned structure:

```text
dataset_pipeline/
  acquire/
    collect_session.py  # implemented
    ports.py            # implemented
    parse_emg.py        # implemented
    parse_glove.py      # implemented
  preprocess/
    align_session.py
    label_from_glove.py
    build_windows.py
  train/
    train_baseline.py
    train_tcn.py
    models.py
  configs/
    actions_basic.yaml
    dataset_v001.yaml
```

Data flow:

```text
B120 BioSpur-GR USB CDC -> emg_raw.jsonl
Mechanical glove USB serial -> glove_raw.csv
Keyboard/prompt markers -> events.jsonl

raw session -> aligned timeline -> ML windows -> baseline/TCN models
```

Design rule for v001:

- B120 handles EMG BLE receive and GR module OTA only.
- Mechanical glove stays on its own USB serial port.
- The PC collector adds `host_time_ns` to both streams.
- Glove values are labels, not model inputs, for the main EMG model.

See `docs/DATASET_COLLECTION_AND_ML_PLAN.md` for the full plan.

## Current Collector

List visible serial ports:

```sh
python3 dataset_pipeline/acquire/collect_session.py --list-ports
```

Short fixed-duration raw capture:

```sh
python3 dataset_pipeline/acquire/collect_session.py \
  --subject subject_zkx \
  --duration 120 \
  --attached-channels 1,2,3,4 \
  --notes "short free-motion capture"
```

Guided action capture using `configs/actions_basic.yaml`:

```sh
python3 dataset_pipeline/acquire/collect_session.py \
  --subject subject_zkx \
  --guided \
  --actions rest,index_flex,index_extend,middle_flex,middle_extend \
  --trials-per-action 3 \
  --attached-channels 1,2,3,4
```

Output goes to:

```text
/mnt/DatenBankHDD/datasets/BioSpur_GR/raw/<subject>/<session_id>/
  manifest.json
  emg_raw.jsonl
  glove_raw.csv
  events.jsonl
  collector.log
```

Manual marker mode is available when running from an interactive terminal:

```text
trial index_flex
phase move
phase hold
end
event electrode_adjusted
q
```
