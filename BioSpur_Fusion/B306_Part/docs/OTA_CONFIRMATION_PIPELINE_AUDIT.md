# B306 OTA confirmation pipeline audit

Date: 2026-08-10. Baseline: `657f800201466b26ba10aeb15ce185d1eb7964f1`.
Scope is host-side inspection and testing; no device operation was performed.

## Authority and state model

The seven board states are `OLD_CONFIRMED`, `TARGET_RUNNING_UNCONFIRMED`,
`TARGET_CONFIRMED`, `TARGET_IDENTITY_MISMATCH`, `ROLLBACK_OBSERVED`,
`UNREACHABLE`, and `UNKNOWN`. A durable pass requires a reply from the requested
node, an exact expected payload identity, and `BOOT CONFIRM STATUS confirmed=1`.
Transaction return code, a firmware marker, updater RTT text, and `V45 GUARD`
are diagnostics only. `V45 GUARD` witnesses a capability; it cannot identify
the exact signed payload.

## Abort-point inventory (24)

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

## Hardcoded constant inventory (31)

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

## Timing audit and blocker

The old pre-confirm upper bound is at least 417.874 s RTT capture + restore +
25 s sleep + confirmation polling, while firmware rolls an unconfirmed image
back after 180 s. This is structurally unsafe. Existing logs must be measured
for earliest target boot through required updater handoff, restore, first exact
identity observation, and `confirmed=1`. Until max, P95, sample count and a
documented margin prove that entire path is below 180 s, fleet rollout is
blocked. Optional RTT collection is not a prerequisite and must be moved out of
the confirmation critical path.

Measured completed RTT captures in the existing v46r2 evidence have `n=9`,
maximum `102.542 s`, and linearly interpolated P95 `94.094 s` (values parsed
from `updater_rtt_console.log`). These numbers end at the updater's handoff and
post-verify markers; the evidence does not carry a common timestamp tying the
earliest possible test-image boot to the later application `confirmed=1`
sample. Consequently no defensible end-to-end maximum or P95 exists yet and no
margin can be selected. The transaction now removes optional RTT capture and
the fixed 25 s sleep from the critical path and requires an explicit deadline
below 180 s, but **fleet rollout remains blocked** until a non-mutating dry-run
or future canary capture provides a shared-clock boot-to-confirm distribution.
