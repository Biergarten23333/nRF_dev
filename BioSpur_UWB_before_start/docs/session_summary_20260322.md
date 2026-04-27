# Session Summary 2026-03-22

This note summarizes the main work completed across the recent UWB / BLE /
reference-tag sessions so the current system state does not live only in chat.

## Current System State

- UWB ranging / positioning mode:
  - `SS-TWR`
  - Tag is the `initiator`
  - Anchors are `responders`
  - Tag does the final on-device localization
- Anchor layout in runtime:
  - stored in [src/uwb_anchor_layout.c](/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/src/uwb_anchor_layout.c)
  - refreshed by the host-side Ref115 autopositioning workflow
- Static reference tag:
  - `760186115`
  - current recommended monitor subset: `B,D,F,G`
- BLE motion tag:
  - OTA-upgradable UWB tag with NUS output
  - checked through `683234364` nRF52840 BLE master

## What Was Done

### 1. Anchor Positioning / Autopositioning

The old anchor-layout assumption was too rigid. It originally treated the
layout as strongly coplanar. That was changed to a more realistic
host-side solve:

- use the stored `A-H` inter-anchor matrix
- use static `Ref115 -> Anchor` range captures
- use a soft `Ref115` height prior near `700 mm`
- use softer structural constraints instead of blindly forcing exact planes

Files involved:

- [scripts/solve_anchor_layout.py](/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/scripts/solve_anchor_layout.py)
- [scripts/solve_anchor_layout_iterative.py](/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/scripts/solve_anchor_layout_iterative.py)
- [data/anchor_layout_ah_calibrated.json](/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/data/anchor_layout_ah_calibrated.json)
- [data/anchor_layout_ah_runtime.json](/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/data/anchor_layout_ah_runtime.json)
- [src/uwb_anchor_layout.c](/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/src/uwb_anchor_layout.c)

Operational workflow:

1. Put `115` into calibration mode.
2. Capture `Range anchor=...` logs and write `ranges.csv`.
3. Run host-side solver.
4. Update runtime anchor layout files.
5. Rebuild and reflash `115` so on-device localization uses the new anchor
   coordinates.

Main automation script:

- [scripts/recalibrate_anchor_layout_with_ref115.py](/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/scripts/recalibrate_anchor_layout_with_ref115.py)

Detailed workflow note:

- [docs/ref115_autopositioning_workflow.md](/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/docs/ref115_autopositioning_workflow.md)

### 2. Reference Tag Workflow

`Ref115` was split into two separate roles.

Calibration mode:

- goal: maximize anchor coverage
- behavior:
  - open to all `8` anchors
  - emit verbose `Range ...` lines
  - produce `ranges.csv` for the autopositioning solver

Monitor mode:

- goal: lowest long-window jitter for static health monitoring
- behavior:
  - use a fixed `4-anchor` subset
  - optimized for stable `XYZ`, not anchor coverage

What was optimized on `115`:

- post-solve position filter parameters
- range continuity gate parameters
- fixed 4-anchor subset choice

Current best `Ref115` monitor settings:

- filter:
  - `APP_TAG_EKF_ENABLE = 1`
  - `APP_TAG_EKF_MEAS_STD_MM = 200`
  - `APP_TAG_EKF_PROC_ACCEL_MM_S2 = 1`
  - `APP_TAG_EKF_OUTLIER_GATE_MM = 35`
- continuity gate:
  - `APP_TAG_RANGE_SOFT_RESIDUAL_MM = 140`
  - `APP_TAG_RANGE_HARD_RESIDUAL_MM = 260`
- fixed subset:
  - `B,D,F,G`

Important optimization scripts:

- [scripts/optimize_tag_ekf_static.py](/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/scripts/optimize_tag_ekf_static.py)
- [scripts/optimize_tag_continuity_static.py](/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/scripts/optimize_tag_continuity_static.py)
- [scripts/optimize_ref115_monitor_count.py](/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/scripts/optimize_ref115_monitor_count.py)
- [scripts/evaluate_ref115_fixed_subset_live.py](/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/scripts/evaluate_ref115_fixed_subset_live.py)

Important result files:

- EKF sweep result:
  - [logs/tag_sessions/ekf_opt_115/ekf115_20260320_final_selection.json](/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/logs/tag_sessions/ekf_opt_115/ekf115_20260320_final_selection.json)
- continuity sweep result:
  - [logs/tag_sessions/continuity_opt_115/result_20260320_cont_opt2.json](/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/logs/tag_sessions/continuity_opt_115/result_20260320_cont_opt2.json)
- monitor-count sweep:
  - [logs/tag_sessions/ref115_monitor_opt/result_20260321_monitor_opt1.json](/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/logs/tag_sessions/ref115_monitor_opt/result_20260321_monitor_opt1.json)

Final fixed-subset ranking from the long live tests:

| Subset | Duration | X std | Y std | Z std | RMS mean | Max mean | Result |
|---|---:|---:|---:|---:|---:|---:|---|
| `B,C,F,G` | 20 min | 4.47 mm | 1.73 mm | 3.69 mm | 63.98 mm | 85.11 mm | stable but noisier |
| `A,B,F,G` | 20 min | 2.01 mm | 2.24 mm | 3.12 mm | 49.19 mm | 74.74 mm | better than `BCFG` |
| `B,D,F,G` | 20 min | 2.61 mm | 1.57 mm | 3.98 mm | 22.93 mm | 33.35 mm | current best |

Key result folders:

- [ref115_fixed_BCFG_20m_20260321](/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/logs/tag_sessions/ref115_fixed_BCFG_20m_20260321)
- [ref115_fixed_ABFG_20m_20260321](/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/logs/tag_sessions/ref115_fixed_ABFG_20m_20260321)
- [ref115_fixed_BDFG_20m_20260321](/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/logs/tag_sessions/ref115_fixed_BDFG_20m_20260321)

### 3. BLE / OTA / Motion-Tag Workflow

BLE side was stabilized and separated into 3 roles:

Normal BLE receiver:

- `683234364`
- scans for NUS
- receives `TagSummary` from the motion tag

OTA master:

- used only for OTA upload / test / reset operations
- supports remote `OS reset` through the MCUmgr SMP path

BLE motion tag:

- UWB tag with BLE/NUS output
- supports OTA
- now also supports a direct BLE `REBOOT` command
- current motion-focused BLE build for `760186127` uses a compact
  `TagSummary` payload and bundles about 3 records per BLE packet to keep
  a safety margin instead of filling the NUS payload to the edge

Files involved:

- [apps/master/src/master_app.c](/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/apps/master/src/master_app.c)
- [apps/master/CMakeLists.txt](/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/apps/master/CMakeLists.txt)
- [apps/master_ota/src/main.c](/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/apps/master_ota/src/main.c)
- [apps/tag/src/uwb_tag_ble.c](/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/apps/tag/src/uwb_tag_ble.c)
- [apps/tag_ble_lite/](/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/apps/tag_ble_lite/)
- [scripts/build_motion_tag_ota_profile.sh](/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/scripts/build_motion_tag_ota_profile.sh)
- [scripts/build_tag_ble_motion.sh](/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/scripts/build_tag_ble_motion.sh)
- [scripts/evaluate_motion_tag_profile.py](/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/scripts/evaluate_motion_tag_profile.py)
- [scripts/capture_master_ble_session.py](/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/scripts/capture_master_ble_session.py)

Important BLE facts established in this work:

- OTA from `nRF52840 -> motion tag` works
- ordinary BLE data path works
- BLE `REBOOT` command now exists and works
- OTA DFU reset path also works
- BLE motion-tag payload can be emitted in a compact form

Proof logs:

- OTA remote reboot:
  - [master_ota_remote_reboot_20260322_raw.log](/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/logs/master_ble_sessions/master_ota_remote_reboot_20260322_raw.log)
- direct NUS `REBOOT`:
  - [master_reboot_once_20260322_raw.log](/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/logs/master_ble_sessions/master_reboot_once_20260322_raw.log)
- restored normal BLE receiver check:
  - [post_reboot_feature_restore_check_20260322/raw.log](/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/logs/master_ble_sessions/post_reboot_feature_restore_check_20260322/raw.log)
  - [post_reboot_feature_restore_check_20260322/summary.json](/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/logs/master_ble_sessions/post_reboot_feature_restore_check_20260322/summary.json)

### 4. UWB Solver / Filtering Changes

The recent code changes on the UWB side were not only parameter tweaks.

Main changes:

- range continuity gate added and tuned
- recent range history emphasized over ancient full-history quality
- weak `3+1` geometry combinations removed from final solve acceptance
- output consistency fixed so reported `xyz` and residuals refer to the same
  solution state
- tag startup now reports whether the post-solve filter is enabled

Files involved:

- [src/ss_twr_init.c](/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/src/ss_twr_init.c)
- [src/uwb_tag_loc.c](/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/src/uwb_tag_loc.c)
- [src/uwb_range_tracker.c](/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/src/uwb_range_tracker.c)
- [src/uwb_ekf.c](/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/src/uwb_ekf.c)
- [include/uwb_ekf.h](/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/include/uwb_ekf.h)
- [apps/tag/src/tag_app.c](/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/apps/tag/src/tag_app.c)

Important note:

- the current codebase calls this block `EKF`
- in practice it is still a lightweight post-solve position filter, not a full
  IMU-fused nonlinear motion EKF

### 5. Pure USB Serial Tag Variant

In addition to the BLE/OTA-capable `apps/tag` app, a separate serial-only
variant was added for cases where the fixed tag should stay on the USB/J-Link
console only and must not carry BLE or MCUboot/OTA plumbing.

Files involved:

- [apps/tag_usb/CMakeLists.txt](/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/apps/tag_usb/CMakeLists.txt)
- [apps/tag_usb/prj.conf](/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/apps/tag_usb/prj.conf)
- [scripts/build_tag_usb.sh](/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/scripts/build_tag_usb.sh)

What it does:

- disables BLE/NUS on the tag
- disables MCUboot / OTA plumbing on the tag
- keeps the UWB ranging / localization path intact
- keeps USB/J-Link serial logging as the primary control/output channel

Typical build command:

```bash
scripts/build_tag_usb.sh 1
```

This builds the pure serial tag image and emits:

- `build-tag-usb-tag1/zephyr/zephyr.hex`

Use this variant when you want a fixed tag that stays on serial only.

## Build Directory Map

The repo has many `build-*` directories. The important ones used in this work
are:

| Build dir | Purpose |
|---|---|
| `build-tag-ref115-autopos` | rebuild/flash target used by Ref115 autopositioning workflow |
| `build-ref115-monitor-4` | Ref115 monitor-mode build with fixed 4-anchor profile |
| `build-ref115-fixed-bcfg` | live fixed-subset test build for `B,C,F,G` |
| `build-ref115-fixed-abfg` | live fixed-subset test build for `A,B,F,G` |
| `build-ref115-fixed-bdfg` | live fixed-subset test build for `B,D,F,G` |
| `build-tag-slot0` | existing TDMA tag image used earlier for a direct USB tag role |
| `build-tag-slot1` | existing TDMA tag image used earlier for a direct USB tag role |
| `build-tag-ota-uwb` | OTA-capable BLE/UWB tag build used for remote BLE/UWB checks |
| `build-tag-ota-motion-v3` | motion-tag OTA profile that became the stable BLE motion baseline |
| `build-tag-ota-motion_v4_tight_20260321` | tighter motion experiment, not kept as final baseline |
| `build-tag-ota-motion_remote_reboot_20260322` | OTA motion build with the new BLE `REBOOT` command |
| `build-tag-ble-motion-tag127-slot0` | motion-focused BLE-lite build for `760186127` |

## Session Update 2026-03-23

This section records the later BLE motion-tag work and the build/flash
corrections that were needed before the motion workflow could be trusted.

### 1. Motion BLE tags were split from the reference tag

The static reference tag stayed untouched:

- `115` remains the fixed reference node
- it keeps the reference / monitoring role
- it is not part of the motion-tag optimization path

The motion side was reorganized around the two BLE-capable tags:

- `113`
- `127`

They were treated as a single OTA-capable motion family with identical
firmware behavior and only a different `TAG_ID` / BLE device name.

Key target naming used in the final rebuild:

- `Tag_rot_113`
- `Tag_rot_127`

The `52840` board stayed as the BLE master / central side that scans and
receives the motion `TagSummary` stream.

### 2. The first 120 s capture was invalid because the tag build was stale

The first long capture attempts were not reliable because the tag image had
been built without the correct per-tag environment values. In particular, the
resulting tag booted with default-looking identity values instead of the
intended `113` / `127` identities.

That made the early 120 s output untrustworthy for two reasons:

- the tag identity was not aligned with the intended motion build
- the BLE/OTA side had not yet been rebuilt with the explicit `TAG_ID`
  / `TAG_DEVICE_NAME` values

The fix was to rebuild the OTA-capable motion images explicitly with the
correct build-time parameters, instead of relying on defaults.

### 3. OTA-capable motion tags were rebuilt with explicit IDs

The motion tag build script was used with explicit tag-specific environment
values so each image carried the correct identity and signing version.

The important rebuild pattern was:

```bash
TAG_ID=127 TAG_DEVICE_NAME=Tag_rot_127 TAG_SIGN_VERSION=0.0.1+127 \
  ./scripts/build_motion_tag_ota_profile.sh \
  build-tag-ota-motion-compact-tag127 \
  build-master-ota-motion-compact-tag127

TAG_ID=113 TAG_DEVICE_NAME=Tag_rot_113 TAG_SIGN_VERSION=0.0.1+113 \
  ./scripts/build_motion_tag_ota_profile.sh \
  build-tag-ota-motion-compact-tag113 \
  build-master-ota-motion-compact-tag113
```

The corresponding images were then reflashed to:

- `760186127`
- `760186113`
- `683234364` for the OTA-capable BLE master side

This rebuild step mattered because the build family and the identity tags
had to match before BLE motion data could be interpreted correctly.

### 4. BLE advertisement and OTA discovery were corrected

The tag-side BLE code was updated so OTA discovery could actually work
reliably from the BLE master side.

The main fix was in the tag BLE advertisement path:

- the DFU SMP UUID was made part of the advertised service data when OTA is
  enabled
- the advertising payload was split into advertisement data and scan-response
  name data
- the tag now starts advertising in a way that the OTA master can detect

Relevant files:

- [apps/tag/src/uwb_tag_ble.c](/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/apps/tag/src/uwb_tag_ble.c)
- [apps/master_ota/src/main.c](/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/apps/master_ota/src/main.c)

Observed effect:

- `52840` OTA master could scan for the OTA-capable tag service
- the tag could boot into the BLE app path and reach BLE init
- the motion build could be identified and tracked by the master side

### 5. BLE packet size was deliberately capped

The motion BLE payload was intentionally kept compact.

The policy that was settled on was:

- batch only a small number of motion summaries per BLE packet
- keep approximately 20 percent payload headroom
- do not fill the BLE payload to the edge

This was done to reduce pressure on the BLE transport and keep the
communication side practical for multiple tags.

### 6. 52840 BLE master roles were kept separate

The `52840` side was used in two clear modes:

- normal BLE receiver mode for motion summary reception
- OTA-capable master mode for DFU / reset / control

The ordinary master path was used to receive `TagSummary` data, while the
OTA-capable path was used when the tag needed to be rebooted or updated.

This split was important because the motion tag work became much easier to
debug once the master role was explicit instead of overloaded.

### 7. The 120 s capture was not yet a clean final result when the session was interrupted

At the point this session was stopped, the key state was:

- the tag images had been rebuilt with explicit IDs
- `127` had been verified to boot into the intended app path and reach BLE
  init
- the BLE master side had been updated to track the OTA-capable motion tag
- the earlier 120 s capture attempts, which ran before the corrected rebuild,
  were not valid final results

The correct next step was to rerun the 120 s capture only after the rebuilt
images were in place and the tag/master pairing had been confirmed.

### 8. Supporting tooling created during the session

The session also produced or updated a few local tools and helpers that now
support this workflow:

- build scripts for the motion OTA profiles
- capture scripts for BLE master sessions
- the direct serial live-view / session viewer tooling
- the separate pure USB tag variant for the reference path

These support files are useful because they let the BLE motion workflow and
the reference workflow stay separate instead of interfering with one another.
| `build-tag-usb-tag1` | pure USB serial tag build without BLE/OTA/MCUboot |
| `build-master-ble` | normal nRF52840 BLE receiver build |
| `build-master-ota` | baseline OTA master build |
| `build-master-ota-motion_v3_restore_20260321` | OTA master used while restoring the stable motion profile |
| `build-master-ota-motion_remote_reboot_20260322` | OTA master used to verify remote DFU reboot |
| `build-master-reboot-once-20260322` | special one-shot BLE master used to verify the new NUS `REBOOT` command |

## Recommended Current Workflow Split

Ref115:

- use as:
  - anchor-layout calibration source
  - static health monitor
- current default monitor subset:
  - `B,D,F,G`

Motion BLE tag:

- use as:
  - dynamic motion / human-motion test target
  - BLE live telemetry source for `683234364`
- current motion BLE build:
  - `760186127 -> build-tag-ble-motion-tag127-slot0/merged.hex`

Autopositioning:

- trigger only when you want to refresh runtime anchor layout
- do not treat it as an always-on runtime network feature

## Fast Entry Points

Main static reference workflow:

```bash
python3 scripts/recalibrate_anchor_layout_with_ref115.py
```

Passive BLE receive check:

```bash
python3 scripts/capture_master_ble_session.py \
  683234364 \
  /dev/serial/by-id/usb-SEGGER_J-Link_000683234364-if00 \
  --duration 15 \
  --no-reset
```

Ref115 fixed-subset live test:

```bash
python3 scripts/evaluate_ref115_fixed_subset_live.py \
  --subset BDFG \
  --duration 120
```

### 9. Desktop UI / Flutter Frontend

A separate desktop frontend was started under `flutter_ui/` so the UWB
workflow would not live only in shell scripts and logs.

The Flutter app was organized as a tabbed interface instead of a single
monolithic page. The main tabs are:

- `Dashboard`
- `Live View`
- `Sessions`
- `Autopositioning`
- `3D View`

What the UI was wired to do:

- read current session summaries from the generated `summary.json` files
- tail live serial / log output instead of only showing static snapshots
- show simple `XYZ`, `rms`, and `max` series in the live view
- expose session start/stop controls that call the existing Python scripts
- render the current anchor layout and active tag position as a 3D-style view
- keep the UI separate from the firmware tree so it does not mix with `src/`
  or `build-*`

Useful files:

- [flutter_ui/lib/app.dart](/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/flutter_ui/lib/app.dart)
- [flutter_ui/lib/features/dashboard_page.dart](/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/flutter_ui/lib/features/dashboard_page.dart)
- [flutter_ui/lib/features/live_view_page.dart](/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/flutter_ui/lib/features/live_view_page.dart)
- [flutter_ui/lib/features/sessions_page.dart](/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/flutter_ui/lib/features/sessions_page.dart)
- [flutter_ui/lib/features/autopositioning_page.dart](/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/flutter_ui/lib/features/autopositioning_page.dart)
- [flutter_ui/lib/features/three_d_view_page.dart](/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/flutter_ui/lib/features/three_d_view_page.dart)
- [flutter_ui/run.sh](/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/flutter_ui/run.sh)

The UI was treated as a convenience layer over the existing capture and
analysis scripts. It did not replace the firmware flow; it only surfaced the
data and session controls in a more usable way.

### 10. Multi-BLE Motion Tags: `113` + `127`

This phase kept `115` untouched as the static reference tag and focused only on
bringing `113` and `127` into a usable shared BLE motion / OTA-capable setup.

Unified identity / naming:

- `113` advertises as `Tag_rot_113`
- `127` advertises as `Tag_rot_127`
- both tags use the same OTA-capable motion firmware family, differing only by
  `TAG_ID` / device name

What was fixed:

1. Shared tag BLE advertising was hardened in
   [apps/tag/src/uwb_tag_ble.c](/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/apps/tag/src/uwb_tag_ble.c):
   - advertising includes DFU SMP UUID
   - advertising includes a BioSpur manufacturer token carrying `APP_TAG_ID`
   - this avoids relying only on the name being present in the same advertising packet
2. The `52840` multi-tag central had a real scan parser bug in
   [apps/master/src/master_multi_app.c](/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/apps/master/src/master_multi_app.c):
   - the same advertisement buffer was parsed multiple times with `bt_data_parse()`
   - earlier parses consumed the buffer, so later UUID/token checks always failed
   - fix: every matcher now parses a local copy of the advertisement buffer
3. The central was also updated to skip duplicate `bt_conn_le_create()` attempts
   for addresses already assigned to an active peer slot.
4. The BLE capture helper
   [scripts/capture_master_ble_session.py](/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/scripts/capture_master_ble_session.py)
   was updated to count the newer log format:
   - `Connected[slot]: ...`
   - `Disconnected[slot]: ...`

What was flashed:

- `113`:
  - [build-tag-ota-motion-compact-tag113/merged.hex](/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/build-tag-ota-motion-compact-tag113/merged.hex)
- `127`:
  - [build-tag-ota-motion-compact-tag127/merged.hex](/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/build-tag-ota-motion-compact-tag127/merged.hex)
- `52840` central:
  - [build-master-multi-tagrot/merged.hex](/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/build-master-multi-tagrot/merged.hex)

Important external verification:

- Linux host BLE scan saw both tags on-air:
  - `Tag_rot_113`
  - `Tag_rot_127`

Successful multi-tag BLE proof session:

- [raw.log](/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/logs/master_ble_sessions/multitag_reset_25s_streamcheck/raw.log)
- [summary.json](/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/logs/master_ble_sessions/multitag_reset_25s_streamcheck/summary.json)

What happened in that session:

- `52840` discovered both BLE tags
- connected to both links
- brought both links to NUS ready state
- received compact motion packets from both links

Raw-log proof lines include:

- `Connected[0]: FF:5E:D2:80:EA:D2 (random)`
- `Connected[1]: D5:53:48:EF:8F:59 (random)`
- `BLE[0] notify: TS ...`
- `BLE[1] notify: TS ...`

Current practical status:

- `115` remains the static reference tag and was not modified in this phase
- `113` and `127` are now on the same BLE motion / OTA-capable build family
- `52840` can now discover and connect to both tags and receive compact BLE motion packets

Remaining caveats:

- `127` still showed some instability in USB-observed runs
- `113` BLE visibility / central connectivity worked, but its direct USB serial behavior remained less clean than `127`
- BLE transport and discovery were fixed first; longer-window motion-quality tuning is still follow-up work

### 2026-03-23 productization pass for `113` / `127`

Goal of this pass:

- keep `115` untouched as static reference
- push `113` + `127` toward a product-like BLE motion runtime
- keep OTA capability on both motion tags
- verify the central can sustain both links above the required `10 Hz`

What was changed:

1. Tag-side BLE advertising in
   [apps/tag/src/uwb_tag_ble.c](/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/apps/tag/src/uwb_tag_ble.c)
   was refined again:
   - compact BLE motion packets remain limited to `3` records per packet
   - manufacturer token still carries `APP_TAG_ID`
   - advertising was updated to use:
     - connectable advertising payload
     - scan-response name payload
   - this fixed the temporary `adv start rc=-22` regression introduced while adding explicit names
2. Central-side multi-link logging in
   [apps/master/src/master_multi_app.c](/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/apps/master/src/master_multi_app.c)
   now records `tag_id` from the BioSpur manufacturer token, so the central no longer relies only on BLE address order.
3. BLE capture parsing in
   [scripts/capture_master_ble_session.py](/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/scripts/capture_master_ble_session.py)
   now supports:
   - richer `BLE[slot:...:tag_id] notify:` prefixes
   - `stream_tag_ids` in `summary.json`
   - `tag_id` and `peer_name` columns in `positions.csv`

What was rebuilt and flashed:

- `113` motion tag:
  - [build-tag-ota-motion-compact-tag113/merged.hex](/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/build-tag-ota-motion-compact-tag113/merged.hex)
- `127` motion tag:
  - [build-tag-ota-motion-compact-tag127/merged.hex](/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/build-tag-ota-motion-compact-tag127/merged.hex)
- `52840` multi-tag central:
  - [build-master-multi-tagrot/merged.hex](/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/build-master-multi-tagrot/merged.hex)

Key runtime verification:

1. `127` USB-side post-flash recovery confirmed:
   - BLE advertising restored with `Tag BLE adv start rc=0`
   - app enters normal motion output
   - fixed subset remains `A,B,F,G`
2. Dual-tag 30 s BLE validation:
   - [summary.json](/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/logs/master_ble_sessions/multitag_30s_product_named_v2/summary.json)
   - [raw.log](/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/logs/master_ble_sessions/multitag_30s_product_named_v2/raw.log)
   - results:
     - `unique_position_samples = 924`
     - `unique_streams = 2`
     - `connected_count = 2`
     - `disconnected_count = 0`
3. Dual-tag 120 s BLE validation before the final naming/parser pass:
   - [summary.json](/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/logs/master_ble_sessions/multitag_120s_product_check/summary.json)
   - results:
     - `unique_position_samples = 4421`
     - `unique_streams = 2`
     - `connected_count = 2`
     - `disconnected_count = 0`

Measured BLE motion rates:

- 30 s named validation:
  - conn `0` / `tag_id=1`:
    - `492` samples
    - `mean_motion_dt_ms = 52.22`
    - `mean_hz = 19.15`
    - `median_hz = 21.74`
  - conn `1` / `tag_id=2`:
    - `432` samples
    - `mean_motion_dt_ms = 51.08`
    - `mean_hz = 19.58`
    - `median_hz = 21.74`
- 120 s long-window validation:
  - conn `0`:
    - `2215` samples
    - `mean_hz = 19.16`
  - conn `1`:
    - `2206` samples
    - `mean_hz = 19.32`

Current interpretation:

- the BLE side is now comfortably above the required `10 Hz`
- the present stable operating point is about `19 Hz` mean per motion tag with compact packet transport
- both links remained connected for the full 120 s window with zero disconnects
- `tag_id=1` corresponds to the `113` build family
- `tag_id=2` corresponds to the `127` build family

Current naming / role convention:

- `115` = static reference tag
- `113` = motion BLE tag, logical `tag_id=1`
- `127` = motion BLE tag, logical `tag_id=2`

Practical status at the end of this pass:

- `115` untouched
- `113` and `127` are on the same OTA-capable compact motion BLE runtime
- `52840` multi-tag central can keep both BLE motion tags online simultaneously
- BLE transport is no longer the blocker for the `>=10 Hz` requirement
