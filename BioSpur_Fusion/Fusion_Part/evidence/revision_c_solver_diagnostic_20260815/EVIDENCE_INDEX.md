# Revision C solver diagnostic evidence snapshot

This directory is a byte-identical, calibration-ledger-free snapshot of the
compact evidence used to audit `FAST_SOLVER_REVISION_C`, the checkpoint-only
audit, and `REVISION_C_SHORT_INSTRUMENTED_SOLVER_DIAGNOSTIC_V1`.

## Frozen conclusions

```text
REVISION_C_VERDICT = FAIL_PREVIEW_CALIBRATION
SHORT_DIAGNOSTIC_RESULT = JACOBIAN_OR_SCALING_QUALIFICATION_FAIL
NEW_RESULT_ADOPTED_AS_CALIBRATION = false
GOLF_STATUS = SEALED
BOXING_STATUS = SEALED
WALK_STATUS = SEALED
FINAL_STILL_STATUS = SEALED
```

The snapshot does not alter the historical verdict, directional-derivative
gate, Jacobian, scaling, finite-difference steps, residuals, weights, solver,
or parameterization. It contains no raw calibration ledger and no held-out
payload.

## Layout

- `revision_c/`: real calibration-only Revision-C result, run freeze, endpoint
  oracle, validity support, row manifest, and input-access/Q2 binding evidence.
- `synthetic_final/`: final Revision-C synthetic qualification evidence.
- `checkpoint_only_audit/`: every file produced by the checkpoint-only audit,
  including its compact JSON/CSV/MD artifacts and small spectrum NPZ files.
- `short_instrumented/`: every one of the 16 files actually produced by the
  short diagnostic, including `TRACE_ARRAYS.npz`.
- `start_4_terminal/`: the selected start-4 terminal checkpoint, including its
  arrays, sparse Jacobian, metadata, and manifest.

`STOP_REASON.json` was not an actual short-diagnostic filename. Its semantic
content maps to `short_instrumented/REVISION_D_DECISION.json` (primary decision)
and `short_instrumented/REPORT.md` (human-readable stop rationale). No synthetic
replacement file was created.

## Reproducibility source status

The committed runtime closure includes the Revision-C runner, objective and
residual implementation, parameterization/scaling, validity logic, strict JSON
writer, checkpoint writer/loader, slow/fast oracle, synthetic qualification,
instrumented TRF/LSMR diagnostic, configuration, and tests.

The historical checkpoint-only audit did **not** persist a standalone generator
source file. A repository-wide and temporary-directory search found no such
`.py` file. This snapshot therefore preserves all of that audit's original
outputs and its SHA manifest but does not claim source-level regeneration of
that one audit. No replacement audit implementation was invented in this
checkpoint.

## Integrity and exclusions

`EVIDENCE_MANIFEST.json` records the absolute source path, repository-relative
copy path, byte size, source SHA-256, evidence SHA-256, artifact role, generator
binding, and NPZ array schemas. Every copied file is marked byte-identical only
after direct SHA comparison.

The raw calibration ledger, raw capture, UWB/T4/Anchor data, operator
measurements, golf, boxing, walk, final_still, media, caches, and build products
are excluded. Git LFS is not used.
