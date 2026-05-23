# To Do Next: Data Workspace Separation

## Goal

Separate fixed program/script code from experiment data output.

Current historical layout mixed these in one place:

```text
/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/autopos_pipeline/erlangen_20260528_mocap
```

That directory contains both stable code/templates and generated experiment data:

```text
tools/
solver/scripts/
docs/
captures/
solver/work/
solver/outputs/
```

The correct model is:

- fixed code stays in the repo
- experiment output goes into a user-selected data workspace

Example workspace:

```text
~/Desktop/Erlangen_Test_20260523/
```

Expected generated structure:

```text
Erlangen_Test_20260523/
├── captures/
│   └── erlangen_20260528_optitrack/
│       ├── sweep_...
│       ├── static_...
│       ├── roto_...
│       ├── wand_...
│       └── session_notes.csv
├── solver/
│   ├── work/
│   │   └── field_dataset_staged/
│   └── outputs/
│       ├── v4io_field_check/
│       └── v1_to_v4_io_field_check/
├── exports/
├── logs/
└── workspace.json
```

## Current Implementation Status

Basic workspace routing has been implemented.

### Flutter UI

File:

```text
flutter_ui_autopos/lib/main.dart
```

Added:

- `activeWorkspaceRoot`
- `capturesRoot`
- `activeCaptureRoot`
- `activeStagedDataset`
- `activeSolverOutputs`
- `workspaceSetup`
- header `Data workspace` input
- `Desktop` shortcut button
- `Use` button
- workspace folder creation
- `workspace.json`
- persisted UI setting:

```text
~/.config/biospur-autopos/settings.json
```

### Shell Alias

File:

```text
autopos_pipeline/erlangen_20260528_mocap/tools/erlangen_aliases.sh
```

Changed:

```bash
export BIOSPUR_CAPTURE_ROOT="${BIOSPUR_CAPTURE_ROOT:-${BIOSPUR_ERLANGEN_ROOT}/captures}"
```

This allows Flutter to override capture root:

```bash
export BIOSPUR_CAPTURE_ROOT=<workspace>/captures
bio_setup erlangen_20260528_optitrack
```

### Solver Routing

Solver commands now pass explicit paths:

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

## Still To Improve

### 1. Real Folder Picker

Current UI uses:

- editable path field
- `Desktop` generated path
- `Use` confirmation

Better future UI:

- `Choose Workspace`
- native folder picker
- maybe `Create Workspace` dialog with name field

Current choice was intentionally simple to avoid adding Flutter plugin dependencies during field-prep work.

### 2. Workspace Manager

Useful later:

- recent workspace list
- open existing workspace
- create new workspace from template
- show workspace name in header
- show whether workspace is on local disk / USB / network

### 3. Stronger Provenance

Each solver output should record:

- exact workspace path
- exact selected sweep folder
- solver mode
- US measurement source
- staged manifest path
- capture session name
- repo path / code version if available

This already exists partially through `workspace.json`, `stage_manifest.json`, and UI chips, but it can be made more explicit.

### 4. Path Safety

Before running destructive clear:

- show active workspace path clearly
- refuse to clear if workspace path equals repo root by mistake
- refuse suspicious paths like `/`, `/home`, `/tmp`, or empty path

Current clear has confirm dialog, but stricter guardrails are still worth adding.

### 5. Copy/Archive Workflow

Add a button later:

- `Archive Workspace`
- create `.tar.gz` or `.zip`
- include `workspace.json`
- include captures, solver outputs, exports, logs
- exclude caches

This would make moving data to another machine cleaner.

## Verification Checklist

Run after any workspace-path changes:

```bash
cd /home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/flutter_ui_autopos
../.tooling/flutter/bin/dart format lib/main.dart
../.tooling/flutter/bin/flutter analyze
../.tooling/flutter/bin/flutter test
```

Manual UI check:

1. Open app.
2. Set `Data workspace` to a new desktop folder.
3. Press `Use`.
4. Confirm these folders are created:

```text
captures/erlangen_20260528_optitrack/
solver/work/
solver/outputs/
exports/
logs/
workspace.json
```

5. Run a short sweep.
6. Confirm sweep appears under:

```text
<workspace>/captures/erlangen_20260528_optitrack/
```

7. Run `Stage Dataset` / solver.
8. Confirm output appears under:

```text
<workspace>/solver/outputs/
```

9. Run capture / playback export.
10. Confirm trajectory export appears under:

```text
<workspace>/exports/
```

## Build Deb

After changes:

```bash
cd /home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/flutter_ui_autopos
../.tooling/flutter/bin/flutter build linux --release
./packaging/linux_deb/build_deb.sh
```

Output:

```text
flutter_ui_autopos/build/deb/biospur-autopos_1.0.0_amd64.deb
```

Install:

```bash
cd /home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start
sudo dpkg -i flutter_ui_autopos/build/deb/biospur-autopos_1.0.0_amd64.deb
sudo apt-get install -f
```

## Design Decision

Do not copy scripts into each workspace.

Keep:

```text
repo = /home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start
```

Variable:

```text
workspace = user-selected experiment output directory
```

Reason:

- one code source
- many clean data containers
- easier to reproduce and archive
- lower risk of running stale copied scripts

