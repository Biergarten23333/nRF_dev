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

## Build strategy

- Building the repository root keeps backward compatibility and currently maps
  to the Anchor app.
- Building `apps/anchor/` gives a clean Anchor-only target.
- Building `apps/tag/` gives a clean Tag-only target.
- Building `apps/master/` gives a clean external-master target.

## Codex guidance

- Change shared UWB behavior in `include/`, `src/`, and `drivers/dw1000/`.
- Change only Anchor behavior in `apps/anchor/`.
- Change only Tag behavior in `apps/tag/`.
- Change only external control-plane behavior in `apps/master/`.
- When a protocol/frame change affects both roles, update shared interfaces
  first, then adjust both role apps explicitly.
