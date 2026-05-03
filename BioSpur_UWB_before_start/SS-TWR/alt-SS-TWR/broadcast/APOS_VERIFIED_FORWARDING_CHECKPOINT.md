## APOS Verified Forwarding Checkpoint - 2026-05-03

### Problem

Raw APOS forwarding through Master_Tag was not trustworthy enough for anchor layout deployment.
The previous path could print an `apos rc` line, but a bad or stale Master target state could still
mean the command never reached the intended Tag. This caused a real risk of pushing an incorrect or
partial layout while believing the layout was applied.

Observed failure mode:

```text
BLE cmd skip[...] target mismatch kind=tag target_name=- target_prefix=BS ...
BLE cmd not sent ...
apos rc=-128 payload=APOS ...
```

The critical requirement is now the same style as OTA version verification:

```text
layout_match=True
```

No APOS deployment should be considered successful unless the target Tag reads back the exact
expected coordinates from `APOS_STATUS`.

### Fix

Added an atomic Master_Tag forwarding command:

```text
APOS_TO <BSxxxx> APOS ...
```

Implementation:

```text
apps/master_control/src/main.c
```

Behavior:

1. Parse target Tag name from the same UART line.
2. Set runtime target kind to Tag.
3. Set runtime target name to the requested `BSxxxx`.
4. Clear prefix/uuid filters to avoid stale mismatch state.
5. Immediately forward the APOS payload with `master_send_command_now()`.
6. Print:

```text
apos_to rc=<n> target=<BSxxxx> payload=<APOS...>
```

Updated verified host script:

```text
scripts/push_apos_layout_verified.py
```

Verification behavior:

1. Sends each row using `APOS_TO <target> APOS <id> <x> <y> <z>`.
2. Requires target-specific `APOS_OK`.
3. Sends `APOS_TO <target> APOS_COMMIT`.
4. Requires target-specific `APOS_COMMIT_OK`.
5. Sends `APOS_TO <target> APOS_STATUS`.
6. Parses all 8 returned coordinates.
7. Compares exact expected vs actual.
8. Prints per-target and all-target verdicts.

Generic `NUS notify` / `BLE[...]` lines are no longer accepted as proof. The script only accepts
lines prefixed by the requested Tag name, for example:

```text
BSF66F notify: APOS_OK ...
```

### Deployment

Master_Tag B120 was rebuilt and flashed with explicit SNR:

```text
SNR: 1050070698
Build dir: build-master-control-b120-m1-master-tag-lfrc-alt-bcast-b61-apos-verify-carrier
Image: build-master-control-b120-m1-master-tag-lfrc-alt-bcast-b61-apos-verify-carrier/zephyr/merged_domains.hex
LFRC assert: passed
Flash tool: scripts/flash_master_control_b120_m1_noninteractive.sh
```

No Tag OTA was performed.
No Anchor OTA was performed.

### Verified Result

Command run:

```bash
python3 scripts/push_apos_layout_verified.py \
  --port /dev/serial/by-id/usb-Master_Tag_BioSpur_BLE_Control_6918E0384172A49F-if00 \
  --targets BSF66F,BS2DCE,BSDC91 \
  --use-default-layout \
  --out-dir logs/apos_verified_b61_all3_apos_to_20260503_004436
```

Final result:

```text
APOS_VERIFY target=BSF66F rows_ok=1 commit_ok=1 layout_match=True source=SETTINGS
APOS_VERIFY target=BS2DCE rows_ok=1 commit_ok=1 layout_match=True source=SETTINGS
APOS_VERIFY target=BSDC91 rows_ok=1 commit_ok=1 layout_match=True source=SETTINGS
APOS_VERIFY_ALL layout_match=True
```

Summary JSON:

```text
logs/apos_verified_b61_all3_apos_to_20260503_004436/summary.json
```

### Verified Layout

The layout read back from all three Tags was:

```text
APOS 0 0 0 0
APOS 1 4738 0 0
APOS 2 3986 3719 34
APOS 3 -455 2738 0
APOS 4 66 -44 1735
APOS 5 4411 71 1552
APOS 6 3851 3760 1640
APOS 7 -553 2722 1561
```

`APOS_STATUS_DONE SRC=SETTINGS` was observed, so the coordinates are persisted in Tag settings/NVS.

### Rule Going Forward

Do not use naked APOS forwarding as a success criterion.

Valid APOS deployment requires:

```text
rows_ok=1
commit_ok=1
layout_match=True
source=SETTINGS
```

Use:

```bash
python3 scripts/push_apos_layout_verified.py \
  --port /dev/serial/by-id/usb-Master_Tag_BioSpur_BLE_Control_6918E0384172A49F-if00 \
  --targets BSF66F,BS2DCE,BSDC91 \
  --layout-file <layout.json-or-APOS.txt> \
  --out-dir logs/apos_verified_$(date +%Y%m%d_%H%M%S)
```

## Master_Tag TDMA10 Carrier Checkpoint - 2026-05-03

After verified APOS deployment, the first 3-tag capture exposed a separate
TDMA scheduler mismatch.

Failing capture:

```text
logs/motion_3tag_after_verified_apos_free_loose_b61_20260503_094322
positions_all=1200
BSF66F positions=0
BS2DCE positions=602
BSDC91 positions=598
CM/CS/CR/CF=0
TR architecture active
```

BSF66F-only probe proved the Tag and APOS layout were not the cause:

```text
logs/probe_BSF66F_only_after_verified_apos_b61_20260503_094713
positions_all=300 / 30s
tr_all=2400
tr_valid_all=2306
all anchors A-H visible
```

Root cause:

```text
Master_Tag carrier still used weighted TDMA period=40ms active=24ms.
Tag b61 lightweight TDMA uses lperiod=10ms lcount=10.
After final 3-tag rebalance, BSF66F entered slot=2/6 mask=0x0024 and received all timeout.
```

Fix:

```text
Rebuilt and flashed Master_Tag B120 only.
SNR: 1050070698
Build dir: build-master-control-b120-m1-master-tag-lfrc-alt-bcast-b61-apos-verify-tdma10-carrier
Build args: APP_MASTER_TDMA_SLOT_PERIOD_MS=10, APP_MASTER_TDMA_SLOT_ACTIVE_MS=9
LFRC assert: passed
No Tag OTA.
No Anchor OTA.
```

Validation capture:

```text
logs/motion_3tag_after_master_tdma10_b61_20260503_095248
positions_all=1602 / 60s
tr_all=12808
tr_valid_all=12175
CM/CS/CR/CF=0
tf_all=0
```

Per-tag:

```text
BSF66F: positions=501, TR valid=3833/4000, anchors A-H visible, RMS median/p95/max=156/196/236 mm
BS2DCE: positions=551, TR valid=4026/4408, anchors A-H visible, RMS median/p95/max=105/223/356 mm
BSDC91: positions=550, TR valid=4316/4400, anchors A-H visible, RMS median/p95/max=105/234/457 mm
```

The `tdma show` output now confirms:

```text
TDMA weighted scheduler: period=10ms active=9ms max_slots=12 freq motion=10Hz static=5Hz roto=10Hz
```

Disposition:

```text
APOS forwarding is verified.
TR/TS/TF architecture is active.
Master_Tag TDMA is now aligned with b61 Tag lightweight TDMA.
Remaining gap: 1602/1800 positions in 60s. This is now a rate/stability tuning issue, not an APOS or CM/CS/CR/CF issue.
```
