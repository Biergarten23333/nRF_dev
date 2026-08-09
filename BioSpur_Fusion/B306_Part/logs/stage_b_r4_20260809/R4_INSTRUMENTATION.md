# R4 — INSTRUMENTATION

## On the next wedge, what does the corpse now answer that it could not before?

**Four questions, all of which the 2026-08-09 wedge left unanswerable:**

1. **"Which line released the connection?"** — every one of the 24
   `bt_conn_set_state()` call sites now stamps its id, the old state, the new
   state and the uptime into `.noinit`, plus a per-site count. Last time the dump
   could only say *that* `acl_conns[0]` was `DISCONNECTED, ref=0`; it could not
   say who did it, and the log that would have said was never stored.
2. **"Did the fatal-disconnect path fire, and with what errno?"** —
   `BSF_V45_TX_FATAL_DISCONNECT` records `err` and the uptime at
   `conn.c` `tx_processor()`, the line that turns one failed send into a dead
   connection with no retry. Last time this was the strongest candidate and could
   not be confirmed.
3. **"Why did the send fail?"** — `BSF_V45_TX_SEND_FAIL` records which of
   `send_buf()`'s three distinct failure returns fired (`-EMSGSIZE`, `-EIO`,
   `-ENOMEM`). "The link died because a send failed" and "the send failed
   because X" are different facts and only the second is actionable.
4. **"Would anything have caught it sooner?"** — two new triggers now would.

## A1 — the fatal-disconnect path, and its origin

| where | mark | records |
|---|---|---|
| `conn.c` `tx_processor()` fatal branch | `BSF_V45_TX_FATAL_DISCONNECT` (40) | `arg0 = err`, `arg1 = uptime_ms`, counter `conn_fatal_disconnects` |
| `send_buf()` `buf->len == 0` | `BSF_V45_TX_SEND_FAIL` (39) | `arg1 = SEND_SITE_EMSGSIZE`, counter `send_fail_emsgsize` |
| `send_buf()` `bt_buf_has_view()` | `BSF_V45_TX_SEND_FAIL` | `arg1 = SEND_SITE_EIO`, counter `send_fail_eio` |
| `send_buf()` no controller buffer | `BSF_V45_TX_SEND_FAIL` | `arg1 = SEND_SITE_ENOMEM_PKTS`, counter `send_fail_enomem` |

**Channel: `BSF_V45_CH_TX_WORK`, determined not assumed.** `DATAFLOW_MAP.md`
§2 and its thread table put `tx_processor` on `k_sys_work_q`, and `conn.c`'s own
header comment states that file owns `CH_TX_WORK` for exactly that context. So
the single-writer rule holds and the marks cannot be dropped as writer
mismatches.

### What A1's origin instrumentation can and cannot see

It can see **which of `send_buf()`'s own three returns fired**. That is the
boundary of what is visible from the host.

It **cannot** see why the controller refused, when the origin is below that
line. `-ENOMEM` at the "no controller bufs" site means `k_sem_take()` on the ACL
credit semaphore failed with `K_NO_WAIT`; the reason the credit was unavailable
lives in the SDC HCI driver and the controller, which are closed. The 2026-08-09
dump measured credits at **4 of 6**, so that site firing would mean a transient
re-entrancy or accounting fault, not sustained starvation — but the marker can
only report *that* it fired, not the controller's internal cause.

`-EIO` is likewise a symptom: `bt_buf_has_view()` true means someone left a view
attached to the buffer, and the marker names the check, not the leaver.

## A2 — which line released the connection

24 call sites (7 in `adv.c`, 17 in `conn.c`), each wrapped:

```c
#define BSF_SET_STATE(c, st, site) \
	do { bsf_v45_conn_state_note((site), (uint8_t)(c)->state, (uint8_t)(st)); \
	     bt_conn_set_state((c), (st)); } while (0)
```

**Recorded into atomics and a plain struct, NOT a trace channel.**
`bt_conn_set_state()` is reached from several threads, so a channel mark would
break the single-writer rule and be dropped as a mismatch. One saturating
per-site counter plus one struct store is context-safe from anywhere, and is
what "a single-word store plus a counter, no logging" asked for. Both live in
`.noinit` and are copied by value into the corpse at capture.

**The site list is GENERATED**, from the patched sources — the ones that
actually compile. Two earlier hand-counts were wrong (16 from a truncated grep,
then 23 from the pristine sources, before the existing v45 patch adds a site).
It is 24. `BSF_V45_CONN_SITE__MAX` is 32 for headroom.

## A3 — the self-contradiction trigger, `CAUSE_CONN_RELEASED` (6)

Fires on `BSF_V45_NOTCONN_STREAK = 320` consecutive `-ENOTCONN` from
`bt_gatt_notify()` while the application still believes it is connected. **No
dwell**: this is not a slow symptom, it is two halves of the same node
disagreeing.

**Why 320.** A legitimate disconnect clears `ble_connected` from the
`disconnected` callback within milliseconds, so an honest race is a handful of
calls. At the measured ~32 notifies/s, 320 is ~10 s — two orders of magnitude
past any race, still twice as fast as the 20 s dwell arms. The 2026-08-09 wedge
produced **19 412** consecutive failures: a 60× margin.

The streak resets on any other outcome, including success, so transient errors
cannot accumulate across minutes of healthy work.

## A4 — third watermark, `CAUSE_NOTIFY_OK` (5)

`publisher_count` advancing while `notify_ok` is frozen, same 20 s dwell.
Watches **delivery** rather than return-from-call, so it catches the
fast-failing-sink class whatever the errno.

This is the arm that would have caught 2026-08-09 on its merits:
`notify_exit_total` kept advancing at ~32/s throughout, so arm A could never
have fired at any dwell, and arm B would have caught it only via the ncp
watermark twenty seconds after the last completed packet.

## A5 — log buffer: evaluated, SKIPPED, and the assumed reason was wrong

Not `log_mpsc_pbuf_area` at 64 B — that is a control block. The real
configuration is `CONFIG_LOG_BUFFER_SIZE=1024`, `LOG_MODE_DEFERRED`, drained
continuously by `CONFIG_LOG_BACKEND_RTT`.

Reading `_acUpBuffer` (1 KB) out of the wedge dump **does** recover text — but
only the boot banner, ending in `--- 11 messages dropped ---`. With no host
attached, RTT fills once and skips the rest.

**So raising the buffer would not have helped.** The deferred buffer is drained;
RTT drops rather than overwrites. The fix is operational and probe-gated:
attach `tools/jlink_rtt_transport.py` for an observation window. Written into
`SWD_WEDGED_BOARD_RUNBOOK.md` as an OPTIONAL step, **not executed** — RTT runs
over J-Link and is `PROBE GO`-gated like any other SWD action.

## No silent no-op — three independent layers

The old `__has_include` fallback meant an unpatched or reinstalled SDK produced
an image that **built, ran, captured corpses, and put nothing in them**.

| layer | catches |
|---|---|
| existing CMake gate calling `sdk_patch.sh verify` | SDK unpatched, or any of the 7 files hash-drifted |
| **new**: SDK translation units `#error` instead of defining no-ops | the header not reachable from the SDK units specifically — the case where the app compiles fine and only the SDK marks vanish |
| **new**: `BSF_V45_SDK_PATCH_VERSION` stamped into `<zephyr/bluetooth/conn.h>`, checked in `bsf_v45.c` | unpatched (macro absent) or stale patch (wrong value), as a hard compile error |

## Patch manager

One merged patch, `ncs-v2.8.0-bsf-v45-r4-instrumentation.patch`, **7 files** —
the previous 5 plus `adv.c` and `zephyr/include/zephyr/bluetooth/conn.h`. It
supersedes the two earlier patch files, which stay in the repository for
provenance and are no longer applied.

```
SDK_PATCH_SELFTEST ok apply=pass verify=pass revert=pass reapply=pass files=7
```
