# Full Sweep After Power Cycle With Known-Good Parameters - 2026-05-02 16:28:42

## Context

Developer manually power-cycled all Anchors and Masters before this run.

This run used the same host-side sweep shape as the known-good dual-master sweeps:

- `sw_sets=10`
- `prewarm_sw_sets=10`
- `warmup_min_quality=90`
- `Master_Anchor` port
- `reuse_resident_anchor_master=true`
- `no_bootstrap_autopos_reset=true`

Known-good references:

- `logs/autopos_then_3tag_capture_20260426_225235/autopos/summary.json`
- `logs/autopos_then_3tag_capture_20260426_230050/autopos/summary.json`

## Log Paths

- Sweep log root: `autopos_pipeline/logs/full_sweep_after_powercycle_known_good_20260502_162842`
- Session matrix guard: `autopos_pipeline/logs/full_sweep_after_powercycle_known_good_20260502_162842/session_role_guard.log`
- Round A: `autopos_pipeline/logs/full_sweep_after_powercycle_known_good_20260502_162842/round_A/master.log`
- Round B: `autopos_pipeline/logs/full_sweep_after_powercycle_known_good_20260502_162842/round_B/master.log`
- Responder restore after abort: `autopos_pipeline/logs/anchor_ready_after_powercycle_sweep_abort_20260502_163029`

## Outcome

Power cycling did not restore full A-H AutoPos sweep.

- Session matrix guard succeeded:
  - `anchor role all matrix`
  - `ready=8/8`
- SW-A ran and produced formal rows after 10 warm-up discards.
- SW-A data quality was poor:
  - final SW-A row: `SW-A,B,4775,41,C,5583,37,D,0,0,E,1741,100,F,4927,100,G,0,0,H,0,0`
  - D/G/H stayed `0,0`
- SW-B did not produce any SW rows.
- SW-B status stayed:
  - `state=staged staged=B last_success=A sets=20`
  - `autopos apply already running`
- Run was manually interrupted after SW-B remained at `sw-set=0/10`.
- A-H responder runtime was restored after abort:
  - `ready=8/8`

## Interpretation

The power cycle changed the initial state from the previous run (`last_success=-` for SW-A), but it did not fix the A->B transition.

The current failure is repeatable across:

1. no prewarm quick probe,
2. known-good host parameters,
3. full power cycle + known-good host parameters.

The remaining variable versus the known-good dual-master sweeps is firmware/runtime behavior:

- Known-good Anchor fw in logs: `anchor-runtime-force-20260426_2`
- Current Anchor fw in logs: `alt-bcast-a13-nosleep-hotpath-g`

Most likely suspect:

- Current Anchor AutoPos finite-master auto-return / sweep completion path does not fully complete or publish state in the same way as `anchor-runtime-force-20260426_2`.
- Master_Anchor then sees `autopos apply already running` on the next master and remains `staged=B`.

No build, flash, or OTA operation was performed during this test.
