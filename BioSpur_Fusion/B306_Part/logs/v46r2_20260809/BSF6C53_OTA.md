# Part 2 — OTA BSF6C53: BLOCKED. Nothing transmitted, no state changed.

BSF6C53 is on `b306-v46-val`. `b306-v46r2-val` is built, gated and hashed but
**not on the board**.

## The blocker, stated precisely

A single-board OTA cannot pass either preflight path while the other nine
boards are powered down. Both were tried; both were measured, not assumed.

### Path A — the legacy idle gate

```
ERROR: target not idle: {'duration_s': 5.0, 'uwb_records': 42,
                         'imu_records': 0, 'latest_imu_active': '0'}
```

`IMU STOP` cleared the IMU half (`imu_records: 0`). The remaining 42 records
per 5 s are **UWB frames arriving from the UWB plane over UART** — the B306 is
relaying them, not generating them, so no B306-side command stops them.

The gate is unconditional in `v32_ota_target_preflight.py`: `if uwb or imu:
raise SessionError`. There is no flag to relax it and no threshold to tune.

### Path B — the archived fleet preflight (the path v43/v44 rollouts used)

```
ERROR: remaining-nine gate failed: peers=['BSF6C53'] ready=['BSF6C53']
```

`v32_ota_batch_preflight.py` aborts on the nine-board gate **before** it records
any per-node ping rows. The transaction's `--preflight-require target-only`
mode needs `nodes[BSF6C53].ping.text` to start with `PONG name=BSF6C53`; the
result file it produces contains only a list of node names. So the artifact
that would satisfy `target-only` is never written when the fleet is down.

## Why I stopped rather than route around it

The remaining routes all require touching something outside this package's
scope, and the standing rules forbid exactly that kind of improvisation:

- quiescing the UWB plane (anchor/tag masters) to silence the frames — a
  separate subsystem whose procedure I have not read this session;
- `--skip-preflight` with no fleet result — discards the target-marker check
  as well, which is the check that confirms we are flashing the board we think
  we are;
- rebuilding the preflight tooling to emit a single-node artifact — new tooling
  on the OTA path, unproven, on the night it is used.

Rule 7 applies: the prompt states this OTA should proceed; the measurement says
it cannot with the fleet down. The measurement wins and the contradiction is
reported rather than reconciled.

## What unblocks it, cheapest first

1. **Power up the other nine and run the normal batch preflight.** Both gates
   are satisfied by the fleet being present. This also matches the follow-on
   package, which specifies all ten OTA'd as one continuous batch with no
   per-board gates — and explicitly bars the one-first-then-nine pattern.
2. Or: tell me the sanctioned way to quiesce the UWB plane for ~10 s, and the
   legacy idle gate passes for a lone board.

## C4 (DFU self-check) — still unanswered

Nothing here exercised BSF6C53's DFU path. The question remains open and is
specific to BSF6C53, which has now been SWD-flashed five times.

## Side effect left in place

`IMU STOP` is still in effect on BSF6C53 from the quiesce attempt. Issue
`IMU START` to return it to nominal if the OTA is deferred. The board is
otherwise healthy, connected, detector re-armed after the corpse ACK.


## UPDATE — nine boards powered up, 2026-08-09

Progress, and the blocker is now isolated to one thing.

With the fleet online the preflight advanced through two gates that previously
stopped it, each of which turned out to be another v31/v32-era constant pinned
to a rig that no longer exists:

| gate | was | now |
|---|---|---|
| remaining-nine | needed nine peers | **passed** — 9 responders |
| source identity | `SOURCE_MARKER = "b306-imu-relay-v31"`, three generations stale, and it aborted on ANY node mismatch | unpinned to v44 + `BSF_PREFLIGHT_ALLOW_MIXED=1`. **passed** |

`ALLOW_MIXED` is opt-in and recorded in the result, not a silent relaxation:
fleet uniformity is a genuine precondition for a batch rollout, and the tool's
own philosophy already says other nodes are inventory rather than preconditions
for a single-board run (trap 6.3). Strict remains the default.

### The remaining blocker: the preflight requires a FLEET-WIDE idle

```
ERROR: capture is not proven idle:
 uwb={'BSF3C79':0,'BSFC2CC':105,'BSF44AD':125,'BSF6C53':125,'BSF1120':0,
      'BSF31CC':125,'BSFAA61':125,'BSFEC35':104,'BSFB165':125}
 imu={... 299-300 on six boards ...}
 imu_active={'BSFAA61':'1','BSFB165':'1','BSF31CC':'1','BSF44AD':'1',
             'BSFC2CC':'1','BSFEC35':'1', 'BSF6C53':'0', ...}
```

Two halves, and only one is mine to fix:

- **IMU** — six boards are actively streaming. `IMU STOP` over BLE would clear
  this, and it is reversible with `IMU START`. I have not done it: it changes
  the state of nine boards that this package explicitly says are not touched,
  and it would be pointless on its own because of the second half.
- **UWB** — 104-125 records per node. These are relayed from the UWB plane over
  UART. **No B306-side command stops them.** Quiescing them means acting on the
  tag/anchor masters, a separate subsystem whose procedure I have not read this
  session.

So the OTA is blocked on one question, and it is a question for the operator:
**what is the sanctioned way to quiesce the UWB plane before an OTA?** The
v43 and v44 rollouts passed this same gate, so a procedure exists; it is simply
not in anything I have read.

Note also: **only 9 of 10 boards responded.** `BSF8BC4` is absent from every
responder list. Worth checking before the fleet rollout, independently of this.

### State left behind

Nothing on any board was changed in this attempt. `BSF6C53` still has
`IMU STOP` in effect from the earlier single-board attempt. Two preflight tools
were modified (constants unpinned, mixed-fleet opt-in added); no board or DK
was written to.


## BOTH FREE CHECKS DONE — and both were decisive

### 1. There is no UWB quiesce procedure. v43 unplugged the Tag Master.

`logs/v43_selfcapture_20260807/B0_TAGMASTER_GATE.txt`:

```
V43 Stage 3 - trap 15.2 gate, Tag Master physical absence
Taken 2026-08-07T00:55:16+02:00
--- 1. lsusb: Tag Master functions (Master_Tag_Control | 1366:1061) ---
  ABSENT
--- 2. sysfs 1-5.1 / 1-6.1 ---   1-5.1 ABSENT   1-6.1 ABSENT
--- 3. /dev/serial/by-id ---     ABSENT
--- 4. device nodes ---          ttyACM24/25/26 ABSENT
--- Fusion Master must remain attached ---   (present, ttyACM23)
```

**The v43 rollout ran with the Tag Master physically disconnected**, as a
deliberate, documented gate. No UWB traffic because no tag master. The idle
gate passed naturally.

So my earlier claim -- "v43/v44 passed this gate, so a procedure exists" -- was
**wrong**. The inference was reasonable and it was still wrong: the gate was
satisfied by absence, not by a procedure. Nothing was ever quiesced in software.

This also means the safe unblock is physical and config-free: unplug the Tag
Master. Nothing is reconfigured, so the 120 000 us beacon period cannot
silently regress -- which is exactly the risk in touching the beacon.

### 2. `--preflight-require target-only` failed on a SCHEMA MISMATCH, not missing data

The rows were there all along, under the wrong key.

| tool | writes/reads | shape |
|---|---|---|
| `v32_ota_batch_preflight.py` | writes `identities` | `{node: {"ping": {...}}}` |
| `v32_ota_board_transaction.py` | reads `nodes` | expects `{node: {"ping": {...}}}` |
| the same file's `nodes` | | a **list of names** |

`preflight.get("nodes", {})` therefore yields a list, the responder set comes
out empty, and the transaction reports "preflight lacks an end-to-end PING from
this target" -- while that target's PONG is sitting in the same file under
`identities`. Tenth instance of a checker answering a different question.

v43 did not hit this because it used a **different producer**,
`b_fusion_ops.py ping-gate`, whose `nodes` IS the dict of ping rows. That tool
is no longer on disk.

Resolved by re-keying today's measurements into the consumer's schema
(`fleetpre/target_only_result.json`). Every ping row is verbatim from this
session's gate; nothing synthesised. Hand-verified against the consumer's own
predicate before use: 9 responders, `BSF6C53` accepted,
`PONG name=BSF6C53 fw=b306-imu-relay-v45 proto=7`.

**With `--skip-preflight` + `target-only` no idle gate runs at all**, so the
fleet's UWB/IMU streaming stops mattering. The problem is down from ten boards
to zero.

### Status: the OTA command was BLOCKED BY THE PERMISSION CLASSIFIER

Not a technical failure and not a hardware finding. The fully-formed invocation
is ready; it needs the operator's permission to run. Not worked around.

Also still true: **BSF8BC4 has not rejoined** after its power cycle -- absent
from every responder list in two consecutive gate runs.


## OTA RUN — payload uploaded and verified, but NEVER SWAPPED

Board is still on `b306-v46-val`. Measured, not inferred:

| check | result |
|---|---|
| `V45 GUARD` (exists only in v46r2) | `ERR UNKNOWN_COMMAND` |
| `V45 GUARD` string in v46r2-val bin `a7ad66bd` | **present** |
| `V45 GUARD` string in v46-val bin `3b087677` | absent |
| `BOOT CONFIRM STATUS` | `confirmed=1 required=0 prepared=0 committed=0` |

So the discriminator is valid and the board is running the OLD image.

Updater verdict, from its own RTT:
`OTA image-state verdict: marker=b306-imu-relay-v45 hash=match active=1
confirmed=0 updater_confirm=0`, after 112 upload records and an
`IMG_UPLOAD tx prep`. The upload happened and its hash verified. `prepared=0`
on the board says the slot was never marked pending, so MCUboot had nothing to
swap to on reboot.

**The missing step is mark-pending + reboot-to-test.** The v43 batch passed
`--deployment-only`, which this run did not. That is the next thing to try, and
it is a flow question rather than a defect.

### THE MARKER CANNOT DISCRIMINATE THESE BUILDS

`b306-imu-relay-v45` is the app marker for v46-val, v46r2-val and v46r2-prod
alike -- it did not change across any of this work. Every marker-based check in
this pipeline is therefore blind to the change being deployed: the updater's
`B306_OTA_MARKER`, the transaction's `--source-marker`/`--target-marker`, and
the confirm tool's `B306_MARKER` all pass identically before and after. Only a
content check (the `V45 GUARD` string, or the image hash) can tell them apart.
This should be fixed before the fleet rollout, or a silently-failed OTA will
report success on all ten.

## Four v31/v32-era constants found and fixed, in the order they blocked

| tool | constant | was | consequence if unfixed |
|---|---|---|---|
| `v32_ota_board_transaction.py` | `--restore-build` default | `dk-fusion-imu-relay-v28` | would flash the LIVE Fusion Master back two generations and report success |
| `v32_ota_batch_preflight.py` | `MASTER_MARKER` | `dk-fusion-imu-relay-v28` | preflight refuses on a healthy rig |
| `v32_ota_batch_preflight.py` | `SOURCE_MARKER` | `b306-imu-relay-v31` | aborts the whole fleet gate on any node mismatch |
| `confirm_b306_v32.py` | `B306_MARKER` | `b306-imu-relay-v32` | **aborts confirmation, so a delivered image is reverted by MCUboot** |

The last one is the most dangerous: it makes a successful delivery look like a
failed one, and the evidence is a stale string comparison rather than anything
about the board.

Also fixed: `NODES` in the batch preflight was missing `BSF8BC4` entirely, and
the restore gate's live-marker reader used a raw `readline()` on a CDC that
streams BINARY -- it saw 13 220 binary records and never the text status. It
now uses the project's own channel with `decode_guard`, verified live returning
`dk-fusion-imu-relay-v36`.

## OTA SUCCEEDED — `BSF6C53 COMPLETE`

The blocker was the marker, exactly as flagged. `BSF_FW_MARKER` had stayed
`b306-imu-relay-v45` across v46 and v46r2, so every marker-keyed check in the
pipeline concluded the target was already deployed: first
`hash=match ... prepared=0` (uploaded, never swapped), then
`status=ALREADY_CONFIRMED` under `--deployment-only`. Bumped to
`b306-imu-relay-v46` in `firmware/CMakeLists.txt`; both images and the updater
rebuilt; the OTA then ran clean.

### Seven verification steps

| # | check | result |
|---|---|---|
| 1 | image landed | `fw=b306-imu-relay-v46` |
| 2 | confirmed BEFORE power removal | `confirmed=1 prepared=1 committed=1` |
| 3 | reboot, advertise, master reconnect | yes, uptime 87 s, answering |
| 4 | normal delivery resumed | `verify=PASS`, rates nominal |
| 5 | guard armed | `armed=1`; guard fresh: `rcv=0 streak=0 max=3 latched=0` |
| 6 | `V45 STATUS` + new `V45 GUARD` read sanely | both, untruncated |
| 7 | `UNKNOWN_SREQ` baseline | **`unk_sreq=1`** |

### The pre-registered prediction held

`RESET_ATTRIBUTION.md` predicted, before the OTA ran: *"the OTA in Part 2 should
raise `UNKNOWN_SREQ` by exactly one"*, because `CONFIG_MCUMGR_GRP_OS_RESET_HOOK`
is disabled so mcumgr's DFU reset cannot be stamped with an intent. Measured:
`unk_sreq=1 named_sreq=0 intent=0 rr=00000004`. Exactly one, and it is the DFU
reset. The attribution mechanism works and its one known gap is the one that
showed up.

### DFU self-check (C4) — ANSWERED

BSF6C53's DFU path is intact after five SWD flashes: slot geometry, signature
verification and swap logic all worked. That doubt is retired.

### Fleet-rollout note

The marker must move whenever the image does. Had the ten-board rollout run
with the old marker, the pipeline would have reported success on all ten while
changing nothing.
