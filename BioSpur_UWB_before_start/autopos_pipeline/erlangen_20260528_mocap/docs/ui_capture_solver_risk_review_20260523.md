# BioSpur AutoPos UI / Capture / Solver Risk Review

Date: 2026-05-23

Scope:

- Flutter UI: `flutter_ui_autopos/lib/main.dart`
- Erlangen aliases and command wrappers
- TDMA capture scripts
- Solver staging / output flow
- Realtime and playback trajectory visualization

This document separates issues into two groups:

- **Needs hardware validation**: cannot be proven from code alone.
- **Can improve in code now**: can be changed without waiting for field hardware.

## Current Validation Status

Checked locally:

- `flutter analyze`: pass
- `flutter test`: pass
- Python compile check for capture / solver scripts: pass

Important limitation:

- These checks only verify syntax and Flutter static analysis. They do not prove BLE / TDMA / AOTA runtime behavior.

## Needs Hardware Validation

### 1. Targeted non-target AOTA behavior

Current behavior:

- Before capture, known BS tags that are **not** selected are configured by name, then sent `cmd_all MODE AOTA`.
- Intended result: only non-target tags enter AOTA, selected target tags keep ranging.

Risk:

- This depends on firmware honoring `ota_target name <tag>` for `cmd_all MODE AOTA`.
- If `cmd_all` ignores the selected OTA target and broadcasts globally, it can stop selected tags too.

Validation test:

1. Power on three tags, for example `BS2DCE`, `BSDC91`, `BSF66F`.
2. Start a Free capture with only `BSF66F` selected.
3. Confirm command log shows non-target AOTA for `BS2DCE,BSDC91`.
4. Confirm realtime / `tr_all.csv` contains only `BSF66F`.
5. Confirm `BSF66F` keeps producing TR rows after the non-target AOTA step.

Expected result:

- Non-target tags are silent.
- Selected target tags remain active.

If this fails:

- Do not rely on current non-target silence behavior.
- Implement or expose a true single-target stop/AOTA command instead of using `cmd_all MODE AOTA`.

Relevant code:

- `SS-TWR/alt-SS-TWR/broadcast/scripts/run_recv_tdma_capture.py`
- Function: `silence_non_target_tags`

### 2. Capture cleanup still stops all tags

Current behavior:

- At the end of capture, cleanup sends `cmd_all MODE AOTA`.

Risk:

- This can stop every powered-on BS tag, including tags not selected for the current run.
- This explains cases where a tag appears online before capture but disappears after another capture.

Validation test:

1. Power on selected Roto tags and one extra static/free tag.
2. Run Roto capture.
3. After capture ends, check whether the extra tag remains active or was sent to AOTA.

Expected result for current code:

- It may be stopped, because cleanup is global.

Recommended direction:

- Change cleanup to target only the tags used in the current run.
- Or add a capture mode flag that disables broad cleanup when multiple unrelated tags are powered.

Relevant code:

- `SS-TWR/alt-SS-TWR/broadcast/scripts/run_recv_tdma_capture.py`
- Function: `cleanup_capture_session`

### 3. Anchor responder state before capture

Current behavior:

- Capture defaults to skipping anchor preflight:
  - `BIOSPUR_SKIP_ANCHOR_PREFLIGHT_FOR_CAPTURE=1`

Risk:

- Capture may start while tags are connected but anchors are not in responder mode.
- This produces the failure mode: tag link exists, but ranging does not work.

Validation test:

1. Run sweep.
2. Run solver.
3. Start Roto/Static capture without pressing "Set all anchor responder".
4. Check whether anchors are actually in responder state and ranging succeeds.

Recommended field rule:

- Before capture, press `Set all anchor responder` or run the equivalent command unless the latest sweep/capture command already guarantees responder mode.

Relevant file:

- `autopos_pipeline/erlangen_20260528_mocap/tools/erlangen_aliases.sh`

## Can Improve In Code Now

### 1. Make capture cleanup target-specific

Problem:

- Cleanup currently uses broad `cmd_all MODE AOTA`.

Suggested change:

- For each selected target:
  - set `ota_target name <target>`
  - send a target-specific stop/AOTA command
- Avoid stopping non-selected tags.

Status:

- Not yet changed.
- Should be prioritized before using many powered-on Free/Static/Roto tags together.

### 2. Make solver output provenance more explicit

Current UI shows:

- Layout version
- Solver run mode
- Requested sweep
- Staged sweep used
- Staged rows
- Layout mtime
- Warning when sweep is not pinned

Remaining risk:

- Solver output directories are global:
  - `solver/outputs/v4io_field_check`
  - `solver/outputs/v1_to_v4_io_field_check`
- A later solver run overwrites the previous layout result.

Suggested improvement:

- Store a small `layout_provenance.json` next to every layout with:
  - selected sweep path
  - selected ultrasound path, if any
  - solver mode
  - layout version
  - generated time
  - source row count

Status:

- UI already displays some provenance.
- Dedicated provenance file would make post-experiment audit cleaner.

### 3. Separate source data from UI-generated trajectory cache

Current behavior:

- Playback export writes temporary files:
  - `/tmp/biospur_trajectory_*.json`
- Realtime export writes temporary files:
  - `/tmp/biospur_realtime_trajectory_*.json`

This is acceptable because:

- The original data remains in capture folders:
  - `raw.log`
  - `tr_all.csv`
  - `summary.json`

Risk:

- After reboot, `/tmp` files may disappear.
- User may confuse UI cache with source data.

Suggested improvement:

- Add UI text or tooltip:
  - "Trajectory JSON is cache only. Source data is tr_all.csv/raw.log."

### 4. Playback list includes partial captures

Current behavior:

- A capture can appear in playback if it has either `summary.json` or `tr_all.csv`.

Benefit:

- Useful for debug and interrupted captures.

Risk:

- Partial / failed captures can look usable.

Suggested improvement:

- Add a status badge:
  - `complete`
  - `partial`
  - `interrupted`
  - `missing summary`

### 5. Clear Experiment Data should show a dry-run list

Current behavior:

- Clear button asks for confirmation, then deletes scoped experiment outputs.

Risk:

- The operation is intentionally destructive.

Suggested improvement:

- Confirmation dialog should list exact directories to be deleted.
- Optional: add "copy current session_notes.csv before clearing".

## Current Design Decisions That Are Acceptable

### Realtime view is visualization only

Realtime capture visualization reads current capture output and exports a temporary solved trajectory.

This should not affect source data:

- It does not modify `raw.log`.
- It does not modify `tr_all.csv`.
- It does not modify capture `summary.json`.

If realtime UI freezes or fails:

- Data capture can still be valid if the underlying capture script completed and wrote source files.

### Stop button process handling

Current behavior:

- UI starts scripts with `setsid`.
- Stop sends signal to the process group.
- If the process does not exit, it sends SIGKILL after a short delay.

This is appropriate for field control because:

- It kills child Python / shell processes, not only the parent shell.
- It avoids orphan capture processes keeping serial ports busy.

## Field Checklist Before Real Experiment

1. Run `bs_init`.
2. Run `bio_ports`.
3. Confirm Master Anchor CDC and Master Tag CDC are correct.
4. Run or select/pin a concrete sweep.
5. Run ultrasound measurement if using US-Z correction.
6. Stage dataset and run solver.
7. Confirm Tab 3 shows the intended:
   - solver layout version
   - solver run mode
   - staged sweep used
   - staged rows
8. Before capture, set all anchors responder.
9. For Free capture, select only desired tags.
10. Confirm realtime `Expected:` tags match the capture target list.
11. After capture, verify source files exist:
    - `raw.log`
    - `tr_all.csv`
    - `summary.json`

## Highest Priority Before Erlangen Field Use

Priority 1:

- Validate whether `cmd_all MODE AOTA` obeys `ota_target name`.

Priority 2:

- Replace broad capture cleanup with target-specific cleanup.

Priority 3:

- Keep solver sweep pinned before capture so Tab 3 layout is unambiguous.

