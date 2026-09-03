# C2 pure-IMU basis + progressive calibration contract set

Status: **PREPARATION ONLY — WAITING FOR EXPLICIT USER START**

These files define the next C2 task before implementation or payload access. Reviewing,
editing, or discussing them does **not** authorize a run. Activation requires a later,
explicit user instruction to start the formal task.

The previous task `01a04a6d-7a01-7480-b6c4-564ef724906b` was stopped on
2026-08-29. Its partial source edits are preserved but are incomplete and unqualified.
They are not an accepted basis, progressive state, synthetic PASS, or runnable result.

Review in this order:

1. `ARCHITECTURE_CONTRACT.md` — normative scientific and execution rules.
2. `COMPLIANCE_MATRIX.json` — every rule's owner, required evidence, and consequence.
3. `RUN_START_CONTRACT.template.json` — exact startup scope, identities, wear priors,
   chronology, forbidden inputs, budgets, and output obligations.

Before a future start, the worker must:

- incorporate any user amendments append-only;
- pass the 60-minute open-source-first bootstrap gate: run the unmodified official QMT
  full-body advanced example, then use a minimal C2 input/topology adapter before writing
  a replacement solver;
- create a fresh immutable run-start contract from the template;
- bind current source/config/authority/anthropometry hashes;
- execute an automated contract validator;
- seal metadata before opening or hashing any new C2 payload;
- stop the current run if an architecture/provenance invariant fails, while continuing
  the task through causal diagnosis and repair rather than declaring a terminal FAIL.

No solver, synthetic qualification, real-data fit, or renderer is authorized by these
files alone.
