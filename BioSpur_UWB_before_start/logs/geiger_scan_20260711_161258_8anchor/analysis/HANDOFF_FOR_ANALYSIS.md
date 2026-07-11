# Geiger MODE_SCAN — Analysis Handoff Brief

Prepared for a deeper analysis pass (e.g. Fable 5). Everything below is in-repo under
`/mnt/nrf_ssd/nRF_dev/BioSpur_UWB_before_start/`. Paths are repo-relative.

---

## 0. Context — what this data is

A DWM1001 UWB device ("the Geiger", on-air short address **0xB1C0**, DW1000 radio,
channel 5 / PRF64 / 128-preamble / 6.8 Mbps) was put into a new **MODE_SCAN** and
carried by hand around a room. Each cycle it:

1. **Ranging pass**: broadcasts one Alt-SS-TWR poll; all 8 anchors reply in ranked
   slots; it computes a single-sided-TWR **range (mm) to each of the 8 anchors**.
2. **CIR pass**: a second poll where **one round-robin target anchor** replies first
   (rank 0); it reads that anchor's **full DW1000 CIR accumulator** (1016 complex taps).

Output is one `LSCAN;...` text line per cycle over USB-CDC/VCOM @ 460800.
Effective rate ≈ **5 Hz** (the 8128-char CIR hex dump dominates). ~**94%** of cycles
carry a CIR. The round-robin means each anchor's CIR is sampled ≈ every 8 cycles
(~60 CIR captures per anchor over the 120 s run).

The system is a fixed UWB anchor cage (8 anchors, 2 planes) that normally tracks
3 "wand" tags. In this capture the Geiger acts as a **mobile self-positioning
channel probe** inside that cage.

### Ground-truth operator activity (this capture)
Reported by the operator, in order:
1. Stood roughly in place and **rotated on the spot** for a while (start).
2. **Scanned the space** (walking around, moving the device).
3. Walked the device in a **large circle (~1 m radius) around the wand-tag device**.
4. Near the end, **rotated on the spot again**.
Note: the operator **sometimes raised the device high**, so some solved-z values go
above the anchor ceiling plane — this is expected/legitimate, not an error.

There is **no per-line wall-clock timestamp** in the data. Use cycle index × (120 s / N)
as an approximate time axis (cycles are ~uniform but CIR cycles run slightly longer).

---

## 1. Raw data available

### 1a. Primary capture (USE THIS ONE — full 8-anchor coverage)
`logs/geiger_scan_20260711_161258_8anchor/scan.log`  (~3.5 MB, 517 LSCAN cycles, 120 s)
- 8/8 anchors respond ~78–87% of cycles; avg 6.4–6.7 valid anchors/cycle; 487 full CIRs.

### 1b. Secondary capture (earlier, pre-firmware-fix — only 4 anchors)
`logs/geiger_scan_20260711_153907/scan.log`  (578 cycles). Before a firmware fix, the
ranging pass dropped every odd-rank responder, so only the 4 even anchors (A,C,E,G)
answered reliably (~78%) and odd anchors (B,D,F,H) only ~8%. Useful only as a
before/after contrast or to study the drop mechanism.
(A first attempt `logs/geiger_scan_20260711_153759` is empty — ignore.)

### 1c. Fixed geometry (calibrated, "V4-io")
`logs/system_calibration_20260710_233443/anchor_layout.json` — 8 anchor poses (mm):

| id | label | x | y | z |
|----|-------|------|------|-------|
| 0 | A | 0 | 0 | 0 |
| 1 | B | 4713 | 0 | 0 |
| 2 | C | 4428 | 3032 | 0 |
| 3 | D | 266 | 2853 | -231 |
| 4 | E | 602 | -231 | -1569 |
| 5 | F | 4740 | 94 | -1484 |
| 6 | G | 4485 | 3200 | -1484 |
| 7 | H | 571 | 2553 | -1779 |

Lower plane (z≈0): A,B,C,D. Ceiling plane (z≈-1500, **z negative = up**): E,F,G,H.
Room ≈ 4.7 m (x) × 3.0 m (y) × 1.6 m (z).

### 1d. Wand-tag positions (the thing the operator circled)
`logs/system_calibration_20260710_233443/wand_positions.json` — 3 tags on one physical
wand: BSCCF4 (2718,981,-934), BS9336 (2831,315,-1043), BS955A (2792,956,-443) mm.
Wand **centroid ≈ (2780, 750, -807) mm**.

### 1e. LSCAN line format (exact)
```
LSCAN;src=0xb1c0;a0=<mm>;a1=<mm>;a2=<mm>;a3=<mm>;a4=<mm>;a5=<mm>;a6=<mm>;a7=<mm>;cir_aid=<0-7>;rcph=<u7>;rxtofs=<i19>;ttcki=<u32>;agc=<u5>;cir=<8128 hex chars>
```
- `a0..a7` — SS-TWR range in **mm** to anchor 0..7. **`-1` = no response** this cycle.
- `cir_aid` — which anchor's CIR is on this line (round-robin 0..7).
- `rcph` — RX carrier phase (RX_TTCKO bits 40-46, 7-bit).
- `rxtofs` — RX time-tracking offset (RX_TTCKO RXTOFS, 19-bit **signed**); a clock
  frequency-offset (CFO) proxy vs that anchor.
- `ttcki` — RX time-tracking interval (RX_TTCKI, 32-bit); ~nominal constant (33292288).
- `agc` — AGC_STAT1 EDG1 (5-bit). **⚠ Currently always 0 — a known unresolved bug**
  (register read timing/offset). Treat `agc` as invalid for now.
- `cir` — the DW1000 accumulator for `cir_aid`: **4064 bytes = 1016 complex taps**,
  each tap = int16 I + int16 Q, **little-endian**, hex-encoded → 8128 hex chars.
  Cycles where no anchor answered omit everything from `cir_aid` onward.

### 1f. Loading + CIR decode (python)
```python
import re, json, numpy as np
def load(path):
    rows=[]
    for L in open(path):
        if not L.startswith("LSCAN;"): continue
        rg={int(m.group(1)):int(m.group(2)) for m in re.finditer(r';a(\d)=(-?\d+)',L)}
        cm=re.search(r';cir_aid=(\d+);',L); dm=re.search(r';rcph=(\d+);rxtofs=(-?\d+);ttcki=(\d+);agc=(\d+);',L)
        hm=re.search(r';cir=([0-9A-Fa-f]+)',L)
        cir=None
        if hm and len(hm.group(1))==8128:
            iq=np.frombuffer(bytes.fromhex(hm.group(1)),dtype='<i2').astype(float).reshape(1016,2)
            cir=iq          # complex taps: cir[:,0]=I, cir[:,1]=Q ; |cir|=np.hypot(I,Q)
        rows.append(dict(rng=rg, cir_aid=int(cm.group(1)) if cm else None,
                         diag=tuple(map(int,dm.groups())) if dm else None, cir=cir))
    return rows
```
Tap spacing ≈ **1.0016 ns ≈ 0.3003 m/tap** (DW1000 accumulator sample period, 64 MHz PRF).

### 1g. Critical DW1000 / firmware facts (don't get fooled)
- **The accumulator is windowed to the first path**: the leading edge sits ~tap **750**
  regardless of range. So **CIR tap index does NOT track range** — range comes from the
  RX timestamp (SS-TWR), not the CIR. CIR is for **channel/multipath**, not ranging.
- Ranging math = single-sided TWR with carrier-integrator (CFO) correction, channel-5
  constants. Range clamped ≥ 0.
- The CIR-pass forces the target to rank 0 (earliest slot) → its own range stays
  accurate and reliably captured; other anchors that cycle come from the ranging pass.

---

## 2. Analyses already performed

Scripts (reproducible, in the same dir):
`analyze.py` (basic), `analyze_wand.py` (wand loop), **`analyze_full.py` (main suite)**.
Figures produced (in `analysis/`):

| # | figure | what it shows | key result |
|---|--------|---------------|-----------|
| 1 | `full1_waterfall_all8.png` | CIR magnitude waterfall (tap × time), **all 8 anchors** | first path locked ~tap 750; per-anchor multipath tails differ (A cleanest, G richest) |
| 2 | `full2_mean_cir.png` | mean CIR channel signature per anchor, peak-aligned | common shape + ~+3 ns multipath bump; A lowest tail (clean LOS) |
| 3 | `full3_firstpath.png` | first-path tap + peak magnitude vs time, per anchor | tap ~constant (windowing); peak mag varies with distance |
| 4 | `full4_range_residuals.png` | per-anchor range residual from solved position | **all biases ±126 mm, noise 130–173 mm**; C(-126),E(-118) most biased; B,H least |
| 5 | `full5_delayspread.png` | RMS delay spread per anchor | 7.5 ns (A) → 10.3 ns (G) |
| 6 | `full6_solve_quality.png` | #anchors used / solve residual / speed vs time | median solve residual **127 mm**; speed reveals stand/rotate vs walk |
| 7 | `full7_diag.png` | rcph/rxtofs/ttcki/agc vs time per anchor | rxtofs differs per anchor (-4..+207), stable; **agc ALL ZERO (bug)**; ttcki const |
| 8 | `full8_loop_circle.png` | wand-loop circle fit | r=1031 mm, center 452 mm off wand, **345° coverage** |
| — | `fig2_track3d.png`, `fig5_wand_loop.png` | 3D track, top view, dist-to-wand(t), height(t) | start=tight cluster; loop encircles wand; z now physical |

Analysis #9 (text only, in `findings_full.txt`): CIR-tap vs SS-TWR range correlation is
weak by design (see 1g).

Trilateration method: per cycle, Levenberg–Marquardt least-squares on all responding
anchors (≥4), warm-started from the previous solution; relaxed z gate to allow
hand-raised highs. 430/517 cycles solved.

### Firmware discovery captured along the way
The even/odd asymmetry in capture 1b was **not** an anchor problem — every anchor answers
~100% when forced to rank 0. It was a firmware bug: a per-frame `dwt_readdiagnostics`
(~1 ms) in the ranging loop overran the 1 ms inter-responder gap and dropped every
odd-rank responder. Removing it (LED/buzzer are off in SCAN) → 4/8 → 8/8. Capture 1a is
post-fix.

---

## 3. Headline numbers
- Position: **median solve residual 127 mm**; static repeatability ~±250–300 mm (start
  cluster std); worst-per-anchor bias 126 mm. Healthy cage.
- Coverage: avg 6.4–6.7 / 8 anchors per cycle; CIR hit 94%.
- Wand loop: radius 1.03 m, 345° angular coverage, center 0.45 m from wand.
- Channel: delay spread 7.5–10.3 ns; ~+3 ns dominant secondary multipath.
- Broken field: `agc` = 0 everywhere.

---

## 4. Analyses NOT yet done (ideas, roughly ranked by value)

1. **Circular-SAR imaging of the wand / reflectors.** The end loop is a ~1 m-radius,
   345°-covered **circular synthetic aperture** around the wand, with per-position CIR
   to every anchor. This is the highest-value untapped analysis: coherently combine the
   CIR vs known Geiger position to image the wand and room reflectors. Relates to prior
   coherent-SAR work in this repo. (Caveats: only ~60 CIR/anchor over the whole run, so
   per-anchor circular coverage is sparse; positions are the noisy trilaterated ones,
   ~±0.15 m — likely below coherent-imaging needs at λ≈6 cm, so treat as incoherent /
   feasibility first.)
2. **Proper leading-edge (first-path) detection** instead of `argmax`: threshold-crossing
   / DW1000-style LDE on each CIR → cleaner first-path index, first-path-to-peak ratio.
3. **LOS/NLOS classification** per anchor per cycle from first-path/peak ratio + delay
   spread; map NLOS onto the trajectory (which room regions shadow which anchors).
4. **Body-shadowing during the two rotations**: correlate per-anchor dropout/level with
   rotation phase (needs a facing-angle proxy — none in data; could infer from which
   anchors drop). Explains the position jitter while rotating.
5. **Static vs dynamic multipath**: average CIR (static room response) vs residual
   (moving scatterers) per anchor.
6. **Trajectory filtering**: constant-velocity Kalman / RTS smoother; compare to raw LS;
   quantify how much smoothing helps given 127 mm residual.
7. **Path-loss / received-power vs distance** per anchor (peak |CIR| or a power proxy vs
   solved range) → per-anchor link budget, antenna-pattern hints.
8. **CFO analysis** from `rxtofs`/carrier integrator per anchor over time (clock stability,
   temperature drift).
9. **Before/after firmware comparison** (1b vs 1a): quantify the coverage/geometry/z-quality
   gain from the fix.
10. **Root-cause the `agc=0`** (firmware/register issue) so a real AGC/RSSI proxy exists.

---

## 5. Caveats for the analyst
- No wall-clock per line → time axis is a cycle-index proxy (non-uniform under CIR).
- Positions are noisy LS solutions (~±0.15 m); z is the weakest axis even with 8 anchors.
- CIR tap index ≠ range (windowed to first path). Range = the `a0..a7` fields.
- `agc` is invalid (all 0). `rxtofs` is a 19-bit signed CFO proxy, valid.
- Round-robin: any single anchor's CIR is sampled ~5 Hz/8 ≈ 0.6 Hz, ~60 frames total.
- Ranges are single-sided TWR (CFO-corrected) — decent but not double-sided accuracy.
- The two `scan.log` files are plain text; everything needed (geometry, wand) is the two
  JSONs in `logs/system_calibration_20260710_233443/`.
