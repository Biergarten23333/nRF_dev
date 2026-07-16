# U5 (Stage-2, Huber/IRLS) — where it lives

**U5 is NOT a separate copy here.** U5 = the **current-working-tree** biospur package,
which is shipped (with its prebuilt `.so`) at:

    ../erlangen_deployment_v4io_t4/stage2_position/

U5 = T4 + the `1c59103af` (2026-07-13) edits to 4 files: Huber/IRLS robust weighting,
per-anchor sigma (`anchor_sigma.json`, default 25 mm), and RF first-path-SNR sigma
inflation. It never drops an anchor (LOO rejection is dead code — Huber down-weighting only).

- **T4** (pristine, the Erlangen Stage-2) = `../erlangen_deployment_v4io_t4/stage2_position_T4_pristine/`
- **U5** (current tree)                  = `../erlangen_deployment_v4io_t4/stage2_position/`
- The two differ only in the 4 files listed in `stage2_position_T4_pristine/T4_PRISTINE_NOTE.md`.

Select which one runs via `PYTHONPATH` + `SolverConfig(method="T4"|"U5")`.
Offline A/B driver: `research_drivers/v5u5_vs_v4iot4/`.
