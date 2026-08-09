# v46r2 — the five modifications

## Which arm depends on which counters, and what each cause means in the field

| arm | condition | counters it depends on | cause when it fires |
|---|---|---|---|
| **A** | `connected ∧ subscribed ∧ notify_attempt advancing ∧ notify_ok frozen ≥ 12 s` | `notify_attempt_total` (node-local), `notify_ok_total` | `NOTIFY_FROZEN` (1) — **the notify path is broken.** Sends are being attempted and none complete. This is the fleet-wedge signature |
| **B** | `ble_connected ∧ ≥ 320 consecutive -ENOTCONN` | `notify_notconn_streak`, `ble_connected` | `NOTCONN` (2) — **the node is contradicting itself.** The application believes it is connected while every send is refused. Wedge #2's signature (it reached 19 412). No dwell |
| — | attempts NOT advancing | `notify_attempt_total` | **never triggers.** Enumerated as `IDLE_NOT_A_FAULT` (3) so the distinction is explicit in code rather than implied by an absence |

Neither arm depends on any remote-visible producer counter. That is deliberate:
in all four fleet wedges `frames` and `imu_records` are telemetry-borne, so the
moment the criterion matters is the moment those inputs stop being observable.

## 1.1 — arm A now asserts its premise instead of assuming it

The old form rested on an unstated assumption: telemetry is 1 Hz and rides the
notify path, so a healthy board advances `notify_ok` at ≥ 1/s. A refactor could
break that silently and the guard would stop working with nothing reporting it
— the exact shape of the eight false verdicts already logged here.

`notify_attempt_total` is incremented once per attempted `bt_gatt_notify()`
regardless of outcome, at the single call site. Attempts advancing while
completions do not is a broken path; attempts not advancing is an idle board.

## 1.2 — arm B clears on three events, not one

`notify_notconn_streak` now resets on **any successful send** (already present),
**the `disconnected()` callback**, and **any connection-epoch change**. A normal
disconnect leaves a burst of `-ENOTCONN` behind it; without the last two, that
burst is still on the counter when the master reconnects and arm B could fire on
a healthy fresh link using evidence from a link that no longer exists.

## 1.3 — general requirements

Own thread (`bsf_recovery`, prio 7) — not sysworkq, MPSL Work, BT RX WQ, the
notify worker or the publisher. 10 s grace after connect. `k_uptime_get_32()`
with unsigned subtraction, correct across the 49.7-day wrap without a special
case. The CRC-protected witness is sealed **before** `sys_reboot()`.

## 1.4 — reset attribution: the complete call-site census

| # | site | intent |
|---|---|---|
| 1 | `bsf_recovery.c` guard cold reset | `RECOVERY_GUARD` |
| 2 | `bsf_v45.c` detector post-capture reboot | `V45_DETECTOR` |
| 3 | `main.c` stall-ring forward (from an ISR) | `RING_FWD` |
| 4 | `main.c` BT RX monitor wedge reboot | `BT_MONITOR` |
| 5 | `main.c` v41 stall recovery | `STALL_RECOVERY` |
| 6 | `main.c` `REBOOT` control command | `CMD_REBOOT` |
| 7 | `main.c` MCUboot confirmation timeout rollback | `BOOT_CONFIRM` |
| 8 | **SDK mcumgr os-group reset (DFU)** | `DFU` — **declared but NOT stampable** |

**Site 8 is an open gap, named rather than implied.**
`CONFIG_MCUMGR_GRP_OS_RESET_HOOK` is not enabled, so the application gets no
callback before mcumgr resets the device. A DFU-initiated reset will therefore
still count as `UNKNOWN_SREQ`. Enabling that hook is the fix and it is not done
here. **The Part 2 OTA is expected to produce exactly one `UNKNOWN_SREQ` for
this reason** — that is a prediction, and if the count rises by more than one
the gap is not the whole story.

No `sys_reboot()` remains outside `bsf_reset_now()`, which seals the intent and
then resets, so the ordering cannot be got wrong at a call site. Zero
`sys_reboot`/`NVIC_SystemReset` calls exist in the patched SDK files.

The contract test was **run before the call sites were converted and failed**,
naming all seven — then passed after conversion. A contract that has only ever
passed is not a gate.

## 1.5 — `hci_rx_pool` exhaustion: B1's first real test

`V45 RXPOOL[=hold_ms]`, validation builds only, default 30 s hold.

This is a **different pool** from `V45 LEAK`, which holds the singleton
`sync_evt_pool` buffer. B1 backports a fix for `bt_buf_get_rx(..., K_FOREVER)`
on the Controller→Host path, and only exhausting `hci_rx_pool` exercises it.
Phase A measured the wrong pool and its before/after cannot speak for B1.

Release is by delayed work, armed **before** the reply is sent — BLE ingress may
itself be behind a retained HCI message, so a commanded release could never
arrive.

Discriminator for the next run: on the unfixed build MPSL Work pends on the pool
and never returns; on v46 it must get `-ENOBUFS`, retain, **exit**, and resume
when buffers return. `rx_retained` non-zero plus a matched
`MPSL_WORK_ENTER`/`EXIT` pair separates the two directly.
