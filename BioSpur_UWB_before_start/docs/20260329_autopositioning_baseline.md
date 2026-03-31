# Current Autopositioning Baseline Registry (Conservative)

Date: 2026-03-29  
Scope: repo-local baseline registry for autopositioning + deployment system status.

## Scoped verification pass (this round, Stage3 frozen)

Pass scope:
- Included: `master`, `matrix`, `responder`, Ref115 `Stage1`, Ref115 `Stage2`
- Excluded/frozen: `Stage3` (`Tag127` / `token206`)

Fresh rerun requirement decision:
- **Real hardware rerun was executed in this pass** for Stage1/Stage2.
- Anchor-role rerun was also attempted with fresh builds, but flash/deploy was blocked by probe routing/SNR mismatch (details recorded in `docs/BlackBox_20260328.md`, section “Real execution pass (2026-03-29 late)”).

### A. Anchor-role status (this pass)

#### `master` (`master` / `master-full`)
- Classification: **A. Currently verified**
- Evidence:
  - master runtime signatures (`Anchor master ...`, `Matrix row ...`) in:
    - `logs/anchor_matrix/matrix_rotate_20260327_134244/anchor_B_master_runtime.log`
    - `logs/anchor_matrix/matrix_rotate_20260327_134244/anchor_C_master_runtime.log`
  - full-rotation matrix completeness (`unique_pairs=28`) in:
    - `logs/anchor_matrix/matrix_full_20260327_145327_with115/pairs_summary.txt`
- Fresh rerun in this pass: **completed (real build + real flash + runtime)**
- Fresh evidence (2026-03-29 night, Phase1):
  - build: `logs/anchor_role_phase1_20260329_224422/build_B_master_full.log`
  - flash: `logs/anchor_role_phase1_20260329_224422/flash_B_master_full_phase1_v3_jlinkonly.log`
  - runtime: `logs/anchor_role_phase1_20260329_224422/runtime_B_master_full_phase1_v3_jlinkonly.log`
  - runtime signature: `Anchor app ready anchor_id=1 master=1`, `Anchor master auto schedule B mode=2`, `Matrix row B: ...`
- Recommended current usage:
  - matrix initiator only; do not leave flashed for Ref115 calibration stage.

#### `matrix`
- Classification: **A. Currently verified**
- Evidence:
  - matrix responder startup with `master=0` and `allow_tag_polls=0` in:
    - `logs/anchor_matrix/matrix_rotate_20260327_134244/anchor_C_startup_in_round_B.log`
    - `logs/anchor_matrix/matrix_rotate_20260327_134244/anchor_D_startup_in_round_B.log`
  - matrix-stage operating flow documented and evidenced in:
    - `docs/anchor_matrix_current_workflow_20260327.md`
- Fresh rerun in this pass: **completed (real build + real flash + runtime)**
- Fresh evidence (2026-03-29 night, Phase1):
  - build: `logs/anchor_role_phase1_20260329_224422/build_C_matrix.log`
  - flash: `logs/anchor_role_phase1_20260329_224422/flash_C_matrix_phase1_v3_jlinkonly.log`
  - runtime: `logs/anchor_role_phase1_20260329_224422/runtime_C_matrix_phase1_v3_jlinkonly.log`
  - runtime signature: `SS-TWR responder ready anchor=2 addr=0xa102 allow_tag_polls=0`
- Recommended current usage:
  - non-initiator role during matrix stage only.

#### `responder` (`tag` anchor baseline)
- Classification: **A. Currently verified**
- Evidence:
  - post-restore startup signatures across A..H (`master=0`, `allow_tag_polls=1`) in:
    - `logs/anchor_restore/reverify_stage2_20260327_1854/startup_A.log` ... `startup_H.log`
  - restore-stage PASS record in:
    - `docs/20260327_autopos.md`
- Fresh rerun in this pass: **completed (real build + real flash + runtime)**
- Fresh evidence (2026-03-29 night, Phase1):
  - build: `logs/anchor_role_phase1_20260329_224422/build_C_tag.log`
  - flash: `logs/anchor_role_phase1_20260329_224422/flash_C_tag_phase1_v2.log`
  - runtime: `logs/anchor_role_phase1_20260329_224422/runtime_C_tag_phase1_v3_jlinkonly.log`
  - runtime signature: `SS-TWR responder ready anchor=2 addr=0xa102 allow_tag_polls=1`
- Recommended current usage:
  - required baseline before Ref115 calibration captures.

### B. Ref115 workflow status (this pass)

#### Stage1 (strict BLE-CM calibration)
- Classification: **A. Currently verified**
- Pass/fail: **PASS**
- Evidence:
  - Fresh run:
    - `logs/master_ble_sessions/hwpass_stage1_ref115_cal_20260329_2140/summary.json`
    - `logs/master_ble_sessions/hwpass_stage1_ref115_cal_20260329_2140/raw.log`
  - key facts: `cm_records=504`, `stop_reason=threshold_met`, all anchors `0..7` observed, per-anchor ok `63`.
  - CM-only evidence in fresh `raw.log` (no `TS`/`TagSummary`).
- Fresh rerun in this pass: **completed**
- Recommended current usage:
  - primary Ref115 calibration baseline path.

#### Stage2 (Ref115 idle guard)
- Classification: **A. Currently verified**
- Pass/fail: **PASS**
- Evidence:
  - Fresh run:
    - `logs/master_ble_sessions/hwpass_stage2_ref115_idle_20260329_2140/summary.json`
    - `logs/master_ble_sessions/hwpass_stage2_ref115_idle_20260329_2140/raw.log`
  - post-reset/post-reconnect analysis artifact:
    - `logs/hw_verify_anchor_ref115_20260329_213439/stage2_post_reset_counts.json`
  - key facts from fresh pass:
    - `cm_post_reset=1` (single reset-boundary straddle line),
    - `cm_post_reconn=0`,
    - `ts_post_reset=0`,
    - `ts_post_reconn=0`.
- Fresh rerun in this pass: **completed**
- Recommended current usage:
  - mandatory guard immediately after Stage1 mode switch.

### C. Frozen item (this pass)

#### Stage3 (Tag127 rotation calibration)
- Status in this pass: **FROZEN / intentionally not executed**
- Reason:
  - Tag127/token206 unavailability (power-off condition) in current checkpoint.
- Interpretation:
  - not a functional fail verdict for Stage3 implementation in this pass.
- Resume policy:
  - resume Stage3 later from the current Stage1/Stage2 baseline without reclassifying Stage3 from frozen until token206 is available.

## 1. Executive summary

Current verified baseline is narrow and BLE-CM-centered:
- Ref115 Stage1 calibration over BLE CM is verified at strict threshold (`cm_records=504`, per-anchor ok >= 59).
- Ref115 Stage2 idle guard is verified post-reset (`CM=0`, `TS=0` after reset/reconnect window).
- Stage3 Tag127 rotation calibration is currently suspended due token206 power/unavailability, so it is not part of the current verified baseline.

This registry is intentionally conservative: items are marked verified only when backed by concrete recent evidence.

## 2. Current verified baseline (Class A: currently verified)

### A1) Ref115 static calibration over BLE CM (strict policy pass)
- Role / purpose: static reference calibration capture for autopositioning input quality.
- Entry points:
  - `scripts/build_ref115_calibration_ota_profile.sh`
  - `scripts/capture_master_ble_session.py` (threshold-aware capture/summary path)
- Verification class: **A. Currently verified**
- Evidence:
  - `docs/BlackBox_20260328.md` Iteration 3 section (`2026-03-29`):
    - Stage1 PASS with strict policy (`cm_records=504`, `stop_reason=threshold_met`, all anchors `0..7` seen, per-anchor ok >= 50).
  - Session:
    - `logs/master_ble_sessions/blecm_iter3_stage1_ref115_cal_20260329_1912/summary.json`
    - `logs/master_ble_sessions/blecm_iter3_stage1_ref115_cal_20260329_1912/raw.log`
- Recommended current use: use as the baseline capture path for Ref115 calibration.

### A2) Ref115 idle guard (UWB-off behavior check post-reset)
- Role / purpose: ensure Ref115 exits active calibration output after idle profile switch.
- Entry point:
  - `scripts/build_ref115_idle_ota_profile.sh`
- Verification class: **A. Currently verified**
- Evidence:
  - `docs/BlackBox_20260328.md` Iteration 3 Stage2 PASS:
    - `cm_post_reset=0`, `cm_post_reconn=0`, `ts_post_reset=0`, `ts_post_reconn=0`
  - Session:
    - `logs/master_ble_sessions/blecm_iter3_stage2_ref115_idle_20260329_1912/summary.json`
- Recommended current use: required guard immediately after Stage1 calibration switch-out.

### A3) Calibration transport contract in active baseline
- Role / purpose: mode isolation for calibration.
- Contract: calibration mode is **CM-only**, no `TS` / `TagSummary`.
- Verification class: **A. Currently verified**
- Evidence:
  - `docs/BlackBox_20260328.md` Iteration 3 Stage1 CM-only check (explicit no `TS;`/`TagSummary` in raw log).
- Recommended current use: treat `TS` observed in calibration mode as a bug.

## 3. Historically validated / conditionally usable (Class B)

### B1) Ref115 monitor path (fixed-subset static monitor)
- Role / purpose: static monitor mode (not calibration acquisition).
- Evidence:
  - `docs/session_summary_20260322.md` monitor split + fixed-subset optimization records.
- Why not Class A now:
  - no fresh 2026-03-29 revalidation in current checkpoint loop.

### B2) Pure USB serial tag path (`apps/tag_usb`)
- Role / purpose: serial-only UWB tag workflow.
- Evidence:
  - `docs/session_summary_20260322.md` section “Pure USB Serial Tag Variant”.
  - `docs/device_workflow.md` build note references `scripts/build_tag_usb.sh`.
- Why not Class A now:
  - current operating direction is BLE-centered; no recent strict revalidation in current checkpoint.

### B3) Normal BLE receiver + OTA master split
- Role / purpose:
  - normal BLE receiver for telemetry
  - OTA master for update/reset operations
- Evidence:
  - `docs/session_summary_20260322.md` BLE/OTA workflow section with proof logs.
- Why not Class A now:
  - evidence is historical (2026-03-22 era), not re-proven in latest strict checkpoint loop.

### B4) BLE motion runtime family for 113/127
- Role / purpose: compact TS-based motion telemetry with OTA capability.
- Evidence:
  - `docs/session_summary_20260322.md` multi-tag sections (`113` + `127`), including `build-master-multi-tagrot` proof sessions.
- Why not Class A now:
  - current loop has Stage3 suspension due token206 unavailability; no current-rerun confirmation.

### B5) `build-master-multi-tagrot`
- Role / purpose: multi-tag BLE central runtime.
- Evidence:
  - `docs/session_summary_20260322.md` references rebuilt/flashed `build-master-multi-tagrot/merged.hex` and dual-tag proof logs.
- Why not Class A now:
  - no recent checkpoint verification under current constraints.

### B6) Anchor role families: `master`, `matrix`, `responder(tag)`
- Role / purpose:
  - `master/master-full`: matrix initiator role
  - `matrix`: matrix responder role during matrix capture
  - `tag` (anchor-side responder baseline): post-matrix runtime responder before Ref115 calibration
- Evidence:
  - `docs/20260327_autopos.md`:
    - `PASS — Matrix Stage` (`master/master-full + matrix` with runtime logs)
    - `PASS — Anchor Restore / Responder Stage` (`master=0`, `allow_tag_polls=1` evidence)
  - `docs/anchor_matrix_current_workflow_20260327.md`:
    - full matrix run flow + final restore flow
    - references `matrix_full_20260327_145327_with115/pairs_summary.txt` (`unique_pairs=28`)
  - `docs/BlackBox_20260327.md`:
    - full rotation run evidence + runtime signatures and restore evidence
- Why not Class A now:
  - not revalidated in latest 2026-03-29 Ref115 BLE-CM checkpoint loop.

## 4. Present in repo but not yet safe to treat as verified (Class C)

### C1) Unified `master_control` path
- Intended role: unified nRF52840 control image (`receiver` + button switch to OTA mode).
- Present evidence:
  - referenced in `docs/device_workflow.md`
  - build helper exists: `scripts/build_master_control.sh`
- Verification class: **C. Present but not verified**
- Reason:
  - current checkpoint docs do not include recent runtime proof sessions for this path.

### C2) Unified “single-command current checkpoint” anchor-role revalidation
- Intended role:
  - fresh same-day revalidation of `master/matrix/responder` in the current BLE-CM checkpoint loop.
- Verification class: **C. Present but not verified**
- Reason:
  - this specific same-checkpoint rerun was not performed in 2026-03-29 BLE-CM iterations; anchor-role status is therefore carried as historical/conditional (Class B6), not current-loop verified (Class A).

## 5. Autopositioning operating split (recommended from current evidence)

### Static reference / autopositioning
- Use Ref115 BLE-CM calibration path (Class A).
- Apply strict capture sufficiency policy in Stage1.

### Static monitor
- Ref115 monitor path remains available (Class B), but currently not re-certified in the latest strict loop.

### BLE motion runtime
- 113/127 TS-oriented motion runtime is historically validated (Class B).
- Current Stage3 status is suspended due token206 unavailability (not a functional fail verdict).

### BLE receiver / OTA master / unified control
- Receiver/OTA split: historical evidence exists (Class B).
- Unified `master_control`: present in repo/docs but currently unverified in latest checkpoint (Class C).

### Anchor-side roles (`master`, `matrix`, `responder`)
- Treat as historically validated and conditionally reusable (Class B6).
- Important implementation note (not proof): per-anchor role images using `APP_ANCHOR_*` should be built with `--no-sysbuild` to avoid wrong inner-role boot.

## 6. Build / workflow quick lookup table

| Build / script / build-dir | Role | Output mode | Class | Notes / caveats |
|---|---|---|---|---|
| `scripts/build_ref115_calibration_ota_profile.sh` + Stage1 capture | Ref115 calibration | BLE CM | **A** | Verified strict pass in `blecm_iter3_stage1_ref115_cal_20260329_1912` |
| `scripts/build_ref115_idle_ota_profile.sh` + Stage2 capture | Ref115 idle guard | quiet post-reset | **A** | Verified post-reset no CM/TS in `blecm_iter3_stage2_ref115_idle_20260329_1912` |
| `scripts/recalibrate_anchor_layout_with_ref115.py` | host solve/promote pipeline | host-side solve | **B** | historically central, but solve/deploy was not the target in latest strict loop |
| `build-ref115-monitor-4` (from docs) | Ref115 static monitor | fixed-subset monitor | **B** | historical validation in session summary |
| `scripts/build_tag_usb.sh` / `apps/tag_usb` | pure USB serial tag variant | UART/USB | **B** | historical and still present; not in current BLE-only active flow |
| `build-master-ble` (historical) | normal BLE receiver | TS receive | **B** | evidenced in 2026-03-22 summary/proof logs |
| `build-master-ota` (historical) | OTA master | OTA upload/reset | **B** | evidenced historically |
| `build-master-multi-tagrot` | multi-tag BLE central | TS multi-link | **B** | historical proof exists; not current-checkpoint revalidated |
| `scripts/build_master_control.sh` / `apps/master_control` | unified master control | mode-switched control | **C** | present in repo/docs; no current checkpoint runtime proof |
| `build-anchor-<X>-master` / `build-anchor-<X>-master-full` | matrix initiator | matrix stage | **B** | historically validated in 2026-03-27 matrix rotation/full sessions; not re-run in latest BLE-CM checkpoint loop |
| `build-anchor-<X>-matrix` | matrix responder | matrix stage | **B** | historically validated with runtime `allow_tag_polls=0` signatures; not re-run in latest BLE-CM checkpoint loop |
| `build-anchor-<X>-tag` | responder baseline | post-matrix / pre-Ref115 calibration | **B** | historically validated with `master=0` + `allow_tag_polls=1` restore signatures; not re-run in latest BLE-CM checkpoint loop |
| `scripts/flash_anchor_auto.sh` + anchor role build dirs | anchor role flashing helper | matrix/restore orchestration | **B** | historically exercised; still requires `--no-sysbuild` build discipline for role-correct images |

## 7. Explicit latest checkpoint state

- Stage1 Ref115 strict calibration over BLE CM: **PASS** (Class A)
- Stage2 Ref115 idle guard: **PASS** (Class A)
- Stage3 Tag127 rotation calibration: **SUSPENDED** (token206 unavailable), therefore not counted as current baseline verification.

## 8. Conservative ambiguity notes

1. `docs/device_workflow.md` mixes multiple eras (BLE-first and USB-direct examples). It is useful as capability inventory, not as sole proof of current operable baseline.
2. `docs/session_summary_20260322.md` is strong historical evidence but not equal to current checkpoint verification.
3. `build-master-multi-tagrot` is treated as historical/conditional until revalidated under current power/device availability.
4. Anchor role families are upgraded from Class C to Class B due strong 2026-03-27 runtime evidence, but still not Class A without fresh same-checkpoint rerun.
