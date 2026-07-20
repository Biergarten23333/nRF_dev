# DEPS_MANIFEST — source paths, purpose, deps, path caveats

All source paths relative to repo working subtree `BioSpur_UWB_before_start/` (`$R`).
Copied 2026-07-17. Integrity in `SHA256SUMS.txt`. Solver logic copied **verbatim** — no
edits (per freeze rule: no core-logic changes). Where a file has hardcoded absolute paths,
they are flagged **[REPOINT]** — repoint after copy; the code was not modified.

## Group 1 — Erlangen deployment chain (V4-io + T4)  ★ PRIMARY

| dest (under solvers/) | source ($R/…) | purpose | deps | caveat |
|---|---|---|---|---|
| `…/stage1_layout/run_v4io_solve.py` | `logs/autopos_diagnostic_20260710/code/run_v4io_solve.py` | V4-io driver | imports `run_clean_full_compare.py` | **[REPOINT]** `FC_PATH`, `REPO` |
| `…/stage1_layout/build_pairs.py` | `logs/autopos_diagnostic_20260710/code/build_pairs.py` | summary.json→pairs_all.csv | stdlib | — |
| `…/stage1_layout/run_clean_full_compare.py` | `autopos_pipeline/outdoor_20260513/run_clean_full_compare.py` | V4-io solver module (`solve_version`,`fuse_all`,`save_layout`) | loads core via `parents[2]` | keep `analysis_20260513_182053/` at same depth, or patch `EVAL` |
| `…/stage1_layout/analysis_20260513_182053/run_full_evaluation_same_pipeline_20260513.py` | `autopos_pipeline/outdoor_20260513/analysis_20260513_182053/…` | **solver core** (`solve_v4`) | numpy/scipy only (LEAF) | — |
| `…/stage2_position/**` | `biospur_tag_positioning_offline_solver/**` (excl. `validation_outputs/`,`__pycache__`) | **U5** package (current tree) + prebuilt `.so` | self-contained pkg | — |
| `…/stage2_position_T4_pristine/**` | same pkg, 4 files @ git `3acfeeda5` + rebuilt `.so` | **T4** package (Erlangen Stage-2) | self-contained pkg | see `T4_PRISTINE_NOTE.md` |
| `…/drivers/run_original_t4.py` | `analysis/v4io_t4_original/run_original_t4.py` | T4 driver | imports the pkg | **[REPOINT]** `REPO`,`CAL`; needs `system_calibration_20260710_233443/{raw/wand_tr.log,anchor_layout.json}` |
| `…/reference_layout_inputs/*.json` | `logs/system_calibration_20260710_233443/{anchor_layout,anchor_layout_v5_scalelock,anchor_sigma}.json` | validation layout inputs | — | small configs, not solver code |
| `…/erlangen_provenance/*` | `autopos_pipeline/28052026_Erlangen_Official/{scripts/*.py, …/method_source_evidence/*.py}` | deployed-solver source snapshots | — | provenance only |

**C-core build command** (from `c_solver.py::build_c_core`; auto-runs on import if `.so` missing):
```
gcc -O3 -std=c99 -fPIC -shared -I <c_core>/include <c_core>/src/tagpos_solver.c -lm -o <c_core>/build/libbiospur_tagpos.so
```
The T4 `.so` here was rebuilt from pristine source; the U5 `.so` is the repo prebuilt. Their
hashes differ (verified). CMake alt: `c_core/CMakeLists.txt`.

## Group 2 — other / research / follow-up variants

| dest (under solvers/other_variants/) | source ($R/…) | purpose | label | caveat |
|---|---|---|---|---|
| `v5/solve_v5.py` | `autopos_pipeline/v5/solve_v5.py` | **V5** Stage-1 layout (scale-locked; common-mode pinned) | research follow-up to V4-io | **[REPOINT]** `EVAL_PATH`,`FC_PATH`,`V4IO_DEPLOYED`; shares Stage-1 core with V4-io (files above) |
| `multilaterate/calibrate_listener_positions.py` | `logs/listener_calibration/calibrate_listener_positions.py` | **multilaterate** engine (scipy least_squares, Huber f_scale=50) — DEPLOYED home-rig static Stage-2 | deployed (home CIR rig) | drives serial HW at runtime |
| `multilaterate/full_system_calibration.py` | `logs/listener_calibration/full_system_calibration.py` | full-system calib orchestrator; phase4→multilaterate→`wand_positions.json` | deployed | imports sibling `calibrate_listener_positions` |
| `multilaterate/pg_lib.py` | `logs/geiger_scan_20260711_161258_8anchor/analysis/pg_lib.py` | Geiger multilaterate lib (`solve_pos`,`parse_log`); dep of 2 research drivers | multilaterate/research | live lib despite living under `logs/` |
| `research_drivers/v5u5_vs_v4iot4/{worker,worker_ext,overnight_power_positioning,compare}.py` | `analysis/v5u5_vs_v4iot4/…` | offline T4/U5 vs V4io/T4 A/B | research | **[REPOINT]** `REPO`,`WORKER`; `compare.py` needs BOTH pkg revisions (T4+U5) |
| `research_drivers/v5_vs_v4io/{compare.py,*.json}` | `analysis/v5_vs_v4io/…` | offline V5 vs V4-io layout + Geiger LOO | research | **[REPOINT]** `PGDIR`,`REPO`; ships its 2 layout JSONs |
| `research_drivers/solver_v2_validation/validate.py` | `analysis/solver_v2_validation/validate.py` | Solver-V2 validation (3 blocks) | research | **[REPOINT]** `REPO`,`PGDIR`; imports `solve_v5` + `pg_lib` |
| `historical/solve_anchor_layout{,_v3_full,_iterative}.py` | `SS-TWR/alt-SS-TWR/broadcast/scripts/…` | v1/v2/v3 anchor-layout solvers | **historical, superseded** | broadcast tree = live tree; `unicast/` & `scripts_reserve_nomore_change/` hold byte-different copies (not shipped) |
| `historical/solve_v4_fusion/{solve_v4,solve_v4_old}.py` | `autopos_pipeline/solve_v4_fusion/…` | standalone V4-fusion (pre-integration predecessor of v4-io) | historical | — |

## Excluded (data, not code)
`biospur…/validation_outputs/` (351 MB), `28052026_Erlangen_Official/{captures,opti_captures,Analysis,solver}` (16 GB),
all `__pycache__`, `analysis/*/*.csv|*.json` result files (kept only the small config JSONs listed above),
`autopos_pipeline/outdoor_20260513/{sweep1000,Static_Test,Roto_Test,Wand_Test,analysis_* outputs}`.
