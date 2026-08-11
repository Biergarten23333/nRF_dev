# B306 v47 fleet rollout audit — 2026-08-10

Final verdict: `V47_FLEET_PASS`

Canonical production freeze and subsequent host-only observability work are
indexed in `V47_CANONICAL_FREEZE.md`,
`V47_OBSERVABILITY_COMPATIBILITY_AUDIT.md`, and
`V47_HOST_EVIDENCE_STATUS.md`. This historical rollout report is unchanged in
meaning; canonical v47 remains the deployed baseline.

## Canonical identity

- marker: `b306-imu-relay-v47`
- MCUboot version: `0.1.47`
- FWID: `f7436728c36efdd28f848e7ef59c7c422437afb8c6ee07dd8924e31967046eed`
- signed payload SHA-256: `161630a68e783ea3fb44f8eab1e70410f4eb9a46903889159dc84e99b2264f35`
- active image SHA-256: `90ef063b227feb4c70499cc186df866c24da658fba98773eacc40da73a0abf98`
- finalized manifest SHA-256: `36c23bb56900fd5588e953249168d1ef438a2d7407d540d00cfe5a4c77196a44`

The artifact was not rebuilt, resigned, or modified. BSF6C53 was excluded from
upload because its independent v47 qualification was already complete.

## Rollout result

| Node | Source active SHA | Run ID | Absolute deadline | Transaction evidence | Final state |
|---|---|---|---:|---|---|
| BSF3C79 | `79af895a…c950` | `v47-fleet-BSF3C79-162768470688` | 162948.822500516 | transaction verifier initially missed restored CDC; independent rescue under the original deadline passed | confirmed v47 |
| BSFC2CC | `9149678b…78a` | `v47-fleet-BSFC2CC-163160077158` | 163340.425287611 | transaction confirmer passed; deadline-bound extra rescue timed out | confirmed v47 |
| BSF44AD | `79af895a…c950` | `v47-fleet-BSF44AD-163286343091` | 163466.686740755 | transaction confirmer passed; deadline-bound extra rescue timed out | confirmed v47 |
| BSF8BC4 | `79af895a…c950` | `v47-fleet-BSF8BC4-163425628098` | 163605.976228361 | transaction confirmer and rescue passed | confirmed v47 |
| BSF1120 | `79af895a…c950` | `v47-fleet-BSF1120-163534373070` | 163714.716483469 | transaction confirmer passed; deadline-bound extra rescue timed out | confirmed v47 |
| BSF31CC | `79af895a…c950` | `v47-fleet-BSF31CC-163656881611` | 163837.226517282 | transaction confirmer passed; deadline-bound extra rescue timed out | confirmed v47 |
| BSFAA61 | `9149678b…78a` | `v47-fleet-BSFAA61-163778238077` | 163958.587079405 | transaction confirmer and rescue passed | confirmed v47 |
| BSFB165 | `79af895a…c950` | `v47-fleet-BSFB165-163883968545` | 164064.328650928 | transaction confirmer and rescue passed | confirmed v47 |
| BSFEC35 | `79af895a…c950` | `v47-fleet-BSFEC35-163997362650` | 164177.708279159 | transaction confirmer and rescue passed | confirmed v47 |
| BSF6C53 | qualified v47 | excluded | — | no upload in this campaign | confirmed v47 |

Every updater that mutated a target recorded the complete terminal sequence
through `READY_FOR_CONFIRM`, including upload completion, secondary hash
verification, pending, and reboot evidence. No per-board upload was retried.
Two host-only batch-start failures and the BSF1120 updater boot that was stopped
before scanning/upload were retained as evidence and did not mutate a target.

The fresh post-transaction live checks independently proved exact requested
identity, marker, FWID, active image SHA and `confirmed=1` on all ten boards.
The fresh MCUboot inventory proved all ten active slots were version 0.1.47 with
the canonical hash, `confirmed=true`, and `pending=false`. Ten consecutive
exact fleet inventory samples passed with no duplicate or unexpected peer.

## Hard-power persistence acceptance

After operator confirmation of a physical hard-power cycle on the nine newly
upgraded boards, every one passed the read-only per-node audit:

- fresh uptime below the ten-minute acceptance ceiling;
- exact node, marker, FWID and active image SHA;
- `confirmed=1`, `RING init=cold`, and `reboot_owner=0`;
- nonzero UWB, IMU and telemetry records during a 20-second observation.

The final all-ten MCUboot inventory again proved canonical v47 active,
confirmed, and not pending. The final ten-sample stable inventory also passed.
BSF6C53 retained its previously accepted v47 state and was not power-cycled.

## Evidence index

Raw evidence is retained under `B306_Part/logs/v47_fleet_20260810/`:

- `pre_ten_peer_inventory/` and `pre_slot_inventory/`: atomic preflight gates;
- `batch/fleet_ota_20260810_145214/`: BSF3C79 transaction and rescue;
- `batch/fleet_ota_20260810_145845/`: remaining eight transactions and ledger;
- `final_live_all_ten*/`, `final_slot_inventory/`, and
  `final_ten_peer_inventory/`: independent pre-power verification;
- `post_hard_power/`: nine per-board cold-start and stream audits;
- `post_hard_power_slot_inventory/` and
  `post_hard_power_ten_peer_inventory/`: final fleet gates.

Transaction return codes and updater markers were treated as diagnostic only;
the verdict derives from exact live identity, exact active hash, durable
confirmation, slot state, and post-hard-power persistence evidence.

The overnight wedge experiment was not started.
