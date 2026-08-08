# TERMINAL_NOTIFICATION_ANALYSIS — §8, plus §4.x and §4.y

## 1. The last eight records, per event

Payload sizes are `sizeof()` on the shipped wire header: UWB 184 B,
IMU batch-of-10 150 B, telemetry 243 B, pool 140 B, queue 58 B. ATT MTU 247,
DLE 251/251 — **every record is one ATT PDU in one ACL packet, never
fragmented.**

| event | last record | stream | bytes | seq / sweep | node µs |
|---|---|---|---|---|---|
| N7 BSF6C53 | 12:16:23.010 | **IMU** | 150 | seq 48843 | 2 540 284 132 |
| N8 BSFEC35 | 15:46:08.520 | **UWB** | 184 | sweep 16550 | 1 986 647 163 |
| N8 BSF1120 | 16:53:08.532 | **UWB** | 184 | sweep 49925 | 5 991 816 213 |
| N8 BSF44AD | 19:51:58.732 | **UWB** | 184 | sweep 139425 | 16 731 890 029 |

Preceding records in every case alternate IMU/UWB at the normal 20 Hz / 8.33 Hz
cadence with no anomaly of any kind. Stream mix over the last 256 records:
180–181 IMU / 75–76 UWB on all four — the exact steady-state ratio.

**No shared terminal stream** (one IMU, three UWB, matching the 70/30 mix by
chance). **No large batch, no control reply, no burst, no stream transition.**

## 2. Connection-event packing

Records clustered by `master_ms mod 50 ms`:

| event | conn events in last 256 records | notif/event mean | max | anchor phase (mod 50) |
|---|---|---|---|---|
| N7 BSF6C53 | 154 | 1.662 | 3 | 0–2 ms |
| N8 BSFEC35 | 169 | 1.515 | 3 | 32–34 ms |
| N8 BSF1120 | 171 | 1.497 | 3 | 37–39 ms |
| N8 BSF44AD | 143 | 1.790 | 3 | 2–4 ms |

Matched same-window controls (6 per event) give 1.45–2.03 notif/event, max 3.
**Every event sits inside its own control range**; the anchor phases are four
different slots. Neither packing nor phase is shared.

## 3. §4.x Fine-grain simultaneity — the load-bearing measurement

Exact reception times of the last record of each stream (`onset_lower` is
the last record of either stream, so these straddle zero by ≤0.5 ms):

| event | last IMU | last UWB | **separation** | last TELEMETRY / QUEUE / POOL (1 Hz) |
|---|---|---|---|---|
| N7 BSF6C53 | −1.0 ms | +0.2 ms | **1.2 ms** | −948 / −902 / −900 ms |
| N8 BSFEC35 | +0.3 ms | −0.2 ms | **0.5 ms** | −1608 / −1562 / −1560 ms |
| N8 BSF1120 | −1.6 ms | −0.4 ms | **1.3 ms** | −248 / −248 / −247 ms |
| N8 BSF44AD | −1.7 ms | −0.3 ms | **1.4 ms** | −776 / −1774 / −1774 ms |

The 1 Hz streams cannot resolve better than their own cadence and their
offsets are pure cadence phase, not information. The informative measurement
is IMU vs UWB, both continuous:

> **The two data streams stop 0.5–1.4 milliseconds apart — inside a single
> 50 ms connection interval, in the same connection event.**

The full interleave is unbroken to the last millisecond. BSF44AD, typical of
all four (ms before onset): UWB −351, IMU −350, −302, −202, UWB −199,
IMU −198, −152, UWB −150, IMU −102, −52, −1.7, UWB −0.3. Both cadences —
50 ms IMU, 120 ms UWB — are exactly on schedule right up to the edge.

Record sequence numbers are also perfectly continuous into the freeze: IMU
`seq` steps by exactly 10 (one batch) on every one of the last twelve records
of every event, UWB `sweep` by exactly 1. **Not one sample or sweep was lost
before the stop.**

> **Both data streams and the control stream stop within a single 50 ms
> connection interval.** The freeze is below the per-stream `k_msgq`s, at the
> single shared conduit — the publisher/notify-worker pair and everything
> under it. Per the pre-registration this separates "below the queues" from
> "above the queues" **and nothing finer**; it does not discriminate H1 from
> H2 from H3 from H4.

## 4. §4.y Controller-drain tail — and why the axis failed

| event | last-5 mean gap | last-50 mean gap | steady-state mean gap | back-to-back records at the end | last record → onset |
|---|---|---|---|---|---|
| N7 BSF6C53 | 49.7 ms | 35.0 ms | 35.3 ms | **0** | 1.0 ms |
| N8 BSFEC35 | 40.3 | 36.0 | 35.3 | **0** | 0.22 ms |
| N8 BSF1120 | 40.3 | 36.0 | 35.3 | **1** | 0.35 ms |
| N8 BSF44AD | 30.4 | 35.0 | 35.3 | **1** | 0.29 ms |

Controls in the same windows: 0–2 back-to-back records. The steady-state mean
inter-arrival is 1/(20 + 8.33 + ~3) = 35.3 ms and the observed last-50 mean is
35.0–36.0 ms on every event.

**Zero drain tail. The stream stops mid-cadence with at most one record in
flight, exactly like a healthy node's last 50 records.**

The pre-registration expected H1 to produce "0–8 further records over
≈300 ms" and treated a zero tail as evidence for H4. **That expectation was
wrong and the axis turns out not to discriminate at all.** The reason is
arithmetic that should have been done in advance: eight `att_pool` buffers at
1.5 notifications per connection event is ~5 connection events = ~250 ms of
output *at the normal cadence*. A drain of the full pool is therefore
**indistinguishable from normal delivery** at this record rate. The last
eight records of every event already are the tail; they simply do not look
like one.

What the zero tail *does* establish is narrower and still useful: **nothing
was backed up.** There is no burst, no compression, no latency spike in the
final records — so at onset the pipeline below the notify worker was not
congested. Combined with §5 below, the node froze with an empty pipeline.

## 5. Node internal state 96 ms before BSFEC35 froze

The single most informative record in the dataset. The master's stall read at
`onset − 96 ms` returned a complete 232-byte payload:

```
FUSION_STALL_READ name=BSFEC35 att_err=0 len=232 v=2 reason=0 armed=1
  sample_ms=1986026  e=61951 x=61951  entry_ms=1986013 exit_ms=1986013 age=0
  stream=0  rc=0  rcc=61951/0/0/0/0  q=0/0/0  qd=0/0/0  td=0/0/0
  hb=56212  alarm=0@0  recovery=0
FUSION_STALL_POOLS name=BSFEC35 count=8 usage=1 sent_cb=0
  pool0=11597b73(acl_tx):8/8  pool1=858969d7(att):8/8
  pool2=a14875f8(discardable):3/3  pool3=2de570ea(fragments):1/1
  pool4=39b3fc03(hci_cmd):2/2
```

Decoded (`COUNTER_SEMANTICS.md`):

| field | value | meaning |
|---|---|---|
| `e = x = 61951` | equal | the notify worker was **idle** — not inside `bt_gatt_notify()` |
| `q=0/0/0` | ctl/uwb/imu | all three publisher queues **empty** |
| `qd=0/0/0` | | zero queue drops, ever |
| `td=0/0/0` | | `notify_timeout_drop` zero, ever — **`bt_gatt_notify()` had never once blocked longer than the 1200 ms accept timeout on this board** |
| `rcc=61951/0/0/0/0` | ok/nomem/notconn/again/other | **every** `bt_gatt_notify()` in this board's life returned 0 |
| `alarm=0 recovery=0` | | the node's own stall detector saw nothing |
| pools | all full | at this unbiased, BT-RX-WQ-serviced instant |

Ninety-six milliseconds and about three notifications later, the board stopped
forever.

The same board's *previous* stall read, 300 s earlier, is the control that
proves the instrument works:
`e=52529 x=52528 age=1 stream=2` — caught mid-call on the IMU stream, 1 ms in
— and `pool1=858969d7:0/0`, i.e. **`att_pool` completely empty at that
instant**, with full recovery and no consequence. See
`POOL_DRAIN_CONSTRAINTS.md` §2.

The last successful stall reads on the other three (minutes rather than
milliseconds before onset) say the same thing: `e==x`, `q=0/0/0`, `td=0/0/0`,
`rcc` all-success. BSF1120's carries `qd=3/0/0` — three lifetime control-queue
drops, consistent with its `q_drop_ctl=3`, and nothing else.
