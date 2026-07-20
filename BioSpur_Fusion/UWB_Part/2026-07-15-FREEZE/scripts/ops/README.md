# ops/ — operational scripts (flash / OTA / capture / AutoPos / escape hatch)

Copied **verbatim** (2026-07-17) from `SS-TWR/alt-SS-TWR/broadcast/scripts/` (tag
`freeze-clean-20260716`). Kept **flat** because these import each other as same-dir
siblings — do not split into subfolders or the imports break. Integrity: `SHA256SUMS.txt`.
Usage + workflows: `../../SCRIPTS_GUIDE.md` (Operational scripts section).

**To RUN:** invoke from the live `broadcast/scripts/` checkout — they reference that tree's
firmware build dirs, an optional `.protec/biospur_ports.env`, and (one optional CIR path)
`flutter_ui_autopos/`. This copy is the frozen inventory + reference.

## Entry-point scripts (what you invoke)
| Script | Role |
|---|---|
| `flash_b120_master_freeze.sh` | JLink-flash a B120 master (recover; Master_Anchor needs the protected-flash env) |
| `flash_listener_freeze.sh` | USB-JLink-flash a listener (recover; never the Geiger SNR) |
| `jlink_reset_by_snr.sh` | cold JLink reset by SNR — load-bearing for anchor-OTA recovery |
| `ota_preflight.py` | pre-OTA inventory of both masters; routes to the escape hatch if a master holds tags |
| `release_all_tags.py` | escape hatch — unstick a master holding the wand tags |
| `ota_deploy_tag_set.py` | BLE-OTA the 3 wand tags from Master_Tag |
| `ota_deploy_anchor_set.py` | BLE-OTA anchors A–H from Master_Anchor (per-anchor reset + wait 8/8) |
| `run_recv_tdma_capture.py` | capture the wand `TR;2` stream via Master_Tag (live CFG_STOP on exit) |
| `parse_recv_tdma_raw.py` | offline-parse a captured raw log → per-sweep records |
| `run_autopos_sweep_loop.py` | drive the anchor↔anchor AutoPos sweep from Master_Anchor → summary.json |
| `autopos_extract_pairs_from_sweep_summary.py` | summary.json (SW-) → inter-anchor pairs (feeds V4-io) |
| `push_apos_layout_verified.py` | push a solved anchor layout back to the anchors (verified) |

## Dependency modules (imported/called by the above — kept as siblings)
| Module | Used by |
|---|---|
| `master_control_port.py` | `run_recv_tdma_capture.py`, `run_autopos_sweep_loop.py` (port resolution + JLink-CDC guard) |
| `run_autopos_round.py` | `run_autopos_sweep_loop.py`, `verify_all_anchor_responder_runtime.py` (UUIDS) |
| `scan_and_map.py` | `run_autopos_sweep_loop.py` |
| `quarantine_tags.py` | `run_autopos_sweep_loop.py` |
| `verify_all_anchor_responder_runtime.py` | `ota_deploy_anchor_set.py`, `run_recv_tdma_capture.py` (sends `anchor role all responder`) |
| `verify_ota_payload_kind.py` | `ota_deploy_anchor_set.py`, `ota_deploy_tag_set.py` |
| `ota_single_shot_stable.py` | `ota_deploy_anchor_set.py` (per-anchor OTA unit) |
| `ota_single_tag_stable.py` | `ota_deploy_tag_set.py` (per-tag OTA unit) |

Optional/not copied: `flutter_ui_autopos/scripts/cir_full_usb_capture.py` (only for the
CIR-full-over-USB capture path in `run_recv_tdma_capture.py`).
