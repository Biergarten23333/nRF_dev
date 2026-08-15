# Test results

- Production reconstruction count: exactly one.
- Historical summary reproduction: PASS.
- Reload-only verification after process exit: PASS.
- All persisted arrays finite: PASS.
- `py_compile`: PASS for R3D/D0 production modules, runners, exporter, and tests.
- Focused pytest: `12 passed in 1.32s`.
- After the final exporter/finalizer source update, its focused suite was rerun:
  `8 passed in 0.12s`; reload-only verification also passed again.
- Exporter tests cover production-evaluator invocation, semantic matrix mutation,
  row/column reorder rejection, mandatory units/scales, soft-prior
  classification, reload-only rank reconstruction, and forbidden imports.
- Existing full D0-B and full R3D synthetic pytest cases were not rerun because
  this task authorized exactly one production D0-B reconstruction and forbade a
  second synthetic run. Their historical results are preserved byte-identically.
- Optimizer iterations: 0.
- Real D0 objective/Jacobian/solver access: none.
- Raw ledger, Q2 cache payload, UWB/T4/Anchor, operator and held-out access: none.
