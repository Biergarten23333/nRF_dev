# BioSpur C2 basis repair + genuine progressive calibration — review package

Status: **PREPARATION ONLY / USER REVIEW / NOT AUTHORIZED TO RUN**

This directory is the proposed controlling contract for the next 12–18 hour C2
task. It incorporates the evidence from the bounded QMT open-source diagnostic
without treating that diagnostic as a scientific or runnable baseline.

Nothing in this directory authorizes source implementation, synthetic execution,
new C2 payload access, solver execution, rendering, or creation of the WORK and
monitor tasks. Activation requires a later explicit user command.

The historical directory
`config/biospur_fusion_v0_c2_progressive_contract/` is preserved unchanged. This
new package supersedes it only after activation and only for the new run.

Review order:

1. `REVIEW_CHECKLIST_ZH.md` — complete Chinese user-review surface and explicit
   confirmation checklist.
2. `USER_ANTHROPOMETRY_AMENDMENT_001.json` — append-only user clarification
   that the second-observer 260–265 mm forearm range applies to both sides,
   without altering the original authority file.
3. `GEOMETRY_AND_PARAMETER_CONTRACT.json` — exact measured values, missing
   internal geometry, parameter ownership, legacy-value disposition, and the
   required active parameter registry.
4. `ACTIVE_PARAMETER_REGISTRY.template.json` — exhaustive parameter-group and
   freeze-order template; the formal registry must resolve every concrete value
   and pass a zero-unregistered-parameter scan before real fitting.
5. `MASTER_CONTRACT.md` — complete human-readable scientific and execution rules.
6. `COMPLIANCE_MATRIX.json` — machine-checkable requirement inventory and
   consequence class.
7. `STARTUP_PARAMETERS.json` — exact paths, node mapping, wear priors, pinned
   upstream facts, phase budgets, and forbidden inputs.
8. `RUN_START_CONTRACT.template.json` — required fields for the future immutable
   activation seal; the template itself is not a run seal.
9. `WORK_PROMPT_EN.md` — proposed prompt for the future English sole-writer task.
10. `MONITOR_PROMPT_ZH.md` — proposed prompt for the future Chinese read-only
   monitor/steer/correction task.
11. `validate_contract.py` — read-only static validator for the reviewed mapping,
   topology, action order, phase budget, consequence matrix, authority-path
   existence, prompt bindings, and future seal file list. It does not inspect C2
   payload or holdout content.

At activation, the future WORK task must copy the reviewed file hashes into a
fresh immutable `RUN_START_CONTRACT.json`. Later changes are append-only
amendments; no reviewed or sealed contract file may be overwritten.

The previous one-hour diagnostic established that the official QMT mechanisms,
C2 unit conversion, raw-axis ordering, quaternion convention, and continuous VQF
input are usable. It did **not** produce C2 heading-corrected trajectories or a
valid anatomical viewer. No additional standalone one-hour bootstrap is required.
