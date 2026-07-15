# FROZEN 4-PIECE SET — 2026-07-15 (`freeze-4piece-20260715`) — V1

Verified-pass production freeze of the four-piece firmware set. Deployed and
verified end-to-end on hardware 2026-07-15. Git tag: `freeze-4piece-20260715`.

**Accepted V1 TR format:** `TR;3` with an **all-zero `D1` trailer when DIAG is OFF**
(the "diaggate" build — diag code present, trailer empty by default). A truly
clean `TR;2` (no `D1` trailer) is **deferred to the freeze-clean batch** — see
"Deferred" at the bottom. For V1 this is the accepted, operator-approved format.

---

## The four pieces

### [1] TAG firmware  (3 wand tags, via BLE OTA from Master_Tag)
- marker:      `tag-freeze-20260715`
- build dir:   `build-tag-freeze-20260715`
- signed.bin sha256: `12681984d516c4b5b0648bba74fc813f697e6f30b1cdda80e3a33cca35aa881e`
- dfu zip:     `build-tag-freeze-20260715/dfu_application.zip`
- deployed to: BS9336, BS955A, BSCCF4 — all verified `fw=tag-freeze-20260715`, mode RUN.
- TR: `TR;3`, `D1` trailer empty when DIAG OFF (default). DIAG is a volatile runtime toggle.

### [2] MASTER_TAG carrier  (B120 nRF5340, J-Link SNR **1050070698**, CDC=Master_Tag)
- build dir: `build-master-control-b120-m1-master-tag-freeze-20260715-boottag`
- **`APP_MASTER_BOOT_PROFILE=tag`** (mode recv → RECV; auto-connect BS* tags)
- embeds the **tag** OTA payload (sha `12681984…`, = piece [1])
- merged_domains.hex sha256 prefix: `1863676228466ef72b0b…`

### [3] ANCHOR firmware  (8 anchors A–H, via BLE OTA from Master_Anchor)
- marker:      `anchor-freeze-20260715`
- build dir:   `build-anchor-freeze-20260715`
- signed.bin sha256: `32769f9a6a8e700be3e5f683703269a8417876d81f3b1df2c30286191492b0c4`
- dfu zip:     `build-anchor-freeze-20260715/dfu_application.zip`
- deployed to: A,B,C,D,E,F,G,H — all OTA class=D (`ota_success_seen=true`).
- NOTE (operator): the anchor image is unchanged from long-standing; the OTA was
  run to **prove anchor OTA is functional** as part of freeze acceptance.

### [4] MASTER_ANCHOR carrier  (B120 nRF5340, J-Link SNR **960148546** — PROTECTED `.protec/noflash960148546`)
- build dir: `build-master-control-b120-m1-master-anchor-freeze-20260715-bootanchor`
- **`APP_MASTER_BOOT_PROFILE=anchor`** (AUTOPOS; rejects wand tags; "no tag scan")
- embeds the **anchor** OTA payload (sha `32769f9a…`, = piece [3])
- merged_domains.hex sha256 prefix: `9054bf34434d0f3aa235…`
- Flash requires `BIOSPUR_ALLOW_PROTECTED_MASTER_ANCHOR_FLASH=1`.

---

## Deploy / restore recipe

**Masters (B120 nRF5340):** use `scripts/flash_b120_master_freeze.sh <SNR> <build_dir>`
(recover + separate-session loadfile + fresh-session 0x0 blank-check). Then
**power-cycle** the B120 for a clean cold boot.

```
# Master_Anchor (protected)
BIOSPUR_ALLOW_PROTECTED_MASTER_ANCHOR_FLASH=1 \
  bash scripts/flash_b120_master_freeze.sh 960148546 build-master-control-b120-m1-master-anchor-freeze-20260715-bootanchor
# Master_Tag
bash scripts/flash_b120_master_freeze.sh 1050070698 build-master-control-b120-m1-master-tag-freeze-20260715-boottag
```

**Tags (from Master_Tag):**
```
python3 scripts/ota_deploy_tag_set.py \
  --port /dev/serial/by-id/usb-Master_Tag_Master_Tag_Control_6918E0384172A49F-if00 \
  --out-dir logs/<dir> --targets BS9336,BS955A,BSCCF4 --prefix BS
```

**Anchors (from Master_Anchor) — the proven recipe (see "Anchor OTA" below):**
per-anchor `ota_single_shot_stable.py`, each preceded by a master reset + settle
to 8/8; NOT the deploy's pre-version phase. Then restore responder roles.

---

## Verification (2026-07-15) — PASS

**Boot-behavior acceptance (permanent, Law 3):**
- Master_Anchor: `Boot profile anchor … no tag scan`; held **conn=0** to all BS wand tags ≥60s while tags advertised. ✅
- Master_Tag: `Boot profile tag …`; connected all 3 wand tags. ✅

**60s ranging (Master_Tag, all 3 tags):**

| tag | ge7 | ge8 | valid% |
|---|---|---|---|
| BS9336 | 0.978 | 0.909 | 96.9 |
| BS955A | 0.979 | 0.936 | 97.3 |
| BSCCF4 | 0.979 | 0.958 | 97.6 |
| **aggregate** | **0.978** | **0.934** | **97.3** |

Gate: ge7≥0.97 ✅ · ge8≥0.90 ✅ · valid%≥96 ✅ · 3 tags ✅ · no TP/RFD/CIR ✅.
`ge7=0.978` matches the recorded fixed-image value. TR format = `TR;3`/blank-D1
(accepted V1; see top). Logs: `logs/freeze_verify_20260715/`, `logs/p0txrf_bootreadback/`.

**Runtime toggles:** DIAG ON → non-empty D1; DIAG OFF → zeroed D1; TXPWR M6/MAX applied. ✅

**Anchor OTA proven functional:** all 8 A–H class=D. Logs:
`logs/freeze_ota_anchorA_single/`, `logs/freeze_ota_anchors_BH_20260715/`,
`logs/freeze_ota_anchor_{C..H}_single/`.

---

## Frozen-firmware laws (V1) — code-anchored, corrected against `experiments/ota_blocker_audit/OTA_BLOCKER_REPORT.md`

1. **Master carrier builds MUST pass an explicit `-DAPP_MASTER_BOOT_PROFILE=<anchor|tag>`.**
   `neutral` is a build error for a role carrier: Master_Anchor=`anchor` (rejects
   wand tags, `master_multi_app.c` ANCHOR-candidate-rejected), Master_Tag=`tag`
   (RECV + BS prefix). The 2026-07-15 tag-grab incident was a `neutral` anchor
   carrier. Verify with `status`/`ota show`/`Boot profile …` after boot.

2. **`control_mode` is NOT in flash** (corrects the earlier "full-erase to clear
   zombie" premise). Master boot mode = compile-time boot profile +
   `__noinit` warm-reboot cookie. A zombie mode is fixed by the correct boot
   profile + a **power cycle** (clears the cookie) — NOT by an erase.
   **Separately and independently:** flashing a **B120 nRF5340** MUST use JLink
   **`recover`** (CTRL-AP mass-erase + unprotect), not plain `erase` — after a
   plain `erase`, debug `loadfile` reports O.K. but does NOT persist across a
   power cycle (blank 0x0 → boot HardFault). Only a **fresh-session** physical
   read of 0x0 proves persistence. Codified in `scripts/flash_b120_master_freeze.sh`.

3. **Boot-behavior verification is permanent acceptance.** After flashing both
   masters, power-cycle and confirm: Master_Anchor prints "no tag scan" and holds
   conn=0 to BS tags ≥60s; Master_Tag connects the wand tags. Any fail → stop.

4. **OTA preflight = check OUR OWN two masters first.** The only hard OTA-lock is
   a tag **held connected** by the wrong master. Before OTA: confirm exactly one
   master owns the tag target-kind; `scan`/power-down any other holder. **OTA does
   NOT need `MODE IDLE`** — `OTA_PREPARE` (sent by the deploy scripts) quiesces the
   tag; a pre-OTA `MODE IDLE` only persists a stopped state. Never hunt external
   "competing centrals" before checking our own masters (2026-07-15: a battery-
   charged Fusion dongle was a red herring; the real holder was our own
   `neutral` Master_Anchor).

5. **Leave tags in RUN; capture scripts must restore `MODE RUN` on every exit.**
   Persisted `MODE IDLE` is not an OTA lock but silently stops ranging; no capture
   script currently restores RUN (tracked for the clean batch).

---

## Hard-won operational findings (2026-07-15) — read before re-running

- **Anchor OTA must be driven from the B120 Master_Anchor** (it is the anchor OTA
  master + BLE comms + AUTOPOS). The 52840 dongle is a **BLE SNIFFER**, not an OTA
  device — do not wait on it. The `--port <52840>` wording in scripts is legacy.
- **Anchor OTA recipe that works:** per-anchor `ota_single_shot_stable.py`
  (`--target-uuid <A..H uuid>`), each preceded by a **JLink reset of Master_Anchor
  + wait for 8/8 anchors `conn=1 ready=1`**. The `ota_deploy_anchor_set.py`
  *pre-version phase* mode-churn destabilizes the master (transient CDC write-
  timeout) → use `--skip-pre-version-verify`, or the per-anchor loop
  (`scratchpad ota_remaining.py` pattern). Anchor A is the finicky one.
- **After anchor OTA, restore responder roles:** send `anchor role all responder`
  via Master_Anchor (the single-shot loop skips the deploy's post-verify). Until
  then tags range with `valid_mask=00`.
- **After tag OTA, reboot Master_Tag** (or clear `ota_target name`): the last tag
  OTA'd leaves `target_name=<that tag>` as the connect filter, so only that one
  tag reconnects. A JLink reset restores `boot=tag` → `prefix=BS` → all 3.

---

## Deferred to the freeze-clean batch

- **[TR-clean] Rebuild the TAG image to emit clean `TR;2` (drop the `D1` trailer
  entirely).** V1 ships `TR;3`/blank-D1 (accepted). The clean-TR;2 tag is a
  freeze-clean deliverable: rebuild tag with the diag-output path compiled out,
  re-OTA the 3 tags, re-verify (ge7/ge8 unchanged expected), then a `freeze-clean`
  tag. Tracked in `experiments/declutter_audit/`.
