# v47 updater handoff repair and BSF6C53 qualification

Final verdict: **READY_FOR_V47_FLEET_AUTHORIZATION**

## Immutable artifact

The canonical B306 artifact was not rebuilt or resigned. Final verification:

- version `v47`, marker `b306-imu-relay-v47`, MCUboot `0.1.47`
- FWID `f7436728c36efdd28f848e7ef59c7c422437afb8c6ee07dd8924e31967046eed`
- signed payload SHA-256 `161630a68e783ea3fb44f8eab1e70410f4eb9a46903889159dc84e99b2264f35`
- active MCUboot image SHA-256 `90ef063b227feb4c70499cc186df866c24da658fba98773eacc40da73a0abf98`
- finalized manifest SHA-256 `36c23bb56900fd5588e953249168d1ef438a2d7407d540d00cfe5a4c77196a44`
- merged hex SHA-256 `9bf27f37050ab42f26e00ea3e1c0a661b66a476608fc34a267235152311516e8`

## Previous failure reconstruction

The previous transaction used host T0 `158005.656035628` and deadline
`158175.656035628`. The updater flash log closed at 13:33:08.192 +02:00 and
the production-Master restore log closed at 13:33:16.226. The updater binary
deliberately waits 15 seconds after boot before auto-starting its BLE scan, but
the host restored v36 approximately eight seconds after updater flashing.
There was no updater RTT capture, upload progress, verify, pending, or reboot
record before restoration. The confirmer started immediately after restore;
the later rescue saw only v46 until the deadline. Post-failure inventory showed
both slots unchanged.

`updater_capture=DEFERRED_UNTIL_AFTER_DURABLE_CONFIRM` was assigned
unconditionally after the updater DK flash returned. DEFERRED did permit the
next statement, `restore_master()`, without a terminal-state check. Because no
RTT was captured, an updater-local failure cannot be excluded from the old run
in isolation; however, the compiled 15-second startup delay and the observed
eight-second overwrite directly prove that restoration occurred before the
updater's scheduled scan start. The old flow therefore could not have begun
the OTA transaction.

## Handoff repair

`ota_updater_handoff.py` now preserves raw RTT bytes and atomically emits
structured records with schema, run ID, target, monotonic time, expected and
observed hashes, progress, and error fields. It enforces monotonic milestones
from `UPDATER_BOOTED` through `REBOOT_QUEUED`. Only a fully validated
`READY_FOR_CONFIRM`, `FAILED`, or `PHASE_TIMEOUT` permits Master restoration.
Malformed fields, wrong run/node/hash, missing predecessors, regressions, and
contradictory terminals fail closed. Free-form success text is ignored.

One absolute deadline covers all phases. With T0 before updater release:

```text
absolute deadline = T0 + 180.000000000 s
updater cutoff    = deadline - 61.193245916 s
updater budget    = 118.806754084 s
```

Every exit restores v36. Exceptions after any possible mutation invoke the
verifier with the original run ID and deadline and contain no upload retry.

Final offline results: 137 Python/host tests PASS, all DK updater C tests PASS
(`OTA_IMAGE_STATE_VERIFY`, `OTA_NO_DIRECT_CONFIRM`, and
`OTA_GRADUATED_READ_RETRY`), Python compilation PASS, diff check PASS, and all
canonical artifact hashes unchanged.

## Authorized transaction

Run ID: `v47-BSF6C53-20260810T142035`

Preflight passed ten consecutive exact ten-peer samples and exact BSF6C53
routing. Slots matched the required unchanged v45 pre-state. The only new
authorized upload produced these host-monotonic milestones:

| Stage | Time |
|---|---:|
| T0 | 160566.889080765 |
| UPDATER_BOOTED | 160584.256617097 |
| TARGET_SCANNING | 160599.623284609 |
| TARGET_CONNECTED | 160599.742354266 |
| SMP_DISCOVERED | 160599.959418966 |
| SMP_SUBSCRIBED | 160601.473317681 |
| UPLOAD_STARTED | 160614.212108872 |
| UPLOAD_COMPLETE | 160636.850808842 |
| SECONDARY_HASH_VERIFIED | 160636.910226065 |
| PENDING_SET | 160636.937615971 |

Raw RTT then recorded an accepted reset response
`group=0x0000 cmd=0x05 status=0` and
`OTA_STATE:reboot_pending detail=full_flow_reset`. The first parser revision
incorrectly expected OS reset command ID `0x00` and treated the legitimate
post-reset reconnection as a duplicate early connection. This caused a
fail-closed emergency v36 restore, not another upload. The parser was corrected
and regression-tested. The independent verifier continued under the original
deadline `160746.889080765`; it observed exact v47 node/FWID/active SHA,
performed PREPARE and COMMIT, and reached `TARGET_CONFIRMED` in 6.192613 s.

Post-confirm slot inventory:

- Slot 0: `0.1.47`, exact image SHA, active=true, confirmed=true,
  pending=false, bootable=true.
- Slot 1: prior `0.1.45`, inactive, confirmed=false, pending=false,
  bootable=true.

No automatic or manual second upload occurred.

## Reboot timing and persistence

Ten BSF6C53 reboot-only samples were all valid. Every sample contained a real
disconnect/reconnect, uptime reset, exact node, marker, FWID, active SHA, and
confirmed=1. Maximum reboot-to-identity/confirmation was 10.448696462 s.

The component-wise conservative v47 bound is:

- production Master restore: 8.302000000 s
- archived ten-board route/reconnect maximum: 16.849154288 s
- fresh v47 identity/confirmation status maximum: 0.448911536 s
- archived PREPARE-to-confirmed maximum: 5.777007000 s
- upper bound: 31.377072824 s
- margin: 30.000000000 s
- bound plus margin: **61.377072824 s < 180 s — PASS**

After explicit operator authorization, BSF6C53 received one physical hard
power cycle. Read-only acceptance proved `RING init=cold`,
`CORPSE reboot_owner=0`, exact v47 FWID and active SHA, confirmed=1, and no
pending image. In 20 seconds it produced 166 UWB, 400 IMU, and 20 telemetry
records. Hard power was persistence/state-clearance acceptance, not activation
or OTA recovery.

## Final fleet protection

The production Master is restored to `dk-fusion-imu-relay-v36`. Final inventory
reached ten consecutive exact stable samples with count=10, ready=10, all
expected peers connected/subscribed and exact requested-node PINGs, and no
duplicate or unexpected peers.

No mutating command was sent to BSF3C79, BSFC2CC, BSF44AD, BSF8BC4, BSF1120,
BSF31CC, BSFAA61, BSFB165, or BSFEC35. The nine-board rollout was not started.

## Evidence index

- `pre_ten_peer_inventory/result.json`
- `pre_slot_inventory/result.json`
- `transaction/updater_raw_rtt.bin` and `transaction/updater_stages.json`
- `transaction/transaction.json`
- `transaction/independent_rescue_r2/result.json`
- `post_confirm_slot_inventory/result.json`
- `reboot_timing_10/result.json` and `reboot_timing_10/v47_bound.json`
- `post_hard_power_audit_r2/result.json`
- `post_hard_power_slot_inventory/result.json`
- `final_ten_peer_inventory/result.json`
