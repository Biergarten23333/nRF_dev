# FREEZE_STATE — 2026-07-15-FREEZE handoff

Frozen firmware set: git tag **`freeze-clean-20260716`** (commit `8b68ee0aa`, parent
`freeze-4piece-20260715` = `642e4a33`). Fleet = 3 wand tags (BS9336/BS955A/BSCCF4)
+ 8 anchors (A–H) + 2 B120 masters (Tag SNR 1050070698 / Anchor SNR 960148546,
protected) + 9 listeners. Verified ge7 0.979 / ge8 0.955 / valid% 97.6.
Firmware binaries + hashes: `firmware/README.md`, `firmware/SHA256SUMS.txt`.

---

# ⚠️ #1 OPEN QUESTION — Reverse SS-TWR multi-tag: distinct-per-tag-slot UNVERIFIED (NOT a blocker)

**CORRECTION 2026-07-17.** An earlier version of this file called slotted TDMA a hard
"#1 BLOCKER — transmits zero." **That was a measurement error, retracted below.** Slotted
TDMA *works*. The real status is an open question, not a death sentence. Written in three
layers on purpose — do not overcorrect "slotting transmits zero" into "slotting is all fine."

## (1) RETRACTED: "epoch-synced slotting transmits zero" was a measurement error

The on-air **source address = `UWB_TAG_BASE_ADDR (0xB100)` + `logical_tag_id`** — code:
`include/uwb_ss_twr_shared.h:82` (`#define UWB_TAG_BASE_ADDR 0xB100U`),
`src/uwb_ss_twr_shared.c:78-81` (`uwb_tag_short_addr(id)=BASE+id`), recomputed on **every
CFG apply** at `src/ss_twr_init.c:2757-2760` (`ss_twr_init_apply_runtime_params`, which even
branches on `local_addr != previous_local_addr`). So `CFG TAG=1` moved all tags to `0xB101`,
`TAG=7 → 0xB107`; the build ids 54/90/244 map to `0xB136/0xB15A/0xB1F4`. The old verify
scripts kept filtering the **pre-migration** addresses → saw 0 = **fake silence** (never real-0;
no unfiltered total was ever captured during an applied slotted config).

**Slotted TDMA transmits — unfiltered on-air (2026-07-17):**

| slotted `CFG` (post-clean-reboot) | on-air UNFILTERED |
|---|---|
| `PERIOD=100 COUNT=1` | **52/s** at `0xb107` |
| `PERIOD=200 COUNT=2` | **19/s** at `0xb101` |
| `PERIOD=9000 COUNT=9` (81 s cycle) | ~0/s (by design) |

Rate ≈ **`1000/(COUNT×PERIOD)` Hz**. `rawrange` is real (e.g. `[3320,2055,2568,3195,3031,2096,2601,3290]`,
8 anchors). Evidence: address-migration code (above) + the on/off unfiltered table. The old
"0.0 Hz — silent" 4-row table was filtered against dead addresses — **delete that conclusion.**

## (2) REAL caveat (keep): live re-config can stick a tag at 0 TX until a cold reboot

Rapid back-to-back live `CFG` changes (especially after a rejected `LIVE=0` config) left tags
in a **genuine stuck-0 state** (unfiltered 0/s) that a **cold `REBOOT` cleared** — the identical
`PERIOD=100` config then transmitted 52/s. This is a real robustness issue for any live
multi-tag reconfiguration: reconfigure deliberately; if a tag goes dark, cold-reboot it.

## (3) The REAL open question: distinct per-tag slots on a common epoch is UNTESTED

Every test used `cmd_all` → all 3 tags got the **same** `TAG`/`SLOT` → same address, colliding
on one slot. Whether N tags in **distinct** slots on a **shared epoch** actually run coordinated
(the reverse-SS-TWR requirement) has **never been tested**. So reverse multi-tag scheduling is
**neither proven blocked nor proven working**. **Needed:** a dedicated test — assign each tag a
different `TAG`/`SLOT` with a common `EPOCH`, measure per-tag on-air at its *own* address
(`0xB100+tag_id`) + ge7. This is the true next experiment, not a firmware rewrite on faith.

## (4) Direction reversal — working slotted may be the CURE, not the obstacle

Measured free-run 3-tag **ge7 is bad and wildly variable** (1%→98% per tag/run; sweeps capture
only 5–6 of 8 anchors — the zeros in the raw-range vectors) due to the BLE↔UWB phase-beat
collision. Coordinated slotted TDMA with **non-overlapping per-tag slots is exactly what would
eliminate that collision.** So slotting is a candidate **solution** to the phase-beat that tanks
multi-tag ge7 — reverse should test slotting *as the fix*, not treat it as a blocker. This
reverses the earlier framing and is worth its own note.

---

## Note — silencing the wand tags (corrected 2026-07-17)

**There is no software command that fully stops the tag transmitter** — it always transmits when
powered. `CFG_STOP` / `MODE IDLE` / `MODE IDLE`+`REBOOT` only gate host-side reporting / throttle.
A slotted `CFG` does **not** silence — it relabels the tag's address (`0xB100+tag_id`) and sets
the poll rate; a large `PERIOD` makes it **near**-silent (`PERIOD=9000` → 81 s cycle → ~0/s,
verified **unfiltered**) but still fires a brief burst ~once a minute. (Earlier "CFG silence =
0 Hz" claims were the address-filter error above.) **Only cutting the shared tag power is
continuous zero.** Best near-silence, tags stay connected + `RUN`, no persisted `IDLE`:
```
cmd_all CFG TAG=1 SLOT=0 COUNT=9 PERIOD=9000 ACTIVE=90 EPOCH=1   # -> ~0/s (81 s cycle), RUN, connected
cmd_all REBOOT                                                    # -> restores build free-run ranging
```
(Master_Tag CDC `/dev/ttyACM0`, pyserial `dtr=False rts=False`. Host CDC line buffer **63 chars max**,
`apps/master_control/src/main.c:3181-3187` — keep `CFG` short; `RUN`/`MASK`/`GEN` default.)

---

## Residual / open issues

1. **[OPEN QUESTION — not a blocker] Reverse-SS-TWR multi-tag distinct-slot scheduling UNVERIFIED**
   — see the ⚠️ #1 section. Slotted TDMA *works* (rate-controllable, unfiltered-verified 52/19/0);
   the earlier "transmits zero" blocker was a measurement error (address migration → filtered dead
   addresses), retracted. **Untested:** distinct per-tag slots on a common epoch. **Caveat:** live
   re-config can stick a tag at 0 TX (cold reboot clears). Next: the distinct-slot + shared-epoch test.
2. **Master warm-reboot AUTOPOS reconnect** (prior open item) — software `mode recv` warm reboot
   cannot reconnect anchors (0/8 for 3+ min); only a cold JLink reset does (8/8 in ~40 s). Firmware
   limitation; matters for reverse SS-TWR anchor plane. See `experiments/anchor_ota_diagnosis/ANCHOR_OTA_ROOTCAUSE.md`.
3. **On-rig anchor markers are MIXED (cosmetic)** — A/B/C run `anchor-freeze-clean-20260716`,
   D–H run `anchor-freeze-20260715`; binaries byte-identical apart from marker string.
4. **Tag TDMA free-run collisions (phase-beat)** — at 3 tags, ge7 is badly degraded (measured
   1–98% per tag/run, only 5–6/8 anchors per sweep); managed by master `reroll`. Per #1(4),
   working distinct-slot TDMA is a candidate *fix* for this, not just a symptom to work around.
