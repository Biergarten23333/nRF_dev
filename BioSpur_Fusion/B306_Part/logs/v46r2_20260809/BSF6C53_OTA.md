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
