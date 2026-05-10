# CaliWand Mode

Calibration Wand mode is the host-side 3-Tag high-cadence capture preset for
wand data collection.

Canonical rule: the Wand Tags run normal broadcast SS-TWR Tag firmware. The
special behavior is in the `Master_Tag` / script layer, where ordinary Tags are
filtered out and only the three Wand Tags are admitted into the TDMA roster.

## Purpose

- Use exactly three Wand Tag BLE identities:
  `Wand-A-BSCCF4`, `Wand-B-BS9336`, and `Wand-C-BS955A`.
- If the boards are still on an old image, use their old `BSCCF4`,
  `BS9336`, and `BS955A` names for OTA only; after the role-prefix image boots,
  use the `Wand-X-BSxxxx` names.
- Reject ordinary `BS*` Tags from the `Master_Tag` scan path by installing the
  Wand-only TDMA roster before scan.
- Configure all three Wand Tags as normal TR/range Tags.
- Use the mature b65 broadcast TR-only timing:
  `guard=1200us`, `resp_spacing=1000us`, `slot_period=10ms`,
  `slot_active=9ms`.
- Request `30Hz` per Wand Tag. With three Wand Tags this is `90Hz`
  aggregate Tag cadence, below the already validated `10Tag x 10Hz = 100Hz`
  system baseline.
- Expected TR volume at full 8-anchor visibility:
  `3 Tags x 30Hz x 8 anchors = 720 TR rows/s`.
- Output normal `TR` records for the offline solver.
- Do not depend on TS/CX/CAL_STATIC/CAL_ROTO output.

## Command

```bash
cd /home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start

python3 scripts/run_caliwand_capture.py \
  --targets Wand-A-BSCCF4,Wand-B-BS9336,Wand-C-BS955A \
  --duration 120
```

Optional listener:

```bash
python3 scripts/run_caliwand_capture.py \
  --targets Wand-A-BSCCF4,Wand-B-BS9336,Wand-C-BS955A \
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
  --wand-targets Wand-A-BSCCF4,Wand-B-BS9336,Wand-C-BS955A
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
  --wand-targets Wand-A-BSCCF4,Wand-B-BS9336,Wand-C-BS955A

# Run sweep/solve and capture, but do not push layout.
python3 scripts/run_autopos_then_caliwand.py \
  --skip-push \
  --wand-targets Wand-A-BSCCF4,Wand-B-BS9336,Wand-C-BS955A

# Run only AutoPos sweep/solve and APOS push/verify.
python3 scripts/run_autopos_then_caliwand.py \
  --skip-caliwand \
  --wand-targets Wand-A-BSCCF4,Wand-B-BS9336,Wand-C-BS955A
```

## Wand Internal Sweep Prototype

Direct Tag-to-Tag internal sweep is not the production Wand Calibration path.
The current supported check is host-side: collect normal TR-derived positions
and compare the inferred Wand geometry.

```bash
python3 scripts/run_wand_internal_sweep.py \
  --targets A:Wand-A-BSCCF4,B:Wand-B-BS9336,C:Wand-C-BS955A \
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
  --targets A:Wand-A-BSCCF4,B:Wand-B-BS9336,C:Wand-C-BS955A \
  --duration 120 \
  --truth AB=500,AC=500,BC=700
```

Analyze an existing capture instead of collecting again:

```bash
python3 scripts/run_wand_internal_sweep.py \
  --targets A:Wand-A-BSCCF4,B:Wand-B-BS9336,C:Wand-C-BS955A \
  --positions-csv <path/to/positions_all.csv> \
  --truth AB=500,AC=500,BC=700
```

## Notes

- Current known Wand Tags are `Wand-A-BSCCF4`, `Wand-B-BS9336`, and
  `Wand-C-BS955A`.
- Current verified Wand firmware marker is
  `alt-bcast-b65-tr3-ledpos-tronly-g1200-r1000-wand-roleprefix-20260509`.
- The wrapper uses `Master_Anchor` for anchor preflight and `Master_Tag` for Tag capture.
- It does not flash firmware.
- It does not require the startup CM probe, because the wand Tags may not include the old fixed static reference Tag.
- If old Tags are powered nearby, the roster allow-list should prevent them from joining the TDMA schedule. The raw log still records any rejected candidates for audit.
- Do not evaluate Wand Calibration with the old `8Hz` preset. The current
  Wand target is `30Hz/tag`; `10Hz/tag` is only the ordinary multi-Tag floor.
