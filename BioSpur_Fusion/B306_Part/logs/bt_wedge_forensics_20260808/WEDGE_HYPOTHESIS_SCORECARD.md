# WEDGE_HYPOTHESIS_SCORECARD — §13

Scored against `PREREGISTERED_SIGNATURES.md`, which was committed before any
of §4–§10 was computed and has not been edited since.

## 1. Where the pre-registration was wrong

Recorded here rather than fixed in the matrix, as required.

| axis | pre-registered expectation | what happened |
|---|---|---|
| (f) controller-drain tail | H1 predicts a 0–8-record burst over ≈300 ms; a **zero tail refutes H1 and supports H4** | **The axis does not discriminate at all.** Eight `att_pool` buffers at 1.5 notifications per connection event is ≈5 connection events ≈250 ms of output *at the normal cadence*. A full-pool drain is arithmetically indistinguishable from healthy delivery at 28 records/s. The arithmetic should have been done in advance. The observed zero tail therefore scores nothing for or against H1/H2/H3, and only weakly for "nothing was backed up". |
| (g) near-misses | H1 predicts a graded tail | Correct as stated, and it fired: **zero near-misses of the wedge phenotype in 107 bh.** But §2 below shows the graded tail *does* exist — it is just not visible in the delivery stream, because every instance recovers inside one second. The axis was right about the mechanism and wrong about where to look. |
| (c) pools | the 1 Hz record's `low_water` treated as a per-second window minimum | It is a **two-point 1 Hz strobe** with no sub-second observer, and it is **biased** — sampled on the same system workqueue that frees the buffers, right after it frees them. `POOL_DRAIN_CONSTRAINTS.md` §0. |

## 2. The finding that reorganises everything

`att_pool` exhaustion is **common, transient and benign**, and the fleet
proves it:

| run | unbiased stall strobes | `att_pool == 0` | wedges |
|---|---|---|---|
| **N5** | 648 | **80 (12.3 %)** | **0** |
| N7 | 70 | 4 (5.7 %) | 1 |
| N8 | 571 | 3 (0.5 %) | 3 |

And `bt_gatt_notify()` calls of 100–400 ms — the direct consequence of an
empty `att_pool` — happen **routinely**: 30+ in-run instances across the
campaign on healthy boards (BSF31CC 7, BSF3C79 5, BSF6C53 4, BSFAA61 4+2,
BSFB165 2+2, BSFEC35 3+1, BSFC2CC 3+3, BSF8BC4 3, BSF1120 1+1, BSF44AD 2),
with a maximum of 400 ms in-run and 4.1 s during DFU. **Every one of them
returned.**

> The resource-contention process that H1 describes **demonstrably exists,
> is 25× more frequent in the run with zero wedges, and always recovers.**
> The wedge is therefore not "that process, but longer". It is the failure of
> *recovery* — the completion source stopping permanently — or something
> else entirely.

## 3. Scorecard

Legend: **++** strongly supports · **+** supports · **·** neutral/unobservable
· **−** contradicts · **−−** strongly contradicts.

| axis | H1 TX-completion stop / ATT seizure | H2 BT RX WQ block | H3 MPSL/`hci_rx_pool` | H4 notify-worker-only | H5 external | H6 depletion |
|---|---|---|---|---|---|---|
| (a) inbound idle 20–45 s on 3/4 | **+** | **−−** | **−−** | + | · | · |
| (b) stationary-then-cliff, no ramp vs controls | + | + | + | **−−** | − | − |
| (c) pools full at last strobe; drain feasible in 3/4, marginal in 1/4 | + | · | **·** no holder | − | · | · |
| (c′) `att_pool==0` 25× commoner in the zero-wedge run | **−** | · | · | − | · | · |
| (d) no shared terminal stream, packing or phase | · | · | · | **−** (predicted ctl-heavy) | **−** | · |
| (e) IMU/UWB stop 0.5–1.4 ms apart | + | + | + | + | − | · |
| (f) zero drain tail | · | · | · | · | **−** | · |
| (g) zero near-misses of the phenotype | **−** | **+** | **+** | − | − | · |
| (h) no necessary condition; rate confounded | · | · | · | · | **−** | **−−** |
| (i) 0/4 IMU-recovery coincidence | · | · | · | **−** | · | · |
| syswq proven alive (watchdog, 4/4) | **−** for the blocked-syswq variant | **−** same | · | · | · | · |
| `e==x`, `q=0/0/0`, `td=0`, `rcc` all-ok at t−96 ms | + | + | + | **−−** | · | · |
| link alive 615 s – 4 h 38 min, `crc_ok` 16–20/window | · | · | · | · | **−−** | **−−** |
| air ratio 0.96–1.10 | · | · | · | · | · | **−−** |

## 4. Ranked verdict

**1. H1′ — TX-completion processing stops permanently, and the notify worker
parks in an unbounded allocation. RANK 1, and it is the only hypothesis
consistent with every observation.**

The mechanism, stated precisely because the loose version has already been
refuted: it is *not* "`att_pool` ran out". It is "**the thing that refills
`att_pool` stopped**". Both unbounded waits reachable from
`bt_gatt_notify()` — `bt_l2cap_create_pdu_timeout(&att_pool, 0, K_FOREVER)`
at `att.c:747`, and the `bt_conn_tx` allocation from the 8-deep `free_tx`
FIFO — are released **only** by `tx_notify_process()` on the system
workqueue, driven **only** by HCI Number-Of-Completed-Packets events arriving
on the MPSL workqueue (`DATAFLOW_MAP.md` §3). Everything downstream is a
consequence:

- the notify worker parks → all four streams stop **in the same connection
  event** (0.5–1.4 ms), because they share that one worker (axis e ✓);
- the inbound ATT read is received and processed, but its response needs
  `net_buf_alloc(&att_pool, BT_ATT_TIMEOUT)` = 30 s → returns NULL → **no
  response is ever sent**, and the master's own 25 s bound fires first. The
  observed timeout is **exactly 25 001 ms on all four events** (axis: the
  25 s figure is the *master's* timer, and the node's 30 s ATT timeout never
  gets the chance to expire) ✓;
- the master's bearer then never recovers → every later operation returns
  `-ENOMEM` ✓;
- the LL keeps ACKing, `crc_ok` 16–20 per window, for hours ✓;
- the system workqueue stays healthy — it has nothing to do, not nothing it
  *can* do — so the watchdog is fed and the board never resets ✓;
- a power cycle clears it — observed once (BSFEC35 reappeared at 21:13:56
  after a brownout, 3 h 46 min after onset) and never contradicted ✓.

**What H1′ does not explain, and this must be said plainly: why the
completion source stops, and why it never restarts.** Nothing in this capture
observes the MPSL/SDC side. That is the gap v45 has to close.

**2. H2 — BT RX workqueue block. RANK 2.**

Supported by the near-miss absence (a `K_FOREVER` on `hci_cmd_pool`, 2 deep,
either happens or does not) and by the no-re-advertise observation, which
`DATAFLOW_MAP.md` §5 localises to "the BT RX WQ never reached or never
returned from `start_advertising()`" — and nothing narrower, per §0.3.

Contradicted hard by the activity census: **the BT RX WQ only runs when
something inbound arrives, and on 3 of 4 events nothing inbound had arrived
for 20–45 s.** For H2 the block must have been *entered*, and there is
nothing to enter it with. `hci_cmd_pool` was never observed below 2/2 in
1 289 unbiased strobes. And on BSFEC35 the BT RX WQ demonstrably completed a
full ATT read-request/response cycle **96 ms before the freeze**.

**3. H3 — MPSL receive-side / `hci_rx_pool`. RANK 3, and undecidable.**

Pre-registered as undecidable and it stayed that way. `hci_rx_pool` was never
observed below 10/10 anywhere in 107 board-hours, and **no holder exists**:
BT RX WQ idle holds none, `BT_MAX_CONN=1`, and no inbound PDU ever fragments
(largest inbound operation is 24 bytes against MTU 247 / DLE 251). Cannot be
promoted or demoted by this data. Note that H1′ and H3 are not fully
disjoint — "the MPSL receive path stops delivering events" *is* an H3-flavour
cause of H1′.

**4. H4 — notify-worker-only / producer deadlock. REFUTED.**

`e == x` (worker idle), `q = 0/0/0` (all queues empty), `td = 0/0/0`
(`bt_gatt_notify` had *never* exceeded 1200 ms on that board),
`rcc = 61951/0/0/0/0` (every call ever returned 0) — 96 ms before BSFEC35
froze. Backlog residual is **exactly 1 record** on all four events. There was
no contention to deadlock on, no ramp (axis b), and the predicted ctl-heavy
terminal record does not appear.

**5. H5 — external. REFUTED.**

Eight or nine matched control nodes are unaffected in every window; the
master's own `FUSION_HEALTH` is clean; `crc_error` and `nak` are ≈0 through
the wedge; the tag keeps transmitting at ratio 0.96–1.10; RSSI −66…−71 dBm;
`uart_restarts` is **zero on every board in every run**.

**6. H6 — depletion. REFUTED for these four, and it is the correct label for
11 other events.** Air ratio 0.96–1.10 vs 0.001–0.185 for the depletion
class, with no overlap and a 5× gap; link survival 615 s – 4 h 38 min;
BSF44AD wedged 58 minutes before the first depletion casualty.

## 5. The honesty constraint

Raw host-side logs cannot localise an internal thread, and this analysis does
not claim to have. What it has produced is the four realistic outcomes the
brief listed, in the order they came out:

1. **A trigger correlation** — searched for and **not found** (§7.1 base
   rates, §7.2 base rates). The one 1-of-4 coincidence at p≈0.04 is named and
   priced.
2. **A deterministic counter boundary** — searched for and **not found**
   (§10).
3. **A terminal signature plus an activity census that selects the v45
   branch** — **found.** The branch is: instrument the completion source, not
   the pools.
4. **An N5 false zero that rewrites the conditions table** — the zero is
   **real**, and it is statistically unremarkable (P = 0.133), so it rewrites
   nothing.

And one outcome the brief did not anticipate: **the `att_pool`-exhaustion
census inverted the leading hypothesis' own premise.** The state everyone
assumed was the fault is 25× more common in the run that never failed.
