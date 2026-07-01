# FROZEN SET — a18 re-verified 2026-07-01 · **8-anchor ranging**

Known-good deployable set, **measured-verified 2026-07-01**. This is distinct from
`FREEZE_4PIECE_20260628.md`: same 4-piece firmware, but this freeze captures the rig **after
Anchor F was repaired** — so it is a **true 8/8** ranging fleet (all 8 anchors range), not the
7/8 of the 6/28 freeze (where F / anchor-id-5 was a non-ranging / broken node).

- **6/28 freeze (`FREEZE_4PIECE_20260628`)**: a18 fleet, F broken → 7/8 ceiling, ge8 ≈ 0.5.
- **THIS freeze (7/1)**: a18 fleet, F repaired → **8/8**, ge8 ≈ 0.97.

Firmware markers are identical between the two; the difference is the **rig state** (F fixed).
Rig: Master_Tag B120 + Master_Anchor B120 + 8 anchors (A–H, all ranging) + 6 tags.

---

## Measured verification (2026-07-01, 3-wand 60 s motion @10 Hz)
Capture: `logs/a18_reverify_capture_3wand_responder_20260701_135449*/`

| metric | value |
|---|---|
| **ge7** | **0.978** |
| **ge8** | **0.972** |
| tr_valid | **97.8 %** (13085/13384; no_anchor_rx 299 = 2.2 %) |
| per-anchor-count histogram | `{7: 11, 8: 1626}` — **every sweep lands 7–8; 99.3 % at full 8/8** |
| per tag | BS9336 ge7 0.978 / BS955A 0.979 / BSCCF4 0.978; all 100 % continuity, 0 dropouts |

Contrast with the broken a19 (6/30): ge7 = **0**, histogram smeared **1–6, never reaches 7**.

---

## The 4-piece set (markers · build dirs · SHA-256 · deploy path)

### [1] Anchor firmware  (×8 DWM1001C, nRF52832)
- marker: `altbcast-responder-a18-g1200-r1000-20260512_154806`
- build dir: `build-anchor-unified-ota-altbcast-responder-a18-g1200-r1000-20260512_154806`
- `dfu_application.zip` (BLE-OTA payload) sha256: **`b1288ef0f8f8e60dd248fb65e6cc666fdac18cb7ef2d2f2a4d1006042f746fc8`**
- `merged.hex` (direct J-Link, MCUboot+app — used for the F repair) sha256: `c5aed4b9781b341d6d2278ca9222f12940c7dc7f8ba2a73b4889e0955d4b0ce6`
- **normal deploy = BLE-OTA through Master_Anchor** (payload embedded in carrier [4]).

### [2] Tag firmware  (×6 DWM1001C, BLE-OTA)
- marker: `compact-sampled-tdmafix-nodiag-a7win-baseline-20260628`
- build dir: `build-tag-ble-unified-tdmafix-nodiag-a7win-baseline-20260628`
- `tag/zephyr/zephyr.signed.bin` sha256: `416d31e3b30d453a01b13f3ff87548f0e38c7acace684684e4eba8ceb21fbcb1`
- `dfu_application.zip` sha256: `693bdc39a6ccf342468b050f7a29ecc89d37d5e5ac7fd2e85889b50ce44b459e`
- config: BROADCAST, nodiag, POSITION_OUTPUT=0, RXAUTR=OFF, RXDBLBUF=OFF.

### [3] Master-Tag carrier  (B120 nRF5340, J-Link SNR **1050070698**, CDC=Master_Tag)
- build dir: `build-master-control-b120-m1-master-tag-lfrc-a7win-reroll-20260628`
- `zephyr/merged_domains.hex` sha256: `a002d0b6a38094e602a9768552529570bf53b4cc87d1fb620f87c70b81e662c4`
- embeds tag payload [2]; has the `reroll <BSxxxx>` CDC cmd. **CDC re-enumerates as
  `usb-Master_Tag_Master_Tag_Control_6918E0384172A49F-if00` after this carrier is flashed.**
- flash (dual-core): `scripts/jlink_flash_nrf5340_dualcore_by_snr.sh 1050070698 <build-dir>`

### [4] Master-Anchor carrier  (B120 nRF5340, J-Link SNR **960148546 = PROTECTED**)
- build dir: `build-master-control-b120-m1-master-anchor-lfrc-anchoronly-altbcast-embed-altbcast-responder-a18-g1200-r1000-20260512_154806`
- `zephyr/merged_domains.hex` sha256: `6a458cde917fad96094d24aa75a68d2d8b45d1d4cc6be31273bff299280462ad`
- embeds anchor payload [1]; boots mode=AUTOPOS, CDC=Master_Anchor (`ttyACM0`).
- **⚠ CURRENT STATE (record): this PROTECTED B120 is now carrying the a18 carrier above** —
  it was **J-Link protected-flashed** on 2026-07-01 (`BIOSPUR_ALLOW_PROTECTED_MASTER_ANCHOR_FLASH=1`,
  dual-core). It previously held the a19/rfdiag-v2 carrier (6/30); before that the frozen a18.
  Guard file `.protec/noflash960148546` stays. Next session: **this B120 = a18 carrier** — don't
  assume otherwise.

**Correct flash tool for BOTH B120s = `scripts/jlink_flash_nrf5340_dualcore_by_snr.sh <SNR> <build-dir>`**
(NET then APP core, `-SelectEmuBySN` → no probe popup). **NOT** the single-`loadfile`
`flash_master_control_b120_m1_noninteractive.sh` — that programs APP core only, skips the net
core → CDC hang.

---

## Deploy / restore recipe (validated 2026-07-01)

```bash
# carriers (dual-core, SNR-selected, no popup)
scripts/jlink_flash_nrf5340_dualcore_by_snr.sh 1050070698 build-master-control-b120-m1-master-tag-lfrc-a7win-reroll-20260628
BIOSPUR_ALLOW_PROTECTED_MASTER_ANCHOR_FLASH=1 \
  scripts/jlink_flash_nrf5340_dualcore_by_snr.sh 960148546 build-master-control-b120-m1-master-anchor-lfrc-anchoronly-altbcast-embed-altbcast-responder-a18-g1200-r1000-20260512_154806

# anchor OTA (pushes carrier's EMBEDDED a18 payload). Master must be un-wedged first (see below).
# The PROVEN recipe (needs --master-reset between anchor-reset and upload):
python3 scripts/ota_deploy_anchor_set.py --port <Master_Anchor> --out-dir logs/... --order ABCDEFGH \
  --pre-reset-first --master-reset-snr-after-pre-reset 960148546 --skip-pre-version-verify \
  --expected-fw-marker altbcast-responder-a18-g1200-r1000-20260512_154806

# tag OTA — FIRST re-stage the tag payload (shared generated/ota_image.inc is per-kind):
python3 scripts/prepare_alt_ota_payload.py --kind tag \
  --marker compact-sampled-tdmafix-nodiag-a7win-baseline-20260628 \
  --build-dir build-tag-ble-unified-tdmafix-nodiag-a7win-baseline-20260628 \
  --signed-bin build-tag-ble-unified-tdmafix-nodiag-a7win-baseline-20260628/tag/zephyr/zephyr.signed.bin \
  --dfu-zip   build-tag-ble-unified-tdmafix-nodiag-a7win-baseline-20260628/dfu_application.zip
python3 scripts/ota_deploy_tag_set.py --port <Master_Tag> --out-dir logs/... --prefix BS \
  --targets BS9336,BS955A,BSCCF4,BSF66F,BS2DCE,BSDC91 \
  --expected-fw-marker compact-sampled-tdmafix-nodiag-a7win-baseline-20260628

# anchors boot MATRIX after OTA → switch to responder before capture:
#   master cmd:  anchor role all responder runtime   (=> rc=0 sent=8 ready=8/8)

# capture (drive Master_Tag; NEVER let it reset the protected anchor):
python3 scripts/run_recv_tdma_capture.py --port <Master_Tag> --controller-reset-snr 1050070698 \
  --targets BS9336,BS955A,BSCCF4 --tr-hz 10 --tdma-profile motion --duration 60 --out-dir logs/...
```

---

## KEY OPS LESSON — CDC "flood" / OTA write-timeout has **TWO** sources (rewrites the 6/30 note)

The a18 "anchor-only" Master_Anchor wedges its CDC when its AUTOPOS scanner is busy hunting
anchors. That busy-scan has **two distinct causes**:

- **(a) Transient AUTOPOS churn** — normal re-connect activity right after a flash/reset. A
  **power-cycle (or jlink-reset + fast-grab to `mode ota`) clears it.**
- **(b) A permanently-missing / broken node** — if one anchor is **dark / no LED / not
  advertising**, the master hunts for it **forever** → CDC stays wedged → **every OTA write
  times out.** Power-cycle only masks this for a few seconds; the real fix is **repair that node.**

**2026-07-01 root cause was (b): Anchor F's firmware image was damaged** (dark, no blue LED,
would not advertise) → roster stuck at `count=7/8` → master looped → CDC wedged → all anchor OTA
blocked at `pre_version_verify` write-timeout / `phase_a_target_selection_not_proven`.

**⇒ NEXT TIME you hit OTA write-timeout: FIRST check whether any anchor is dark / not
advertising (roster `count<8`). Fix that node before blaming the flood.** Power-cycling the
master in a loop is treating the symptom.

### Anchor F repair (one-time, done 2026-07-01)
F = J-Link probe SNR **760186124** (nRF52832; mapping in `scripts/flash_all_anchors.sh`).
1. Direct J-Link flash a18: `scripts/flash_anchor_auto.sh build-anchor-unified-ota-altbcast-responder-a18-g1200-r1000-20260512_154806 760186124` (writes `merged.hex` = MCUboot+app).
2. **Gotcha:** a full-chip `merged.hex` **erases the anchor identity config at 0x7E000** → F booted as `ANCHOR_ID:E, role=unset`. Re-provision it:
   `scripts/provision_anchor.py --probe-serial 760186124 --anchor-id F --role matrix --verify`
   (writes the 32-byte `anchor_config_t`, schema 2, reads F's real UID; BS_CODE derives from UID.)
3. F then boots `ANCHOR_ID:F role=matrix cfg_valid=1`, advertises → roster completes (count=8) →
   master stops wedging → OTA works. (F's OTA path re-verified afterward: class=D, config preserved.)

---

## Restore-notes
- All artifacts present + SHA-verified on disk (2026-07-01). Re-deployable now.
- Related: `FREEZE_4PIECE_20260628.md` (the 7/8 predecessor), `PROXY_DIAGON_A19_HANDOFF_20260630.md`
  (a19 timing root cause + fixed-a19 plan). Next planned step: build **fixed-a19** (move
  `dwt_readdiagnostics` post-`dwt_starttx`, all ranks) to get anchor ΔP **and** stable ranging.
