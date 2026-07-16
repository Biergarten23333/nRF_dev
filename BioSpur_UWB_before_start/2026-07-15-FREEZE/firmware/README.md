# Freeze firmware snapshot — 2026-07-15-FREEZE

Copied from the live tree (`SS-TWR/alt-SS-TWR/broadcast/build-*`) 2026-07-16.
Source of truth: git tag **`freeze-clean-20260716`** (commit `8b68ee0aa`, parent
`freeze-4piece-20260715` = `642e4a33`). SHA256 of every file: `SHA256SUMS.txt`.

| Piece | file(s) here | flash/OTA to | how | marker / commit | signed.bin / hex sha256 |
|---|---|---|---|---|---|
| **TAG** | `tag/tag-freeze-clean-20260716.{signed.bin,dfu_application.zip,merged.hex}` | wand tags **BS9336, BS955A, BSCCF4** | **BLE OTA** from Master_Tag (`ota_deploy_tag_set.py`) | `tag-freeze-clean-20260716` / `8b68ee0aa` | signed `a0c7007f…` |
| **ANCHOR** | `anchor/anchor-freeze-clean-20260716.{signed.bin,dfu_application.zip,merged.hex}` | anchors **A–H** | **BLE OTA** from Master_Anchor (`ota_deploy_anchor_set.py`, per-anchor reset) | `anchor-freeze-clean-20260716` / `8b68ee0aa` | signed `6fdef0b7…` |
| **MASTER_TAG carrier** | `master_tag/…-boottag.merged.hex` | **B120 SNR 1050070698** | JLink flash (`flash_b120_master_freeze.sh`) | boot=tag + 6a banner / `8b68ee0aa` | merged `ded58a94…` |
| **MASTER_ANCHOR carrier** | `master_anchor/…-bootanchor.merged.hex` | **B120 SNR 960148546 (PROTECTED)** | JLink flash + `BIOSPUR_ALLOW_PROTECTED_MASTER_ANCHOR_FLASH=1` | boot=anchor + 6a banner / `8b68ee0aa` | merged `330b6fd8…` |
| **LISTENER** | `listener/listener-freeze-20260715.zephyr.hex` | 9 listeners (USB J-Link) — **NEVER SNR 760185886 (Geiger)** | `build_uwb_listener_poll_diag.sh listener-freeze-20260715` + USB JLink flash | build `listener-freeze-20260715` (CIR=1, id=255) | `c4cff12b…` |

## Notes
- **ANCHOR binary is byte-identical to `anchor-freeze-20260715`** — the only
  difference from the freeze-4piece anchor is the embedded marker string
  (`anchor-freeze-clean-20260716`). batch6b added only a compile-time `#error`
  guard to `ss_twr_resp.c` (no emitted code). See FREEZE_STATE.md.
- **Current on-rig anchor markers are MIXED (cosmetic):** anchors A/B/C run
  `anchor-freeze-clean-20260716`; D–H still run `anchor-freeze-20260715`. Same
  binary. This snapshot ships the freeze-clean-marked build for all 8.
- **TAG** is the clean-TR;2 image (DIAG default OFF → literal `TR;2`, no `;D1`,
  no `;TP`). DIAG on → `TR;3` + `;D1` (runtime toggle only).
- Build recipes: each piece's `build.source.json` records the exact build command.
  Full rebuild from source = check out `freeze-clean-20260716` and re-run the recipe
  (see SCRIPTS_GUIDE.md).
