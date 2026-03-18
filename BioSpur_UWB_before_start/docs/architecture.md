# BioSpur UWB Layout

This repository is structured around a shared UWB core plus two role-specific
applications plus an external master control app.

## Shared code

- `drivers/dw1000/`: imported Decawave DW1000 driver sources and headers.
- `include/`: public interfaces shared by both roles.
- `src/`: shared transport, bring-up, and protocol implementations.

## Role apps

- `apps/anchor/`: Anchor-specific app entry, scheduling, and future anchor-only logic.
- `apps/tag/`: Tag-specific app entry, measurement, and future tag-only logic.
- `apps/master/`: external control-plane app intended for the nRF54L15 DK.

## BLE control plane

- The nRF54L15 DK is now the BLE central and control host.
- The DWM1001 tag exposes a BLE peripheral service for health, status, and
  OTA handshake traffic.
- The BLE path is the control plane; UWB still carries ranging and localization.
- Full BLE OTA requires a bootloader-ready tag image and reserved upgrade
  partitions. See [ble_ota_plan.md](ble_ota_plan.md).

## Build strategy

- Building the repository root keeps backward compatibility and currently maps
  to the Anchor app.
- Building `apps/anchor/` gives a clean Anchor-only target.
- Building `apps/tag/` gives a clean Tag-only target.
- Building `apps/master/` gives a clean external-master target.

## Current Tag Architecture

- Static calibration and single-tag exploration still exist, but the active
  default path is `adaptive 8-anchor SS-TWR + quality-based subset selection`.
- Fixed-anchor and TDMA builds are still available as explicit multi-tag
  variants, but they are not the default Tag runtime.
- Each Tag keeps the full 8-anchor table for shared topology knowledge.
- Each Tag can still be assigned:
  - one fixed non-coplanar 4-anchor subset for TDMA/multi-tag experiments
  - one TDMA slot index inside a shared slot cycle
- The Tag runtime config now carries:
  - `tag_id`
  - full anchor table
  - fixed-anchor mode and fixed anchor IDs
  - TDMA slot settings

## Multi-Tag Build Path

- Use `scripts/build_tag_adaptive.sh` to build the default adaptive Tag image
  that ranges all 8 anchors and lets the solver choose the best subset.
- Use `scripts/build_tag_tdma.sh` to build a per-Tag image with:
  - `tag_id`
  - `slot_index`
  - `slot_count`
  - `slot_period_ms`
  - `slot_active_ms`
  - fixed 4-anchor subset
- Example:
  - `scripts/build_tag_adaptive.sh 0`
  - `scripts/build_tag_tdma.sh 0 0`
  - `scripts/build_tag_tdma.sh 1 1`
- The current baseline target for scaling is `10 tags`, `10 slots`,
  `10 ms/slot`, `9 ms active window`.

## Codex guidance

- Change shared UWB behavior in `include/`, `src/`, and `drivers/dw1000/`.
- Change only Anchor behavior in `apps/anchor/`.
- Change only Tag behavior in `apps/tag/`.
- Change only external control-plane behavior in `apps/master/`.
- When a protocol/frame change affects both roles, update shared interfaces
  first, then adjust both role apps explicitly.
