# v46 — root cause and fix

## The deadlock (fixed by B1)

`bt_buf_get_rx(..., K_FOREVER)` on the Controller→Host RX path blocks the MPSL
work queue. Once the host RX pool is empty, MPSL Work parks inside the
allocation and processes nothing further — including the work that would free
buffers. The link layer keeps running, so the connection stays up and the
master keeps seeing QoS reports, while delivery is dead. That is the observed
phenotype exactly.

Nordic fixed this class after 2.8.0 in sdk-nrf
`52b63957ada504114ca3b330f7627708124509e8` ("bluetooth: hci_driver: Fix deadlock
in MPSL workq"), whose commit message names `bt_buf_get_rx` with `K_FOREVER`
blocking the MPSL work queue as the cause. Backported here onto NCS v2.8.0. No
NCS 3.x migration.

### Verified, not assumed

- **`SDC_HCI_MSG_TYPE_NONE` does not exist in nrfxlib v2.8.0.** The enum is
  `DATA=0x02, EVT=0x04, ISO=0x08`, so 0x00 is free. A local
  `BSF_V46_MSG_TYPE_NONE` zero sentinel is used, with a `BUILD_ASSERT` that it
  cannot collide with any real value. If a future nrfxlib adds a zero
  enumerator this fails to compile instead of silently treating a real message
  as "nothing retained".
- **The Zephyr companion was genuinely missing.** `bt_buf_rx_freed_cb_set` does
  not exist in NCS v2.8.0's Zephyr, so zephyr
  `c2488fdd3021bacb5c8f2cc4a6fe43cb2d0515d2` had to be backported as well.
  Without it there is nothing to resubmit the receive worker and the deadlock
  is merely relocated to a permanently parked worker.
- **The ISR-safety correction IS required.** `net_buf`'s destroy hook runs on
  the last unref, in whatever context that unref happened — not guaranteed to
  be a thread. The freed handler therefore does nothing but test the sentinel
  and call `receive_signal_raise()`, which wraps `k_work_submit()` and is
  ISR-safe and idempotent for an already-queued item.
- **`CONFIG_BT_HCI_ACL_FLOW_CONTROL=n` on this build**, so one shared
  `hci_rx_pool` backs both `BT_BUF_EVT` and `BT_BUF_ACL_IN`. The destroy hook
  therefore reports `BT_BUF_EVT | BT_BUF_ACL_IN`; reporting one alone would
  leave the other waiter parked, which is the same bug in a new place.

### The message is retained, never dropped

On `-ENOBUFS` the fetched message stays in the driver's static buffer and
`retained_msg_type` records its type; the next entry re-processes it *before*
any new fetch. Dropping instead would silently lose an ACL fragment and
desynchronise reassembly — a worse failure than the deadlock, and quieter.
The sentinel is cleared exactly once, only after successful delivery.

## The guard (B2) — and what it does NOT cover

Trigger: `connected ∧ subscribed ∧ notify_ok frozen ≥ 12 s`, plus a faster arm
on `≥ 320` consecutive `-ENOTCONN` while the application believes it is
connected. Action: CRC-protected `.noinit` witness, bounded node-derived
jitter, then `sys_reboot(SYS_REBOOT_COLD)`.

**No producer term**, deliberately. In all four fleet wedges the producer
counters are themselves telemetry-borne, so the moment the criterion matters is
the moment both inputs stop being observable; requiring the producer would have
missed four of the five known events. Telemetry is a 1 Hz heartbeat on the same
notify path, so frozen delivery on a connected subscribed link is sufficient.

**Not `bt_conn_disconnect()`.** Phase A invariant 4 measured it: a wedged node
that is disconnected does not re-advertise — zero advertising reports across
130 s, no reconnection after the master returned to RECV. Disconnecting turns a
wedged-but-reachable node into a wedged-and-invisible one.

## TWO LINES OF WORK. NEITHER IS EVIDENCE FOR THE OTHER.

**Wedge #2 is not explained by the backport.** Its terminal state was the host
having released its `conn` object entirely (state DISCONNECTED, `ref=0`) while
the controller still held the link, with `disconnected()` never run and 19 412
consecutive `-ENOTCONN`. That is not a blocked MPSL work queue and the
`K_FOREVER` fix does not address it. It is covered **only** by the guard's
second arm.

So: B1 removes a proven deadlock mechanism; B2 provides recovery for anything
that still wedges, including mechanisms not yet identified. A clean fleet run
does not prove B1 fixed the fleet wedges — see the acceptance note on natural
guard resets.

## Status of the phenotype match

Phase A reproduced the wedge on the unfixed build and the phenotype matches the
four fleet wedges closely: nominal delivery at ~31.3/s to the last sample, then
an abrupt latch, link alive throughout, no error counters moving. **Matching
phenotypes are consistent with a shared cause, not proof of one.** The fleet
wedges' terminal states remain unobservable past onset because telemetry rides
the path that fails.

## Standing statement on B1's scope (v46r2, verbatim)

> B1 is a correct backport of the fix for a known NCS 2.8.0 MPSL/HCI-RX
> permanent-blocking defect. It may explain some or most of the fleet wedges. It
> is not proven that all four historical events were caused by it, and it does
> not explain the observed host/controller split-brain terminal state (wedge
> #2). The two lines run in parallel; neither is evidence for the other.
