# solvers/ — frozen positioning-solver set (copied 2026-07-17)

Every positioning solver + variant in the repo, copied here "一个不漏" (miss nothing).
CODE + small configs only — the 351 MB / 16 GB data trees were excluded (see DEPS_MANIFEST.md).
Integrity: `SHA256SUMS.txt`. Per-file source path / purpose / deps: `DEPS_MANIFEST.md`.
Operational usage + how to reproduce: `../../SCRIPTS_GUIDE.md` (Positioning Pipeline section).

## Layout
```
erlangen_deployment_v4io_t4/        ★ PRIMARY — Erlangen 2026-05-28 field deployment
  ERLANGEN_CHAIN.md                    (V4-io + T4 = 72.7 mm static / 102.6 mm dynamic)
  stage1_layout/                       V4-io anchor-layout solver (+ core + preprocessor)
  stage2_position/                     biospur package @ current tree = U5 (prebuilt .so)
  stage2_position_T4_pristine/         biospur package @ git 3acfeeda5 = T4 (rebuilt .so)
  drivers/run_original_t4.py           T4 driver
  reference_layout_inputs/             layout/sigma JSON used for validation
  erlangen_provenance/                 deployed-solver source snapshots (provenance)
other_variants/                     other / research / follow-up (install-all, labelled)
  v5/solve_v5.py                       V5 Stage-1 layout (scale-locked) — research follow-up
  u5_note.md                           U5 == stage2_position/ (pointer, not a copy)
  multilaterate/                       DEPLOYED home-CIR-rig static Stage-2 (calibrate_listener_positions)
  research_drivers/                    offline A/B: v5u5_vs_v4iot4, v5_vs_v4io, solver_v2_validation
  historical/                          superseded Stage-1: solve_anchor_layout v1/v2/v3, solve_v4_fusion
```

## The one thing to understand first
There are **two different Stage-2 tag-position solvers**, for two different systems —
this is not a contradiction:
- **T4 / U5** (biospur C-core) = the Erlangen field-deployment lineage + its offline
  research/consolidation. **T4 = the Erlangen production Stage-2** (this is the ★ chain).
- **multilaterate** (`calibrate_listener_positions.py`) = the Stage-2 that produced the
  **home CIR-listener rig's** committed *static-calibration* positions (`wand_positions.json`).

Stage-1 for both = **V4-io** (V5 is a research follow-up). Nothing here is a live wand
*tracker* — `wand_positions.json` is a static calibration snapshot for the imaging channel matrix.
