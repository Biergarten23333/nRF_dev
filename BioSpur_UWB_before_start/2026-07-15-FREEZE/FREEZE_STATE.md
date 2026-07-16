# FREEZE_STATE — 2026-07-15-FREEZE handoff

Frozen firmware set: git tag **`freeze-clean-20260716`** (commit `8b68ee0aa`, parent
`freeze-4piece-20260715` = `642e4a33`). Fleet = 3 wand tags (BS9336/BS955A/BSCCF4)
+ 8 anchors (A–H) + 2 B120 masters (Tag SNR 1050070698 / Anchor SNR 960148546,
protected) + 9 listeners. Verified ge7 0.979 / ge8 0.955 / valid% 97.6.
Firmware binaries + hashes: `firmware/README.md`, `firmware/SHA256SUMS.txt`.

---

# ⚠️ #1 BLOCKER — Reverse SS-TWR multi-tag scheduling does NOT work as-is

**This is a hard blocker, not a note. Read before scoping any reverse-SS-TWR / 8–10 tag work.**
Established by direct hardware experiment on 2026-07-16 (Geiger on-air capture +
Master_Tag CFG readback), not inference. The user explicitly required this be
measured before assuming multi-tag feasibility.

## The finding: only free-run transmits; every epoch-synced slotted config is silent

Pushed TDMA configs at runtime via `cmd_all CFG …` on the frozen tag firmware and
measured each tag's on-air poll rate (Geiger listener, ttyACM6) against the
Master_Tag `CFG_STATUS` readback:

| Config pushed at runtime (`cmd_all CFG …`)                 | Accepted?            | Readback (`CFG_STATUS`) | On-air (Geiger)   |
|------------------------------------------------------------|----------------------|-------------------------|-------------------|
| `PERIOD=500 COUNT=10`  (epoch-synced slot)                 | `CFG_OK LIVE=1 RUN=1`| `src=MASTER period=500` | **0.0 Hz — silent** |
| `PERIOD=10 COUNT=10`   (epoch-synced slot)                 | `CFG_OK LIVE=1 RUN=1`| `src=MASTER period=10`  | **0.0 Hz — silent** |
| `PERIOD=200 COUNT=2`   (200 ms slot window, 50 % duty)     | `CFG_OK LIVE=1 RUN=1`| `src=MASTER period=200` | **0.0 Hz — silent** |
| build free-run (`epoch_valid=false`)                       | —                    | `src=BUILD`             | **8–9 Hz ✓ transmits** |

The split is clean and repeatable across every variation tried (fast/slow period,
1-of-10 duty, generous 200 ms slot at 50 % duty): **the moment `epoch_valid=true`,
the tag transmits nothing.** Free-run (`epoch_valid=false`, the build default) is the
**only** mode that puts polls on air.

## Mechanism (code-confirmed)

- **Config *delivery* IS runtime-controllable** — every CFG was accepted live
  (`CFG_OK`, `LIVE=1`), stored (`src=MASTER`), **no reboot needed**. The runtime
  config path is healthy. Handler: `apps/tag/src/uwb_tag_ble.c:1938-1973`.
- **`epoch_valid=false` → free-run**: `uwb_tdma_schedule_now_ms` returns the tag's
  own local clock → poll-ASAP, ungated. `src/uwb_tdma.c:91-92`; forced at
  `src/ss_twr_init.c:3451-3454`. (This is also why `CFG_STOP` / `MODE IDLE` cannot
  silence a running tag — see silence note below.)
- **`epoch_valid=true` → slotted, gated on `sync_local_ms`**: set by
  `uwb_tdma_sync_schedule_epoch` (`src/uwb_tdma.c:63-81`), which the runtime CFG path
  DOES invoke (`src/ss_twr_init.c:6544-6548`). The firmware even has the multi-tag
  convergence primitive **designed in** — comment `src/uwb_tdma.c:71-76`: *"EPOCH as a
  relative delay… each tag converts that delay to its own local epoch start so
  sequential BLE delivery still converges on one common TDMA phase."* **Yet driven
  from the exposed runtime interface it yields zero transmissions.** Slot gating:
  `ss_twr_init_tdma_period_remaining_ms` (`src/ss_twr_init.c:2533-2578`),
  `ss_twr_init_tdma_exchange_can_start` (`src/ss_twr_init.c:2581-2601`).

## Corroboration

Production has **never** used slotted TDMA. The wand runs **free-run** and manages
tag collisions with the master's BLE-phase **`reroll`** workaround
(`apps/master_control/src/main.c:2579-2605`; the "tdma-capacity-ble-phase-beat"
issue). Consistent with the slot-execution path being present-but-non-functional.

## Implication for reverse SS-TWR (8–10 tags)

- You CAN push per-tag configs at runtime (live, no reboot). You **CANNOT** get tags
  to run coordinated slots — enabling epoch-synced slotting makes them go dark.
- Coordinated multi-tag TDMA is therefore a **firmware development task**, not a
  config-and-go: the epoch-synced slot-execution path must be made to actually
  transmit (likely requires master-side UWB epoch/sync coordination that the
  BLE-`CFG`-only path does not supply) and then validated on hardware.
- The one working mode — **free-run + BLE-phase reroll — is a statistical workaround
  already marginal at 3 tags** (2-of-3 collapse to ~1 Hz via phase-beat). It will not
  scale cleanly to 8–10 tags.
- **Scope caveat:** tested via the exposed runtime interface (BLE `CFG`). If a deeper
  master-coordinated sync path exists, it is undocumented and is not what production
  uses — so "push slot configs → tags run slots" is **false today** regardless.

**Bottom line: reverse-SS-TWR multi-tag scheduling cannot be assumed. It needs the
epoch-synced slot path fixed + validated first.**

---

## Note — the REAL way to silence the wand tags (corrects earlier wrong methods)

Because the tag poll broadcast free-runs whenever powered, **`CFG_STOP`, `MODE IDLE`,
and even `MODE IDLE`+`REBOOT` do NOT stop on-air transmission** — they only gate
host-side TR reporting / throttle. (Earlier conclusions claiming these silence the
tag were WRONG; verified on air.) The tags also have **no JLink debugger** (OTA-only),
so they can't be held in reset.

**Clean software silence** (0 Hz, tag stays **connected + `MODE=RUN`**, no persisted
`IDLE`, reboot restores ranging) — push an epoch-synced slotted config, which the
scheduler correctly gates to silence:

```
cmd_all CFG TAG=1 SLOT=0 COUNT=2 PERIOD=200 ACTIVE=90 EPOCH=10   # -> all tags 0 Hz, RUN, connected
cmd_all REBOOT                                                    # -> restores build free-run ranging
```
(Sent over Master_Tag control CDC `/dev/ttyACM0`, pyserial `dtr=False rts=False`.
Host CDC line buffer is **63 chars max** — `apps/master_control/src/main.c:3181-3187` —
so keep `CFG` commands short; `RUN`/`MASK`/`GEN` may be omitted, they default.)
The only *total* silence remains cutting the tag power supply (all 3 share one).

---

## Residual / open issues

1. **[BLOCKER] Reverse-SS-TWR multi-tag TDMA scheduling non-functional** — see the ⚠️
   section above. Epoch-synced slotting transmits zero; only free-run works; slot
   path exists in code but is non-operational from the drivable interface. Firmware
   development + hardware validation required before any 8–10 tag reverse work.
2. **Master warm-reboot AUTOPOS reconnect** (prior open item) — software `mode recv`
   warm reboot cannot reconnect anchors (0/8 for 3+ min); only a cold JLink reset does
   (8/8 in ~40 s). Firmware limitation; matters for reverse SS-TWR anchor plane. See
   `experiments/anchor_ota_diagnosis/ANCHOR_OTA_ROOTCAUSE.md`.
3. **On-rig anchor markers are MIXED (cosmetic)** — A/B/C run `anchor-freeze-clean-20260716`,
   D–H run `anchor-freeze-20260715`; binaries byte-identical apart from marker string.
4. **Tag TDMA free-run collisions (phase-beat)** — at 3 tags, 2 routinely collapse to
   ~1 Hz; managed by master `reroll`. Underlying cause of #1's scaling limit.
