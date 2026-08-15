# Test results

- `py_compile`: PASS for all new R3D/D0 modules, runners and tests.
- Unit tests with the repository source root declared as
  `PYTHONPATH=Fusion_Part/src`: `6 passed in 9.77s`.
- A preceding bare `pytest` invocation omitted that source root and stopped
  during collection with `ModuleNotFoundError: biospur_fusion`; no test body
  or scientific calculation ran in that invocation.
- Five run-directory SHA manifests: PASS.
- Historical tracked R3C source/config/evidence unchanged: PASS.
- Staged files: 0.
- Real D0, held-out, UWB/T4/Anchor and operator data access: none.
