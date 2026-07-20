# This is T4 (pristine) — the Erlangen Stage-2 solver

**T4 and U5 are the SAME package at two git revisions, not two codebases.**

- This folder = the biospur package with **4 files reverted to git `3acfeeda5`**
  (2026-06-20), the parent of the U5 edit commit `1c59103af` (2026-07-13 "…solver updates").
- The 4 files that differ between T4 and U5 (the entire T4→U5 delta):
  1. `biospur_tag_positioning_offline_solver/c_solver.py`
  2. `biospur_tag_positioning_offline_solver/layout_io.py`
  3. `c_core/include/biospur_tagpos/tagpos_solver.h`
  4. `c_core/src/tagpos_solver.c`
- The rest of the package here is identical to the U5 copy at
  `../stage2_position/` (that copy = the current working tree = U5).

## The `.so` here was REBUILT from the pristine C source
The shipped prebuilt `.so` in the repo is the **U5** build. The `.so` in this folder
(`c_core/build/libbiospur_tagpos.so`) was rebuilt from the pristine-T4 C source with:

```
gcc -O3 -std=c99 -fPIC -shared \
  -I <this>/c_core/include \
  <this>/c_core/src/tagpos_solver.c \
  -lm \
  -o <this>/c_core/build/libbiospur_tagpos.so
```
(`c_solver.py::build_c_core()` runs this automatically if the `.so` is missing/stale;
`c_core/CMakeLists.txt` is a CMake alternative.)

## Use
`PYTHONPATH=<this folder> python … SolverConfig(method="T4")`. The driver code is
identical for T4 and U5 — behavior is selected purely by which package is on the path.

Authoritative T4/U5 explainer ships in-package: `docs/version_chain.md` and
(in the repo original) `validation_outputs/T4_VARIANT_COMPARISON.md` (the latter was a
351 MB data dir, excluded from this freeze — see DEPS_MANIFEST).
