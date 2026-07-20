# BUILD.md — build the Task A tag and reproduce baseline roles

This is now the writable `fusion-link` derivative. Build the Task A tag with:

```bash
./scripts/build_tag_ble_unified.sh 0 10 build-tag-fusion-link
```

The wrapper's working-copy default firmware marker is
`tag-fusion-link-v2`. The remaining recipes below document how the imported
baseline roles were built; Task A does not modify or rebuild anchor/master
firmware.

Imported from the four-piece firmware + listener at tag
**`freeze-clean-20260716`** (commit `8b68ee0a`). This directory still mirrors
the original build layout, and the scripts compute
`repo_root="$(dirname "$0")/.."`, so they resolve `apps/*`, `UWB_listener`,
`configs/`, `src/`, `drivers/`, and `apps/master_ota/generated/` locally.

> **✅ VERIFIED BUILDABLE (2026-07-17):** the listener piece was test-built from THIS snapshot
> (`./scripts/build_uwb_listener_poll_diag.sh freeze-verify`) — clean compile, 153/153 targets,
> linked `zephyr.elf` (FLASH 6.54 %), produced a valid `zephyr.hex`. The `#include`/CMake closure
> resolves with **zero dangling references** and all three batch6b guards are present. The build
> artifacts were removed after verification.

## Prerequisites (SDK stays on the machine — NOT in this snapshot)
- **nRF Connect SDK v2.8.0 + west workspace + toolchain, already installed on this machine**
  at `NCS_ROOT` (default `/home/zekaixiao/ncs/v2.8.0`; toolchain `~/ncs/toolchains/b81a7cd864`).
  Override with `NCS_ROOT=<path> WEST_BIN=<path/to/west>` if elsewhere.
  *A different physical machine would need the same NCS v2.8.0 SDK installed — this snapshot
  deliberately does not carry it (same-machine handoff).*
- Boards used: `decawave_dwm1001_dev/nrf52832` (tag/anchor/listener, DW1000) and
  `nrf5340dk/nrf5340/cpuapp` (both B120 masters).

## Imported baseline recipes (run from THIS directory)
```bash
cd <this firmware/src dir>

# 1) TAG  (initiator; apps/tag; sysbuild+MCUboot)
./scripts/build_tag_ble_unified.sh 0 10 build-tag-freeze-clean-20260716

# 2) ANCHOR  (responder; apps/anchor; sysbuild+MCUboot; prj.conf;prj_ota.conf)
./scripts/build_anchor_ota_control_bundle.sh \
     build-anchor-freeze-clean-20260716 build-anchor-control-freeze-clean-20260716 \
     anchor-freeze-clean-20260716

# 3) MASTER_TAG  (apps/master_control; boot profile = TAG)
./scripts/build_master_tag.sh    build-master-tag-freeze-clean-20260716-boottag

# 4) MASTER_ANCHOR  (apps/master_control; boot profile = ANCHOR)
./scripts/build_master_anchor.sh build-master-anchor-freeze-clean-20260716-bootanchor

# 5) LISTENER  (RX-only; UWB_listener; --no-sysbuild)
./scripts/build_uwb_listener_poll_diag.sh listener-freeze-20260715
```
Output `.hex` lands in `<build_dir>/…/zephyr/merged.hex` (sysbuild pieces) or
`<build_dir>/zephyr/zephyr.hex` (listener). `write_build_source.py` drops a `.source` sidecar.

## What actually distinguishes the five (the `-D` → piece map)
All five compile from ONE shared `src/` + `drivers/dw1000/`; they differ by app dir, board,
conf, and role flags. **The masters are the SAME app + SAME board — they differ by ONE flag:**

| piece | app (`west -s`) | board | role-defining flags |
|---|---|---|---|
| tag | `apps/tag` | dwm1001/nrf52832 | initiator: `-DAPP_ALT_SS_TWR_MODE=2 -DAPP_TAG_BLE_ENABLE=1 -DAPP_TAG_TDMA_ENABLE=1 …` (compiles `ss_twr_init.c`) |
| anchor | `apps/anchor` | dwm1001/nrf52832 | responder: `-DAPP_ANCHOR_ROLE=0 -DAPP_ANCHOR_SCHEDULE_MODE=2 …` + `CONF_FILE="prj.conf;prj_ota.conf"` (compiles `ss_twr_resp.c`) |
| **master_tag** | `apps/master_control` | nrf5340/cpuapp | **`-DAPP_MASTER_BOOT_PROFILE=tag`** + `EXTRA_CONF_FILE=configs/b120_master_tag_lfrc.conf` |
| **master_anchor** | `apps/master_control` | nrf5340/cpuapp | **`-DAPP_MASTER_BOOT_PROFILE=anchor`** + `EXTRA_CONF_FILE=configs/b120_master_anchor_lfrc.conf` |
| listener | `UWB_listener` | dwm1001/nrf52832 | RX-only: `-DAPP_LISTENER_CIR_CAPTURE_ENABLE -DAPP_LISTENER_TAG_ADDR=0xB1C0 …` |

The wrappers inject the boot flag: `build_master_tag.sh:33` (`…BOOT_PROFILE=tag…`),
`build_master_anchor.sh:28` (`…BOOT_PROFILE=anchor…`). This is the boot-profile distinction
behind the 2026-07-15 tag-grab incident — get it right per master.

## Self-test — the batch6b compile-time guards MUST fire
These are part of the source; a correct build tree enforces them:
1. **Neutral master = build error.** `apps/master_control/CMakeLists.txt:44-48` raises a CMake
   `FATAL_ERROR` if `APP_MASTER_BOOT_PROFILE` is neither `tag` nor `anchor`. Test:
   `WEST_BIN=west NCS_ROOT=… (cd $NCS_ROOT && west build -b nrf5340dk/nrf5340/cpuapp -s <this>/apps/master_control --no-sysbuild)`
   with **no** `-DAPP_MASTER_BOOT_PROFILE` → must fail with *"neutral is a build error"*.
2. **DIAG hot-path guard** `src/ss_twr_init.c:254` — `#error` if `APP_TAG_RF_DIAG_TAG_RX_ENABLE=1`
   AND `APP_TAG_RF_DIAG_RUNTIME_DEFAULT_ON=1` (the ge7=0 combo).
3. **fixed-a19 guard** `src/ss_twr_resp.c:123` — `#error` if `APP_ANCHOR_RESP_PAYLOAD_DIAG_V2_ENABLE=1`
   without `APP_ANCHOR_RESP_POST_TX_DIAG_PAYLOAD_DELAY_ENABLE=1`.

## Verify a rebuild vs the frozen binary
Zephyr embeds a build timestamp, so **a rebuild is NOT byte-identical** to the frozen `.hex`
in `../` — do **not** expect the sha256 to match `../SHA256SUMS.txt`. Expected: **functional
equivalence** (same size class, same behavior). Confirm by flashing + the boot banner
(`=== MASTER BOOT: profile=… ===`) and ge7 (see `../../HARDWARE_STATE.md`, `../../SCRIPTS_GUIDE.md`).
The frozen `.hex`/`.signed.bin` in `../` remain the flash-what-was-verified reference.

## Notes / couplings
- **Master OTA payload:** the master carriers embed `apps/master_ota/generated/{ota_image.inc,
  *_ota_manifest.*, active_ota_payload.json}` — the frozen snapshot of these is included here.
  `build_master_tag.sh`/`build_master_anchor.sh` gate on `active_ota_payload.json` via
  `assert_active_ota_payload.py --expected tag|anchor`. To rebuild a master with a *different*
  payload, re-stage `generated/` with `prepare_alt_ota_payload.py` first (as the tag/anchor
  OTA-bundle build does). As-is, it reproduces the frozen master.
- `.protec/noflash960148546` is a machine-local flash guard, **not** in this snapshot; the
  master wrappers source it only `if [ -f ]` — its absence is non-fatal.
- Top-level `CMakeLists.txt` + `boards/your_board.overlay` + `src/uwb_control_proto.*` +
  `apps/master/src/main.c` + `apps/master_ota/src/bt_rand.c` are present for fidelity but are
  **not on any of the five build paths** (see MANIFEST.md).

## Reverse SS-TWR — where to edit
- **Tag adds a FINAL frame / fix the TDMA slot-execution path:** `src/ss_twr_init.c` (initiator
  state machine) + `src/uwb_tdma.c` + `src/broadcast_tdma.c` (the epoch-synced slot path that
  currently transmits zero — the #1 blocker in `../../FREEZE_STATE.md`).
- **Gateway FINAL-frame parsing:** `UWB_listener/src/main.c` (RX-only listener → gateway).
- Rebuild the affected piece per the recipe above.
