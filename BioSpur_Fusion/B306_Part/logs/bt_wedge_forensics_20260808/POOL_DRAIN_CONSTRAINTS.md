# POOL_DRAIN_CONSTRAINTS — §9, and the census that changes the answer

## 0. Correction that has to come first

`low_water` is **not** a continuous window minimum. `pool_low_water[]` is
written at exactly two sites in the firmware — `main.c:523/538` inside
`sample_pool_usage()`, and `main.c:3530` at init. There is no allocation hook
and no ISR fold-in, so

> `low_water` = `min(previous strobe's avail, this strobe's avail)`.

The pool telemetry is a **1 Hz strobe**, and a dip that opens and closes
between strobes is invisible. `COUNTER_SEMANTICS.md` §3 carries the same
correction.

Worse, the strobe is **biased**. `sample_pool_usage()` runs inside
`telemetry_work_handler()` on the **system workqueue** — the same
single-threaded queue that runs `tx_notify_process()` (which frees the ATT
buffers) and `tx_processor`. The strobe therefore fires right after a
completion pass has emptied `conn->tx_complete`. It systematically reads the
pool at its fullest.

That bias is not a theory. It is measurable, and measuring it is what §9
turned out to be about.

## 1. Timing at each onset, in NODE time

Master-time comparisons are contaminated by 10–50 ms of transport latency,
so all four rows use node clocks: `FUSION_POOL.node_ms` against the last
data record's own generation timestamp (`frame_us`/`base_us`).

| event | last strobe (node ms) | last record generated (node ms) | gap | all 8 pools at that strobe |
|---|---|---|---|---|
| N7 BSF6C53 | 2 539 388 | 2 540 284 | **896 ms** | full |
| N8 BSFEC35 | 1 986 026 | 1 986 647 | **621 ms** | full |
| N8 BSF1120 | 5 991 592 | 5 991 816 | **224 ms** | full |
| N8 BSF44AD | 16 730 475 | 16 731 890 | **1415 ms** | full |

"full" = `acl_tx 8/8, att 8/8, discardable 3/3, fragments 1/1, hci_cmd 2/2,
hci_rx 10/10, pkt 4/4, sync_evt 1/1` — identical on all four.

At the measured 31.4 notifications/s, draining `att_pool` from 8 to 0 takes
**≈258 ms** if completions stop dead. Three of the four gaps exceed that
comfortably; BSF1120's 224 ms does not, which would put the start of any
`att_pool` seizure within ~34 ms of a strobe that read the pool completely
full. Tight, but not a refutation — see §3.

## 2. The unbiased census — this is the actual §9 result

`FUSION_STALL_POOLS` is emitted whenever the master reads the stall
characteristic. That read is serviced **on the BT RX workqueue at an
arbitrary instant**, not on the system workqueue after a completion pass. It
is therefore an **unbiased sample of the same pools**, and there are 1 289 of
them across the campaign.

| run | strobes | `att_pool` avail < 8 | `att_pool` avail **== 0** | wedges |
|---|---|---|---|---|
| **N5** (v43, 54.0 bh) | 648 | 102 (15.7 %) | **80 (12.3 %)** | **0** |
| **N7** (v43, 5.8 bh) | 70 | 6 (8.6 %) | 4 (5.7 %) | 1 |
| **N8** (v44, 47.2 bh) | 571 | 5 (0.9 %) | 3 (0.5 %) | 3 |

By node, `att_pool == 0`: N5 — BSFAA61 ×72, BSFC2CC ×7, BSF6C53 ×1;
N7 — BSFAA61 ×2, BSFB165 ×2; N8 — BSF1120, BSFEC35, BSFB165 ×1 each
(all three at 15:41, during the connect/subscribe burst at run open).

Two things follow, and they point the same way:

1. **The 1 Hz strobe's bias is real and large.** Every one of the 194 255
   scheduled `FUSION_POOL` records in N5 reads `att_pool 8/8`; the
   unbiased strobe on the same boards in the same run reads it **empty 12 %
   of the time**. Any §9 argument built on the scheduled record alone is
   built on a biased instrument.
2. **`att_pool` exhaustion is a common, transient, benign condition.** It is
   **25× more frequent in the run that produced zero wedges** than in the run
   that produced three. On BSFAA61 in N5 it was caught in 72 of ~72 of that
   board's strobes, across six hours, with no wedge in any run.

## 3. Verdicts, one per pool, per the required template

| pool | verdict |
|---|---|
| `att_pool` (8) | **Refutes a progressive leak** (full at every scheduled strobe for 107 board-hours, and the counters `q_hwm`, `enq`, `publisher_count` track exactly). **Permits a sub-second seizure** at every event (gaps 224–1415 ms vs a 258 ms drain). But the census above shows the seizure state itself is *ordinary and self-clearing*, so "att_pool hit zero" cannot be the explanation — only "att_pool hit zero **and never recovered**" can, which relocates the question to the completion source. |
| `acl_tx_pool` (8) | Same shape, same verdict; never observed below 8 in any unbiased strobe. |
| `hci_cmd_pool` (2) | Never observed below 2 in **any** strobe, scheduled or unbiased, in 107 board-hours. No evidence for the `bt_hci_cmd_create()` `K_FOREVER` path (DATAFLOW_MAP §4.1), and no evidence against it either — 2 buffers can be taken and returned inside one strobe interval. **INSUFFICIENT.** |
| `hci_rx_pool` (10) | **Never observed below 10, anywhere, ever** — 1 289 unbiased strobes plus 578 908 scheduled records across four runs. Combined with §4 below: **undecidable from raw data, v45 holder sampling required.** Not a rank change. |
| `discardable_pool` (3), `fragments` (1), `sync_evt_pool` (1), `pkt_pool` (4) | Always full. No role. |

## 4. RX-side model — H3 has no holder

`hci_rx_pool` = MAX(EVT 10, ACL 6) = 10. For it to be exhausted, ten buffers
must be *held*:

- The BT RX WQ holds at most one at a time, and if it is idle it holds none.
- `CONFIG_BT_MAX_CONN=1`, so there is exactly one `conn->rx` reassembly slot.
- Inbound PDUs never fragment: the largest inbound operation in the entire
  campaign is a 24-byte control write (`BSFxxxx QUEUE PUB HIST=0`, len=24)
  against an ATT MTU of 247 and a negotiated DLE of 251 bytes
  (`FUSION_DLE_UPDATED tx_len=251 rx_len=251`). Nothing inbound ever needs a
  second L2CAP fragment, so `conn->rx` can never hold a buffer across
  packets.
- Empty LL PDUs — which is what the peripheral actually receives 20 times a
  second — allocate no host buffer at all.

**There is no identifiable holder in this capture.** The honest verdict is
"undecidable; v45 must sample holders", exactly as pre-registered.

What *would* count as holder evidence, stated so a future run can falsify
this: any delivered record, scheduled or stall-strobed, showing
`hci_rx_pool avail < 10` on any board. Zero such records exist today.

## 5. Buffer cost of one notification, source-verified

One `FUSION_UWB` notification is a 184-byte payload
(`sizeof(bsf_ble_uwb_packet_t)`, compiled against the shipped header); one
IMU batch is 150 bytes (`10 + 10×14`); telemetry 243; pool 140; queue 58.
ATT MTU is 247 and DLE is 251/251, so **every record fits in a single ATT PDU
and a single ACL packet — no fragmentation, no second buffer**. The cost of
one notification is therefore exactly: one `att_pool` buffer (`att.c:765`),
held until `chan_sent_cb` unrefs it from `tx_notify_process()` on the system
workqueue, plus one `bt_conn_tx` slot from the 8-deep `free_tx` FIFO
(`CONFIG_BT_CONN_TX_MAX=8`), released by `tx_free()` in the same place.

Both are released only by TX-completion processing. **Both are 8 deep. Both
drain in ≈258 ms at 31.4 notif/s if completions stop.** They are two names
for the same 258 ms fuse.
