# BioSpur AutoPos Flutter UI Desktop Handoff

This note is for moving the current Flutter UI work to another Linux desktop and continuing from there.

## Current Status

The app is `flutter_ui_autopos/`. It is a Flutter Linux desktop UI for:

- AutoPos sweep
- Anchor layout analysis / solver
- Static / roto / wand / free tag capture
- Realtime capture visualization
- Playback visualization
- Workspace-based experiment output

The latest `.deb` was built successfully:

```bash
/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/flutter_ui_autopos/build/deb/biospur-autopos_1.0.0_amd64.deb
```

Validation already run on the current machine:

```text
flutter analyze: PASS
flutter test: PASS
flutter build linux --release: PASS
deb build: PASS
```

## Copy Scope

Recommended: copy the whole repo/workspace directory:

```text
/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start
```

That keeps these paths together:

- `flutter_ui_autopos/`
- `autopos_pipeline/erlangen_20260528_mocap/`
- `SS-TWR/alt-SS-TWR/broadcast/`
- `.tooling/flutter/`

If `.tooling/flutter/` is not copied, install/provide Flutter on the desktop and adjust commands below.

## Important Fixed Paths

The Flutter app currently assumes this repo root:

```dart
const repoRoot = '/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start';
```

If the desktop uses the same absolute path, no code changes are needed.

If the desktop path differs, update this line in:

```text
flutter_ui_autopos/lib/main.dart
```

Then rebuild.

## Workspace Output Model

The UI now supports a user-selected data workspace.

Default workspace:

```text
/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/autopos_pipeline/erlangen_20260528_mocap
```

In the app header:

- edit `Data workspace`
- click `Browse` to choose a workspace folder
- click `Use` to activate it; the UI shows a confirmation dialog with the active path

The selected workspace is persisted here:

```bash
~/.config/biospur-autopos/settings.json
```

Each active workspace gets this structure:

```text
<workspace>/
├── captures/
│   └── erlangen_20260528_optitrack/
├── solver/
│   ├── work/
│   │   └── field_dataset_staged/
│   └── outputs/
├── exports/
├── logs/
└── workspace.json
```

Runtime data should go into the workspace, not necessarily into the repo.

## Install Current Deb

On the desktop:

```bash
cd /home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start
sudo dpkg -i flutter_ui_autopos/build/deb/biospur-autopos_1.0.0_amd64.deb
sudo apt-get install -f
```

Launch from the app menu as `BioSpur AutoPos`, or run:

```bash
biospur-autopos
```

## Run From Source

From repo root:

```bash
cd /home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/flutter_ui_autopos
../.tooling/flutter/bin/flutter run -d linux
```

If Flutter is installed globally:

```bash
cd /home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/flutter_ui_autopos
flutter run -d linux
```

## Build Deb Again

```bash
cd /home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/flutter_ui_autopos
../.tooling/flutter/bin/flutter build linux --release
./packaging/linux_deb/build_deb.sh
```

Output:

```text
flutter_ui_autopos/build/deb/biospur-autopos_1.0.0_amd64.deb
```

## Hardware Setup Reminders

Repository operating rules still apply:

- Never use `nrfjprog`.
- Use repo J-Link scripts only.
- Master Anchor SNR: `960148546`
- Master Tag SNR: `1050070698`
- B120 master-control builds must use internal LFRC oscillator.

CDC defaults in `erlangen_aliases.sh`:

```bash
BIOSPUR_ANCHOR_PORT=/dev/serial/by-id/usb-BioSpur_BioSpur_BLE_Control_87EA2F4A526C5A02-if00
BIOSPUR_TAG_PORT=/dev/serial/by-id/usb-Master_Tag_BioSpur_BLE_Control_6918E0384172A49F-if00
```

If the desktop sees different `/dev/serial/by-id` names, use the UI `Connect`/`bio_ports` information and update env vars as needed.

## Current Relevant Changes

### Workspace Routing

`flutter_ui_autopos/lib/main.dart` now has:

- `activeWorkspaceRoot`
- `capturesRoot`
- `activeStagedDataset`
- `activeSolverOutputs`
- `workspaceSetup`

Sweep/capture/US commands now export:

```bash
BIOSPUR_CAPTURE_ROOT=<workspace>/captures
```

Then call:

```bash
bio_setup erlangen_20260528_optitrack
```

### Shell Alias Change

`autopos_pipeline/erlangen_20260528_mocap/tools/erlangen_aliases.sh`

Now allows external capture root override:

```bash
export BIOSPUR_CAPTURE_ROOT="${BIOSPUR_CAPTURE_ROOT:-${BIOSPUR_ERLANGEN_ROOT}/captures}"
```

### Solver Routing

Solver staging and outputs are routed to the active workspace:

```bash
python3 stage_field_dataset.py \
  --session <workspace>/captures/erlangen_20260528_optitrack \
  --out <workspace>/solver/work/field_dataset_staged

python3 run_v4io_field_check.py \
  --staged <workspace>/solver/work/field_dataset_staged \
  --out <workspace>/solver/outputs/v4io_field_check

python3 run_v1_to_v4_io.py \
  --staged <workspace>/solver/work/field_dataset_staged \
  --out <workspace>/solver/outputs/v1_to_v4_io_field_check
```

### Capture Preflight Toggle

Tab 3 has an anchor preflight toggle:

- OFF: fast capture, skips anchor preflight
- ON: capture verifies/sets anchor responder state first

Underlying env:

```bash
BIOSPUR_SKIP_ANCHOR_PREFLIGHT_FOR_CAPTURE=1  # OFF
BIOSPUR_SKIP_ANCHOR_PREFLIGHT_FOR_CAPTURE=0  # ON
```

## Known Risk Areas

See also:

```text
autopos_pipeline/erlangen_20260528_mocap/docs/ui_capture_solver_risk_review_20260523.md
```

Main things to keep in mind:

- Realtime UI is visualization only. Raw capture data remains the source of truth.
- Solver outputs are per active workspace now, but if the workspace selector is wrong, analysis will use the wrong data container.
- `Stage + Run Solver` automatically stages the selected/latest complete sweep before running the solver.
- If no sweep is pinned, solver can still use latest complete sweep at run time. Prefer selecting a concrete sweep in Anchor Layout Analysis before running solver.
- Free tag capture currently tries to silence non-target BS tags through targeted AOTA. This needs hardware validation because it depends on firmware target filtering behavior.
- Capture cleanup may still include broad AOTA behavior in lower-level scripts; be careful when unrelated powered tags are nearby.

## Quick Desktop Smoke Test

After copying and installing:

```bash
cd /home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/flutter_ui_autopos
../.tooling/flutter/bin/flutter analyze
../.tooling/flutter/bin/flutter test
```

Then open app:

```bash
biospur-autopos
```

In UI:

1. Set `Data workspace` to a desktop test folder.
2. Click `Use`.
3. Verify these folders are created:
   - `captures/erlangen_20260528_optitrack`
   - `solver/work`
   - `solver/outputs`
   - `exports`
   - `logs`
4. Click `Connect`.
5. Verify CDC status for Master Anchor / Master Tag.

## If Continuing Development

Before editing:

```bash
cd /home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/flutter_ui_autopos
../.tooling/flutter/bin/flutter analyze
```

After editing:

```bash
../.tooling/flutter/bin/dart format lib/main.dart
../.tooling/flutter/bin/flutter analyze
../.tooling/flutter/bin/flutter test
```

Rebuild deb:

```bash
../.tooling/flutter/bin/flutter build linux --release
./packaging/linux_deb/build_deb.sh
```
