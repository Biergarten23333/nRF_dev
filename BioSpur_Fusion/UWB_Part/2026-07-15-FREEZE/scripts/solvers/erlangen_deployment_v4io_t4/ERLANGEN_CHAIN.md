# Erlangen deployment chain — V4-io (Stage-1) + T4 (Stage-2)  ★ PRIMARY

> **This is the production positioning solver used in the Erlangen 2026-05-28 field
> deployment — it produced 72.7 mm static / 102.6 mm dynamic. It is the first-class,
> must-freeze solver.**

Two stages, file-mediated (Stage-1 writes a layout JSON, Stage-2 reads it):

```
anchor↔anchor ranging ──▶ [Stage-1: V4-io] ──▶ anchor_layout.json ──▶ [Stage-2: T4] ──▶ tag XYZ
                                                        ▲
                                   tag↔anchor ranging ──┘
```

## Stage-1 — V4-io anchor-layout solver  (`stage1_layout/`)
| File | Role |
|---|---|
| `run_v4io_solve.py` | driver: runs the production V4-io layout solve on a pairs CSV |
| `build_pairs.py` | preprocessor: AutoPos `summary.json` → `pairs_all.csv` |
| `run_clean_full_compare.py` | solver module (`solve_version`, `fuse_all`, `save_layout`; V1–V5 dispatch incl. `v4-io`) |
| `analysis_20260513_182053/run_full_evaluation_same_pipeline_20260513.py` | **solver core** (`solve_v4`, Huber, ±60 mm delay box, MDS init) |

- Input = fused inter-anchor pair distances (`pairs_all.csv`). Output = `anchor_layout.json`
  (anchor XYZ + per-anchor delays). "io" = inter-anchor-only.
- **Keep the `analysis_20260513_182053/` subdir at this depth** — `run_clean_full_compare.py`
  loads its core via `Path(__file__).parents[2]/…`. See DEPS_MANIFEST for the repoint note.

## Stage-2 — T4 tag-position solver  (`stage2_position_T4_pristine/` + `drivers/`)
| File | Role |
|---|---|
| `stage2_position_T4_pristine/` | the biospur package **at the T4 revision** (see T4_PRISTINE_NOTE.md) — includes a `.so` rebuilt from the pristine C source |
| `drivers/run_original_t4.py` | T4 driver: V4-io layout + original-T4 solver on captured tag TR; caliper check |

- Input = tag↔anchor ranging (TR) + `anchor_layout.json`. Output = tag XYZ.
- Selects behavior by `PYTHONPATH` (which package) + `SolverConfig(method="T4")`.

## Reproduce (conceptual — repoint hardcoded paths first, see DEPS_MANIFEST.md)
1. `build_pairs.py <autopos summary.json> > pairs_all.csv`
2. `run_v4io_solve.py pairs_all.csv` → `anchor_layout.json`  *(V4-io Stage-1)*
3. `PYTHONPATH=stage2_position_T4_pristine python drivers/run_original_t4.py`
   with `anchor_layout.json` + captured `wand_tr.log` → tag XYZ  *(T4 Stage-2)*

Reference layout + inputs used for validation: `reference_layout_inputs/` (the
`system_calibration_20260710_233443` V4-io layout + V5 scale-lock + anchor sigma).

`erlangen_provenance/` holds small code snapshots of the deployed solvers as they
existed at Erlangen (method_source_evidence) + the staging helpers — provenance only.
