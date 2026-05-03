# Full Sweep With Known-Good Parameters - 2026-05-02 16:23:05

## Command

```bash
python3 scripts/run_autopos_sweep_loop.py \
  --port /dev/serial/by-id/usb-Master_Anchor_BioSpur_BLE_Control_87EA2F4A526C5A02-if00 \
  --order ABCDEFGH \
  --sw-sets 10 \
  --prewarm-sw-sets 10 \
  --warmup-min-quality 90 \
  --verbose 1 \
  --quiet-tag-name - \
  --reuse-resident-anchor-master \
  --no-bootstrap-autopos-reset \
  --round-retries 1 \
  --out-dir autopos_pipeline/logs/full_sweep_known_good_params_20260502_162305
```

## Log Paths

- Sweep log root: `autopos_pipeline/logs/full_sweep_known_good_params_20260502_162305`
- Session matrix guard: `autopos_pipeline/logs/full_sweep_known_good_params_20260502_162305/session_role_guard.log`
- Round A: `autopos_pipeline/logs/full_sweep_known_good_params_20260502_162305/round_A/master.log`
- Round B: `autopos_pipeline/logs/full_sweep_known_good_params_20260502_162305/round_B/master.log`
- Post-abort responder restore: `autopos_pipeline/logs/anchor_ready_after_known_good_params_abort_20260502_162529`

## Outcome

Partial run only. The old known-good host parameters improved SW-A collection but did not restore full A-H AutoPos sweep behavior.

- Session role guard succeeded:
  - action: `anchor role all matrix`
  - ready: `8/8`
- Round A succeeded at the script level:
  - `apply_success_seen=true`
  - `sw_count=10`
  - `warmup_discarded_count=10`
- Round A data quality was still poor:
  - final row: `SW-A,B,4739,22,C,5606,25,D,2808,25,E,0,0,F,0,0,G,0,0,H,3307,100`
  - E/F/G stayed `0,0`
  - warnings: no output as Matrix from E/F/G during SW-A
- Round B failed to start producing sweep data:
  - no `SW-B` rows
  - stayed at `state=staged staged=B last_success=A sets=20`
  - `autopos apply already running`
- The run was manually stopped after SW-B stayed in `stage=sweeping` with `sw-set=0/10`.
- A-H were restored to responder runtime after abort:
  - ack ok: true
  - ready: `8/8`

## Key Evidence

Known-good parameter effect:

```text
SW-A warmup discard 1/10 ... 10/10
SW-A ... 10 kept rows
```

So the earlier single all-zero SW-A row was plausibly a cold-start row caused by disabling prewarm.

But Round B still does not run:

```text
AUTOPOS round staged: master=B sets=20
AUTOPOS: mode=AUTOPOS state=staged staged=B last_success=A sets=20 error=-
autopos apply already running
AUTOPOS sweep converge rc=-116 master=B timeout_ms=4000 min_q_observe=90 consecutive=1
AUTOPOS sweep listen attach: master=B uuid=B9179575C776C98F1CB132DD6EDC6223
AUTOPOS: mode=AUTOPOS state=staged staged=B last_success=A sets=20 error=-
```

## Interpretation

This separates two issues:

1. **Warmup issue confirmed:** accepting the first SW row is wrong for AutoPos; use `prewarm_sw_sets=10`.
2. **Current AutoPos runtime issue remains:** even with old successful host parameters, the current `alt-bcast-a13-nosleep-hotpath-g` anchor image and/or current Master_Anchor AutoPos state machine does not progress from A to B.

Compared with known-good runs from `2026-04-26`:

- Known-good Anchor fw in AutoPos state lines: `anchor-runtime-force-20260426_2`
- Current Anchor fw: `alt-bcast-a13-nosleep-hotpath-g`
- Known-good SW-B produced 10 kept rows after 10 warmup rows.
- Current SW-B produced 0 rows and stayed staged.

## Current State

Responder runtime was restored after the aborted test:

```text
ready=8/8
```

No build, flash, or OTA operation was performed during this test.

## Suggested Next Diagnostic

Check Master_Anchor / Anchor AutoPos changes against the known-good `anchor-runtime-force-20260426_2` path before touching sweep solver logic. The likely area is the finite-master auto-return / apply completion state path, because SW-B reports `autopos apply already running` while the system remains `staged=B`.
