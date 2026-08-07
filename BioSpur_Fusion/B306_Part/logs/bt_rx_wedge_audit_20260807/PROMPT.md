# K1 (rev. 2) — the config symbols, and what BT RX is stuck in

**Batch:** `bt_rx_wedge_audit_20260807` · Evidence root:
`B306_Part/logs/bt_rx_wedge_audit_20260807/` · Copy this file to `<evidence root>/PROMPT.md`.

**Supersedes the earlier K1 draft.** It was written before J1 returned; J1's verdict changes the
scope, and the changes are marked below.

**Offline source and config reading only. No hardware, no commands, no J-Link, no deployment, no
code changes.** BSF44AD is powered on a charging POGO that keeps it live; nothing here goes near it.

---

## 1. What J1 settled, and what it therefore points at

`atomic_clear(&ble_connected)` is **statement 19 of 26** in v41's `disconnected()` — it moved from
statement 3 in v36, so formally the ordering did regress. **But nothing ahead of it can block**:
`LOG_INF` is non-blocking through three independent stages, `k_work_cancel_delayable` is the async
variant, and both spinlocks are BASEPRI raises on a single core, so the thread and the ring ISR can
never wait on each other.

**Therefore: PAIRED lit ⟹ statement 19 was never reached ⟹ the BT RX thread is wedged *upstream of
callback dispatch*.**

**That is the scope change.** The thread is not stuck inside `disconnected()`. It is stuck somewhere
it entered earlier — in another callback, or inside the host stack's own RX processing, before it
ever got to dispatching the disconnect event.

**And that is exactly the shape of DRGN-23518**, which is why the priority order below has changed.

## 2. K1 — the config audit — now the direct implication, not just the cheapest check

Read from the **canonical build's `.config`**, not `prj.conf` — defaults and dependencies resolve
only at configure time.

Report every one of:

```
CONFIG_BT_MAX_CONN
CONFIG_BT_BUF_ACL_RX_COUNT
CONFIG_BT_BUF_ACL_TX_COUNT
CONFIG_BT_HCI_ACL_FLOW_CONTROL
CONFIG_BT_CONN_TX_NOTIFY_WQ
CONFIG_BT_ATT_TX_COUNT
CONFIG_BT_CONN_TX_MAX
CONFIG_BT_L2CAP_TX_BUF_COUNT
CONFIG_BT_RX_STACK_SIZE
CONFIG_BT_ATT_SENT_CB_AFTER_TX
CONFIG_BT_CONN_FRAG_COUNT
```

### `CONFIG_BT_BUF_ACL_RX_COUNT` — answer this first

**It has never been reported in this project.** An earlier audit recorded `BT_ATT_TX_COUNT=8`,
`BT_CONN_TX_MAX=8`, `BT_L2CAP_TX_BUF_COUNT=8`, `BT_BUF_ACL_TX_COUNT=8`, controller TX 6 and
**`BT_MAX_CONN=1`** — and did not record the RX count.

Nordic's **DRGN-23518** states that `BT_BUF_ACL_RX_COUNT <= BT_MAX_CONN` can deadlock **ACL
reassembly**, with NCS 2.8.0 listed as affected. **With `BT_MAX_CONN=1`, a default of 1 satisfies
that condition exactly.**

**An ACL reassembly deadlock sits precisely where J1 says the thread is** — in RX processing,
upstream of callback dispatch. It stops HCI RX advancing, so the disconnect event is never
dispatched, ATT is never answered, and nothing is ever notified. **One config value would explain
the entire failure.**

Report the number and state plainly whether the condition holds.

Then do the same for **NCSDK-30959** (deadlock when `BT_HCI_ACL_FLOW_CONTROL` is off *and* the
application blocks a host callback) and **NCSDK-29354** (flash writes stalling BT traffic via the
system workqueue; workaround `BT_CONN_TX_NOTIFY_WQ=y`).

## 3. K2 — can the B306 write internal flash at all after boot?

NCSDK-29354 needs a flash operation on the system workqueue. **Establish whether one can occur.**

A previous partition audit found **no NVS or settings partition** on the B306 — 1 MiB fully
allocated with no gap. **The NVS persisted schedule is on the tag's nRF52832, not the B306.** That
demotes this issue but does not close it.

Search for every path that can touch internal flash — `nvs_*`, `flash_*`, `settings_*`, MCUboot
image confirm, anything writing the image trailer — and state for each **whether it can run outside
boot**, when, and on which thread. **If nothing writes flash after boot, say so and demote
NCSDK-29354 explicitly.**

Note separately that `BT_CONN_TX_NOTIFY_WQ=y` may be worth having regardless: **any** slow work item
delays TX notify, and the watchdog argument only proves work items complete within 30 s, not that
they are fast.

## 4. K3 — rescoped: what could BT RX be stuck *inside*?

**`disconnected()` is done — do not re-audit it.** J1 established the thread never reached it.

The thread entered something earlier and never came out. Enumerate the candidates and, for each,
state whether it can block and on what:

- **every other callback the B306 registers** — `connected`, `ccc_changed`, GATT read and write
  handlers, MTU exchange, security, notify-complete, L2CAP, and anything else. **The GATT write
  handler is worth particular attention**: control commands arrive that way, and it is the callback
  most likely to do real work.
- **host-internal RX processing**, where no application callback is involved at all: ACL
  reassembly, ATT request dispatch, L2CAP segmentation. **This is where DRGN-23518 lives**, and J1's
  verdict points here as much as at any callback.
- anything the application registers that the host may invoke from the RX context without it
  looking like a callback.

For every blocking primitive found, give the timeout. **Flag every `K_FOREVER`, every unbounded
queue operation, every synchronous work cancel or flush, every flash access, and every ATT/GATT
call made from inside a callback** — Nordic's own workaround for the known deadlock is precisely
"do not send ATT requests from the BT RX thread".

## 5. K4 — `boot_confirm_lock`, reframed

J1 found `k_mutex_lock(&boot_confirm_lock, K_FOREVER)` at **statement 23** — *after* the clear, so
**it cannot explain BSF44AD**. Do not present it as a candidate for this event.

**It is a separate latent fault with its own predicted signature**, and that is why it still matters:
five other sites take that mutex, **two of them on the system workqueue**. If a wedged workqueue held
it, `disconnected()` would stop between clearing `ble_connected` and calling `start_advertising()` —
producing **a board whose PAIRED goes dark and which never re-advertises**. That is a *different*
observable from the one on BSF44AD.

Report every holder of `boot_confirm_lock`, what each holds while holding it, and whether an ABBA
ordering exists against anything the publisher or notify path needs. The proposed fix — a bounded
acquire that skips the two policy assignments on timeout — should be stated but **not implemented
here**.

## 6. K5 — the invariant J1 exposed, and it should be pinned

J1's verdict rests on a property that **is asserted nowhere**: statements 1–18 of `disconnected()`
do not block *today*. And those blocks are exactly where every recent round has added code.

**The moment anything there can block, the diagnostic inverts silently.** A lit PAIRED would stop
meaning "the callback never started" and start meaning "it started and hung" — pointing at an
entirely different subsystem, with nothing to flag the change.

**Specify a test that pins it** — same shape as the signed-hash rule and the non-blocking Master
test, both of which exist to stop a future tidy-up from quietly invalidating a conclusion. Write the
reasoning into the test file itself. **Specify it; do not implement it in this batch.**

## 7. K6 — the RX stack, still unmeasured

Report `CONFIG_BT_RX_STACK_SIZE`, and the BT RX thread's high-water if any run ever sampled it. An
earlier round sampled publisher, parser, IMU, notify worker and system workqueue — **the BT RX
thread was not among them.** If it has never been measured, say so: a thread that is never measured
is a thread whose margin is unknown, and it is now the prime suspect.

## 8. What this round must NOT do

**Change nothing.** Not a Kconfig value, not a callback, not a test, not a build.

Three config changes plus a callback refactor shipped together, against a fault occurring roughly
**once per 26.5 board-hours**, gives an unattributable result either way — and at that rate,
"it stopped" needs enormous exposure before it is a claim rather than an absence.

**The specimen is preserved indefinitely on the charging POGO, so nothing is forcing a fix ahead of
the diagnosis.**

## 9. Deliverable

`BT_RX_WEDGE_AUDIT.md`: the config table first, with an explicit verdict on
`BT_BUF_ACL_RX_COUNT <= BT_MAX_CONN`; then the flash-path answer promoting or demoting NCSDK-29354;
then the rescoped K3 table of everything BT RX could be stuck inside; then `boot_confirm_lock` with
its distinct predicted signature; then the pinned-invariant specification; then the RX stack figure
or `INSUFFICIENT`.

**End with a ranked list of what the evidence now supports, and for each, the cheapest observation
that would confirm or refute it** — including which of them the preserved BSF44AD could answer under
a debugger, since that is the next physical step and its session should be planned, not improvised.

**No changes, no hardware.** End with a literal banner and STOP.
