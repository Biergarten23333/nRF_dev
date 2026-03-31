# 20260330 AutoPos Mode Switch Guide

Date: 2026-03-30  
Scope: Current autopositioning workflow using unified anchor firmware + mode switching (no new firmware flash required during runtime rounds).

## 1) Executive Summary

Current validated path for AutoPos:
- Unified anchor firmware is already deployed.
- Role switching is done by config update (preferred stable path) instead of reflashing role-specific firmware each round.
- Full A->H initiator rotation matrix run was completed with:
  - `unique_pairs=28`
  - `missing_pairs=0`
- After matrix stage, all anchors were restored to responder (`allow_tag_polls=1`).

Primary evidence session:
- [real_positioning_rotate_20260330_161846](/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/logs/real_positioning_rotate_20260330_161846)

## 2) Required Build / Artifact

Unified anchor build used:
- [build-anchor-unified-bscode-20260330](/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/build-anchor-unified-bscode-20260330)

Build command:
```bash
./scripts/build_anchor_unified.sh build-anchor-unified-bscode-20260330 2
```

Notes:
- This build includes unified role runtime (`master|matrix|responder`) and BS short code output (`BSxxxx`).
- Role is selected from config (`anchor_config_t`), not by separate role firmware images.

## 3) Two Role-Switch Methods

## 3.1 Stable method (recommended): provisioning write via J-Link

Script:
- [provision_anchor.py](/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/scripts/provision_anchor.py)

Example:
```bash
python3 scripts/provision_anchor.py --probe-serial 760185876 --anchor-id B --role master --verify
```

Use this method for:
- all production matrix rounds
- deterministic A->H role orchestration
- recovery from noisy serial environments

## 3.2 Serial command method (available, bench-sensitive)

Script:
- [serial_switch_role.py](/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/scripts/serial_switch_role.py)

Recommended command (boot-window mode):
```bash
python3 scripts/serial_switch_role.py \
  --port /dev/serial/by-id/usb-SEGGER_J-Link_000760185876-if00 \
  --role responder --anchor-id D --save --reboot --boot-window-reboot --timeout 6
```

Firmware command mapping used in boot-window mode:
- `M` -> role master
- `X` -> role matrix
- `P` -> role responder
- `ID <A..H>` -> anchor id
- `S` -> config save
- `RB` -> reboot

Use serial method for:
- quick manual switching / debugging
- not as sole orchestration path when console is very noisy

## 4) AutoPos Matrix Round Procedure (A->H rotation)

Per round `master = A..H`:
1. Set master anchor role=`master`.
2. Set all other anchors role=`matrix`.
3. Reboot and capture master runtime:
   - `Matrix <Master>-<Peer> ... filt=... mm`
4. Extract rows to `pairs_master_<Master>.csv`.

Merged outputs:
- [pairs_all.csv](/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/logs/real_positioning_rotate_20260330_161846/matrix/pairs_all.csv)
- [pairs_summary.txt](/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/logs/real_positioning_rotate_20260330_161846/matrix/pairs_summary.txt)
- [inter_anchor_matrix_ah_realpos.json](/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/logs/real_positioning_rotate_20260330_161846/matrix/inter_anchor_matrix_ah_realpos.json)

Result from 2026-03-30 run:
- `total_rows=171`
- `unique_pairs=28`
- `missing_pairs=0`

## 5) Restore Procedure After Matrix

Mandatory restore:
- Set all A..H to `responder`.
- Verify startup includes:
  - `ROLE: responder`
  - `allow_tag_polls=1`

Evidence:
- [startup_restore_A.log](/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/logs/real_positioning_rotate_20260330_161846/startup_restore_A.log)
- [startup_restore_B.log](/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/logs/real_positioning_rotate_20260330_161846/startup_restore_B.log)
- [startup_restore_C.log](/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/logs/real_positioning_rotate_20260330_161846/startup_restore_C.log)
- [startup_restore_D.log](/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/logs/real_positioning_rotate_20260330_161846/startup_restore_D.log)
- [startup_restore_E.log](/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/logs/real_positioning_rotate_20260330_161846/startup_restore_E.log)
- [startup_restore_F.log](/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/logs/real_positioning_rotate_20260330_161846/startup_restore_F.log)
- [startup_restore_G.log](/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/logs/real_positioning_rotate_20260330_161846/startup_restore_G.log)
- [startup_restore_H.log](/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/logs/real_positioning_rotate_20260330_161846/startup_restore_H.log)

## 6) Identity Output (for operations)

Startup signature now includes:
- `ANCHOR_ID`
- `ROLE`
- `BS_CODE` (human readable, `BSxxxx`)
- `DEVICE_UUID` (full stable identifier)

Example:
```text
ANCHOR: unified; ANCHOR_ID: B; ROLE: master; BS_CODE: BS592A; DEVICE_UUID: B917...6223; MCU_UID: ...
```

## 6.1 A-H Identity Projection (BS code <-> long UUID)

Source:
- [whole_cycle_summary.json](/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/logs/phase21_whole_cycle_20260330_160828/whole_cycle_summary.json)

| Anchor | BS short code | Long stable ID (`DEVICE_UUID`) |
|---|---|---|
| A | `BS6B72` | `4DC6B8187E33803AE8601FB0D7992B96` |
| B | `BS592A` | `B9179575C776C98F1CB132DD6EDC6223` |
| C | `BS5380` | `CEE5A7EFCB35F8A56B430047629F5309` |
| D | `BS441A` | `AB14CCA262A092E70EB26B0ACB0A394B` |
| E | `BS4B52` | `A892AF05DD59CF0D0D3408AD74F364A1` |
| F | `BS928B` | `840C68591E90019821AACFF1B73AAA34` |
| G | `BSEC88` | `B3087BC3D87CCCD316AEDC6B71D6677F` |
| H | `BS780E` | `1EABFBEC28B8053FBB0D5C448112AE93` |

Notes:
- `BSxxxx` is human-readable short identity.
- `DEVICE_UUID` is the full stable identifier and should be treated as source-of-truth key.

## 7) Minimal Command Cookbook

Flash unified build once (if needed):
```bash
scripts/flash_anchor_auto.sh build-anchor-unified-bscode-20260330 760185876
```

Set one anchor to master (stable path):
```bash
python3 scripts/provision_anchor.py --probe-serial 760185876 --anchor-id B --role master --verify
```

Set one anchor to matrix:
```bash
python3 scripts/provision_anchor.py --probe-serial 760185878 --anchor-id C --role matrix --verify
```

Restore one anchor to responder:
```bash
python3 scripts/provision_anchor.py --probe-serial 760186081 --anchor-id D --role responder --verify
```

Serial switch (boot window):
```bash
python3 scripts/serial_switch_role.py --port /dev/serial/by-id/usb-SEGGER_J-Link_000760185876-if00 --role matrix --anchor-id B --save --reboot --boot-window-reboot --timeout 6
```

## 8) Current Recommendation

For next AutoPos round:
- Keep unified firmware as-is.
- Do not reflash for each role change.
- Use provisioning role switching for A->H orchestration.
- Use serial switching only as supplementary operator path.

## 9) AutoPos V2 Preparation

V2 preparation is now in place for tomorrow's live test. The intent is to move from simple bidirectional fusion to a bias-aware bidirectional matrix workflow that can later incorporate Ref115 / Tag127 feedback.

Preparation artifacts:
- [logs/autopos_v2_prep_20260330_smoke](/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/logs/autopos_v2_prep_20260330_smoke)
- [final_pair_distances_v2.csv](/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/logs/autopos_v2_prep_20260330_smoke/v2_fused/final_pair_distances_v2.csv)
- [pair_decision_report_v2.json](/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/logs/autopos_v2_prep_20260330_smoke/v2_fused/pair_decision_report_v2.json)
- [inter_anchor_matrix_v2fused.json](/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/logs/autopos_v2_prep_20260330_smoke/v2_fused/inter_anchor_matrix_v2fused.json)
- [anchor_layout_v2_iterative.json](/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/logs/autopos_v2_prep_20260330_smoke/v2_fused/anchor_layout_v2_iterative.json)
- [ref115_feedback_weights.json](/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/logs/autopos_v2_prep_20260330_smoke/v2_fused/ref115_feedback_weights.json)
- [v2_prep_manifest.json](/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/logs/autopos_v2_prep_20260330_smoke/v2_prep_manifest.json)

New prep scripts:
- [scripts/fuse_bidirectional_matrix_v2.py](/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/scripts/fuse_bidirectional_matrix_v2.py)
- [scripts/prepare_autopos_v2.py](/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/scripts/prepare_autopos_v2.py)

V2 pair-decision behavior:
- Compute directional `n / mean / pstdev / ci95` for `i->j` and `j->i`.
- Run Z-bias test.
- If bias is not significant: use inverse-variance weighted combine (`COMBINED_IVW`).
- If bias is significant: keep the better direction and mark solver recommendation as bias-aware / best-direction preserve.

Current preparation result on existing `s50` data:
- `28` pairs available.
- `3` pairs are safe for `COMBINED_IVW`.
- `25` pairs show significant directional bias and should not be naively averaged.

Implication for tomorrow:
- Run fresh bidirectional `sweep=50` capture.
- Feed `pairs_all.csv` into `prepare_autopos_v2.py`.
- Use the generated `pair_decision_report_v2.json` to decide whether the next solver pass should remain mostly single-direction-per-pair or move toward stronger bias-parameter solving.
- Compare updated layout against Ref115 static CM baseline using the prepared feedback weights.

## 9) Anchor Test Safety Rule (mandatory)

When running anchor test workflows (matrix/autopos/loop-link):
- Use **anchor probes only** (`7xxxxxx`).
- Never use nRF52840 probe path (`683234364`) in anchor test commands.
- Any probe serial starting with `6` is forbidden for anchor tests.

Reason:
- Prevent wrong-probe bind and SEGGER popup window during anchor test execution.

Operational note:
- Even if external tools (e.g. VSCode extension hotplug scanner) attempt probe discovery,
  the anchor test scripts now hard-fail before executing any forbidden 6xxxx probe command.

## 10) AutoPos V1 (Bidirectional Fusion Strategy)

### Strategy
For each pair `(i,j)`:
1. collect both directions: `i->j` and `j->i`
2. compute direction stats: `mean`, `pstdev`, `n`, `var(mean)=s^2/n`
3. bias test:
   - `Z = (mean_ij - mean_ji) / sqrt(v_ij + v_ji)`
4. decision:
   - if `|Z| > 2.0`: treat as significant directional bias, choose lower-variance direction
   - else: combine by inverse-variance weighted mean (IVW)

Output artifacts:
- `final_pair_distances.csv` (fused final distances + decision per pair)
- `pair_decision_report.json` (audit details)
- `anchor_coords_v1.json` (relative coordinates solved from fused distances)

### Current implementation
- Script:
  - [fuse_bidirectional_matrix_v1.py](/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/scripts/fuse_bidirectional_matrix_v1.py)

### Latest executed V1 test result
- Input matrix sample set:
  - [pairs_all.csv](/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/logs/real_positioning_rotate_20260330_161846/matrix/pairs_all.csv)
- V1 output:
  - [final_pair_distances.csv](/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/logs/autopos_v1_bidirectional_20260330_195217/final_pair_distances.csv)
  - [pair_decision_report.json](/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/logs/autopos_v1_bidirectional_20260330_195217/pair_decision_report.json)
  - [anchor_coords_v1.json](/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/logs/autopos_v1_bidirectional_20260330_195217/anchor_coords_v1.json)

Decision summary (this run):
- `COMBINED_IVW`: 1 pair
- `BIAS:USE_A_TO_B`: 15 pairs
- `BIAS:USE_B_TO_A`: 8 pairs
- `SINGLE_A_TO_B`: 3 pairs
- `SINGLE_B_TO_A`: 1 pair

Interpretation:
- Directional bias is dominant in current environment; most pairs are not safe to blindly average.
- V1 already prevents biased merge by pair-level decisioning before solving coordinates.

## 11) Final Production Solve Chain (Recommended)

Use this chain for final physical layout output:

1. **Collect matrix raw pairs (A->H master rotation)**
   - input artifact: `matrix/pairs_all.csv`

2. **Run bidirectional fusion (V1)**
   - script:
     - [fuse_bidirectional_matrix_v1.py](/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/scripts/fuse_bidirectional_matrix_v1.py)
   - output:
     - `v1_fused/final_pair_distances.csv`
     - `v1_fused/pair_decision_report.json`
     - `v1_fused/anchor_coords_v1.json` (**relative MDS initial embedding only**)

3. **Convert fused pair table to solver input matrix JSON**
   - output:
     - `v1_fused/inter_anchor_matrix_v1fused.json`

4. **Run constrained iterative layout solver**
   - script:
     - [solve_anchor_layout_iterative.py](/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/scripts/solve_anchor_layout_iterative.py)
   - internally calls:
     - [solve_anchor_layout.py](/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/scripts/solve_anchor_layout.py)
   - output:
     - `v1_fused/anchor_layout_constrained_iterative.json` (**final layout to use**)
     - `v1_fused/anchor_layout_constrained_iterative_iter_history.json`

### Important
- Do **not** use `anchor_coords_v1.json` as final physical layout.
- `anchor_coords_v1.json` is unconstrained MDS (good initial geometry, not final physical-constrained solution).
- Final operational layout file is:
  - `anchor_layout_constrained_iterative.json`

### Latest full-chain run (2026-03-30)

- Session:
  - [autopos_bidirectional_s50_20260330_203907](/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/logs/autopos_bidirectional_s50_20260330_203907)
- Final layout:
  - [anchor_layout_constrained_iterative.json](/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/logs/autopos_bidirectional_s50_20260330_203907/v1_fused/anchor_layout_constrained_iterative.json)
