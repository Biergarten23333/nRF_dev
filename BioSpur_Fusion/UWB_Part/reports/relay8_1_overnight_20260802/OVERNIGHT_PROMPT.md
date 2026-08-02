# PROMPT — relay8.1 overnight: finish OTA → continuous ten-node capture to depletion (UNATTENDED, NO TOKENS)

Save as `UWB_Part/logs/relay8_1_20260801/hardware_arc/OVERNIGHT_PROMPT.md`.

## Standing overrides for TONIGHT ONLY

- **NO OPERATOR TOKENS.** The stage-token protocol is suspended for this run. Every stage
  boundary proceeds automatically on success. The operator is asleep and will not answer.
- **NO POWER CYCLE.** The system stays exactly as it is: anchors, all seven listeners and the
  Fusion Master DK remain powered and untouched. The ten tags stay powered on battery — they
  are NOT re-docked.
- **NO SW100 / no anchor re-survey.** The existing anchor geometry stands.
- **NO Tag Master activity after the OTA batch ends.**
- Everything else in the relay8.1 campaign rules remains in force: zero write retries;
  composed-CFG idle only; `BEACON_SYNC` always explicit; `CFG_STOP` never; bare `MODE IDLE`
  never; tag-domain rates; report integrity (verdict → evidence + SHA-256); zero-progress
  alarm and drain watchdog armed throughout.

## Failure policy (replaces "stop and wait" — there is nobody to wait for)

- **A board that fails OTA or verification is QUARANTINED, not retried:** leave it exactly as
  it is, record its state, and **continue to the next board.** Never re-flash, never reset,
  never re-upload unattended. The MCUboot rollback net handles an unconfirmed image by design.
- **The capture runs regardless of how many boards reached relay8.1.** Record per board which
  firmware it carries and treat mixed firmware as recorded context, not as a blocker. The
  night's data is worth more than firmware uniformity.
- Only two conditions abort the whole run: the Fusion Master CDC is unrecoverable, or the
  fleet reaches zero live tags. Both trigger the terminal sequence below.

## Ground truth (do not re-derive)

- Deployed so far: **BSF3C79** and **BSFC2CC** on relay8.1, canonical IMGSTAT, `confirmed=1`.
  **BSF44AD**: OTA upload/test/reset clean, zero write retries, both stability gates passed,
  but the readback never answered — version and confirm state UNKNOWN. Seven boards untouched
  on relay8: BSF6C53, BSF8BC4, BSF1120, BSF31CC, BSFAA61, BSFB165, BSFEC35.
- **Readiness discriminator (established tonight):** the true new-application marker is the
  tag-owned **sweep-counter backward discontinuity** (e.g. 12431→40). "Post-reboot UWB"
  records before that jump are stale pipeline data. BSF44AD's single `VERSION` landed
  3.086 s after its discontinuity and was lost; BSF3C79's query at +287 s answered in 70 ms.
- A command sent before the tag's command path is up is **lost, not queued**.
- Main beacon is currently at 100,000 µs; ten-tag operation needs 110,000 µs
  (COUNT=11, PERIOD=10, slot 0 = beacon, tags 1–10).
- Battery: the tags have been off-dock for hours, so tonight's run measures **remaining**
  capacity, not a full-charge endurance record — report it as context and say so explicitly.
  The clean full-charge endurance record remains un-taken.

---

# PHASE 1 — finish the OTA batch (corrected gate + bounded polling)

Per board, in this order: **BSF44AD (re-query only, no OTA) → BSF6C53 → BSF8BC4 → BSF1120 →
BSF31CC → BSFAA61 → BSFEC35 → BSFB165 (last, per convention).**

- **BSF44AD**: read-only. Apply the polling policy below directly; do NOT re-OTA. Whatever it
  reports — relay8.1, rolled-back relay8, or still silent — record it and move on.
- **Each remaining board**: name-pinned OTA (upload/test/reset, **zero write retries**) → wait
  for the **sweep-counter backward discontinuity** → wait 15 s from that true app start →
  require one further complete UWB record → then the readback.
- **Readback = bounded polling, read-only:** send `VERSION`; if no reply, repeat every 30 s up
  to **6 minutes past the discontinuity**; stop at the first reply; then one `IMGSTAT`.
  Zero-retry remains absolute for writes; this applies to reads only.
- **Record per board the delay from sweep discontinuity to first successful reply.** After
  seven boards this is the measured distribution of the tag command-path warm-up — the number
  this campaign has guessed at three times and a required input for the T3 fix.
- Board result = COMPLETE (relay8.1 + canonical hash + `confirmed=1`) or QUARANTINED (anything
  else, with its evidence). Continue either way.

# PHASE 2 — pre-capture reconciliation (commands only, no power cycle)

1. **Anchors: verify responder 8/8 and re-issue if short.** Nothing was power-cycled, but this
   is cheap insurance — an unattended capture against non-responder anchors would produce six
   hours of `valid=0x00`. Command only.
2. Fusion Master: dk-v29 confirmed live; 10/10 BLE connected + subscribed; spacing ON/5000
   verified (rebuild if the readback disagrees); service gate ×10 recorded as context — a
   degraded draw does NOT block the capture.
3. Main beacon → **110,000 µs**; verify the new generation; sub SLAVED.
4. Tag entry in contract order: slot map recorded, `BEACON_SYNC=1` explicit, `RUN=1`.
   Boards that just rebooted from OTA get the same readiness discipline before any command.
   **Where a CFG reply does not arrive, behavioral acceptance is authorized** (UWB resumes at
   the expected slot and rate, listeners see it) — the path already approved for BSF3C79.
5. `IMU RATE=200`, `IMU BATCH=10`, `IMU START` on every live tag, echoes verified where the
   reply path answers, behaviorally where it does not.

# PHASE 3 — continuous capture to depletion

One uninterrupted full-load capture, ten nodes, until the last tag dies. H4-style snapshots
every 5 minutes; all ledgers, alarms and listener streams recording throughout; raw stream
immutable.

**The first 30 minutes after the field stabilizes are designated NOW, prospectively, as the
W qualification window.** Gates, per node, alive window, tag domain:

- `q_drop_imu` delta = 0 AND `q_drop_uwb` delta = 0 on ALL TEN;
- delivered IMU ≥ 99.9 % of 200 Hz per node, zero sequence gaps;
- **UWB ≥ 99 % of 9.0909 Hz on ALL TEN — no waiver; slot 10 gated like everyone else**;
- ledgers zero incl. DK aggregate latch delta and host loss; zero malformed/decoder/
  disconnect; sub SLAVED; main start-failures reported (flag > 1 %);
- `imu_i2c_err` / `imu_hreset`: recorded context, **not gates**;
- recorded (not a gate): fitted epoch mod 16 == carried T4 bits, all ten.

**The four relay8.1 fix readings are extracted from the same window** and reported separately:
Δmod16 = +1 on ≥99.9 % of consecutive records; listener absolute-epoch match 100 % exact;
beacon window miss fraction ≈ 0; slot-10 tag-domain rate ≥ 9.00 Hz.

A W failure does **not** stop the capture — the run continues to depletion either way.

Battery-death protocol per node: bounded reconnect ×3, mark dead, continue to the last node.
FM-death protocol as before. Hard stop at fleet death or +12 h, whichever comes first.

# PHASE 4 — automatic terminal and morning report (no token)

At fleet death or the hard stop: composed-CFG idle for any survivor (90 s witness),
`IMU STOP` verified where reachable, snapshots flushed, `end_state.json`, main beacon left at
100,000 µs, logs closed AFTER cleanup. No hardware action pending; the tags stay off-dock
where they are.

Then produce `OVERNIGHT_REPORT.md`, opening with **three sentences**: the OTA outcome
(how many on relay8.1, how many quarantined), the W verdict, and the endurance result. Then:

1. Phase-1 table incl. the command-path warm-up distribution;
2. the W gate table and the four fix readings — **did relay8.1 kill the slot-10 waiver?**;
3. **the data products**: the 3D plot of all ten positions with per-node RMS (scatter about
   each node's own mean; absolute accuracy explicitly out of scope, no ground truth), and
   per-node 6-axis IMU plots with bias/noise tables — computed from the first 10 minutes of
   the capture;
4. endurance: per-node alive time and the distribution, **stated as remaining-capacity, not a
   full-charge record**;
5. anything that broke; UNKNOWNs; evidence SHA-256 throughout.
