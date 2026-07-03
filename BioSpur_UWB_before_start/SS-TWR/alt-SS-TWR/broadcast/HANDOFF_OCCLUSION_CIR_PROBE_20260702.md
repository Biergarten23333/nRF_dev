# HANDOFF — Anchor-B occlusion / L-B CIR probe (2026-07-02, site sealed)

Meat back in fridge; work paused. Rig left standing by. Resume per "Next session" below.

## Rig state (DO NOT TOUCH)
- **L-B (`760184545`) = CIR-probe build** `build-uwb-listener-poll-diag-cirprobe_lb_20260702`
  (hex sha256 `0b85d2c124ed2bfb…`). Leave flashed. Boot banner: `cir=1 near_anchor=1 period=10`.
  Emits `LCIRM`/`LCIRD`/`LCIRE` full accumulator for up-link poll frames + still LPD.
  Both self-heal fixes verified compatible under CIR dumping (`self_recover=0`, `ring_drops=0`, fps normal).
- **L-E / L-F / L-955A / L-9336 = frozen selfheal-LPD** (commit 53fb4d006). Untouched. Carry the
  scalar-ΔP chain (4-layer, |Δ| proxy, LPD preflight). 盖格 `760185886` untouched.
- Fleet split recorded in `docs/broadcast_tag_inventory.md` (deliberate, not drift).
- **Live viewer** (persisted in-repo, reuse next time):
  `python3 SS-TWR/alt-SS-TWR/broadcast/scripts/cir_live_view.py --port /dev/serial/by-id/usb-SEGGER_J-Link_000760184545-if00`
  Only ONE viewer at a time (port is shared/split otherwise).
- **Session drivers** persisted verbatim in `SS-TWR/alt-SS-TWR/broadcast/handoff_scripts_20260702/`
  (`phase2_anchorB.sh`, `listener_preflight_lstat.sh`, `build_listener_cirprobe.sh`). They contain
  absolute scratchpad `$SP` cross-refs from today's session — re-point `$SP` (or re-stage) next session.
- **Ranging keeper**: 30-min `run_recv_tdma_capture` with `--tag-cir compact`; let it self-stop at
  1800 s, no extend.

## Data point banked today — meat pressed ON the two antennas (B + L-B)
**Result: near-field, NO attenuation — triple-verified.**
- ΔP ratio flat (B ΔP 2.7–3.7, all LOS, same as clear E/F controls)
- rxpacc-normalized RX power flat: **B − mean(E,F) = +0.1 / +0.4 / −0.3 dB**; L-B − controls = −0.1/−0.5/−0.1 dB
- range counts equal (B 41/41 per tag, like controls), ge7=96.6% (B not dropped)
- Live CIR baseline: sharp strong leading edge, rxpow ~42.5 dB, FP/peak ~2–3 dB

**Why:** (1) on-antenna = uniform absorption → first-path and total drop together → ΔP *ratio* invariant
(ΔP is blind to it — the "贴死=均匀衰减" null case). (2) But power *also* didn't drop → the ray was never
intercepted: **B is a floor anchor (z=0), tags are elevated ~1.4 m**, so the dominant ray descends and
flies over meat standing on the floor / draped on the box side.

## NEXT STEP (todo)
1. Suspend the occluder (meat, or another water-rich body) **10–20 cm in front of Anchor B**, angled to
   **intercept the descending up-link ray** toward the tag zone (not on the antenna, not floor-standing short).
2. Watch the live CIR view; **notch = 3 signals together**:
   - `rxpow` drops **~10 dB and holds** (real absorption in path)
   - `FP/peak` **rises** (direct ray killed, multipath arrives late)
   - sparkline leading `@`/`#` **collapses / shifts right while the tail bars remain**
3. Pin the position once the notch is stable.
4. Fire the **45 s four-layer capture** (`handoff_scripts_20260702/phase2_anchorB.sh`, LDUR already fixed
   to 160 s to span the reset warmup) → full 4-layer verdict + **anchor-side proxy** (Anchor-B self-read vs L-B).

## Next session start sequence
1. LSTAT preflight all 5 listeners (`handoff_scripts_20260702/listener_preflight_lstat.sh`) — L-B alive via heartbeat.
2. Start ranging keeper + L-B CIR view.
3. Grab occluder, move per NEXT STEP.

## Also parked
- **AutoPos SW100 re-solved today** (C/G pair moved): canonical **v4-io** layout
  `logs/autopos_sw100_v4io_cg_moved_20260702_195145/anchor_layout_v4io.json` (rms 100 mm / median 45 mm).
  C/G confirmed movers via gauge-invariant distance diff. **NOT pushed to firmware** `uwb_anchor_layout.c`.
- **Claude Science academic assessment**: NOT back in the repo yet as of 2026-07-02. When it lands, drop the
  conclusion summary here and review it next session alongside the occlusion work.

## Overnight 2026-07-02 → morning (RUNNING autonomously — tracks #1 + #2)
User approved LOS-baseline+CIR soak (#1) and notch-detector tooling (#2); dropped #3 (AutoPos, matrix-mode
risk unattended) and #4. Two riders honored: (1) no live tag-vbat telemetry in this build → per-tag
**continuity** is the battery proxy (dropout timestamps in the soak log; morning report flags any dead wand);
(2) notch score is **per-tag→B per-link**, single-link alarm, thresholds z-scored from tonight's per-link
baseline (NOT joint — confirmed necessary: per-tag baselines differ, e.g. FP/tail BS9336≈16 vs BS955A≈7).

- **Soak** (responder-only, NO matrix): `logs/overnight_soak_20260702_221041/`, 8×45-min chunks (~6 h),
  each = 5 listener captures (L-B raw.log carries LCIRD) + recv (3 wands @10 Hz, `--tag-cir compact`,
  controller reset per chunk). Self-restarts per chunk; continuity + listener health appended to
  `soak_continuity.log`. Chunk-1 verified: ge7 97%, L-B ~1.7 CIR/s, rxpow std ~0.11 dB (very sensitive).
- **Notch detector** `scripts/cir_notch_detector.py` (baseline / replay / live). Decode verified on live data.
- **Per-link background census** `scripts/soak_link_census.py` (added per user rider): anchor self-read for ALL
  tag→anchor links → rxpow/ΔP median+IQR per link, per-chunk ΔP series → flags natural static-NLOS candidates
  (`NLOS-bg`/`wide`, e.g. furniture at A; `proxy-ready(L-B/E/F)` where a listener is co-located) and
  `TIME-VARY` links (chunk-median ΔP range ≥3 dB = something moved → baseline suspect). Static furniture is
  PART of the baseline (per-link z absorbs zero-points); env must stay static tonight→through tomorrow's run.
  NOTE: C/G moved today, so which links are naturally NLOS has likely shifted — trust tonight's fresh census,
  not the old "BS9336→G ≈10.8" (measured 4.2 now). Runs inside `soak_morning_report.sh`.
- **Morning**: run `scripts/soak_morning_report.sh` → builds `lb_los_baseline.json` from all chunks +
  continuity/health summary + prints the LIVE notch command. (Or next session I do it.)
- **Tomorrow's live notch**: `cir_notch_detector.py live --baseline <lb_los_baseline.json> --port <L-B>` —
  replaces the raw sparkline viewer; auto-prints `>>> OCCLUDED (link BSxxxx->B)` per link.
- Soak is orphaned/detached (survives session end). Stop early if needed: `touch <soak base>/STOP`.

## Overnight MORNING VERDICT (2026-07-03, soak complete 8/8 chunks, ~6.4 h)
Hardware all clean: 3 wands survived (0 dropouts, 100% span every chunk — batteries held), L-B CIR
probe healthy (~31k captures, self_recover +6/chunk, ring_drops=0), other 4 listeners ring_drops=0.
Anchor coverage bimodal (ge7 96% chunks 2/3/8 vs ~74% chunks 4/5) = the BLE/UWB phase-beat reroll,
expected. LOS baseline built: `lb_los_baseline.json` (per-tag→B rxpow ~42.5±0.3 dB, FP/tail 8/13/17).

**Both census "events" were RE-INVESTIGATED via L-B CIR + anchor-ΔP decomposition + 5-min time-resolve
and BOTH are INTERNAL (TDMA/BLE-UWB phase-beat), NOT physical / NOT bystander:**
- The anchor-side ΔP moves are a **first-path register artifact**: fp1 amplitude drops ~15–30%
  (fpterm −2.3 dB) while **total cir_pwr and rxpow stay flat** → ΔP rises purely through the
  fp/total ratio. Reflection (would raise total/tail) and absorption (would drop rxpow) are BOTH
  falsified at both instruments. Co-located **L-B CIR is flat** for all three tags in chunks 7/8
  (FPamp −0.2%, tail −1.2%, rxpow +0.05 dB) — the up-link *waveform* never changed.
- Time-resolved: BS955A→B and BSCCF4→B **toggle two-state** (ΔP≈2.4↔6.5) all night and are
  **mutually exclusive** (one sacrificed tag at a time); **BS9336→B is dead flat** (fp1 7250±60, 6 h).
  "03:39 BS955A→B step" = BS955A sacrificed in the last windows (also toggled 00:10–00:55 & 01:20–01:40
  **while user asleep** → not the user). "02:05–02:52 room-wide transient" = **BSCCF4 sacrificed in
  chunk 6** → all BSCCF4 links dropped fp1 together → census misread one-tag-many-anchors as room-wide.
- **Conclusions:** (a) environment was STATIC all night (BS9336→B flat = proof); no roommate testimony
  needed. (b) **Anchor-side ΔP is contaminated by the phase-beat (±2 dB via fp1); the co-located CIR
  probe is the clean arbiter.** (c) The **live notch detector is immune** (3-signal waveform criterion
  rejected all of it) — but the **45 s anchor-side proxy MUST gate on L-B CIR, never anchor ΔP alone.**
- Census-detector caveat to harden later: attribute "one tag / many anchors move together" to the
  **sacrificed-tag (TDMA)** mechanism, not "environment" (currently mislabels it multi-link/room-wide).
  Scripts promoted to `scripts/`: `cir_mech_discriminators.py` (L-B CIR FP/tail/rxpow),
  `anchor_dp_decompose.py` (ΔP → rxpow/cir/fp split), `anchor_dp_timeresolve.py` (per-tag→anchor bins).

### ARCHIVAL CORRECTIONS (2026-07-03, accepted — calibrated, do not overstate)
1. **Structural fact:** anchor scalar ΔP carries a **sub-threshold (~±2 dB) protocol (phase-beat)
   artifact** — a property of the diagnostic *system*, not of one night. All historical anchor-side
   ~2 dB-level attributions (incl. prior "E +2.2 dB environmental background"-type reads) are flagged
   **"may contain a phase-beat component, not CIR-arbitrated"** until re-checked against a co-located CIR
   probe. This does NOT retract those reads — it scopes their uncertainty.
2. **NLOS determination UNAFFECTED (state separately):** the 10 dB NLOS threshold keeps its margin —
   both events were ≤6.5 dB, far below 10; no NLOS verdict was ever wrong. The artifact is a
   fine-attribution (sub-threshold) concern only, never a LOS/NLOS mislabel.
3. **Methodology phrasing (calibrated):** write *"anchor scalar carries a sub-threshold (±2 dB)
   protocol artifact; fine-grained attribution requires CIR arbitration"* — NOT "anchor ΔP throws false
   positives." The listener-necessity argument uses the accurate **±2 dB** magnitude (no reviewer opening).
4. **Event-2 testimony still to collect (zero cost, pending):** ask roommate whether he entered the
   living room ~02:00–03:00. YES → the census protocol-vs-room signature taxonomy needs review (a real
   room event to separate from the BSCCF4-sacrifice); NO → one more independent corroboration of the
   internal mechanism. Append verdict here when it lands.
5. **Operational discipline:** anchor-side proxy verdict is **gated on L-B CIR, never anchor ΔP alone**;
   operator **leaves the room during the 45 s formal capture**.
