# APS011 Deployment — Sequential Flash Checklist

Date 2026-07-11. Execute top to bottom. **Nothing here has been flashed by the
build step — this is your manual runbook.** If any step fails, STOP and report.

## Artifacts (already built + embedded)

| what | path | flash via |
|------|------|-----------|
| Geiger (APS011 + 100 mm) | `build-uwb-listener-modescan-aps011/UWB_listener/zephyr/zephyr.hex` | J-Link SNR **760185886** |
| Master Tag OTA (embeds wand image) | `build-master-ota-wand-aps011/zephyr/zephyr.hex` | J-Link SNR **1050070698** (nRF52840DK) |
| Wand tag image (inside Master OTA payload) | `build-tag-wand-aps011/tag/zephyr/zephyr.signed.bin` | BLE OTA (pushed by Master) |

Embedded payload verified: `verify_ota_payload_kind.py --expected tag` → ok, tag
markers present, no anchor markers; embedded `tag_ota_image_sha256` == `sha256`
of the signed tag binary (`31feaf46…`). Wand image built with
`APP_TAG_WAND_MODE_ENABLE=1` and **`APP_TAG_BLE_NAME_PREFIX=""`** so tags keep
their `BSxxxx` advertised names (the master matches by the `BS` prefix).

Build env for any `west` command below:
```bash
source /tmp/claude-1000/.../scratchpad/ncs_env.sh   # toolchain PATH/PYTHONHOME/PYTHONPATH
cd /mnt/nrf_ssd/nRF_dev/BioSpur_UWB_before_start
```

---

## A. Flash Geiger (J-Link, low risk)
```bash
west flash -d build-uwb-listener-modescan-aps011 --runner jlink --snr 760185886
```
Verify:
- MODE_LISTEN (boot default): LED/buzzer as before.
- Hold button → MODE_SCAN; over USB (`/dev/ttyACM5` area) you get `LSCAN` lines.
  Ranges are now APS011-corrected + 100 mm. Watch 30 s; compare to a pre-flash scan.

## B. Flash Master Tag (J-Link, direct — this is the OTA carrier)
Master Tag = nRF52840DK, J-Link **SNR 1050070698** (per AGENTS.md; `960148546` is
the *Anchor* master — do not confuse them).
```bash
west flash -d build-master-ota-wand-aps011 --runner jlink --snr 1050070698
```
Verify on the Master's CDC console:
- Boots, prints `OTA target filter: …`, BLE scanning starts.
- `ota_target show` → prints current filter (default prefix `BS`).

## C. OTA the wand tags (Master → tags over BLE, ONE AT A TIME)
The three wand tags advertise as **BS9336 (0xB102), BS955A (0xB103), BSCCF4
(0xB104)**. Identify the Master Tag's CDC port first (by SNR 1050070698 / by-id);
call it `$MPORT` (e.g. `/dev/ttyACM_master`).

1. Silence the fleet: `cmd_all MODE IDLE`.
2. Push per tag with the reboot-aware launcher (one at a time):
   ```bash
   python3 scripts_reserve_nomore_change/ota_single_tag_stable.py \
     --port "$MPORT" --target-name BS9336 --target-prefix BS \
     --out-dir logs/ota_wand_aps011_20260711
   #   → after success, verify BS9336 ranges, then repeat:
   #     --target-name BS955A   then   --target-name BSCCF4
   ```
   Or drive all three in sequence:
   ```bash
   python3 scripts_reserve_nomore_change/ota_deploy_tag_set.py \
     --port "$MPORT" --out-dir logs/ota_wand_aps011_20260711 \
     --targets BS9336,BS955A,BSCCF4 --prefix BS
   ```
   (Under the hood these send `ota_target name BS9336` → `ota_target show` →
   start, then wait for the tag to re-advertise as DFU-ready and confirm.)
3. After EACH tag: resume it, confirm it ranges and range magnitudes are sane.
4. After all three: resume all, confirm fleet healthy.
5. **If any tag fails to take the image or does not re-advertise: STOP, report.**
   MCUboot keeps the old slot on a failed/again unconfirmed image — the tag is
   not bricked, but do not force it.

Wand tags get **APS011 only — no +100 mm** (that offset is Geiger-only).

## D. System verification (after all flashing)
- 30 s capture: all 7 listeners + 3 tags. Compare wand/anchor ranges **before vs
  after** APS011 (expect a small RSL-dependent change — a few cm, sign depends on
  distance; short range reads slightly longer, far range slightly shorter).
- All 7 listeners still MODE_LISTEN with CIR?
- Geiger functional in MODE_LISTEN and MODE_SCAN?

## E. Geiger proxy-gate re-scan
```bash
# Geiger in MODE_SCAN; walk same pattern (perimeter, wand area, rotation dwell, 2–3 min)
#   log to: logs/geiger_scan_20260711_post_aps011/scan.log
python3 logs/geiger_scan_20260711_161258_8anchor/analysis/pg_pipeline.py \
   <same args/thresholds as the pre-APS011 run>
```

| metric | before APS011 | after APS011 | improved? |
|--------|---------------|--------------|-----------|
| LOO median mm | 158 | ??? | |
| slope % | +3.65 | ??? | |
| common-mode mm | −100 | ??? | |
| best partial rho | −0.103 | ??? | |
| best AUC | 0.62 | ??? | |
| verdict | UNDERPOWERED | ??? | |

Expectation: the +100 mm Geiger offset should absorb the −100 mm common-mode
(post ≈ 0), and APS011's RSL slope should absorb part of the +3.65 %. The
predictive proxy-gate correlation (partial rho / AUC) is a separate question and
is not expected to move much from range calibration alone.

---

## Rebuild-from-scratch recipe (if you need to change wand params)
```bash
source .../ncs_env.sh && cd <repo>
# 1) wand tag (set WAND_MODE_ENABLE "1" default in apps/tag/CMakeLists.txt, build, revert):
west build -b decawave_dwm1001_dev/nrf52832 -s apps/tag -d build-tag-wand-aps011 -p always
# 2) embed + verify:
python3 scripts_reserve_nomore_change/gen_ota_image_inc.py \
    build-tag-wand-aps011/tag/zephyr/zephyr.signed.bin apps/master_ota/generated/ota_image.inc
python3 scripts_reserve_nomore_change/verify_ota_payload_kind.py --expected tag
# 3) master:
west build -b nrf52840dk/nrf52840 -s apps/master_ota -d build-master-ota-wand-aps011 --no-sysbuild -p always
```
Keep `APP_TAG_BLE_NAME_PREFIX=""` unless you deliberately want to rename the tags
(a non-empty prefix changes the advertised name and breaks the `BS`-prefix match).
