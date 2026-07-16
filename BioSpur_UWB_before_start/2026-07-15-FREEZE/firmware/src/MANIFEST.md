# MANIFEST — firmware source snapshot provenance

**Provenance:** every file here was extracted via `git archive freeze-clean-20260716`
(tag → commit `8b68ee0a`) from `SS-TWR/alt-SS-TWR/broadcast/` (the ONLY live firmware tree;
repo-root `src/` is dead and not referenced). Tracked-source only — build outputs / logs /
`*.hex` auto-excluded by `git archive`. **COPY, not move** — the live tree is untouched.

**Exactness:** no firmware source file changed after `freeze-clean-20260716` (the only later
commits build the `2026-07-15-FREEZE/` archive itself), so this snapshot is byte-identical to
the source that produced the frozen binaries in `../`. Integrity: `SHA256SUMS.txt` (150 files).

**Directory mapping** (each `firmware/src/X` ⟵ `SS-TWR/alt-SS-TWR/broadcast/X` @ `freeze-clean-20260716`):

| here | ⟵ source | role |
|---|---|---|
| `src/` (incl. `src/anchors/unified/`) | `broadcast/src/` | shared C: ss_twr_init (initiator), ss_twr_resp (responder), uwb_tdma, tag_ble, anchor BLE modules, … |
| `include/` | `broadcast/include/` | shared headers (17) |
| `drivers/dw1000/` | `broadcast/drivers/dw1000/` | **in-repo DecaWave DW1000 driver** (NOT in the SDK — required) |
| `apps/tag/` | `broadcast/apps/tag/` | TAG app (initiator) |
| `apps/anchor/` | `broadcast/apps/anchor/` | ANCHOR app (responder) — needs `prj_ota.conf` |
| `apps/master_control/` | `broadcast/apps/master_control/` | MASTER app (both masters; boot-profile flag; batch6b guard#1) |
| `apps/master/` | `broadcast/apps/master/` | `master_multi_app.c` (linked into master_control) |
| `apps/master_ota/` | `broadcast/apps/master_ota/` | OTA-payload embed (`generated/ota_image.inc` + manifests) |
| `UWB_listener/` | `broadcast/UWB_listener/` | LISTENER app (RX-only → gateway for reverse) |
| `configs/` | `broadcast/configs/` | B120 master conf files (tag/anchor lfrc) |
| `boards/`, `CMakeLists.txt` | `broadcast/{boards,CMakeLists.txt}` | present for fidelity (not on any of the 5 build paths) |
| `scripts/` | `broadcast/scripts/build_*` + helpers (13) | the build wrappers (drop-in; compute repo_root relative to themselves) |

**The five pieces:** tag=`apps/tag`, anchor=`apps/anchor`, master_tag/master_anchor=`apps/master_control`
(distinguished ONLY by `-DAPP_MASTER_BOOT_PROFILE=tag|anchor`), listener=`UWB_listener`. Full
`-D`→piece table + rebuild recipe + guard self-test in **`BUILD.md`**.

**NOT included (by design — same-machine handoff):** the nRF Connect SDK v2.8.0 / west workspace /
toolchain (on-machine at `NCS_ROOT`, referenced by path); `.protec/noflash*` (machine-local flash
guard, optional); all `build-*/`, `logs/`, non-`build_*` scripts.

**Completeness:** verified — 0 dangling `#include`/CMake references; listener test-built clean
from this snapshot (see BUILD.md). Cleanly-separable subtree; only external dep is the SDK.
