# BSF1120 post-reboot control-path audit

Date: 2026-08-10. Baseline: `95c641ad7c79a29d0745fa01e52a2435c08037d5`.
No OTA, image upload, pending mark, confirmation mutation, B306 slot write or
B306 reset was performed.

## Offline timing salvage

The versioned `ota_timing_evidence.py` evaluator reparsed the original raw CDC
log without editing its result. BSF3C79, BSFC2CC and BSF44AD each have an exact
REBOOT QUEUED reply, later disconnect, and subsequent requested-node PONG.
None has a post-reboot STATUS or BOOT CONFIRM STATUS record, so all verdicts are
`INVALID_MISSING_UPTIME_CONFIRMATION`. Evidence:
`logs/ota_timing_qualification_20260810_104547/offline_salvage_v1.json` (SHA-256
`d82182bcd78b0c19aefa04daa1139cebca4880fea03b3cbf7180a601e424735d`).
No sample was promoted.

## Preserved live failure

The 64.269009 s unfiltered capture is
`logs/bsf1120_control_failure_20260810_112910/`. Its raw log SHA-256 is
`d1c7c2cd3761e54115519a420ae209457e81fc9aed4b2611445256602672ef25`.
Three rounds each sent PING to BSF6C53, BSF1120 and BSF31CC, followed by ten
seconds without another BSF1120 probe.

| Observation | Result |
|---|---|
| Healthy peer PINGs | 6/6 correct PONGs |
| BSF1120 Master writes | 3/3 `FUSION_COMMAND_TX err=0` |
| BSF1120 rejects | 0 |
| BSF1120 replies | 0 |
| BSF1120 UWB / IMU / telemetry | 0 / 0 / 0 |
| BSF1120 Master QoS | continued, with frozen delivered counters |

Periodic telemetry was absent, so `ctrl_rx` before/after cannot be observed.
The strict decision-matrix classification is `INCOMPLETE_EVIDENCE`: Master
admission and GATT write submission succeeded, the failure is not
Master-global, but there is no B306 application witness distinguishing a lost
downlink from a blocked application notification path. The simultaneous loss
of every B306 notification kind makes a notification-path wedge plausible,
but this is an inference rather than a proven root cause.

## Source path

| Stage | Implementation and state |
|---|---|
| Host command | CDC decoder accepts `BSFxxxx command` after syntax checks. |
| Master lookup | `peer_by_name()` selects an allocated slot; readiness requires non-null `bt_conn`, `bridge_ready`, and nonzero control handle. |
| GATT write | `bt_gatt_write_without_response(peer->conn, peer->control_value_handle, ...)`; `FUSION_COMMAND_TX err` is submission status, not remote receipt. |
| B306 RX | `control_write()` validates length/content, puts a request into `control_queue`, then increments `ctrl_rx`. |
| Command worker | Dedicated `control_thread` validates the requested BSF name, allocates correlation, and calls `process_control()`. |
| PING | `process_control()` formats PONG and calls `publish_control_reply()`. |
| Response enqueue | Control reply enters `q_ctl`, shared with other control records. |
| Notification | The single publisher/notify worker services control, UWB, IMU and telemetry through `bt_gatt_notify()`. A wedge here suppresses all application notifications while LL QoS can continue. |
| Master reply | Data notification parser emits node/source/correlation/text as `FUSION_REPLY`; host controller matches the pending requested-node reply. |

Master disconnect handling calls `release_peer()`, aborts peer reads, unreferences
the connection, and zeroes the entire slot. Reconnect allocates a new slot and
rediscovers service, CCCs and control handle; no old characteristic handle is
intentionally retained. There is no separate host pending-correlation object
in the Master: correlation is assigned by the B306 control worker.

## Recovery ladder

Recovery A sent the Master-local `BSF1120 RECONNECT`. The targeted disconnect
succeeded, the other nine peers all remained controllable, but BSF1120 never
advertised/reconnected and emitted no stream. Raw log SHA-256:
`17a9f59ac35fa78db3670233587d50980d06c35958f655fa5ecc0eb3c3529ebe`.

Recovery B reset the production Master through J-Link SNR `683234364` without
flashing. The marker remained exactly `dk-fusion-imu-relay-v36`. Nine peers
were rebuilt and all answered PING; after 180 s BSF1120 was still absent while
the Master reported `count=9 ready=9 scanning=1`. CDC log SHA-256:
`24e5de830a973ffbc507471e4d2cc5af1b9156cd34e9c281c119b34ef6b9b0f2`.

Recovery C is pending a physical BSF1120 reset/power cycle. Because Recovery A
removed its route and Recovery B did not rediscover it, no remote B306 reset is
available. Fusion-PCB SWD is a human handover and was not attempted.

## Timing disposition

Valid original samples remain BSF6C53 (11.450379 s, v46) and BSF8BC4
(12.453154 s, v44). Valid salvaged samples: none. Missing valid samples:
BSF3C79, BSFC2CC, BSF44AD, BSF1120, BSF31CC, BSFAA61, BSFB165 and BSFEC35.
BSF1120 is not healthy enough for another qualification attempt. Registry code
keys samples by node, Master firmware, B306 firmware, tool schema,
configuration and evidence SHA, and rejects mixed identities. The fleet gate
remains BLOCKED; max/P95 and the strict margin predicate are unavailable.
