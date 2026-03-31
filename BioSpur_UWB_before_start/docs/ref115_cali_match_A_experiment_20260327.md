# Ref115 Calibration Coverage vs Anchor Build Match-A Experiment (2026-03-27)

## Hypothesis
- Anchor A is on the correct responder family/build.
- B–H may be on a different or stale family.
- If B–H are reflashed to match A, Ref115 calibration output should recover multi-anchor coverage.

## Phase 1 — What build/family A is actually running (evidence-first)

### Identified build/family for A
- **A is on `build-anchor-A-tag` (tag responder family).**

### Proof
1. Most recent A flash record:
   - `logs/anchor_matrix/matrix_full_20260327_145327_with115/flash_restore_A.log`
   - Contains:
     - `J-Link>LoadFile build-anchor-A-tag/zephyr/zephyr.hex`
2. Current runtime startup signature for A (fresh reset-read in this experiment):
   - `logs/anchor_restore/match_A_20260327_180031/startup_A_preopen.log`
   - Contains:
     - `Anchor app ready anchor_id=0 master=0`
     - `SS-TWR responder ready anchor=0 addr=0xa100 allow_tag_polls=1`

### Is A same family as B–H before reflashing?
- Last-flash evidence shows **SAME AS B–H** (all were restored to `build-anchor-<X>-tag` in `matrix_full_20260327_145327_with115`).
- Explicit latest flash records:
  - `.../flash_restore_B.log -> build-anchor-B-tag`
  - `.../flash_restore_C.log -> build-anchor-C-tag`
  - `.../flash_restore_D.log -> build-anchor-D-tag`
  - `.../flash_restore_E.log -> build-anchor-E-tag`
  - `.../flash_restore_F.log -> build-anchor-F-tag`
  - `.../flash_restore_G.log -> build-anchor-G-tag`
  - `.../flash_restore_H.log -> build-anchor-H-tag`

## Phase 2/3 — Map and reflash B–H to match A

## Effective family cloned from A
- `tag` family
- B–H reflashed to:
  - `build-anchor-B-tag`
  - `build-anchor-C-tag`
  - `build-anchor-D-tag`
  - `build-anchor-E-tag`
  - `build-anchor-F-tag`
  - `build-anchor-G-tag`
  - `build-anchor-H-tag`

## Flash session
- `logs/anchor_restore/match_A_20260327_180031`

## Per-anchor flash logs
- `logs/anchor_restore/match_A_20260327_180031/flash_B.log`
- `logs/anchor_restore/match_A_20260327_180031/flash_C.log`
- `logs/anchor_restore/match_A_20260327_180031/flash_D.log`
- `logs/anchor_restore/match_A_20260327_180031/flash_E.log`
- `logs/anchor_restore/match_A_20260327_180031/flash_F.log`
- `logs/anchor_restore/match_A_20260327_180031/flash_G.log`
- `logs/anchor_restore/match_A_20260327_180031/flash_H.log`

## Post-flash startup/runtime verification (required)
- B:
  - `logs/anchor_restore/match_A_20260327_180031/startup_B_preopen.log`
  - `Anchor app ready anchor_id=1 master=0`
  - `SS-TWR responder ready anchor=1 addr=0xa101 allow_tag_polls=1`
- E:
  - `logs/anchor_restore/match_A_20260327_180031/startup_E_preopen.log`
  - `Anchor app ready anchor_id=4 master=0`
  - `SS-TWR responder ready anchor=4 addr=0xa104 allow_tag_polls=1`

## Phase 4 — Fresh Ref115 calibration-output observation

- Fresh observation session:
  - `logs/tag_sessions/ref115_cali_match_A_test_20260327_180532`
- Raw log:
  - `logs/tag_sessions/ref115_cali_match_A_test_20260327_180532/raw.log`

## Phase 5 — Coverage comparison (old fail vs new match-A test)

### Old failing baseline
- `logs/tag_sessions/ref115_cali_output_test_20260327_174346/raw.log`
- Valid range anchors: `{0}`
- Range counts:
  - anchor 0: 740
- Timeout counts:
  - anchors 1..7: ~739–740 each

### New test after B–H reflashed to match A
- `logs/tag_sessions/ref115_cali_match_A_test_20260327_180532/raw.log`
- Valid range anchors: `{0}`
- Range counts:
  - anchor 0: 735
- Timeout counts:
  - anchors 1..7: ~738–739 each

### Per-anchor coverage verdict
- Multi-anchor coverage **NOT** restored.
- Behavior remains single-anchor (A-only valid).

## Final verdict
- **Hypothesis rejected (for this cycle).**
- Matching B–H to A’s effective `tag` responder family did **not** recover multi-anchor Ref115 calibration coverage.
- Calibration output remains **INVALID** (single-anchor collapse).

## Next most likely root cause
- Not a build-family mismatch between A and B–H.
- Most likely a **physical/link-layer reachability issue** for B–H from Ref115 path (RF/channel/placement/antenna/power path), while A remains reachable.

## Additional hardening pass (same day, after first match-A run)

To remove the "partial flash / stale state" possibility, B–H were reflashed again with explicit erase+flash (SN-pinned, non-interactive JLink path).

### Erase+flash logs
- `logs/anchor_clone_A_20260327_181548/flash_B_erase.log`
- `logs/anchor_clone_A_20260327_181548/flash_C_erase.log`
- `logs/anchor_clone_A_20260327_181548/flash_D_erase.log`
- `logs/anchor_clone_A_20260327_181548/flash_E_erase.log`
- `logs/anchor_clone_A_20260327_181548/flash_F_erase.log`
- `logs/anchor_clone_A_20260327_181548/flash_G_erase.log`
- `logs/anchor_clone_A_20260327_181548/flash_H_erase.log`

All erase logs contain `Erasing done.` and load `build-anchor-<X>-tag/zephyr/zephyr.hex`.

### Post-erase startup signature check (B–H)
From:
- `logs/anchor_clone_A_20260327_181548/startup_post_B.log` ... `startup_post_H.log`

Observed for each B..H:
- `master=0`
- `SS-TWR responder ready ... allow_tag_polls=1`
- expected anchor IDs and addresses (`0xa101` ... `0xa107`)

### Live co-observation (Ref115 + two responders)
Files:
- `logs/anchor_clone_A_20260327_181548/live_ref115_20s.log`
- `logs/anchor_clone_A_20260327_181548/live_B_20s.log`
- `logs/anchor_clone_A_20260327_181548/live_E_20s.log`

Result:
- Ref115 stream remained A-valid + B..H-timeout pattern.
- B/E serial captures did not show positive responder activity lines during this window.

### Updated conclusion after hardening
- Even after erase+flash + startup-signature alignment, Ref115 calibration still collapses to A-only.
- This further weakens "wrong build family on B..H" as root cause.
