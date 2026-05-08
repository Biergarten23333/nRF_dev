# CaliWand Mode

Calibration Wand mode is a 3-Tag high-cadence capture preset for wand data collection.

## Purpose

- Use exactly three Tag BLE names, for example `BS1111,BS2222,BS3333`.
- Reject other `BS*` Tags from the Master_Tag scan path by installing the TDMA roster before scan.
- Configure all three Tags as `motion` profile.
- Request `8 Hz` per Tag. With the current broadcast TDMA slot period of `40 ms`, total practical UWB cadence is about `25 slot/s`, so three Tags can share about `8.33 Hz` each.
- Write normal `positions_all.csv`, `cm_all.csv`, `cf_all.csv`, per-Tag folders, and an extra `caliwand_summary.json`.

## Command

```bash
cd /home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start

python3 scripts/run_caliwand_capture.py \
  --targets BS1111,BS2222,BS3333 \
  --duration 120
```

Optional listener:

```bash
python3 scripts/run_caliwand_capture.py \
  --targets BS1111,BS2222,BS3333 \
  --duration 120 \
  --with-listener
```

## AutoPos -> CaliWand Pipeline

For the full next-day workflow, use the stage wrapper:

```bash
cd /home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start

python3 scripts/run_autopos_then_caliwand.py \
  --sw-sets 100 \
  --caliwand-duration 120 \
  --wand-targets BSCCF4,BS9336,BS955A
```

This runs:

1. AutoPos inter-anchor sweep and V3-box solve.
2. APOS push + verify through `Master_Tag` to the Wand Tags.
3. CaliWand 3-Tag high-cadence capture.
4. A top-level `pipeline_summary.json` that links the sweep, layout, push verify, and capture summaries.

Useful partial reruns:

```bash
# Reuse an existing layout and only push + capture.
python3 scripts/run_autopos_then_caliwand.py \
  --skip-autopos \
  --layout-json <path/to/anchor_layout_v3_box.json> \
  --wand-targets BSCCF4,BS9336,BS955A

# Run sweep/solve and capture, but do not push layout.
python3 scripts/run_autopos_then_caliwand.py \
  --skip-push \
  --wand-targets BSCCF4,BS9336,BS955A

# Run only AutoPos sweep/solve and APOS push/verify.
python3 scripts/run_autopos_then_caliwand.py \
  --skip-caliwand \
  --wand-targets BSCCF4,BS9336,BS955A
```

## Wand Internal Sweep Prototype

Use this when the three Wand Tags are assembled and you want a quick internal side-length check.

```bash
python3 scripts/run_wand_internal_sweep.py \
  --targets A:BSCCF4,B:BS9336,C:BS955A \
  --duration 120
```

The first implementation is host-side:

- It runs the normal CaliWand 3-Tag capture.
- It reads `positions_all.csv`.
- It time-matches the three Wand Tags by nearest `host_elapsed_s`.
- It outputs AB/AC/BC side-length sequences and statistics.
- It does not yet perform direct Tag-to-Tag UWB ranging in firmware.

After measuring the physical side lengths, pass them in millimeters:

```bash
python3 scripts/run_wand_internal_sweep.py \
  --targets A:BSCCF4,B:BS9336,C:BS955A \
  --duration 120 \
  --truth AB=500,AC=500,BC=700
```

Analyze an existing capture instead of collecting again:

```bash
python3 scripts/run_wand_internal_sweep.py \
  --targets A:BSCCF4,B:BS9336,C:BS955A \
  --positions-csv <path/to/positions_all.csv> \
  --truth AB=500,AC=500,BC=700
```

## Notes

- Replace the three placeholder `BSxxxx` names with the real wand Tag names.
- Current known Wand Tags are `BSCCF4`, `BS9336`, and `BS955A`.
- The wrapper uses `Master_Anchor` for anchor preflight and `Master_Tag` for Tag capture.
- It does not flash firmware.
- It does not require the startup CM probe, because the wand Tags may not include the old fixed static reference Tag.
- If old Tags are powered nearby, the roster allow-list should prevent them from joining the TDMA schedule. The raw log still records any rejected candidates for audit.
