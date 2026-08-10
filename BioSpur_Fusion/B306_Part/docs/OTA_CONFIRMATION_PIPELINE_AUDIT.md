# B306 OTA confirmation pipeline audit

Date: 2026-08-10. Implementation baseline:
`d72d02683cb987c8346358653a61b7bd3fddedae`. No OTA or B306 slot write was
performed. Two timing-rehearsal attempts stopped before T0 because the
production Master reported `count=0 ready=0`; consequently no B306 received a
reboot command.

## P0 closure implemented

Build identity is now an explicit three-stage lifecycle:

1. `ota_build_identity.py prepare` canonicalizes the complete build inputs and
   derives the FWID.
2. The build exports that FWID as `BSF_FWID`; CMake rejects a missing or
   malformed value and the application embeds it.
3. `ota_build_identity.py finalize` verifies the embedded FWID in the final
   signed binary, computes its whole-file SHA-256 and MCUboot image SHA-256,
   writes the final manifest, and collision-checks the registry. One FWID may
   not acquire a different signed payload or MCUboot image hash.

The two hashes have different meanings. `signed_payload_sha256` covers the
complete signed file used for transfer and registry provenance.
`mcuboot_image_sha256` is MCUboot's image digest over the header, image and
protected TLVs. The B306 reads the active slot's latter value through NCS
v2.8.0's supported `img_mgmt_active_image()`, `img_mgmt_active_slot()` and
`img_mgmt_read_info()` APIs and returns it with FWID in PONG. Durable identity
therefore directly compares requested node, expected embedded FWID, expected
active MCUboot image SHA-256, and `confirmed=1`; the whole signed-file hash
remains bound through the finalized manifest and registry.

The classifier implements all seven states: `OLD_CONFIRMED`,
`TARGET_RUNNING_UNCONFIRMED`, `TARGET_CONFIRMED`,
`TARGET_IDENTITY_MISMATCH`, `ROLLBACK_OBSERVED`, `UNREACHABLE`, and `UNKNOWN`.
An approved, reachable source identity with `confirmed=1` is
`OLD_CONFIRMED`. Seeing the target and subsequently the source is rollback,
not an unreachable board.

The transaction records T0 before releasing the updater flash operation and
derives one absolute host-monotonic deadline. The confirmer receives that
absolute value and cannot create a new budget. After target boot the critical
path is limited to production-Master restoration, CDC framing, requested-node
routing and exact identity confirmation. Spacing/schedule reconstruction runs
only after `TARGET_CONFIRMED`. The fleet driver launches an independent live
rescue verifier after every transaction outcome and supplies the original
deadline. Its final fleet pass launches another fresh verifier per board and
writes separate `final_live_verification` evidence; it never promotes a cached
transaction result.

The focused OTA pipeline suite has 17 passing tests, including finalized
payload binding, embedded FWID, collision rejection, payload-hash comparison,
`OLD_CONFIRMED`, inherited deadline expiry, spacing order, rescue after a
crashed transaction, and fresh final verification. Existing host-binary,
updater, rollout/source-contract and firmware-policy suites also pass. A
no-flash B306 build passed the memory gate at 47.24% FLASH and 53.47% RAM with
an explicit zero-byte C malloc arena.

## Authority and state model

The seven board states are `OLD_CONFIRMED`, `TARGET_RUNNING_UNCONFIRMED`,
`TARGET_CONFIRMED`, `TARGET_IDENTITY_MISMATCH`, `ROLLBACK_OBSERVED`,
`UNREACHABLE`, and `UNKNOWN`. A durable pass requires a reply from the requested
node, an exact expected payload identity, and `BOOT CONFIRM STATUS confirmed=1`.
Transaction return code, a firmware marker, updater RTT text, and `V45 GUARD`
are diagnostics only. `V45 GUARD` witnesses a capability; it cannot identify
the exact signed payload.

## Historical abort-point inventory at the audit baseline (24)

`secondary` means the new image may already be in the secondary slot; `test`
means it may already be running unconfirmed. Deadlines are host monotonic unless
stated otherwise.

| # | File/function | Abort condition | secondary / test | rollback risk | retryable | deadline/source | evidence before abort | last known state | checker input / actual question |
|---:|---|---|---|---|---|---|---|---|---|
| 1 | target preflight/main | decode guard fails | no / no | no | yes | 15 s literal | CDC log/result | unknown | live CDC / is framing decodable |
| 2 | target preflight/main | master status missing/mismatch | no / no | no | yes | 5 s helper/default | CDC log/result | unknown | status marker / does current DK report expected marker |
| 3 | target preflight/main | PING absent or marker mismatch | no / no | no | yes | controller 3 attempts × 8 s | CDC log/result | old/unknown | PONG marker / does app self-report marker |
| 4 | target preflight/main | node not idle | no / no | no | yes | 5 s default | telemetry counts | old confirmed | live records / is data plane idle now |
| 5 | transaction/main | artifact hash mismatch/missing | no / no | no | no | none | transaction JSON | old confirmed | local files / are supplied artifacts exact |
| 6 | transaction/main | archived preflight missing/invalid | no / no | no | yes | none | transaction JSON | old confirmed | stale JSON / did an earlier run see PONG |
| 7 | transaction/main | live preflight fails | no / no | no | yes | child bounds | child logs | old/unknown | live queries / identity and idle now |
| 8 | transaction/main | restore BIN lacks marker | no / no | no | no | none | transaction JSON | old confirmed | local bytes / does restore contain text marker |
| 9 | transaction/read_dk_marker | live DK marker unavailable/mismatch | no / no | no | yes | 20 s nominal, helper calls 15+5 | temp log not durable; transaction JSON | old confirmed | live status / would restore alter master generation |
| 10 | transaction/flash updater | J-Link updater flash/verify fails | no / no | no | yes | subprocess unbounded | J-Link log | old confirmed | verifybin / did DK updater flash verify |
| 11 | capture/classify | explicit updater failure marker | yes / possible | yes | conditional | 417.874 s default | RTT/console/transaction | unknown | updater RTT / did updater report explicit failure |
| 12 | capture/classify | RTT markers absent | yes / possible | yes if delayed | yes | 417.874 s calculated bound | RTT/console | unknown | RTT strings / were two diagnostic strings captured |
| 13 | restore_master | production master restore flash fails | yes / possible | yes | yes | subprocess unbounded | restore log | unknown | verifybin / did DK restore verify |
| 14 | transaction/main | fixed supervision sleep | yes / yes | yes | n/a | 25 s literal | none | target unconfirmed | elapsed time / assumes old central disappearance |
| 15 | confirm/main | decode guard fails | yes / yes | yes | yes | 15 s | CDC/result | unknown | CDC / is framing decodable |
| 16 | confirm/main | fleet readiness/master mismatch | yes / yes | yes | yes | ready 60 s, master 5 s | CDC/result | unknown | live status / is unrelated fleet ready and DK marker expected |
| 17 | confirm/main | bridge retry expires | yes / yes | yes | yes | 180 s default | retry count only | unreachable | PING errors / can master route now |
| 18 | confirm/main | non-whitelisted PING error | yes / yes | yes | conditional | controller bound | last exception only | unknown | error text / did helper classify error retryable |
| 19 | confirm/main | first PONG old marker | yes / yes | **yes** | **yes, but old code aborts** | single sample | one PONG | old/transition | marker / what marker did one sample report |
| 20 | confirm/main | status says neither confirmed nor required | yes / yes | yes | conditional | single sample | status | mismatch/unknown | confirm flags / is image currently confirmable |
| 21 | confirm/extract_token | PREPARE token absent | yes / yes | yes | yes | controller bound | PREPARE reply | target unconfirmed | reply syntax / was token parseable |
| 22 | confirm/main | COMMIT command fails | yes / yes | yes | yes | controller bound | command error/result | target unconfirmed | commit reply / did app accept token |
| 23 | confirm/main | confirmed status not seen | yes / yes | yes | yes | 15 s literal | last successful status not retained | target unconfirmed | repeated status / did confirmation finish |
| 24 | fleet/main | build/transaction/content subprocess exception or timeout | varies | varies | yes | build/txn 1800 s, content 300 s | partial logs; old driver aborts | unknown | rc/guard / subprocess completion or capability only |

Aborts 11–23 can occur after upload. Therefore none may erase evidence, infer
rollback merely from a nonzero transaction return code, or suppress a later
durable verifier pass.

## Historical hardcoded constant inventory at the audit baseline (31)

| Area | Constants | Effect |
|---|---|---|
| confirm | master `v28`; B306 environment fallback `v45`; ten-node choices; ready 60 s; bridge 180 s; master 5 s; decode 15 s; retry sleep 2 s; post-commit 15 s/1 s | stale identities and independent deadlines |
| transaction | SNR `683234364`; device `NRF52840_XXAA`; ten nodes; source `v31`; eight updater hashes; restore v28 hashes; capture formula `15+180+21.437+180+21.437`; target/build/master v32/v28 defaults; 25 s sleep; spacing 120 s; RTT address `0x20002010`; post-match 0.25 s | hardware/artifact identity and critical-path delay |
| preflight | master `v28`; ten nodes; decode 15 s; status helper 5 s; observe 5 s | stale master default |
| fleet v46r2 | nine nodes; payload/build/log paths; target/source v46/v44; prefix; archived preflight; restore build/marker/two hashes; subprocess 1800 s; content 300 s; bench 10/3 s | campaign knowledge embedded in executable |
| firmware | fallback marker `v37`; confirm timeout 180000 ms | manual identity and rollback deadline |
| b306_command | controller timeout 8 s; max attempts 3; no resend after TX | per-query latency bound |
| RTT capture | read 4096 B; idle sleep 0.01 s | evidence behavior |

Campaign values must move to an explicit build/deployment manifest. Stable
hardware registry values (probe serial/device) may remain named constants but
must be recorded in run evidence.

## JSON producer/consumer audit

| Producer | Consumer | Contract used | Finding |
|---|---|---|---|
| target preflight `result.json` | transaction archived-preflight branch | top `status`; `nodes[node].ping.text` | **Mismatch:** target preflight produces singular `node`/`ping`, while transaction expects fleet `nodes`; only a separate fleet producer is compatible |
| confirm `result.json` | transaction deployment-only | `status`, `ping.text`, `after.text` | **Mismatch:** `ALREADY_CONFIRMED` has no `after`, so durable success is rejected |
| transaction `transaction.json` | fleet/log reader | no enforced schema; fleet records rc only | **Gap:** no authoritative board state or verifier samples |
| bench `commands_*.json` | fleet content checker | fleet parses console text instead of JSON | **Mismatch:** structured command fields are ignored; capability is mislabeled content identity |
| fleet `ledger.json` | operator | ad-hoc `boards`, `txn_rc`, `content.updated`, `status` | **Gap:** non-atomic writes, no schema version/run ID/transitions/final verification |
| updater generated manifest/header | DK updater C | image length, MCUboot image hash, payload SHA | Compatible locally; not propagated into B306 application confirmation |

All new result documents use a schema version and preserve samples. Consumers
must reject absent identity fields instead of supplying marker defaults.

## Timing qualification and gate

The qualification tool records T0 through T4 on one host monotonic clock and
refuses to transmit until stable CDC identity, framing and production-Master
marker are decoded. It uses a reboot-only rehearsal: no image upload, pending
mark or confirmation mutation. Since the production Master and CDC are
already live, its per-board T1 and T2 equal T0; the independently measured
production-Master restore maximum is added to the conservative bound. The
archived PREPARE-to-confirmed command maximum is likewise added separately.

Archived inputs are production-Master restore max `8.302 s` and
PREPARE-to-confirmed max `5.777007 s` (`n=4`, samples `5.731211`, `5.741493`,
`5.766303`, `5.777007` s). They are not sufficient without ten live T0--T4
samples.

Attempt evidence:

- `logs/ota_timing_qualification_20260810_103454/`: decoded the correct port,
  framing and `dk-fusion-imu-relay-v36`, then stopped at pre-PING because the
  node was not connected.
- `logs/ota_timing_qualification_20260810_103518/`: waited for fleet readiness;
  repeated fresh Master status remained `count=0 ready=0`. The wait was
  stopped before T0. No board reboot was transmitted in either attempt.

Exact gate result: **BLOCKED (0/10 common-clock samples; max, P95 and component
maxima unavailable; no conservative upper bound can be calculated; therefore
the required `upper_bound + max(30 s, 25%) < 180 s` predicate is not proven).**
Fleet OTA remains prohibited until all ten boards are powered/reachable and a
fresh reboot-only qualification produces ten raw samples and a strict PASS.

### Hardened rerun, 2026-08-10

`qualify_ota_confirmation_timing.py` now parses numeric Master fields exactly
and requires ten consecutive complete inventory samples before any reboot. A
sample requires the v36 marker, aggregate and Master `count=10 ready=10`, the
exact ten unique LIST peers, connected/subscribed state, and a requested-name
PING from every board. Failure evidence retains a per-node presence/link/PING
table, first/last seen timestamps, errors, and unexpected peers. Reboot samples
retain pre/post PONG and STATUS, retry errors, confirmation status and Master
state. Freshness requires disconnect evidence, a subsequent requested-node
PONG, and decreasing `STATUS up_ms`.

The initial read-only inventory at
`logs/ota_timing_inventory_20260810_104526/result.json` passed ten consecutive
samples with the exact fleet and no unexpected peer. The guarded reboot run at
`logs/ota_timing_qualification_20260810_104547/result.json` then recorded:

| Node | Reboot sent | Valid | Reboot-to-status | Result |
|---|---:|---:|---:|---|
| BSF3C79 | yes | no | — | post-reboot disconnect/reconnect join not accepted by the first tool revision |
| BSFC2CC | yes | no | — | same |
| BSF44AD | yes | no | — | same |
| BSF6C53 | yes | yes | 11.450379 s | disconnect/reconnect, uptime reset, confirmed=1 |
| BSF8BC4 | yes | yes | 12.453154 s | disconnect/reconnect, uptime reset, confirmed=1 |
| BSF1120 | yes | no | — | no post-reboot control reply within bound |
| BSF31CC | **no** | no | — | exact fleet gate did not recover |
| BSFAA61 | **no** | — | — | run stopped before node |
| BSFB165 | **no** | — | — | run stopped before node |
| BSFEC35 | **no** | — | — | run stopped before node |

The evidence-join defect was corrected so a retryable route/reply failure plus
a later requested-node PONG constitutes disconnect/reconnect evidence; the
uptime-reset witness remains mandatory. Before any rerun, the final read-only
inventory `logs/ota_timing_inventory_20260810_105516/result.json` ran for 21
samples and failed closed: `BSF1120` was always present, connected and
subscribed but answered no PING, while all other nine passed and there were no
unexpected peers. Per the hardware-preparation stop rule, no second reboot run
was attempted.

Exact current gate: **BLOCKED — 2/10 valid common-clock samples.** A ten-board
maximum, P95, complete component maxima and conservative upper bound are
unavailable, so the margin predicate remains unproven. No OTA, upload, pending
mark, PREPARE/COMMIT, or B306 slot write occurred.

BSF1120 localization and the bounded recovery ladder are recorded in
`BSF1120_CONTROL_FAILURE_AUDIT.md`. Neither Master-side peer redraw nor an
unchanged-firmware Master restart recovered the board. The operator's physical
power cycle restored its control and streaming paths. The subsequent exact
ten-peer inventory passed, and a BSF1120-only guarded run transmitted exactly
one REBOOT and produced a valid 10.314229 s sample with disconnect/reconnect,
uptime reset and `confirmed=1`. The partial run correctly reported BLOCKED
rather than PASS because it contained only one sample. There are now three
valid samples across the preserved evidence (BSF6C53 v46, BSF8BC4 v44 and
BSF1120 v44), zero salvaged samples, and seven missing nodes. Mixed firmware
cohorts are not silently aggregated. Fleet qualification remains BLOCKED and
no OTA or slot write occurred.

### Seven-board continuation and mixed-firmware rule

After an operator hard power cycle and a fresh exact ten-peer read-only PASS,
the guarded continuation at
`logs/seven_board_timing_qualification_20260810_120753/` rebooted only the seven
missing nodes, once each. All seven returned `REBOOT QUEUED` and disconnected.
Only BSFB165 completed the current online witness sequence: v44, 17.050621 s,
disconnect/reconnect, uptime 414189/16140 ms, and `confirmed=1`. The other six
later returned a requested-name PONG, but the run did not obtain their
post-reboot STATUS and BOOT CONFIRM STATUS records. The versioned offline
evaluator therefore reported `INVALID_MISSING_UPTIME_CONFIRMATION` for
BSF3C79, BSFC2CC, BSF44AD, BSF31CC, BSFAA61 and BSFEC35; none was promoted.
Result SHA-256 is
`42f6643791974bf7344c034b6c0a999424b11654518b4a29ddb5611efc64c477`;
raw CDC SHA-256 is
`5af41c661af33148102c4617c30e105737dadb7614a7da55842835f179c2425a`.
No repeat reboot was used to repair missing evidence. The post-run read-only
inventory passed ten consecutive exact samples.

The deployed mixed-firmware qualification rule is now explicit. v44 and v46
remain separate reported cohorts and firmware identity remains part of every
sample key. A fleet result may conservatively combine them only when all ten
unique deployed boards have an independently valid current-schema sample and
Master identity, tool schema, timing configuration and archived component
inputs match. The fleet upper bound uses the worse maximum of each component
across both cohorts; it does not average cohort maxima or discard the slower
cohort. This is an explicit conservative bound for the observed deployed mix,
not a claim that v44 and v46 are one firmware cohort. Requiring ten samples of
one B306 version would conflict with the no-OTA constraint and is not the
qualification criterion for this mixed deployed fleet.

Current observed-only cohorts (not qualification PASS inputs) are v44: n=3,
maximum 17.050621 s, interpolated P95 16.590874 s; and v46: n=1, maximum/P95
11.450379 s. Using the observed component maxima would yield v44 upper
31.193246 s and upper-plus-margin 61.193246 s; v46 upper 25.529386 s and
upper-plus-margin 55.529386 s. These figures cannot qualify the fleet because
six unique boards still lack valid samples. Exact gate: **BLOCKED, 4/10 valid**.
