# 180° Antenna Flip Experiment

A/B test: is DWM1001C PCB-antenna directionality a per-anchor range-bias source?
Wand on a mic stand, T-plane vertical, room center. Capture 120 s, flip the whole
wand 180°, capture 120 s. Tags **9336 & 955A** share antenna orientation; **CCF4**
is mounted 180° opposite — so if directionality is real, CCF4's per-anchor deltas
should *invert* relative to its siblings.

## How the 3 tags reach the host

The wand tags are **not** directly connectable over BLE from the PC — the B120
master board is the BLE central and holds a NUS link to each tag inside its TDMA
scheduler, forwarding every tag's per-anchor `TR;` report over its own USB serial
console (`[RECV] BSxxxx notify: TR;...`). That master serial stream *is* the BLE
pipeline. `capture.py` observes it (read-only) and times the flip; it does not
reconfigure the rig.

## Run

1. Make sure a wand session is streaming all 3 tags. If not, start one first
   (any of your usual wand launchers), e.g.:

   ```bash
   python3 scripts_reserve_nomore_change/run_recv_tdma_capture.py \
       --caliwand-mode --targets BS9336,BS955A,BSCCF4 \
       --duration 400 --skip-anchor-preflight --reuse-tag-links
   ```

2. Capture (auto-detects the master port; `--port` to override):

   ```bash
   python3 experiments/antenna_flip_180/capture.py
   ```

   It runs a 15 s preflight (confirms all 3 tags stream TR), waits for ENTER, then:
   `PHASE 1 (120 s, hold still)` → `TURN 180° NOW (20 s buffer)` → `PHASE 2 (120 s)`.
   Follow the on-screen wall-facing prompts for each phase.

3. Analyze:

   ```bash
   python3 experiments/antenna_flip_180/analyze.py
   ```

## Outputs (this directory)

| file | written by | contents |
|---|---|---|
| `raw_tr.log` | capture | every TR line, `elapsed_s \t wall_iso \t line` |
| `metadata.json` | capture | phase offsets, orientations, port, per-tag TR counts |
| `REPORT.md` | analyze | delta table + Q1–Q4 + caliper prediction + verdict |
| `results.json` | analyze | all numbers, machine-readable |

## Reading the verdict

- **Q1** anti-correlation `corr(Δ_9336, −Δ_CCF4)` > 0.7 → directionality signature.
- **Q2** `RMS(Δ)` > 30 mm → major contributor; < 10 mm → not the main issue.
- **Confound (important):** a 180° flip also *translates* any off-pivot tag, which
  injects a real geometric range change on the same axis. The report solves each
  tag's per-phase position (displacement) and reports a CCF4-specific *relative*
  delta that cancels the common rigid-body translation — that residual, not the raw
  Δ, is the clean antenna signal. A positive Q1 alone is necessary but not sufficient.
