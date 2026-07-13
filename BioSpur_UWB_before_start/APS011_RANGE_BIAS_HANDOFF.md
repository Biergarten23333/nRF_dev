# APS011 Range-Bias Correction + Geiger Delay Calibration — Handoff

Date: 2026-07-11. Firmware branch: `feature/wand-internal-sweep`.
Author: Claude Code (implementation + builds). **Flashing is manual — steps below.**

---

## 1. What changed (code)

APS011 §3.4 correction (`actual = reported − dwt_getrangebias(chan, range_m, prf)`)
applied to every DW1000 SS-TWR range. `dwt_getrangebias()` was already linked
(`drivers/dw1000/src/deca_range_tables.c`) but had **zero call sites** and **no
header prototype** — it was being garbage-collected out. Verified contract from
the definition: **input `range` is in metres, return value is the correction in
metres** (ch5 / PRF64 table = `range25cm64PRFnb[3]`, offset span −17…+8 cm).

| file | change |
|------|--------|
| `src/ss_twr_init.c` | Added local `extern` prototype + `static ss_twr_init_range_bias_correct_mm()` helper. Applied at **both** range sites: wand peer ranging (`ss_twr_init_wand_range_peer`, gated by `APP_TAG_WAND_MODE_ENABLE`) and normal anchor ranging (feeds the tag's own position tracker). |
| `UWB_listener/src/main.c` | Added `extern` prototype + `#define GEIGER_ANTENNA_DELAY_OFFSET_MM 100`. In `scan_calc_range_mm()`: APS011 correction, **then** `+100 mm` antenna-delay intercept (from the P2 gauge fit). Applied to every anchor range. |
| anchor firmware | **UNCHANGED.** Range is computed tag-side only (`ss_twr_resp.c` just timestamps + responds). No anchor OTA needed. |

Decision worth noting: I corrected **both** range sites in `ss_twr_init.c`, not
just the wand-peer path. Both suffer the identical RSL-dependent bias, so a shared
helper keeps them consistent. If you want to isolate the effect and leave the
tag's self-position path untouched, revert site 2 (the call at the normal
anchor-ranging path) and keep only the wand-peer call.

The `+100 mm` Geiger offset lives **only** in `UWB_listener/src/main.c`. The wand
tags get APS011 only — no delay offset.

---

## 2. Builds (already done)

NCS v2.8.0, board `decawave_dwm1001_dev/nrf52832`, toolchain `b81a7cd864`.
Build environment (the old `scripts/` are gone — reconstructed from
`~/ncs/toolchains/b81a7cd864/environment.json`):

```bash
source /tmp/claude-1000/.../scratchpad/ncs_env.sh   # or set PYTHONHOME/PYTHONPATH/PATH to the toolchain
```

### Geiger (turnkey — direct J-Link flash OK)
```bash
west build -b decawave_dwm1001_dev/nrf52832 -d build-uwb-listener-modescan-aps011 UWB_listener -p always
```
- Image: `build-uwb-listener-modescan-aps011/UWB_listener/zephyr/zephyr.hex`
  (identical content to `.../merged.hex`; single image, no bootloader).
- Size: FLASH 34901 → **37093 B** (+2192, the now-linked `dwt_getrangebias` + 4
  range tables). RAM **unchanged** (13752 B). No warnings.

### Wand tag (OTA image — deployed via Master OTA; see `APS011_DEPLOY_CHECKLIST.md`)
The current OTA-capable wand app is **`apps/tag`** (not the older `apps/tag_ota`):
it has `sysbuild.conf`+MCUboot, forwards the wand flags natively, and has the
maintained sign version. Built with wand mode on and the **default (empty) name
prefix** so tags keep their `BSxxxx` advertised names (the Master OTA matches by
the `BS` prefix; a non-empty prefix would rename them to `Wand-…-BSxxxx` and break
name-matching + the deploy tooling):
```bash
# apps/tag defaults APP_TAG_WAND_MODE_ENABLE=0, APP_TAG_BLE_NAME_PREFIX="".
# NCS 2.8 sysbuild does NOT forward -D<image>_<var> CMake cache vars, so set only
# WAND_MODE_ENABLE "1" (leave NAME_PREFIX "") in apps/tag/CMakeLists.txt, build,
# then revert the file — which is exactly what was done here:
west build -b decawave_dwm1001_dev/nrf52832 -d build-tag-wand-aps011 apps/tag -p always
```
- Verified: `APP_TAG_WAND_MODE_ENABLE=1`, `APP_TAG_BLE_NAME_PREFIX=""`, wand path
  compiled (`ss_twr_init_wand_set_enabled` present), OTA capability intact
  (`APP_TAG_BLE_ENABLE=1`, `APP_TAG_BLE_OTA_ENABLE=1`, `APP_TAG_MCUBOOT_ENABLE=1`,
  `CONFIG_BT=y`, `CONFIG_BOOTLOADER_MCUBOOT=y`).
- Signed OTA image: `build-tag-wand-aps011/tag/zephyr/zephyr.signed.bin`
  (also `dfu_application.zip`, and `merged.hex` for J-Link recovery only).
- Size: app FLASH **201192 B / 84.87 %**, RAM **61984 B / 94.58 %** of the 64 KB.
  APS011 delta ≈ **+2.2 KB FLASH, +0 RAM** (same code as the Geiger, measured
  exactly +2172 B there; tables are const/rodata so RAM does not move — no RAM
  headroom risk from this change).
- Tag identity is assigned at **runtime** (`ss_twr_init_local_tag_id =
  params->logical_tag_id`, roster-driven), so **one image serves all three wand
  tags** (BS9336=0xB102, BS955A=0xB103, BSCCF4=0xB104 → tag IDs 2/3/4).

| firmware | APS011 | delay offset | image path | size delta |
|----------|--------|--------------|------------|-----------|
| wand tag | YES | NO | `build-tag-wand-aps011/tag/zephyr/zephyr.signed.bin` | ~+2.2 KB FLASH / +0 RAM |
| geiger   | YES | +100 mm | `build-uwb-listener-modescan-aps011/UWB_listener/zephyr/zephyr.hex` | +2192 B FLASH / +0 RAM |
| anchor   | — | — | (unchanged) | — |

---

## 3. Manual steps (you)

### 3.1 — Flash Geiger (USB / J-Link, low risk)
Geiger J-Link S/N **760185886** (≈ /dev/ttyACM5 area). Direct J-Link flash of the
Geiger/listener is allowed (it is not a fleet tag/anchor).
```bash
west flash -d build-uwb-listener-modescan-aps011 --snr 760185886
# or your usual J-Link flow with that SNR + build-uwb-listener-modescan-aps011/UWB_listener/zephyr/zephyr.hex
```
Verify:
- MODE_LISTEN (boot default): LED/buzzer behave as before.
- MODE_SCAN via USB: `LSCAN` lines appear; ranges are now APS011-corrected +100 mm.
- Compare 30 s of scan ranges to a pre-correction capture (expect roughly a
  common-mode shift + a mild RSL-dependent slope).

### 3.2 — Flash wand tags (BLE OTA via Master Tag)
The wand image is **already embedded** into the Master OTA payload
(`apps/master_ota/generated/ota_image.inc`, verified kind=tag, sha256-matched) and
`apps/master_ota` is built at `build-master-ota-wand-aps011`. Deployment = flash
the Master Tag (J-Link SNR 1050070698), then have it push to each wand tag over
BLE. **Full sequential runbook is in [`APS011_DEPLOY_CHECKLIST.md`](APS011_DEPLOY_CHECKLIST.md).**

Summary:
1. Silence all tags: `cmd_all MODE IDLE`.
2. Flash Master Tag: `west flash -d build-master-ota-wand-aps011 --runner jlink --snr 1050070698`.
3. OTA each tag one at a time (BS9336 → BS955A → BSCCF4) via
   `ota_single_tag_stable.py --target-name BSxxxx` (or `ota_deploy_tag_set.py --targets …`).
4. After each: resume, verify ranging. After all three: resume all, verify fleet.
5. **If OTA fails or a tag is unresponsive: STOP. Do not brick** (MCUboot keeps the
   old slot on an unconfirmed image, so a failed push is recoverable).

Wand tags get APS011 only — **no** delay offset.

### 3.3 — Verification after flashing
- 30 s wand-tag capture (all 7 listeners + 3 tags). Compare ranges before vs after
  APS011 (expect a small RSL-dependent change; a few cm, sign depends on distance).
- Confirm all 7 listeners still in MODE_LISTEN with CIR.
- Confirm Geiger functional in both MODE_LISTEN and MODE_SCAN.

### 3.4 — Re-run Geiger proxy-gate scan
- Switch Geiger to MODE_SCAN; walk the same pattern as before (perimeter, wand
  area, rotation dwell, 2–3 min).
- Save to: `logs/geiger_scan_20260711_post_aps011/scan.log`.
- Re-run `pg_pipeline.py` with the **same** thresholds. Fill:

| metric | before APS011 | after APS011 | improved? |
|--------|---------------|--------------|-----------|
| LOO median mm | 158 | ??? | |
| slope % | +3.65 | ??? | |
| common-mode mm | −100 | ??? | |
| best partial rho | −0.103 | ??? | |
| best AUC | 0.62 | ??? | |
| verdict | UNDERPOWERED | ??? | |

Note: the −100 mm common-mode should now be absorbed by the +100 mm Geiger offset
(so post-correction common-mode ≈ 0), and the +3.65 % slope partly absorbed by the
APS011 RSL slope. The proxy-gate correlation is a separate (predictive) question
and is not expected to move much from range calibration alone.

---

## Constraints checklist
- [x] All outputs under repo path.
- [x] Anchor firmware NOT modified (range is tag-side only).
- [x] Overnight capture not disrupted (no ttyACM port was held open; no process running).
- [x] Units explicitly verified (getrangebias in/out = metres) before implementing.
- [x] Geiger delay offset ONLY in Geiger firmware.
- [x] Build succeeded before any flash instruction (both images build clean).
- [x] `apps/tag/CMakeLists.txt` restored (git clean); only `src/ss_twr_init.c` +
      `UWB_listener/src/main.c` changed.
- CPU: 12-thread i7-8700K, load ~2.8 during builds (builds are the only heavy step;
  no analysis run yet — that comes in §3.4 with `pg_pipeline.py`).
