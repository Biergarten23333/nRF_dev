# Anchor Build Families And Workflow

## Scope

This document is the operator source of truth for:

- Anchor firmware families (`A..H`) and when to use each
- Ref115/F66F autopositioning workflow dependencies
- Stage-by-stage firmware switching
- Recovery path when wrong anchor family is left flashed

Repository: `BioSpur_UWB_before_start`  
Primary anchor app: [`apps/anchor`](/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/apps/anchor)

---

## Root Cause (Why Ref115 Saw Only Anchor A)

Most likely failure chain:

1. Matrix experiments used `master`, `master-full`, or `matrix` families on some anchors.
2. Those families were not restored to responder runtime family before Ref115 calibration capture.
3. Ref115 capture then saw only anchor(s) still in tag-poll responder mode (often only `A`), while `B..H` mostly timed out.

Direct evidence from recent failed session:

- [`session_sufficiency.json`](/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/logs/tag_sessions/ref115_fresh_20260327_rerun1/session_sufficiency.json)
  - valid anchors: only `A`
  - `near_single_anchor_collapse = true`
- [`raw.log`](/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/logs/tag_sessions/ref115_fresh_20260327_rerun1/raw.log)
  - repeated `Range anchor=0 ...`
  - no effective `B..H` ranges

This is consistent with anchors left on non-responder family for Ref115 capture.

---

## Matrix-Stage Verification (2026-03-27)

Question verified: does matrix stage follow `1 initiator (master/master-full) + N-1 matrix responders`?

### Intended behavior from code

- `APP_ANCHOR_MASTER=1` enters initiator path (`ss_twr_anchor_init_start`) in [`apps/anchor/src/anchor_app.c`](/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/apps/anchor/src/anchor_app.c).
- `APP_ANCHOR_MASTER=0` enters responder path (`ss_twr_resp_start`).
- `APP_ANCHOR_ALLOW_TAG_POLLS=0` causes responder to drop tag-initiated polls in [`src/ss_twr_resp.c`](/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/src/ss_twr_resp.c), which is what matrix workers should do.

### Observed matrix sessions

1. [`fresh_20260326_224949`](/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/logs/anchor_matrix/fresh_20260326_224949)
- logs: `anchor_B_master.log ... anchor_H_master.log` (sequential initiator captures)
- pairs: `27` unique pairs (near full matrix)
- verdict: mostly consistent with sequential `master + matrix` workflow.

2. [`fresh_20260327_001212`](/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/logs/anchor_matrix/fresh_20260327_001212)
- logs: multiple `_master.log` files
- pairs: only `7` unique pairs, all `A-*`
- verdict: only partial matrix capture (usable for A-star, not full matrix).

3. [`fresh_20260327_003324`](/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/logs/anchor_matrix/fresh_20260327_003324)
- files are named `anchor_B.log ...` but content shows `Anchor master timeout ... Matrix row ...` (still master behavior)
- pairs: effectively `A-*` only (single-star collapse)
- verdict: suspicious/incomplete matrix run; family assignment or peer responsiveness was wrong for non-A edges.

4. [`fresh_20260327_131214_matrix_hw_validate`](/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/logs/anchor_matrix/fresh_20260327_131214_matrix_hw_validate)
- this is a fresh hardware validation round with explicit flashing:
  - `B -> master-full`
  - `A,C,D,E,F,G,H -> matrix`
- evidence:
  - `flash_A.log ... flash_H.log`
  - `anchor_B_master_runtime.log` shows `master=1`, `mode=2`, and valid edges for `B-A..H`
  - matrix responders startup logs show `master=0` and `allow_tag_polls=0`
- coverage:
  - `pairs_with_header.csv` has 7 unique pairs (`A-B`, `B-C`, `B-D`, `B-E`, `B-F`, `B-G`, `B-H`)
- verdict:
  - assignment is correct in practice.
  - single initiator round is star-only by design; full matrix requires rotating initiator.

### Practical matrix-stage conclusion

- The repo’s intended matrix logic is correct (`master/master-full` initiator + `matrix` responders).
- Recent runs are **partially correct**:
  - one run close to correct (`fresh_20260326_224949`)
  - later runs degraded to A-star only (not a correct full matrix outcome).
- Therefore current process is not reliably enforcing/validating full matrix coverage after flashing.

---

## Empirical Classification Pass (2026-03-27)

This section is test-first evidence, not naming-only.

Executed evidence sources:

- build artifact inventory (`build-anchor-*` directories + hex presence)
- representative rebuild pass (`cmake --build`) for each major family
- runtime signatures from existing logs
- script dry-run paths (`restore_anchors_runtime_for_ref115.sh --dry-run`)

Representative local build checks (all passed):

- `build-anchor-A-tag`
- `build-anchor-a-safe`
- `build-anchor-a-fast`
- `build-anchor-E-worker`
- `build-anchor-A-matrix`
- `build-anchor-A-master`
- `build-anchor-B-master-full`
- `build-anchor-B` (plain)

## Anchor Family Table (From Actual Build Caches + Runtime Evidence)

All families below build from `apps/anchor` and are controlled by `APP_ANCHOR_*` macros in [`apps/anchor/CMakeLists.txt`](/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/apps/anchor/CMakeLists.txt).

| family | build source / entrypoint | key macros (observed from cache + defaults) | expected runtime role | tested evidence | usable stage | must-not-use stage | operator note |
|---|---|---|---|---|---|---|---|
| `tag` | `build-anchor-<A-H>-tag` + `scripts/flash_anchor_auto.sh` | `MASTER=0`, `ALLOW_TAG_POLLS=1`, `RESP_DELAY_UUS=900`, `SCHEDULE_MODE=1` | tag-poll responder | build OK (`build-anchor-A-tag`), responder signature found in logs: `SS-TWR responder ready ... allow_tag_polls=1` | Ref115 calibration capture, normal runtime responder | matrix master stage | default restore target |
| `safe` | `build-anchor-<a-h>-safe` + restore helper | `MASTER=0`, `ALLOW_TAG_POLLS=1`, `RESP_DELAY_UUS=900` | conservative responder | build OK (`build-anchor-a-safe`); restore dry-run now resolves lowercase dirs | Ref115 capture fallback, runtime conservative mode | matrix master stage | same timing as `tag` in current cache |
| `fast` | `build-anchor-<a-h>-fast` + restore helper | `MASTER=0`, `ALLOW_TAG_POLLS=1`, `RESP_DELAY_UUS=500` | aggressive responder | build OK (`build-anchor-a-fast`); restore dry-run resolves lowercase dirs | runtime throughput test | noisy/unstable environments; matrix master stage | higher risk of delayed-TX misses in poor RF |
| `worker` | `build-anchor-<X>-worker` | `MASTER=0` (+ defaults from `apps/anchor/CMakeLists.txt`: `ALLOW_TAG_POLLS=1`, `RESP_DELAY_UUS=900`) | responder | build OK (`build-anchor-E-worker`), only 1/8 worker build currently present | special responder experiments | matrix master stage | incomplete fleet (not all A..H present) |
| `matrix` | `build-anchor-<A-H>-matrix` | `MASTER=0`, `ALLOW_TAG_POLLS=0` | anchor-only responder (rejects tag polls) | cache proof + responder filter logic in `ss_twr_resp.c` (`if !allow_tag_polls && is_tag => continue`) | matrix stage worker | Ref115 capture; daily runtime | leaving this flashed causes Ref115 timeouts |
| `master` | `build-anchor-<A-H>-master` (sparse in tree) | `MASTER=1`, auto schedule | anchor initiator master | build OK (`build-anchor-A-master`), logs show `Anchor master ...` | matrix master stage | Ref115 capture; runtime responder stage | only 3/8 builds currently present |
| `master-full` | `build-anchor-<A-H>-master-full` | `MASTER=1`, `SCHEDULE_MODE=2`, `ALLOW_TAG_POLLS=0` | full-sweep anchor master | build OK (`build-anchor-B-master-full`), logs show `Anchor master timeout ... sweep ... Matrix row ...` | full matrix diagnostics | Ref115 capture; runtime responder stage | dangerous if forgotten after matrix run |
| plain `build-anchor-<X>` | legacy/manual | mixed, not normalized | ambiguous | build OK (`build-anchor-B`) but macro set inconsistent across roles | manual/legacy only | operator-critical stage | do not use as workflow default |

### Log signatures to identify role quickly

- **Responder family expected**: `SS-TWR responder ready anchor=... allow_tag_polls=1`
- **Wrong for Ref115 capture**:
  - matrix responder: `allow_tag_polls=0`
  - master families: matrix/master logs, anchor-initiator behavior

---

## Stage-By-Stage Firmware Map

## A) Inter-anchor matrix stage

Purpose: collect anchor-anchor pair distances.

- One or more anchors temporarily in `master` / `master-full`
- Remaining anchors in `matrix` workers (`allow_tag_polls=0`)
- Ref115 tag is **not** the measurement driver here

Do not run Ref115 calibration capture before restoring anchors out of matrix/master families.

## B) Ref115 autopositioning capture stage

Required:

- Anchors `A..H` in responder runtime family (`tag` recommended; `safe`/`fast` acceptable if intentional)
- Ref115/115 in calibration profile (OTA-capable ref calibration family)

Ref115 calibration mode requirements are enforced on tag side:

- `APP_TAG_CALIBRATION_MODE=1`
- runtime/master TDMA commands ignored in calibration mode

## C) Post-autopositioning daily runtime stage

- Anchors remain responder family (`tag` or selected `safe/fast`)
- Ref115 should be monitor profile (not calibration)

Never leave these after experiments:

- `master`, `master-full`, `matrix` on production responders

## D) BLE motion / OTA stage (113/127/886 and central)

- Motion BLE/OTA family belongs to motion tags (`113/127/886` etc.)
- Ref115/F66F as static reference should stay in reference family (calibration or monitor mode)

F66F can validate:

- BLE identity reachability
- OTA delivery/reboot chain
- reference-mode output path

F66F cannot by itself prove anchor responder correctness.  
Anchor responder correctness must be validated by:

- anchor family state
- responder signatures/logs
- Ref115 range coverage (`ranges.csv` + sufficiency gate)

---

## Exact Switching Points That Cause Operator Error

Current risk points:

1. Matrix runs flash anchors into `master`/`matrix` families.
2. No mandatory automatic restore to responder family before Ref115 capture.
3. Build dir names like `build-anchor-<X>` are ambiguous and can hide role intent.

Result: easy to start Ref115 capture with wrong anchor family distribution.

---

## Automation Improvements Added

### 1) Persistent anchor flash-state tracking

Updated:

- [`scripts/flash_anchor_auto.sh`](/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/scripts/flash_anchor_auto.sh)

Now records each anchor flash event into:

- `data/anchor_flash_state.json`

Fields include: role, family, snr, hex, build root, timestamp.

### 2) Explicit restore helper before Ref115 capture

Added:

- [`scripts/restore_anchors_runtime_for_ref115.sh`](/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/scripts/restore_anchors_runtime_for_ref115.sh)

Default action:

- flash `A..H` to `build-anchor-<X>-tag`
- now resolves uppercase/lowercase anchor build-dir variants (`build-anchor-A-tag` and `build-anchor-a-safe` style)
- now passes explicit per-anchor SNR to `flash_anchor_auto.sh` in restore flow

### 3) Pre-capture anchor-family sanity gate in Ref115 workflow

Updated:

- [`scripts/recalibrate_anchor_layout_with_ref115.py`](/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/scripts/recalibrate_anchor_layout_with_ref115.py)

Before fresh capture, it now checks `data/anchor_flash_state.json` and blocks capture if anchors are missing or on disallowed families (`master`, `master-full`, `matrix`, unknown), unless explicitly overridden.

### 4) Case-safe flash path parsing

Updated:

- [`scripts/flash_anchor_auto.sh`](/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/scripts/flash_anchor_auto.sh)

Now infers role/family from both uppercase and lowercase build-dir names (e.g. `build-anchor-A-tag` and `build-anchor-a-safe`).

---

## Exact Commands

## Build / flash anchors

Flash one anchor by inferred role:

```bash
scripts/flash_anchor_auto.sh build-anchor-A-tag
```

Restore all anchors for Ref115 capture (recommended):

```bash
scripts/restore_anchors_runtime_for_ref115.sh tag
```

Dry-run:

```bash
scripts/restore_anchors_runtime_for_ref115.sh tag --dry-run
```

## Ref115 capture pipeline (safe path)

```bash
python3 scripts/recalibrate_anchor_layout_with_ref115.py \
  --capture-mode calibration \
  --post-mode none
```

If you intentionally override anchor-family mismatch:

```bash
python3 scripts/recalibrate_anchor_layout_with_ref115.py \
  --capture-mode calibration \
  --post-mode none \
  --allow-anchor-family-mismatch
```

## Expected signatures

- Responder good:
  - `SS-TWR responder ready anchor=... allow_tag_polls=1`
- Bad for Ref115 capture:
  - `allow_tag_polls=0`
  - anchor master/matrix polling behavior

---

## Recovery Path If Wrong Family Was Flashed

1. Restore anchors:

```bash
scripts/restore_anchors_runtime_for_ref115.sh tag
```

2. Re-run Ref115 capture/solve workflow.
3. Verify `session_sufficiency.json` has multi-anchor coverage across both planes before solve acceptance.

---

## Golden Workflow (Operator)

1. **Matrix stage**
   - run matrix masters/workers for pair data
2. **Restore anchors**
   - `scripts/restore_anchors_runtime_for_ref115.sh tag`
3. **Ref115 calibration capture**
   - OTA Ref115 into calibration profile
   - capture session
4. **Solve + acceptance**
   - run recalibration script
   - verify `session_sufficiency.json` and `anchor_layout_acceptance.json`
5. **Ref115 monitor**
   - switch Ref115 to monitor mode/profile after accepted solve
6. **BLE motion runtime**
   - keep motion tags separate from Ref115 reference-role logic

---

## Common Failure Modes

- **Near-single-anchor collapse**
  - cause: anchors not in responder family; or RF visibility collapse
- **Only Anchor A responding**
  - common cause: `B..H` left in matrix/master family; only `A` still tag-poll responder
- **Tag115 on wrong family**
  - motion-family or wrong profile during calibration capture
- **Anchors left in matrix/master family**
  - no restore performed after matrix stage
- **Runtime layout promoted from bad capture**
  - prevented by sufficiency + acceptance gates; do not override without explicit diagnosis

---

## Matrix Rotation Validation Run — 20260327_134244

Session:
- [`logs/anchor_matrix/matrix_rotate_20260327_134244`](/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/logs/anchor_matrix/matrix_rotate_20260327_134244)

Verified in hardware:
- full A→H initiator rotation executed with real flashing
- initiator role: `master-full` (A used `master` because `build-anchor-A-master-full` is absent)
- responders role: `matrix` (`allow_tag_polls=0` observed in startup logs)
- merged result:
  - [`pairs_all.csv`](/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/logs/anchor_matrix/matrix_rotate_20260327_134244/pairs_all.csv)
  - [`pairs_summary.txt`](/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/logs/anchor_matrix/matrix_rotate_20260327_134244/pairs_summary.txt)
  - `unique_pairs=28`, `missing_pairs=0`

Post-run restore:
- all anchors flashed back to `tag`
- direct `allow_tag_polls=1` startup evidence captured for B..H
- A restore startup line was not captured in this run (evidence gap only for direct startup log on A)
