# UWB Volume Sensing — Findings Brief v2 (2026-07-04)

A concept note for discussion — **what we found**, not procedure. Self-contained for a fresh
reader. Setup: an 8-anchor DW1000/DWM1001C UWB room (alt-SS-TWR broadcast, TDMA), 3 stationary
"wand" tags polling at 10 Hz, and 4–6 passive co-located listeners that dump full DW1000 CIR
(1016-tap complex accumulator). Program goal: use the live UWB field as a multistatic sensor of
the room's physical state and feed it back into positioning. This note reports what two nights
of soak data actually support.

**v2 changelog (after external review):** (i) the motion/stillness dichotomy in Finding 0 was
wrong for living targets — respiration makes a "still" person a narrowband self-referencing
signal; (ii) two facts the reviewer didn't have are now folded in: full-CIR capture is only
~0.5 Hz **per link** (not 10 Hz), and **no dataset ever had a truly empty room** — the "clean"
windows had two sleeping occupants; (iii) numeric fix (82.5 ms commensurability, stated
effect-first), calibrated wording throughout, normalization stated explicitly, DoF estimate
halved; (iv) bistatic link-budget answer folded into Finding 3.

---

## Finding 0 — What the observable actually is (corrected)
CIR measures the room's **current static channel state**; the observable is **change**. Three
target classes, not two:

- **Moving body** — continuous state change → temporal variance, self-referencing, easy.
- **Still living body (seated/sleeping)** — *not* actually static. Respiration (0.2–0.4 Hz,
  chest displacement ~4–12 mm) swings the echo path by up to ~2× that; at λ ≈ 4.6 cm the echo
  component's phase swings ~1–3 rad, and its interference with same-tap static clutter modulates
  tap **magnitude** at the breathing rate (no absolute carrier phase needed — only intra-tap
  relative phase, which DW1000 magnitudes preserve). So a still person is a **narrowband
  low-frequency line**, separable from aperiodic broadband room drift in the spectrum —
  self-referencing again, no absolute reference required.
- **Inanimate static change** (furniture moved, object placed) — the only class that is truly
  a **level-shift vs an absolute quiet reference**. This is also exactly the class AutoPos edge
  hygiene cares about.

**Sampling constraint (hardware fact, bounds feasibility):** polls run at 10 Hz, but full-CIR
dumps are listener-USB-limited to ~1.5/s shared across 3 tags → **~0.5 Hz per link**, at
irregular capture times. Breathing at 0.2–0.4 Hz sits at/above the mean-rate Nyquist; detection
must lean on irregular sampling (Lomb–Scargle) and minutes-scale windows (at 0.5 Hz, the noise
in a breathing-band bin integrates down to ~0.2% of first path in ~1 min, ~0.07% in ~10 min),
or the capture budget must be re-planned (one probe dedicated to one link ≈ 1.5 Hz). The
per-poll 10 Hz "compact CIR" channel exists but covers only a 6-tap first-path window — it can
see Fresnel-grazing modulation of the direct path, not off-line echo taps.

---

## Finding 1 — The ranging layer is a *confounded* sensor; do not sense with it
The cheap "presence" signal — per-sweep anchor coverage ("ge7") — is **dominated by a protocol
artifact**, and in our (small) sample it pointed the wrong way: the night a person actually
entered, ge7 stayed ~96%; on empty-room nights it collapsed to 60–75%.

- Mechanism: each tag holds a BLE link at a **7.5 ms** connection interval (firmware-pinned,
  min=max), and its 8 anchor responses occupy a **7.18 ms** window each sweep. The observed
  slow walk of the missing anchor (below) implies the sweep period is locked near-commensurate
  with the BLE grid (**≈ 11 × 7.5 = 82.5 ms**; host-timestamped medians ~83 ms agree within
  measurement crudeness). One BLE event then lands inside the response window on ~96% of sweeps
  (the free gap is 7.5 − 7.18 = **0.32 ms, 4%**) and clobbers whichever anchor slot it overlaps.
  That is why this was never fixed, only probabilistically minimized: the interval physically
  cannot hold the window plus a guard.
- The collision point is not stationary: when it creeps, it advances at ~**0.35 slots/min**
  (≈ 5 ppm — ordinary crystal offset), so the missing anchor **walks H→G→…→A over ~15–20 min**.
  Pooled across tags this **mimics a moving occluder**. The phase dynamics are nonstationary
  (11–30 min parked-quiet stretches, entire 45-min blocks clean), consistent with slow thermal
  wander around commensurability rather than a fixed beat.
- It is **per-tag** (each tag's own BLE phase, own crystal) and **re-randomized every
  reconnect**: which tag is destroyed rotates block-to-block (one night: the same tag was worst
  at 86% single-anchor miss in one block and cleanest at 7% in another).

Three discriminators separate the artifact from any real environmental effect (all three must
hold before calling anything "environmental"): **(1)** dropout order is anchor-ID/slot order,
not a spatial trajectory (the apparent "walk" teleports corner-to-corner and ceiling-to-floor —
no object moves like that); **(2)** the **co-located CIR first path stays flat (±0.2 dB)**
while ranging drops — RF path intact ⇒ protocol rejection, not blockage (14/15 event-windows
flat; one −6 dB blip at one probe remains unexplained); **(3)** per-tag rotation — geometry
cannot spare one co-located tag while destroying another, nor switch victims on reconnect.

---

## Finding 2 — CIR is the clean substrate, and we measured its limits
- **No artifact coupling detected.** CIR waveform statistics from artifact-hammered vs clean
  blocks are **statistically indistinguishable at our ~1% floor** (first-path peak, tail RMS,
  resolvable-tap count matched pairwise). The collision drops *ranging*; the waveform of any
  poll that goes on air is a clean physical measurement. Sensing can therefore be **decoupled
  from positioning** (tags blink, listeners capture) — the BLE/TDMA mess is a positioning
  problem, not a sensing blocker.
- **Fingerprint richness.** Each tag→listener link resolves 13–36 taps above 10% of peak out to
  ~80 ns. With ~2 ns true resolution (499.2 MHz bandwidth; the 1 ns tap grid is ~2× oversampled)
  and sidelobes of strong paths inflating the count, the honest figure is **~100–150 independent
  multipath degrees of freedom** across the 12 links.
- **Quiet-room floor.** Per-tap standard deviation is **~0.7–1.6% of the first-path peak**,
  stable over 6 hours. Normalization: amplitudes are RXPACC-normalized (absolute channel units);
  "% of peak" is a display unit, so this floor is not contaminated by first-path fluctuation.
  (Processing note going forward: keep absolute tap amplitudes as the primary channel and treat
  first-path power as its own channel, so Fresnel-grazing FP changes can't masquerade as
  whole-room change under FP-normalization.)
- **Critical caveat discovered post-hoc: the room was never empty.** The 6-hour "quiet" windows
  had **two sleeping occupants**. So the 0.7–1.6% floor is really a *static-occupied* floor —
  which cuts both ways: it is the harsher, more realistic quiet condition (any breathing
  coupling from the beds is already inside that 1%), but it also means **no true empty-room
  reference exists in any dataset yet**, and the level-shift class (inanimate changes) has never
  had a certified-empty template to shift against.
- The same stability makes the reference question mostly **operational, not physical**: an
  N-frame averaged template drives the level-shift threshold well below 1% (drift-limited, not
  noise-limited); the open items are how to *certify* emptiness when recording it and when to
  invalidate it. A per-tap Allan-deviation-vs-integration-time curve from the existing ~31k
  captures/probe would quantify the drift-limited floor with zero new experiments.

---

## Finding 3 — Presence couples to the *volume*, not just the line-of-sight link
A body influences links it is not standing on, via **(a) echo** (new multipath taps),
**(b) Fresnel-zone grazing** (the "direct path" is an ellipsoid tens of cm wide — near-line
bodies partially obstruct without touching the line), **(c) global multipath reshaping**. Two
regimes with opposite budgets:

- **Shadow** (on a Tx→Rx line): 10–20 dB first-path drop — huge, binary, per-link, tells you
  "this link is blocked", not where.
- **Echo** (off all lines, in the illuminated volume): amplitude ratio echo/direct ≈
  (d_dir/(d₁·d₂))·√(σ/4π) with human bistatic RCS σ ~ 0.1–1 m². Example: 5 m link, body 3 m
  from each end → **~5–15% of first path**, excess delay ~1 m (clears the ~0.6 m first-path
  blur) — resolvable and an order of magnitude above the ~1% floor. Each Tx–Rx pair seeing the
  echo constrains the scatterer to a bistatic ellipsoid (shell thickness ~0.3–0.6 m); ≥3
  angularly-diverse links intersect to a ~0.5 m-class fix. **Placement objective:** maximize
  d_dir/(d₁·d₂) subject to excess delay > ~0.6 m; corners far from every link are dead zones.
  No cross-frame carrier phase exists (rules out coherent SAR), but intra-frame phase relative
  to the first path is usable — the semi-coherent processing vital-sign radars use.

---

## Finding 4 — Geometry/coverage is a hard gate
On the one night with a ground-truthed human event (entry ~03:45, then sitting still), the only
CIR probe was co-located with a **floor anchor (z = 0)**. Three independent analyses (deviation
from a quiet baseline, frame-to-frame change, occupied-vs-quiet level) all stayed **inside the
quiet-room noise**. Consistent readings: the probe was **geometrically blind** (floor-grazing
rays pass under a seated torso) **or** the echo sat below the ~1% floor at that vantage — and
the "empty" baseline itself was uncertain (the subject may have been at the desk during it).
Either way the lesson stands: detection needs **elevated probes whose paths cross the occupied
region**, ≥3 angularly-diverse vantages for localization, and a certified-empty segment.

---

## What we CANNOT yet claim (honest ledger)
- **No immune-channel (CIR) evidence yet that a person changes the field.** The datasets never
  aligned: the night with 4 good elevated probes had no *moving/entering* person (but see the
  standing opportunity below); the night with a real entry had one floor-blind probe and an
  uncertain baseline. The "person affects the volume" belief so far rests on the confounded ge7
  channel.
- **No certified-empty reference was ever recorded** (two sleeping occupants throughout the
  "clean" windows; desk-occupancy uncertainty on the other night).
- The **near-monostatic tail enrichment** at the listener 10 cm from a wand (candidate steel-
  stencil signature that scalar power missed) is unattributed — no stencil-out A/B yet.
- **No demonstration** of still-person detection (breathing line or level shift) nor of echo
  localization (ellipsoid intersection) at the measured floor.

**Standing opportunity (zero new hardware):** the 2.3 h clean-window data contains two sleeping
people + 4 probes at ~0.5 Hz/link with irregular timing. A Lomb–Scargle band-power search at
0.15–0.5 Hz over 5–10-min windows of tail-tap amplitudes is a **free test of the respiration
hypothesis on existing data** — a positive would be the first immune-channel human-presence
evidence; a null bounds bed-geometry coupling below the floor at 0.5 Hz sampling.

---

## Open questions for discussion
1. **Respiration-line budget at 0.5 Hz/link.** With irregular ~0.5 Hz sampling, what
   integration time × echo-coupling combinations make a seated adult's breathing line
   detectable, and does dedicating one probe to one link (~1.5 Hz) change the answer
   qualitatively? What does breathing-rate wander over minutes do to the line?
2. **Inanimate change without a certified-empty room.** Given a quiet-but-occupied reference is
   the realistic best case, can furniture-class changes be detected as level-shifts against a
   slowly-adapted occupied template, or does that class strictly require scheduled empty
   segments?
3. **Echo vs static clutter.** Respiration labels *living* scatterers. For inanimate new
   objects, is differential-vs-template the only discriminator, or is there a structural
   signature (tap birth at a delay inconsistent with the static map)?
4. **Feedback value first.** A per-link "currently perturbed" flag (shadow-regime, 10–20 dB,
   self-referencing, per-sweep latency) is already enough to down-weight links in the tag
   solver and to gate AutoPos edges. Is sensing-assisted positioning the right near-term
   deliverable, with standalone room imaging deferred?
5. **Field positioning.** Opportunistic sensing on comm/positioning waveforms is ISAC territory
   (802.11bf standardizes WLAN sensing; 802.15.4ab is adding UWB sensing; RTI covers the
   shadow regime on RSS meshes; WiFi-CSI and IR-UWB radar own respiration). The defensible
   wedge, if any: **retrofit passive co-located listeners on an unmodified commercial TWR
   deployment, closing the loop back into the positioning solver**. Literature sweep required
   before any novelty sentence is written.

---

### One-paragraph summary
Ranging coverage (ge7) is a poisoned well for sensing — a per-tag BLE/UWB timing collision
(7.5 ms interval vs 7.18 ms response window, ~5 ppm creep) fakes a moving occluder and, in our
sample, read *cleaner* when a real person was present. The clean substrate is the CIR waveform:
no artifact coupling detected at its ~1%/tap quiet floor, ~100–150 independent multipath DoF
across 12 links, and physically coupled to the whole volume (echo + Fresnel grazing), not just
lines-of-sight. The v1 framing that a still person is a pure level-shift problem was wrong:
respiration makes living targets narrowband self-referencing signals even when seated — the
truly reference-dependent class is inanimate change, and *that* is the class positioning
hygiene needs. The embarrassing-but-productive discovery is that no dataset ever contained a
certified-empty room (the quiet windows had two sleeping occupants) — which simultaneously
hardens the measured floor (it survives two breathing bodies) and hands us a free experiment:
search the existing sleep-window CIR for breathing lines. The decisive next recording needs
elevated angularly-diverse probes, a certified-empty segment, a scripted enter/sit(breath-hold)/
leave sequence, and a stencil in/out — all watched only through CIR, never ge7.
