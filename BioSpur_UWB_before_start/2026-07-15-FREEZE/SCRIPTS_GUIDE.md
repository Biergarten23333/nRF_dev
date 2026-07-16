# SCRIPTS_GUIDE — 2026-07-15-FREEZE

Guide to the frozen scripts shipped in this handoff. This revision documents the
**Positioning Pipeline** (solver chain). Operational scripts (OTA / capture / flash /
AutoPos sweep) live in the live tree and are indexed by `docs/COMMAND_REFERENCE.md` +
`docs/DEPLOYMENT.md`; copy them into `scripts/` in a follow-up if the handoff needs them
standalone.

---

# Positioning Pipeline

A **two-stage** chain. Stage-1 solves the anchor layout from anchor↔anchor ranging;
Stage-2 solves tag position from tag↔anchor ranging **+ the Stage-1 layout**. The handoff
between stages is **file-mediated**: Stage-1 writes an `anchor_layout*.json`, Stage-2 reads it.

```
anchor↔anchor ranging ─▶ [Stage-1: layout] ─▶ anchor_layout.json ─▶ [Stage-2: position] ─▶ tag XYZ
                                                       ▲
                                  tag↔anchor ranging ──┘
```

All code lives under `scripts/solvers/` (SHA256 in `solvers/SHA256SUMS.txt`, per-file source
paths + deps + repoint caveats in `solvers/DEPS_MANIFEST.md`). Solver logic was copied
**verbatim** — hardcoded absolute paths are flagged `[REPOINT]` in DEPS_MANIFEST, not rewritten.

## ★ PRIMARY — Erlangen deployment chain: V4-io + T4
`scripts/solvers/erlangen_deployment_v4io_t4/`  ·  full detail: `…/ERLANGEN_CHAIN.md`

> **This is the production solver from the Erlangen 2026-05-28 field deployment. It
> produced 72.7 mm static / 102.6 mm dynamic. Reproduce those numbers with THIS chain.**

- **Stage-1 = V4-io** (`stage1_layout/`): `run_v4io_solve.py` (driver) → `build_pairs.py`
  (preprocessor) → `run_clean_full_compare.py` (module) → `analysis_20260513_182053/…` (core
  `solve_v4`). Input inter-anchor pairs → `anchor_layout.json`.
- **Stage-2 = T4** (`stage2_position_T4_pristine/` + `drivers/run_original_t4.py`): the biospur
  package at git `3acfeeda5`, with a `.so` rebuilt from its pristine C source. Input tag TR +
  layout → tag XYZ.
- **T4 vs U5** = the SAME package at two revisions (4 files differ). T4 = Erlangen production;
  U5 = the later Huber/IRLS research variant. See `…/stage2_position_T4_pristine/T4_PRISTINE_NOTE.md`.
- Reproduce: `build_pairs.py` → `run_v4io_solve.py` → `anchor_layout.json` →
  `PYTHONPATH=stage2_position_T4_pristine run_original_t4.py`. Repoint paths first (DEPS_MANIFEST).

## OTHER / research / follow-up variants
`scripts/solvers/other_variants/` — installed in full, each labelled. Not the Erlangen chain.

| Variant | File(s) | What it is |
|---|---|---|
| **V5** (Stage-1) | `v5/solve_v5.py` | Scale-locked layout solver — research follow-up to V4-io; pins the common-mode delay to lock metric scale. Shares the V4-io Stage-1 core. |
| **U5** (Stage-2) | `u5_note.md` → `erlangen_deployment_v4io_t4/stage2_position/` | The current-tree biospur package = T4 + Huber/IRLS + per-anchor σ + RF-SNR σ inflation (`1c59103af`). Not a separate copy. |
| **multilaterate** (Stage-2) | `multilaterate/{calibrate_listener_positions,full_system_calibration,pg_lib}.py` | The **deployed** Stage-2 for the **home CIR-listener rig**'s static calibration: scipy `least_squares` Huber f_scale=50. `full_system_calibration.py` phase4 → `multilaterate` → `wand_positions.json`. Named "listener" because listeners self-locate as tags; the wrapper reuses the same engine for the wand tags. |
| **research drivers** | `research_drivers/{v5u5_vs_v4iot4,v5_vs_v4io,solver_v2_validation}/` | Offline A/B comparisons + validation harnesses. Not deployed. |
| **historical** (Stage-1) | `historical/solve_anchor_layout{,_v3_full,_iterative}.py`, `historical/solve_v4_fusion/` | v1/v2/v3-era layout solvers + the pre-integration V4-fusion predecessor. **Superseded — do not use for production**; kept for lineage. |

## Two Stage-2 solvers — not a contradiction (important)
- **Erlangen field deployment** → Stage-2 = **T4** (biospur C-core). ★ the primary chain.
- **Home CIR-listener rig static calibration** → Stage-2 = **multilaterate**
  (`calibrate_listener_positions.py`) — this is what produced that rig's committed
  `wand_positions.json` / `listener_positions.json`.
- **T4/U5 (biospur)** is the Erlangen lineage + its offline research/consolidation; it was
  never the home-rig deployed Stage-2. Stage-1 for both systems = **V4-io** (V5 is research).
- Nothing here is a live wand **tracker** — `wand_positions.json` is a **static calibration
  snapshot** (wand held still) for the multistatic-imaging channel matrix. Live tracking is a
  separate solver (`export_capture_trajectory.py`, shipped as reference inside the biospur pkg).

See also `FREEZE_STATE.md` (firmware freeze + the reverse-SS-TWR TDMA blocker) and
`HARDWARE_STATE.md` (fleet map).
