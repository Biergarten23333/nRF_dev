# Method source evidence bundle

This folder contains copies of the source files referenced by:

```text
../METHOD_SOLVER_PROTOCOL_NOTES_FOR_CLAUDE.md
```

The copies are provided so the Method chapter can be written from a stable local bundle.

## Official 2026-05-28 provenance

```text
official_extra_analysis_run_meta.json
official_layout_v1_old.json
official_layout_v2.json
official_layout_v3_lite.json
official_layout_v3_full.json
official_layout_v4_io.json
```

Use these to confirm that the official layout versions are `v1-old`, `v2`, `v3-lite`, `v3-full`, and `v4-io`, and that official V1 declares `archive_v1_classical_mds_only`.

## Layout solver code

```text
layout_run_clean_full_compare.py
layout_run_full_evaluation_same_pipeline_20260513.py
```

These are the primary official layout wrapper and implementation files.

## Tag solver code

```text
tag_t_series_design.md
tag_c_solver.py
tagpos_solver.c
```

These define T1--T4 and the C-core range-only WLS implementation.

## SS-TWR / Matrix protocol code

```text
broadcast_uwb_ss_twr_shared.h
broadcast_uwb_ss_twr_shared.c
broadcast_ss_twr_init.c
broadcast_ss_twr_resp.c
matrix_ss_twr_anchor_init.c
autopos_matrix_traditional_sstwr_a17_checkpoint.md
work_summary_20260501_20260502.md
```

Use these to distinguish tag broadcast Alt SS-TWR from AutoPos matrix rotating-master unicast SS-TWR.

## Non-official / historical comparison files

```text
reserve_run_autopos_solve_v1_v2_v3_v3full_from_existing.py
historical_run_v1_to_v5.py
```

These are included only to explain possible confusion:

- `reserve_run_autopos_solve_v1_v2_v3_v3full_from_existing.py` creates a `v1_soft_iterative` output in a reserve workflow, but that output is not present in the official 2026-05-28 analysis.
- `historical_run_v1_to_v5.py` contains older V4 joint variants with tag variables. Do not treat them as the official `v4-io` field-check layout unless explicitly discussing historical variants.
