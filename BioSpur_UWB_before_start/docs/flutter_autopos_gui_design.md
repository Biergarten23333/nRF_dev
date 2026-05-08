# BioSpur Flutter AutoPos GUI Design

Date: 2026-05-06

Scope: design a Flutter desktop GUI that can identify the two B120 control masters, run the AutoPos pipeline, solve an anchor layout, push the selected APOS layout to Tags, and validate the result without requiring the operator to remember shell commands.

## Goals

The GUI should make the field workflow boring and repeatable:

- Detect `Master_Anchor` and `Master_Tag` automatically.
- Show whether each control plane is healthy before a run starts.
- Run AutoPos A-H sweep from a button.
- Convert sweep output into `pairs_all.csv`.
- Solve and summarize the anchor layout.
- Let the operator review the candidate layout.
- Push the approved APOS layout to Tags and verify `layout_match=true`.
- Run a short validation capture and show TR rate / ranging quality.
- Keep every command, artifact path, and pass/fail decision visible.

The GUI should not hide safety-critical operations. In particular, APOS push can be one-click once armed, but it should still have a visible review gate before changing Tag NVS.

## Existing Flutter Foundation

Current useful pieces:

- `flutter_ui/lib/app.dart` already has a full tab shell: Dashboard, Connection, Live View, Sessions, 3D View, Autopositioning.
- `flutter_ui/lib/main.dart` currently launches only `BioSpurScannerApp`; later we should switch it to `BioSpurApp` or add a launcher choice.
- `flutter_ui/lib/features/autopositioning_page.dart` is currently read-only and shows layout/range summaries.
- `flutter_ui/lib/features/connection_page.dart` already scans serial ports from `/dev/serial/by-id` and `/dev/tty*`.
- `flutter_ui/lib/shared/services/script_runner.dart` can run repo-local shell commands and stream logs to Flutter.

Recommended implementation style:

1. Keep Flutter as the operator UI.
2. Add a small Python backend wrapper for workflow commands once the prototype stabilizes.
3. Use `ScriptRunner` for the first version so we can build quickly.

## Device Auto-Detection

The GUI should classify serial ports into lanes:

| Lane | Expected name | SNR | Purpose |
| --- | --- | --- | --- |
| Anchor control | `Master_Anchor` | `960148546` | Anchor BLE control plane, AutoPos sweep, responder mode |
| Tag control | `Master_Tag` | `1050070698` | Tag BLE/NUS control plane, TDMA capture, APOS push |
| Listener | optional SEGGER CDC | varies | Passive UWB listener |

Detection rules, in order:

1. Prefer `/dev/serial/by-id` symlink names containing `Master_Anchor` or `Master_Tag`.
2. If the name is generic, match known USB serial identity from the by-id path when possible.
3. Fall back to manual selection with a clear warning: `Role not proven`.
4. Persist the last confirmed ports in a local GUI settings file.

The UI should display three states per lane:

| State | Meaning |
| --- | --- |
| Missing | No candidate port found |
| Candidate | A matching port was found but not runtime-verified |
| Verified | Port opened and runtime command/preflight confirmed expected role |

The GUI should never silently swap `Master_Anchor` and `Master_Tag`. If both lanes point to the same port, block all workflow buttons.

## Main Screens

### 1. Connection / Control Planes

Purpose: prove that the two masters are connected and distinct.

Controls:

- `Detect Ports`
- `Verify Master_Anchor`
- `Verify Master_Tag`
- `Open Port Settings`

Displayed data:

- Port path
- Friendly USB name
- Expected SNR/name
- Last verify time
- Runtime mode if known
- BLE link readiness summary

First version can use symlink name + command dry-run status. Later version should query firmware using a stable status command.

### 2. AutoPos Workflow

This should be a stepper, not one giant page.

| Step | Button | Script/action | Success condition |
| --- | --- | --- | --- |
| 0 | `Preflight Anchors` | `verify_all_anchor_responder_runtime.py` | ready `8/8` |
| 1 | `Run Sweep` | `run_autopos_sweep_loop.py` | all A-H rounds success |
| 2 | `Extract Pairs` | extractor from sweep summary | `pairs_all.csv` exists with 28 pairs |
| 3 | `Solve Layout` | inter-anchor solver / selected AutoPos solver | report produced, RMS within threshold |
| 4 | `Review Candidate` | GUI summary only | operator approves |
| 5 | `Push APOS` | APOS_TO/APOS_COMMIT verified flow | all target Tags report `layout_match=true` |
| 6 | `Validate` | 60s/180s TDMA capture | TR rate and valid ranges pass threshold |

Recommended default sweep:

```bash
python3 scripts/run_autopos_sweep_loop.py \
  --port "$BIOSPUR_ANCHOR_PORT" \
  --order ABCDEFGH \
  --sw-sets 500 \
  --prewarm-sw-sets 10 \
  --timeout-s 2400 \
  --warmup-min-quality 90 \
  --quiet-tag-name - \
  --no-bootstrap-autopos-reset \
  --reuse-resident-anchor-master \
  --out-dir "$OUT/autopos"
```

The GUI should expose `sw-sets` as presets:

- `100` quick check
- `500` recommended layout solve
- `1000` high-confidence overnight/long run

### 3. Layout Review

Before APOS push, show:

- 3D anchor layout view.
- Pair residual table A-H.
- Inter-anchor RMS and inlier RMS.
- Worst pairs.
- Known weak anchors/pairs flags.
- Previous runtime APOS vs new candidate delta.
- Candidate JSON path.

Recommended approval rule:

- Green: RMS and residuals are within known-good field range.
- Yellow: allow push only with manual checkbox.
- Red: block push unless user explicitly enables developer override.

### 4. APOS Push / Verify

The GUI should treat APOS push as a state-changing operation.

Inputs:

- Candidate layout JSON.
- Target Tags, default current online Tags.
- `Master_Tag` port.

Expected command model:

```text
APOS_TO <BSxxxx> APOS <anchor-id> <x_mm> <y_mm> <z_mm>
APOS_TO <BSxxxx> APOS_COMMIT
APOS_TO <BSxxxx> APOS_STATUS
```

Pass condition:

```text
APOS_VERIFY_ALL layout_match=True
```

The GUI should show per-Tag status:

| Tag | Connected | APOS sent | Commit | Readback | Layout match |
| --- | --- | --- | --- | --- | --- |

If any Tag fails, do not mark the layout as deployed. Keep the failed Tag list visible and offer `Retry Failed Tags`.

### 5. Validation Capture

The GUI should run a validation capture after APOS push.

Default quick validation:

```bash
python3 scripts/run_dual_master_tdma_capture.py \
  --anchor-port "$BIOSPUR_ANCHOR_PORT" \
  --tag-port "$BIOSPUR_TAG_PORT" \
  --duration 180 \
  --targets BSF66F,BS2DCE,BSDC91 \
  --profiles BSF66F:static,BS2DCE:roto,BSDC91:roto \
  --static-hz 5 \
  --roto-hz 10 \
  --motion-hz 5 \
  --with-listener \
  --out-dir "$OUT/validation"
```

Show:

- TR rate per Tag.
- Per-anchor valid rate.
- `quality_flag_percent`.
- Missing sweeps.
- Whether TS/TF output is disabled or enabled.
- Latest static 3D std if offline solver is available.
- Roto circle-fit dynamic error if roto captures are present.

## Operator Workflow

Recommended normal flow:

1. Open GUI.
2. Press `Detect Ports`.
3. Confirm `Master_Anchor` and `Master_Tag` are distinct and verified.
4. Press `Preflight Anchors`.
5. Press `Run 500-set AutoPos`.
6. Press `Solve Layout`.
7. Review layout table and 3D view.
8. Arm `Push APOS`.
9. Press `Push + Verify`.
10. Press `Run 180s Validation`.
11. Save/export the report.

The GUI should support a later `Run All Safe Path` button, but internally it should still stop at the layout review gate before APOS push.

## State Machine

```text
Idle
  -> PortsDetected
  -> ControlPlanesVerified
  -> AnchorPreflightReady
  -> Sweeping
  -> SweepComplete
  -> PairsExtracted
  -> LayoutSolved
  -> ReviewApproved
  -> AposPushing
  -> AposVerified
  -> ValidationRunning
  -> ValidationComplete
```

Failure states:

- `PortAmbiguous`
- `AnchorPreflightFailed`
- `SweepFailed`
- `SolverFailed`
- `LayoutRejected`
- `AposVerifyFailed`
- `ValidationFailed`

Every failure state should preserve:

- exact command
- exit code
- output directory
- last 200 log lines
- suggested retry action

## Safety Gates

Hard blocks:

- `Master_Anchor` and `Master_Tag` are the same port.
- Required port missing.
- Anchor preflight not ready if the selected operation needs anchors.
- Candidate layout JSON missing or malformed.
- APOS push requested with no target Tags.
- APOS verification mismatch.

Soft warnings:

- D/H pair residuals are large.
- Inter-anchor RMS above field threshold.
- Fewer than 28 pair distances in the sweep.
- Tag links are not fully ready.
- Listener missing for a validation run that requested listener.

Firmware flashing should not be in GUI v1. If added later, it must honor the B120 LFRC rule and repository J-Link scripts only.

## Suggested Flutter Architecture

Add these files gradually:

```text
flutter_ui/lib/features/autopos/
  autopos_workflow_page.dart
  autopos_controller.dart
  autopos_models.dart
  autopos_command_builder.dart
  device_detector.dart
  widgets/
    control_plane_card.dart
    workflow_step_card.dart
    command_log_panel.dart
    layout_review_panel.dart
    apos_push_panel.dart

flutter_ui/lib/shared/services/
  device_port_detector.dart
  workflow_artifact_repository.dart
```

Later, add a backend wrapper:

```text
scripts/gui_autopos_backend.py
```

The backend should emit JSON-lines events:

```json
{"type":"stage","stage":"sweep","status":"running"}
{"type":"metric","name":"round_A_sw_count","value":500}
{"type":"artifact","kind":"pairs_csv","path":".../pairs_all.csv"}
{"type":"gate","name":"layout_review","status":"needs_approval"}
```

This keeps Flutter simple and avoids encoding long shell parsing logic in Dart.

## Minimum Viable Implementation

Build this first:

1. Switch Flutter entrypoint to the full `BioSpurApp` shell or add a launch selector.
2. Add a `DevicePortDetector` that classifies `Master_Anchor` and `Master_Tag`.
3. Replace the read-only Autopositioning page with:
   - control plane status cards
   - workflow step cards
   - command preview
   - run/stop buttons using `ScriptRunner`
4. Add buttons for:
   - `Detect Ports`
   - `Preflight Anchors`
   - `Run 500-set Sweep`
   - `Extract Pairs`
   - `Solve Layout`
   - `Run 180s Validation`
5. Add APOS push only after the layout review panel exists.

## Recommended v1 Button Set

| Button | Risk | Notes |
| --- | --- | --- |
| Detect Ports | low | no hardware state change |
| Preflight Anchors | low | runtime role verify |
| Run AutoPos Sweep | medium | changes anchor runtime roles but finalizes responder |
| Extract Pairs | low | file-only |
| Solve Layout | low | file-only |
| Review Candidate | low | GUI-only |
| Push APOS + Verify | high | writes Tag NVS; require arming |
| Run Validation Capture | medium | starts TDMA capture |
| Stop / Cleanup | medium | sends SIGTERM and then role cleanup |

## Things Worth Adding

- A report browser for the latest AutoPos, validation, solver, and dynamic roto reports.
- A “known Tag inventory” panel using `docs/broadcast_tag_inventory.md`.
- A layout version registry: deployed layout, candidate layout, previous layout.
- A one-click export bundle for publication/advisor review.
- A quality dashboard:
  - TR rate
  - per-anchor valid rate
  - per-anchor sigma
  - pair residuals
  - GDOP map
  - dynamic circle-fit residual
- A dry-run mode that prints commands without running them.

## My Recommendation

Do not make APOS push fully invisible. The fastest safe UX is:

```text
AutoPos -> Solve -> Review -> Push + Verify -> Validate
```

The user experience can still feel like a few buttons, but the GUI should always expose the candidate layout and pass/fail gates before writing APOS to Tags. This is the right tradeoff for the current system because the sweep/solver can produce plausible but wrong layouts when geometry, anchor health, or Tag placement is bad.

