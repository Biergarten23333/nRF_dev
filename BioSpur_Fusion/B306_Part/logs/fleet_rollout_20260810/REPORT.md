# B306 fleet rollout qualification and deployment disposition

Date: 2026-08-10

## Result

- Stage 1 timing qualification: **PASS (10/10)**.
- Stage 2 read-only Slot 1 preflight: **PASS (7/7)**.
- Stage 2 production rollout: **BLOCKED before the first transaction**.
- Stage 3 persistence acceptance: **not entered**. No hard power cycle was
  requested or performed.

No OTA, upload, pending mark, PREPARE, COMMIT, or Fusion-PCB SWD operation was
performed during this continuation. The only flashes were the already-audited
read-only inspector on Fusion Master DK `683234364`, followed by restoration of
`dk-fusion-imu-relay-v36`.

## Stage 1: reboot-only timing qualification

The six requested samples all contained `REBOOT QUEUED`, a real
disconnect/reconnect, a lower post-reboot uptime, exact requested-node PONG,
and `BOOT CONFIRM STATUS confirmed=1`. A post-run read-only MCUboot inventory
bound each active slot to its exact image SHA and confirmed state.

| board | T0-T4 (s) | route/restore (s) | status/confirm (s) |
|---|---:|---:|---:|
| BSF3C79 | 8.796837236 | 8.598026716 | 0.198810520 |
| BSFC2CC | 9.302373415 | 9.098884531 | 0.203488884 |
| BSF44AD | 7.400802110 | 7.198164408 | 0.202637702 |
| BSF31CC | 13.651496798 | 13.452215555 | 0.199281243 |
| BSFAA61 | 6.600867841 | 6.399073287 | 0.201794554 |
| BSFEC35 | 8.601871202 | 8.398547065 | 0.203324137 |

Combined with the four independently valid current-schema samples for
BSF6C53, BSF8BC4, BSF1120 and BSFB165, the strict fleet result is:

| quantity | result |
|---|---:|
| valid samples | 10 |
| invalid samples used | 0 |
| maximum T0-T4 | 17.050620550 s |
| interpolated P95 | 15.521014862 s |
| archived production-Master restore maximum | 8.302000000 s |
| measured route/reconnect maximum | 16.849154288 s |
| measured status/confirmation maximum | 0.265084628 s |
| archived PREPARE-to-confirmed maximum | 5.777007000 s |
| conservative upper bound | 31.193245916 s |
| margin `max(30 s, 25%)` | 30.000000000 s |
| upper bound plus margin | **61.193245916 s** |

Therefore `valid_samples == 10` and `61.193245916 < 180`: **PASS**.
The v44 and v46 cohorts remain separately identifiable in the raw evidence;
the fleet bound uses the component-wise worse maxima as the explicitly
conservative mixed-deployment bound.

## Stage 2: preflight and fail-closed stop

The seven rollout targets were exactly:

`BSF3C79 BSF44AD BSF8BC4 BSF1120 BSF31CC BSFB165 BSFEC35`

Every target reported:

- Slot 0 active and confirmed, image SHA
  `79af895a2477f00320429300db6d73a31f2e76f36950a75f29104192a164c950`;
- Slot 1 inactive, bootable, `pending=false`, image SHA
  `9149678b381d361128aab458a92aee03a962be0eea3007dca7cb255947f6a78a`.

There is no tested activation-only path in the repository. The hardened
standard transaction was therefore the only permitted candidate, but the
candidate was rejected before execution: the exact archived v46r2-prod signed
payload contains the `b306-imu-relay-v46` marker but contains no embedded
64-hex FWID and no `fwid=%s` / `image_sha=%s` PONG format. Its hashes are:

- signed payload SHA-256:
  `122d01e825354d32fa5184db4a11f6d4cdb64fd40abab475f9e3dfec9f4d0494`;
- MCUboot image SHA-256:
  `9149678b381d361128aab458a92aee03a962be0eea3007dca7cb255947f6a78a`.

The current hardened confirmer requires requested-node identity, exact FWID,
exact active MCUboot image SHA and `confirmed=1`. With this older payload it
would classify the newly booted target as an identity mismatch and refuse
PREPARE/COMMIT. Starting that transaction could only create a temporary
target-unconfirmed state followed by rollback; it cannot produce the required
durable PASS. Implementing an ad-hoc pending/activation path was explicitly
prohibited.

Stage 2 is therefore **BLOCKED without modifying any B306**. A final read-only
inventory after restoring the Master passed the exact stable ten-peer gate;
BSF6C53 remained excluded from deployment.

## Evidence digests

| evidence | SHA-256 |
|---|---|
| `ota_timing_qualification_20260810_104547/result.json` | `7b0d861d991ce6f2aa7d9f3854a3ae5f3c79589be5aa2b9feeced85c0c643124` |
| `bsf1120_timing_qualification_20260810_114712/result.json` | `c7906dc87fda165575b50a724c2d1337d9c25fd48d99e409eb61361e20904f5f` |
| `seven_board_timing_qualification_20260810_120753/result.json` | `42f6643791974bf7344c034b6c0a999424b11654518b4a29ddb5611efc64c477` |
| `stage1_six_board_timing_20260810_125731/result.json` | `10f058c7e2dc46cc662030a76bbef58b74c6db860eef8354f52813c9b9dc493e` |
| `stage1_post_reboot_slot_inventory_20260810_125953/result.json` | `367e1c20d79ef6a277fccad78591d6415c5f4c0fa4154d6784b2be0cacd18d27` |
| `stage2_slot1_preflight_20260810_130459/result.json` | `43aae6bcfcf5d78e3c62d9a92e873eaea1cfb0da3662d69a02033718b4122daa` |
| `stage2_abort_recovery_inventory_20260810_130946/result.json` | `1772569adcfb889c8cc22a84046e6dc31e1a64ad0d7406717a0263df3b71232e` |
