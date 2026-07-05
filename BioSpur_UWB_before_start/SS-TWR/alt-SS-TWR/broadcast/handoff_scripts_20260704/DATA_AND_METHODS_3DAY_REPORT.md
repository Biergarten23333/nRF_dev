# UWB Volume-Sensing / Room-Imaging — 3-Day Data & Methods Report

**Purpose:** a complete, honest account of what was attempted 2026-07-02 → 07-05, every dataset
collected, its type (raw vs processed), and every result (including the negative ones), so an
outside reviewer (Claude chat) can judge the work and the path forward. Written 2026-07-05.

---

## 0. The goal

Use the live UWB positioning field as a **multistatic sensor of the room's physical state** —
extract spatial features (occupancy, occluders, ideally a "point-cloud"-like image of the room)
from the channel, not just tag coordinates. The concrete north star that kept coming up: a 2D
back-projected **room image** where walls / furniture / a person light up.

---

## 1. Hardware / rig (as of 07-05)

- **RF config (all nodes):** DW1000 **Channel 5** (6489.6 MHz, 499.2 MHz BW → **λ ≈ 4.6 cm**),
  **PRF 64 MHz**, **preamble length 128**, PAC 8, preamble code 9 (Tx=Rx), non-standard SFD,
  **data rate 6.8 Mbps**, PHR STD, PAN 0xDECA. (from `UWB_listener/src/main.c` dwt_config_t.)
- **8 fixed anchors A–H** (DWM1001C, nRF52832), positions solved by AutoPos:
  A(0,0,0) B(4347,0,0) C(4399,2909,-29) D(224,3044,0) E(171,116,1618) F(4374,12,1624)
  G(4350,3165,1622) H(312,2950,1626) mm. Layout file:
  `logs/autopos_v3box_noref_20260704_030908/solve_v3_box/anchor_layout_v3_box.json`.
- **Wand tags (3)** = `BSCCF4, BS9336, BS955A` — a **rigid calibration T-bar**, tape-measured
  (`docs/wand_mode.md`): local coords CCF4=(-285,0,0), BS9336=(385,0,0), BS955A=(0,-595,0) mm;
  pairwise 660–709 mm. Fixed (does NOT rotate). Sits near volume center.
- **RotoArm tags (2)** = `BS2DCE, BSDC91` — on the two ends of a motorized arm that **rotates
  ~7 s/turn in a (tilted, ~vertical) plane**. Brought online 07-05 for synthetic aperture. No
  co-located listener; only transmits/reports over BLE + is ranged by the master.
- **6 CIR-probe "listeners"** (co-located passive DW1000 receivers running `cirprobe_gen`
  firmware, sha 37e26c7f): wand-side **L-CCF4, L-9336, L-955A** (at the wand, ~volume center)
  + anchor-side **L-B, L-E, L-F** (at anchors B/E/F). L-9336 & L-955A were scalar-only until
  flashed to full-CIR on 07-05. These are the only sources of raw waveform data.
- **2 master boards:** Master-Tag-Control (drives tag TDMA / logs TWR) and
  Master-Anchor-Control (forces anchors into responder mode).

---

## 2. Data types collected (schemas + meaning)

All capture CSVs are host-timestamped (`host_epoch_s`). Two producers: the **listeners**
(passive CIR) and the **recv/master** (TWR ranging).

### 2a. RAW data

| file | producer | 1 row = | key fields | what it is |
|---|---|---|---|---|
| **`lcird.csv`** | listener | one 512-byte slice of one CIR | `accepted_polls, offset, len, hex` | **RAW CIR waveform**: hex = the DW1000 1016-tap complex accumulator (int16 I,Q per tap), split across offset-rows; reassemble per `accepted_polls`. This is the only true raw-signal data. |
| **`lcirm.csv`** | listener | one CIR's metadata | `tag_id, fp_index, fp1/fp2/fp3, cir_pwr, rxpacc, carrier_integrator, rx_ts_lo32` | per-CIR summary: first-path index (`fp_index/64`), first-path taps, total CIR power, preamble-accum count, carrier freq offset. Joins to `lcird` by `accepted_polls`. |
| **`lpd.csv`** | listener | one poll (~10 Hz) | `tag_id, src, fp_index, fp1/2/3, cir_pwr, rxpacc, std_noise, poll_mask` | per-poll **scalar** diagnostics (no waveform). Enough to compute first-path power, RX power, ΔP (tail/multipath indicator). |
| **`lstat.csv`** | listener | periodic | good_frames, cir_captures, rx_errors, self_recover, fps… | listener health counters. |
| **`tr_all.csv`** | recv | one (tag,anchor) range | `peer_name, tag_id, anchor_id(0–7), range_mm, quality_percent, valid, rx_mask` + IMU (`acc_norm_*`), temp, vbat | **TWR ranging**: SS-TWR distance from each tag to each of 8 anchors, per sweep. Used to solve tag positions and (for the RotoArm) its moving trajectory. |

CIR sample period ≈ 1.0016 ns/tap; true range resolution ~600 mm (499.2 MHz BW).
Derived scalars: FP power = 10·log10((fp1²+fp2²+fp3²)/rxpacc²); RX power =
10·log10(cir_pwr·2¹⁷/rxpacc²); ΔP = RX−FP.

### 2b. PROCESSED data (derived, in scripts/figures)

- **Tag positions** — multilaterated from `tr_all` (least-squares vs 8 anchors). Wand tags
  static; RotoArm tags = time-series trajectory → circle fit (center/radius/plane normal).
- **Mean/aligned CIR per (tag,listener) link** — reassembled from lcird+lcirm, aligned to
  first path, normalized.
- **Back-projected images** (see §4).

---

## 3. Datasets on disk (`SS-TWR/alt-SS-TWR/broadcast/logs/`, ~18 GB total)

| session | size | date | contents / purpose |
|---|---|---|---|
| `overnight_soak_20260702_221041` | 4.8 G | 07-02 | first long CIR soak (3 wand tags), chunked raw CIR |
| `cirprobe_ranging_keeper_20260702_210930` | 216 M | 07-02 | CIR + ranging keeper |
| `occlusion_prep_keeper_20260703_060002` | 285 M | 07-03 | occlusion test capture |
| `occlusion_prep_keeper2_20260703_064330` | 426 M | 07-03 | occlusion test capture |
| `overnight_soak_v2_20260704_032348` | 6.6 G | 07-04 | main overnight CIR soak (chunked); the "false person" dataset |
| `autopos_v3box_noref_20260704_030908` | 556 K | 07-04 | anchor-layout solve (positions used everywhere) |
| `stencil_session_20260704_223223` | 56 M | 07-04 | static-net in/out occlusion test |
| `netblock_20260704_230113` | 70 M | 07-04 | static-net block test |
| `slowchop_231800` (+ figs) | — | 07-04 | slow steel-net chopper on RotoArm |
| `roto_sar_overnight_20260705_012548` | 5.9 G | 07-05 | **circular-SAR overnight**: 36 chunk-slots, 14 with complete ranging, 5 tags @ 5 Hz + 6 CIR listeners |

The 07-05 roto-SAR set is the headline dataset: **14 good 15-min chunks**, each = 6 listener CIR
streams (lcird/lcirm/lpd) + recv `tr_all`. ~1,500–1,600 CIR frames/listener/chunk, ge7 95–97%.
RotoArm trajectory: ~13,500 solved positions/tag; 49,924 RotoArm CIR frames back-projected.

---

## 4. Methods tried and results (chronological, honest)

### 4.1 "Room detection" from the overnight soak → **artifact, retracted**
The 07-04 soak seemed to show a moving occluder ("person walking 3–6 am"). **False.** Room was
static (occupants asleep). Verified 3 independent ways it was the **BLE/UWB TDMA collision**, not
a person: (1) dropouts follow anchor-id/TDMA-slot order, not a spatial path; (2) co-located CIR
first-path stays flat (±0.2 dB) during the dropout; (3) which tag is hit rotates per BLE
reconnect. Quantitatively welded: 7.5 ms BLE interval ≈ 7.18 ms 8-anchor window, ~5 ppm crystal
drift → collision creeps across slots faking motion. **Lesson: the scalar/ge7 channel is
confounded by the radio artifact; CIR waveform is the trustworthy channel** (chunk-to-chunk CIR
statistically identical across the "event").

### 4.2 Occlusion detection (scalar + CIR) → **4 negatives**
Water bottle, A3 stencil, small steel net (fast chop), small steel net (slow chop). All produced
**no CIR change above the ~1 % / tap noise floor**. DW1000 first-path re-locks onto multipath;
small passive occluders (size ≪ human) are undetectable in this multipath room. Figures:
`slowchop_result.png`, `chopper_fast_*.png`.

### 4.3 Monostatic radar profile from volume center → **real, but 1-D**
Treat each wand-side listener as a monostatic radar at the volume center (its co-located tag
transmits, it receives; tail taps = round-trip room echoes). The 3 wand tags — at the same point
— produce the **same** echo-vs-range curve (validates it): bumps at ~0.9 m (floor), ~1.6 m
(y-wall), ~2.2 m (x-wall). Real, but range-only, no azimuth. Figure: `wand_radar_image.png` (A).

### 4.4 Static multistatic back-projection → **"bullseye" (PSF, no scene)**
Back-project mean CIR tails over 15–18 links onto a room grid. Result is concentric-arc point-
spread, not resolved reflectors. Two root causes identified: (a) node positions were estimated
(later fixed by measuring), (b) **absolute single-snapshot imaging of a static room with a tiny
fixed aperture has no angular resolution**. Figures: `room_image.png`, `wand_radar_image.png` (B).

### 4.5 Measured the wand geometry (answered "how spread are the 3 tags")
Multilaterated the 3 wand tags from `tr_all`: pairwise 650–793 mm — **consistent with the known
660–709 mm T-bar**. Confirmed the tags are genuinely ~0.7 m apart (NOT one point); the earlier
mistake of collapsing them to a centroid was wrong. But absolute z is poor (vertical DOP), and at
the 50 cm scale the ranging RMS (~10–20 cm) makes inter-tag distance/collinearity unmeasurable —
the rigid known geometry must be used instead.

### 4.6 Circular-SAR with the rotating RotoArm → **pipeline works, image does NOT resolve the room**
The one path a fixed aperture can't give: a **moving TX**. RotoArm tags rotate → swept from many
positions, heard by the 6 fixed listeners → synthetic aperture. Overnight capture (§3) →
`roto_sar_image.py`: circle-fit each RotoArm tag's trajectory (BS2DCE R≈400 mm, BSDC91 R≈510 mm,
plane normal ≈[0.05,0.70,-0.71]), then back-project each of **49,924** moving-TX CIR frames with
TX at its interpolated position.
- **Positive:** the image is no longer a symmetric bullseye — it has asymmetric structure, i.e.
  the moving aperture IS adding angular information; tracking + pipeline are clean.
- **Negative:** it does **not** resolve walls/furniture. Energy piles on the RotoArm's own
  near-field. A near-field gate (drop excess <1800 mm, `roto_sar_image_gate1800.png`) removes the
  self-blob but **no wall structure emerges** — just a low-contrast haze.

---

### 4.7 Data-quality notes & known gaps (from review)
- **6** full-CIR listeners during the 07-05 overnight (L-9336/L-955A were flashed to full-CIR
  *before* the 01:25 start); 49,924 roto frames = 6×14×~1550×2/5, consistent with 6 probes.
- **Anchor coverage is full, not 7-of-8:** over 108,819 sweeps, each anchor A–H covered 97–100 %,
  **ge7 98 %, ge8 92 %, median 8 anchors/sweep.** So the ~80 mm RotoArm circle residual is
  intrinsic TWR + fast-motion noise, **not** anchor dropout / DOP loss.
- **Reliability gap:** 22 of 36 chunk-slots were lost to anchors falling out of responder mode
  (BLE/TDMA collision); the self-heal recovered each but skipped those windows. Root cause = the
  same collision mechanism documented in §4.1; a real rig-hardening item.
- Monostatic §4.3 x-axis is **one-way range** (path/2 applied); resolution ~300 mm.
- **No ground-truth room survey yet** (laser tape, ~10 min) — needed to score any wall map.
- DWM1001C chip antenna is **anisotropic** (pattern nulls) — some listeners are blind to some
  walls; must be accounted for when explaining which surfaces appear.
- `carrier_integrator` (CFO) is logged every poll but **unused** — free material for CFO
  correction / Doppler.

## 5. Why the SAR image failed — MODEL failure, not detection failure (revised after review + Step-0 audit)

The first draft blamed a "physics ceiling / ±10–20 cm ranging RMS". A reviewer + a zero-cost
audit (§5a) show that was **partly wrong**. Corrected diagnosis, in priority order:

1. **Wrong scattering model (dominant cause).** Indoor walls are smooth vs λ (4.6 cm), so they
   reflect **specularly**, not diffusely. Back-projection tomography assumes every voxel scatters
   to all receivers; under specular reflection each (Tx,Rx)-wall pair has energy at only **one
   mirror point**, so energy never accumulates *along* a wall → no wall in the image. §4.3 already
   proved the wall echoes are *detectable*; the image failed because the **estimator** is wrong,
   not because the signal is absent. Correct model = image theory (mirror-Tx, delay-ridge vs
   rotation angle, fit plane parameters).
2. **Incoherent magnitude summation.** Cross-range blur of incoherent back-projection ≈
   (c/B)·R/D ≈ 0.6 m·2.5/0.9 ≈ **1.7 m** — this is exactly the haze width observed. Per-look
   normalization / coherence weighting cannot fix it; the PSF stays ~1.7 m wide.
3. **Rotation-plane geometry — earlier claim was BACKWARDS.** Plane normal ≈ [0.05, 0.70, −0.71]
   means the rotation plane nearly **contains the x-axis**: x-aperture is full ±R, the compressed
   axis is **y** (×0.70). So aperture geometry is *not* the main killer; (1) and (2) are.

### 5a. Step-0 audit (zero cost, existing data) — flips the coherent verdict
Ran `step0_audit.py` on the overnight set:
- **Phase stability (the never-used asset):** on a fully static link (LB←BSCCF4, 364 frames,
  fp_index std 11 mm), the **first-path-referenced** phase of SNR>6 dB tail taps has circular
  std **3.6–16.4°, median 15.3°**. Well under the ~40° coherence threshold. **⇒ the phase is
  coherent-grade.** The clock/timing is removed by FP-referencing; the ±10–20 cm figure is a
  *position/geometry* error, which is **not** the same as phase error. **Coherent SAR is
  therefore NOT dead** (contrary to the first draft), contingent on autofocus + a point-target
  beacon (walls give no point to autofocus on).
- **RotoArm rigidity:** BS2DCE R=429 mm, BSDC91 R=530 mm; per-frame circle residual ~80 mm;
  **speed non-uniform (jitter 36–41 %, period ~4.2 s** — not the ~7 s eyeballed); the two roto
  tags are **956±101 mm apart = one rigid arm** (±101 mm is just solve noise). So the 50k-frame
  TX track compresses to ~8 rigid parameters + a rigid-arm constraint → **parametric autofocus is
  feasible** to beat down the 80 mm per-frame noise.
- **Respiration caveat:** CIR rate ≈ 0.34 Hz/link (5 tags sharing USB) is at/below the 0.2–0.4 Hz
  respiration Nyquist → respiration-via-CIR-phase **aliases on this dataset**; it needs a
  dedicated fast capture (1–2 tags, ~1–2 Hz CIR). Coherent SAR / mirror-model integrate over
  angle and are not rate-limited.

---

## 6. What is established (net result of 3 days)

**Works / proven:**
- A 6-CIR-vantage + 5-tag multistatic capture rig, ge7 97 %, self-healing overnight driver.
- The BLE/TDMA collision is the source of the false "occupancy" signal; **CIR is the immune
  channel**. Discriminators documented.
- CIR **detection floor** ≈ 1 %/tap in a static room; small passive occluders undetectable.
- Real **monostatic range profile** from the volume center (walls at measured ranges).
- End-to-end **circular-SAR pipeline** (moving-TX tracking + back-projection), 49,924 frames.

**Ruled out:**
- **Incoherent** voxel/point-cloud imaging of the static room — PSF ~1.7 m, no amount of
  reprocessing helps. Also confirmed: small passive occluders (< human) are undetectable.

**Re-opened by the Step-0 audit (were wrongly called dead in the first draft):**
- **Coherent SAR** — phase is coherent-grade (15° floor). Contingent on parametric autofocus over
  the ~8 rigid trajectory params + a corner-reflector beacon to focus/verify.
- **Parametric wall map** — walls are specular and *detectable*; the right estimator is image-
  theory mirror-model fitting (not tomography). Expected: 3–6 planes at ~5–15 cm from existing
  data. This is the highest ROI-per-effort path.

**Reachable but untested end-to-end (needs a short experiment / dedicated capture):**
- **A moving person** — large self-referencing perturbation (differential/Doppler); does not need
  absolute focusing. Expected ~0.3–0.7 m localization at a few Hz. Zero data yet — the biggest
  experimental gap; a 1-hour walk test (tag as ground truth) would settle it.
- **Respiration of a still person** — needs a dedicated fast (1–2 tag) CIR capture; phase floor
  (15°) is well below the mm-scale chest-motion phase, so it is a literature-supported capability
  once sampled above Nyquist.

---

## 7. Decided plan (post-review, time-boxed, with kill criteria)

The reviewer answered the original 4 questions; verdicts folded into §5–§6. Q2 = incoherent is
un-salvageable (stop). Q1/Q3 = coherent + parametric wall-map are alive (model failure, not
detection failure). Remaining plan:

- **Step 0 — DONE (§5a).** Phase audit PASS (15°), RotoArm rigidity confirmed → coherent &
  wall-map branches green; respiration needs a faster capture.
- **Step 1 — moving-person differential (1 h experiment, biggest data gap).** Person wears a tag
  (ground truth), walks a taped L-path 5–10 min + 5 min empty background. Analyse per-link
  differential CIR → ellipse back-projection → does the blob track the tag? Kills/confirms the
  whole moving-target branch. *Needs the user.*
- **Step 2 — parametric wall map (≤2–3 days, existing 07-05 data).** Image-theory mirror model:
  per-frame CLEAN (remove FP + sidelobes) → bin by rotation angle → delay-ridge tracking → joint
  fit of plane params. Score vs a 10-min laser survey. Success = ≥3 walls within 15 cm; kill at 3
  days (MPC association is a known rabbit hole, ~50 % prior).
- **Step 3 — coherent autofocus (1–2 days, conditional on Step 0 = pass, which it did).** Add a
  ~30 cm corner-reflector beacon; parametric autofocus over the 8 rigid trajectory params. Focus
  to ~λ ⇒ coherent confirmed; else close the coherent question permanently.
- **Strategic:** the "static room point-cloud" north star has no use case (cheap lidar/SLAM wins).
  Retarget to **people** — presence / track / fall / respiration — all on the physically-reachable
  list. The single most valuable 3-day byproduct — the quantified BLE/TDMA collision mechanism —
  belongs to BioSpur mainline **G0.x** diagnostics and should flow back there.

## 8. Reproduce
- Overnight driver: `handoff_scripts_20260704/roto_sar_overnight.sh` (5 tag @ 5 Hz, 15-min
  self-healing chunks).
- SAR image: `python3 handoff_scripts_20260704/roto_sar_image.py <base> [min_excess_mm]`.
- Static/monostatic: `wand_radar_image.py`, `room_backproject.py`. Differential: `diff_backproject.py`.
- Data: `logs/roto_sar_overnight_20260705_012548/` (raw CIR + ranging, 14 good chunks).
