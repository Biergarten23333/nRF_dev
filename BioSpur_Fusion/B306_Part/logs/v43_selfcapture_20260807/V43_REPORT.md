# V43 — commit, instrument, deploy, run overnight

**Batch:** `v43_selfcapture_20260807` · Evidence root: `B306_Part/logs/v43_selfcapture_20260807/`
**Started** 2026-08-07 00:51 · **Operator asleep, pre-authorised; no decision was ever referred.**

---

## 0. Headline

**v43 is built, validated end to end on a canary, deployed to nine boards, and ran a clean six
hours — and no board wedged, so there is no corpse.** The trap works and is armed; nothing walked
into it.

Exposure was **54.0 board-hours** against a rate of one event per 26.5, so **2.04 events were
expected and P(0) = 0.130**. Zero is a 13 % outcome: uncommon, ordinary, and **not evidence that
anything changed**. The workaround was never applied (§16), so nothing that could suppress the fault
was enabled.

Three things worth knowing before the detail:

**1. Stage 2 earned its place immediately.** The canary caught a false-positive trigger that would
have rebooted **every board on every disconnect**. `DEFERRED_RESCHEDULE_AFTER` is the final mark of
the disconnect path and I had not classified it as terminal, so an idle disconnected board looked
mid-operation. The DK restore disconnects boards for far longer than the 5 s threshold, so the
monitor fired on a perfectly healthy board, rebooted an image that had not yet earned its MCUboot
confirmation, and **MCUboot correctly reverted it to v41**. Two OTA attempts were spent before it was
diagnosed. The guard the brief told me not to remove is the reason this cost two attempts instead of
a fleet.

**2. A latent bug was already deployed, and it was not mine.** The DK's ring/status discriminator
was `== BSF_STALL_RING_VERSION`, which baked the *then-current* version into the image and defeated
the intent stated three lines above it in its own comment. H1's bump to v4 had therefore already
made **dk-v34 misparse v42's ring pages** into the status branch, dropping 64 bytes and running the
pool loop off ring payload. Found by disassembling the deployed image (`cmp r3, #3` at `0x10136`).
dk-v35 fixes it in one line.

**3. Two measurements this campaign has never had.** The BT RX WQ stack — the thread no round has
ever sampled, because `bt_workq` is `static` in `hci_core.c` with no accessor — is **664 of 1024
bytes used, 360 free**. And the worst *in-flight* stage dwell on a healthy board is **single-digit
CPU cycles**, because `k_work_flush()` returns immediately when nothing is queued. The 5 s threshold
is not a guess.

---

## 1. Stage 0 — git and provenance

The firmware's committed history ended at `b306-imu-relay-v32`. Everything from v33 to v42 existed
only as uncommitted working-tree state.

| | |
|---|---|
| **v42 baseline** | `f1a128984` — 121 files, 12,358 insertions |
| **v43 source** | `f0ebbb815` — before any OTA, as required |
| **v43 fix** | `3904e7576` — the terminal-stage fix and its contract test |
| Branch | `feature/b306-bringup`, all three pushed |
| SDK | `~/ncs/v2.8.0` (shared install, **outside** this repository) |
| nrf | `a2386bfc84016fa571f997ac871b25bd67ca481a` (v2.8.0) |
| zephyr | `0bc3393fb112ec80ebeab48cd023d69b1e9db757` (v3.7.99-ncs1) |
| toolchain | `~/ncs/toolchains/b81a7cd864` — Zephyr SDK 0.16.8, gcc 12.2.0 |

**v33–v41 are permanently unrecoverable as source text**, v41 included — the tree is a single state
and it was v42 when this batch began. DWARF in the preserved build directories supports *comparison*
against those images, not regeneration of them. That is stated rather than papered over.

**Scope deviation, recorded.** The commit covers `B306_Part/{firmware,include,host,tools,docs}` —
the source that produces and validates the image. It deliberately excludes **106,291 pending
deletions** under `BioSpur_UWB_before_start/` and ~110 MB of untracked scratch workspaces under
`BioSpur_Fusion/` (`54L15/` alone is 84 MB). Neither has anything to do with image reproducibility,
and sweeping them in risked the LFS quota incident again. Everything the image needs is committed.

---

## 2. Build

### 2.1 The host patch is a repository artifact

`B306_Part/firmware/patches/ncs-v2.8.0-bt-conn-stage-trace.patch`, applied and verified by
`host_patch.sh {apply,verify,revert,status}`, which gates on three hashes and recognises exactly
three states — pristine, patched, or **refuse**. CMake runs `host_patch.sh verify` and will not
configure without it.

**The shared-SDK hazard is not hypothetical — it fired on the first build.** The Fusion Master DK
compiles the same `conn.c`, and its build failed on the missing header. The patch is therefore
self-neutralising: it keys on `__has_include(<bsf_bt_stage.h>)`, so any project that does not put
the B306 `src/` on the include path compiles the instrumentation out to nothing. dk-v35 then built
clean and byte-reproducible, which is the proof that the guard works.

### 2.2 Canonical artifacts

| Image | Hash | |
|---|---|---|
| **b306-imu-relay-v43** unsigned | `f0bf05a20f393ed8abab011fefd58653018581760d1e20274dab4793adffd659` | reproducible |
| MCUboot | `aa252296f1e9bb41802df14c0d48eb1a24a8a814870a64203cac9f78dd46e307` | reproducible, unchanged from v42 |
| signed (build A) | `97909763fdbc34ff268de6fc4a89e7c78e545d0a561feb6d68093cee171fa498` | **cannot** be reproducible |
| **dk-fusion-imu-relay-v35** app | `d91bbfd5b6675dfdc0db5e7e243fa23b1c7cc156487655f21acf95d04d0b4b73` | reproducible |
| dk-v35 merged | `dcf0d639b8fef7a575e2b3c384dc84babac224edfade92c4d139187e602cc2b9` | reproducible |

FLASH **222,904 B (44.65 %)**, RAM **116,164 B (44.31 %)** — +3,308 B and +3,712 B over v42.

Two pristine builds agree on the unsigned application and on MCUboot. They cannot agree on the
signed image: imgtool draws a fresh ECDSA P-256 nonce per run, so the signature trailer and even its
DER length differ. The first build script asserted on `merged.hex` and correctly reported NOT
REPRODUCIBLE; the check list was wrong, not the build.

### 2.3 Frozen, deliberately

`BT_CONN_TX_NOTIFY_WQ=n`, `BT_HCI_ACL_FLOW_CONTROL=n`, `BT_RX_STACK_SIZE=1024`,
`BT_CONN_FRAG_COUNT=1`, `BT_MAX_CONN=1`, every buffer count unchanged. Nordic's documented
workaround for this exact RX→TX-notify→system-workqueue dependency (NCSDK-29354) stays off. The
contract test fails if any of them moves.

---

## 3. What v43 does

**Instrumentation** — 14 stages across `bt_conn_recv()`, `bt_conn_tx_notify()` and the
disconnect-complete state path, exactly where J1 localised the fault. Each transition is a
`static inline` over plain 32-bit stores: no log, no allocation, no lock, no work submission, no
flash. The sequence counter is published *last*, after the stage, so a reader that sees a new
sequence is guaranteed to see the stage it belongs to.

**Flight recorder** — 128 × 12 B lock-free ring in ordinary RAM. Not RTT: RTT is full and skipping
on every board, which is how the thread-analyzer output has been discarded all campaign.

**Monitor** — its own thread at 1 Hz, on neither suspect queue, depending on nothing BLE depends on.
Triggers on a non-quiescent stage whose sequence has not advanced for 5 s. It deliberately does
**not** trigger on notifications stopping, which would readmit the producer, RF, scheduling, the
central and the application.

**One reboot budget**, shared with v42's ring ISR, never one each. Precedence is explicit and
enforced in code: the monitor wins, because its corpse **embeds the ring tail**, so yielding to it
loses nothing the ring would have reported.

**Corpse** — 812 B in `noinit` (verified at `0x20007940`, not `.bss`): stage, sequence, age, BT RX
thread state and saved PSP, stack size and unused, the private `bt_conn` fields read inside the
patched `conn.c`, `k_work_busy_get()` of `tx_complete_work` and `deferred_work`, liveness counters,
the flight-recorder tail and the ring tail. `magic` + `schema` + `length` + CRC32, with `valid`
written **last**, so a cold boot's uninitialised RAM is rejected rather than believed.

**Export** — 4 × 232 B pages on the existing stall characteristic, retained until a positive ACK
carrying the right sequence. No flash write was added anywhere: flash × system workqueue ×
`bt_conn_tx_notify` is still in the suspicion tree.

### 3.1 What was NOT implemented, and why

**Stack unwinding of the parked BT RX thread** (§8, explicitly optional). Not attempted. The corpse
preserves the saved PSP, the thread state and the stack geometry, so the context can be
reconstructed offline from the frozen `zephyr.elf` — which is what the brief asked for as the
fallback rather than forcing a fragile unwinder into production.

~~**Cold-boot rejection of a stale corpse is not hardware-verified.**~~ **Superseded — now verified
on hardware, see §10.** It was recorded `INSUFFICIENT` during the run because verifying it requires
removing power and §14 forbids power-cycling. The operator power-cycled BSF44AD after the run, which
provided the test for free.

---

## 4. Stage 2 — single-board validation (BSFAA61)

### 4.1 The bug the canary caught

Two OTA attempts failed with the image correctly written. The updater reported
`marker=b306-imu-relay-v43 hash=match active=1 confirmed=0` and handed off for app confirmation, and
~60 s later the board answered PING as **v41**.

Cause: `DEFERRED_RESCHEDULE_AFTER` is the last mark of the disconnect path and was not in the
terminal set. Any disconnected board therefore looked mid-operation. The DK restore disconnects
every board for far longer than 5 s → monitor fires → `sys_reboot()` → unconfirmed image →
**MCUboot reverts**. Deployed as-is, all ten boards would have rebooted on every disconnect and
never held v43 at all.

Fixed by classifying it terminal, and pinned by `firmware/tests/test_bt_stage_contract.py`, which
now requires **every** stage in the enum to be classified terminal or in-flight — adding a stage
without making that decision fails the test.

A second, smaller fix: `reason=not_connected` is now retried on the same terms as
`bridge_not_ready` and `reason=syntax`. The updater resets the target into its new image; until that
image advertises and the Master reconnects, the Master has no peer to route to. PING is an
idempotent read query, so re-asking is not an OTA write retry.

### 4.2 The validation, after the fix — PASS

| Check | Result |
|---|---|
| baseline `STATUS` reports v43 | ok |
| no corpse before the trigger | ok |
| `CORPSE FORCE` accepted | ok |
| board self-reset and reconnected | ok, **20.7 s** |
| corpse retained across the reset | ok |
| all four pages fetched, each CRC16 ok | ok, offsets 0/220/440/660 |
| corpse CRC32 | ok |
| classified as a pipeline test, not a fault | `DIAGNOSTIC_FALSE_POSITIVE` |
| BT RX thread located | `0x20003720`, state `PENDING` |
| BT RX stack measured | **1024 B, 664 used, 360 unused** |
| `bt_conn` fields captured | state 7, `tx_complete_busy` none, `deferred_busy` none, `pkts_avail` 0 |
| wrong ACK refused | ok |
| correct ACK cleared the marker | ok |

**The forced trigger validated the recorder and recovery pipeline only. It is not a reproduction of
the BLE failure and is not reported as one** — the decoder classifies it on the trigger field alone.

### 4.3 Measured healthy stage dwell — what justifies the 5 s threshold

| Stage | Max dwell (ms) | |
|---|---:|---|
| `TX_NOTIFY_EXIT` | 10.4425 | terminal |
| `DEFERRED_RESCHEDULE_AFTER` | 2.1921 | terminal |
| `IDLE` | 0.9601 | terminal |
| `CONN_RECV_EXIT` | 0.1535 | terminal |
| `TX_NOTIFY_BEFORE_SUBMIT` | 0.0001 | **in flight** |
| `TX_NOTIFY_BEFORE_FLUSH` | 0.0001 | **in flight** |
| `CONN_RECV_ENTER`, `TX_NOTIFY_ENTER`, `TX_NOTIFY_AFTER_SUBMIT`, `TX_NOTIFY_AFTER_FLUSH` | 0.0000 | in flight |

The four long entries are **terminal** stages — they measure idle time between operations, and the
monitor excludes them by construction. The figure that matters is the worst **in-flight** dwell,
which is `0.0001 ms`: single-digit CPU cycles at 64 MHz.

That number is itself informative. `TX_NOTIFY_BEFORE_FLUSH → AFTER_FLUSH` is the duration of
`k_work_flush()`, and on a healthy board it returns essentially instantly, because
`work_busy_get_locked()` finds nothing queued and returns without waiting. **A wedge there is
therefore unmistakable** — the healthy and failed cases differ by seven orders of magnitude, not by
a factor of two. The 5 s threshold is not close to anything.

*(The Stage 2 script's own headline margin figure, 478×, is computed against `TX_NOTIFY_EXIT` and is
the wrong metric — that stage is terminal. The correct comparison is the one above.)*

---

## 5. Stage 3 — deployment

### 5.1 Pre-flight

| Gate | Result |
|---|---|
| Tag Master physically absent (trap 15.2) | **ABSENT on all four checks** — lsusb, sysfs `1-5.1`/`1-6.1`, `by-id`, `ttyACM24/25/26` |
| Fusion Master present | yes, `8D3AC42D4D90FAE8` |
| dk-v35 flashed | 8.6 s, marker confirmed on hardware |
| Spacing after DK flash (trap 15.1) | came back **OFF / 7500** exactly as warned → rebuilt to **ON / 5000, generation 2, APPLIED** |
| End-to-end PING (trap 15.4) | **9 of 10 answered** |

**BSF44AD did not come back from the operator's power cycle** — `reason=not_connected`. Quarantined
per §12.1; the night was not spent on it. **BSFC2CC answered on v38**, not v41 — it was wedged
through the entire N6 rollout and never took that image. It jumps **five generations, v38 → v43**,
and is this batch's stress sample; the other eight are a clean v41 → v43 control group.

### 5.2 Rollout — 8/9 in one batch, 19.7 min

All in one batch, no board first: BSFAA61 had already taken v43 as the Stage 2 canary and is
confirmed, so re-running its transaction would only have re-opened a rollback window on a board that
was already correct. Every target used `--preflight-require target-only` (trap 15.3).

| # | Board | Verdict | Transaction | Updater phase | From |
|--:|---|---|--:|--:|---|
| — | BSFAA61 | **PASS** (Stage 2) | — | — | v41 |
| 1 | BSF6C53 | PASS | 139.7 s | 79.8 s | v41 |
| 2 | BSF1120 | PASS | 157.2 s | 101.7 s | v41 |
| 3 | BSF31CC | PASS | 134.0 s | 78.2 s | v41 |
| 4 | BSFEC35 | PASS | 135.0 s | 78.8 s | v41 |
| 5 | BSFB165 | PASS | 138.1 s | 77.4 s | v41 |
| 6 | BSF3C79 | PASS | 137.0 s | 78.7 s | v41 |
| 7 | BSF8BC4 | PASS | 135.9 s | 79.1 s | v41 |
| 8 | **BSFC2CC** | **PASS** | 132.5 s | 74.7 s | **v38 — five generations** |
| 9 | BSF44AD | QUARANTINE (`rc=2`) | 0.041 s | — | absent |

Batch wall **1184.9 s**. Zero write retries. Every transaction stayed far inside the 417.874 s
bound; the slowest updater phase was 101.7 s.

**BSFC2CC took the five-generation jump in 132.5 s — the fastest transaction of the batch.** It is
this round's stress sample and it behaved like a control.

**BSF44AD refused in 41 ms**, before any write, on its own per-target PING gate — which is exactly
the behaviour trap 15.3 exists to produce. It is a measured sample, not a failure.

**Pool occupancy never moved.** Ten samples across the rollout: min 80, max 83 records. The
rollout's own Master-plane interruptions did not degrade the pools.

**Post-rollout gate: all nine answer an end-to-end PING on `fw=b306-imu-relay-v43`.** BSF44AD alone
returns `not_connected`.

### 5.3 The run

Opened **02:12:39** on `/dev/ttyACM9`, `COUNT=12 PERIOD=10`, spacing ON/5000 generation 2, listener
array capturing in parallel. Two launch faults were fixed before it took:

* the launcher pre-created `B5_RUN`, which the driver rejects with `exist_ok=False` — deliberately,
  so a re-launch can never overwrite a capture;
* `next_corpse` was read before assignment on the first loop iteration, because my edit anchored on
  an initialisation line that did not exist in that form. Caught in ~30 s by the run's own console.

Data was already flowing during the failed attempt — the first archived record is a `FUSION_UWB`
from BSF3C79 with `valid=0xff`, i.e. 8/8 links.

**No `RECONNECT` is ever issued.** It was shown to remove a board permanently while adding nothing,
and the ring it was meant to reach now travels inside the corpse. On a wedge the board captures its
own corpse, resets itself, returns, and hands it over on the next `CORPSE STATUS` sweep (90 s).
One `STALL READ` per silence episode is still issued purely to capture the status-snapshot form at
the moment of silence; nothing escalates past it.

Corpse sweeps confirmed live within the first minute: 20 `CORPSE STATUS` commands, 18 replies, all
`present=0`. Nine nodes producing UWB at the TDMA ceiling.

#### The canary spent its reboot budget — a design finding, recorded not fixed

The first sweep shows **`BSFAA61 … reboot_owner=2`** while all eight others show `owner=0`. Stage 2's
`CORPSE FORCE` consumed that board's shared reboot budget, and the budget is one **per power cycle**
— it lives in `.noinit`, so it survives the very `sys_reboot()` it authorises, by design.

The consequence is specific and worth stating plainly: **BSFAA61 will still capture a corpse on a
real wedge — capture happens before the budget is claimed — but it will not reset itself.** A wedged
board cannot answer `CORPSE STATUS`, so that corpse would sit unreachable until someone power-cycles
the board, which erases `.noinit` and destroys it. For tonight BSFAA61 is a reduced-capability
fleet member; the other eight have a full budget.

**The design lesson is that an artificial trigger should refund the budget, because it is a test and
not a fault.** That is a one-line change and it is deliberately **not** being made: the run is live,
and changing firmware under a running experiment to fix a non-blocking issue on one of nine boards
would cost more exposure than it buys. It goes to the next round with the fix stated.

No intervention was taken. Per §14 this is a measured sample, and eight boards with a full budget is
ample for a fault that occurs about once per 26.5 board-hours.

#### BSFAA61's IMU is degraded — measured, board-specific, not intervened in

At T+4.5 min, eight of nine boards deliver IMU records at **20.07 rec/s**, exactly the expected
200 Hz ÷ batch 10. **BSFAA61 delivers 6.19 rec/s** — 1,554 records against ~5,434, with 580 sequence
gaps and 29,757 samples lost (13,476/43,233 delivered).

Two things make this worth recording rather than dismissing:

* **Its UWB is perfect** over the same window — 2,106 records at 8.365 Hz, 100 % at 7+/8 links, zero
  sweep loss. So this is not a link or a scheduling problem; it is confined to the IMU stream.
* **It is board-specific, not image-specific.** All eight other boards run the identical v43 image
  and are nominal. That exonerates the instrumentation directly, which is the discriminator that
  matters when a new image is on the fleet and something looks wrong.

BSFAA61 is also the board that has been through the most tonight: two OTA attempts, a rollback, a
third OTA, and a forced corpse capture with its reset. Whether that is causal is **`INSUFFICIENT`**
on the evidence so far. It is left running and untouched — §14 — and if it wedges it will still
capture a corpse, though see the budget note above for why it may not be able to hand it over.

---

### 5.4 The run closed clean — and caught nothing

**Ran the full 6.0 h ceiling. `stop_reason: None`** — it ended on duration, not on any of the three
abort conditions. Every 60 s tick from open to close reported `delivering=9 linked=9 silent=[]`.

| | |
|---|---|
| Duration | 21,600.0 s (02:12:39 → 08:12:39) |
| Boards delivering | **9 of 9, continuously, start to finish** |
| `DATA_PLANE_SILENT` episodes | **0** |
| `NODE_GONE` | **0** |
| Battery deaths | **0** |
| Corpses captured | **0** |
| Listener array | 7 listeners, 21,600.1 s, 18,847,859 merged records |
| Disk at close | 169.5 GB free |

**The primary deliverable was not obtained. No board wedged, so no natural corpse exists.**

That is a real outcome and it is stated plainly rather than dressed up. What it is worth:

**Exposure was 9 boards × 6.0 h = 54.0 board-hours.** At the established rate of one event per
26.5 board-hours that is **2.04 expected events, and P(0 events) = 0.130**. Seeing zero is a 13 %
outcome — uncommon but entirely ordinary, and **not evidence that anything changed.**

**It must not be read as v43 fixing the fault, and three things say so directly:**

* `CONFIG_BT_CONN_TX_NOTIFY_WQ` stays `n`, ACL flow control stays off, `BT_RX_STACK_SIZE` stays
  1024, `BT_CONN_FRAG_COUNT` stays 1. **The workaround was never applied** — §16 — and the contract
  test fails if any of them moves.
* The instrumentation on the hot path is a handful of aligned stores with no lock, allocation or
  syscall. It is not nothing, but it is not plausibly the difference between a fault that fires
  twice a night and one that fires zero times.
* One clean night is far short of the exposure needed to claim "it stopped". At this rate, absence
  only becomes a claim after many multiples of 26.5 board-hours.

The honest summary is: **the trap is built, proven end to end, and armed on nine boards, and nothing
walked into it tonight.**

---

## 6. Yield ladder

Six hours, per node. Rates are `(records − 1) / (last − first)` on the live block after the stale
prefix is split off. Every ratio carries its denominator.

| Node | UWB | Rate (Hz) | 8/8 | 7+/8 | UWB delivered | IMU samples | IMU delivered |
|---|--:|--:|---|---|---|--:|---|
| BSF1120 | 180,001 | 8.3333 | 179,656/180,001 | 180,001/180,001 | 180,001/180,001 | 4,319,752 | 4,319,752/4,319,752 |
| BSF31CC | 180,001 | 8.3334 | 179,588/180,001 | 179,944/180,001 | 180,001/180,001 | 4,319,798 | 4,319,798/4,319,798 |
| BSF3C79 | 180,000 | 8.3333 | 179,404/180,000 | 179,747/180,000 | 180,000/180,000 | 4,319,820 | 4,319,820/4,319,820 |
| BSF6C53 | 180,001 | 8.3335 | 179,679/180,001 | 179,844/180,001 | 180,001/180,001 | 4,319,889 | 4,319,889/4,319,889 |
| BSF8BC4 | 180,001 | 8.3334 | 179,935/180,001 | 180,001/180,001 | 180,001/180,001 | 4,319,845 | 4,319,845/4,319,845 |
| BSFAA61 | 180,005 | 8.3336 | 179,720/180,005 | 180,005/180,005 | 180,005/180,005 | 358,799 | **358,799/1,018,671** |
| BSFB165 | 180,003 | 8.3334 | 179,943/180,003 | 180,003/180,003 | 180,003/180,003 | 4,319,860 | 4,319,860/4,319,860 |
| BSFC2CC | 180,002 | 8.3334 | 179,878/180,002 | 180,002/180,002 | 180,002/180,002 | 4,318,003 | 4,318,003/4,319,813 |
| BSFEC35 | 180,002 | 8.3334 | 179,788/180,002 | 180,002/180,002 | 180,002/180,002 | 4,319,802 | 4,319,802/4,319,802 |

**Fleet: 8/8 = 1,617,591/1,620,016 = 99.8503 %. 7+/8 = 1,619,549/1,620,016 = 99.9712 %.**

**Zero UWB sweep loss on all nine boards** — delivered equals expected everywhere, counted from
sweep-number jumps rather than `q_drop`.

Every node sat on **8.3333–8.3336 Hz**, i.e. exactly the `COUNT=12 × PERIOD=10` ceiling, for six
hours. Against the reference figures (99.9053 % at 8/8, ge7 100.0 %) this run is **slightly below on
both**: 99.8503 % at 8/8 and 99.9712 % at 7+/8. Not a regression worth chasing on one night, but not
an improvement either, and recorded as measured.

**Freshness: `INSUFFICIENT` — static bench.** Labelled, not numbered, as required.

**BSFAA61's IMU was degraded for the entire run**, ending at 358,799/1,018,671 (35.2 %) against
~4.32 M on every other board, while its UWB was flawless (180,005 sweeps, 100 % at 7+/8, zero loss).
Board-specific, not image-specific. **BSFC2CC**, the five-generation stress sample, finished at
4,318,003/4,319,813 = **99.958 %** IMU and 100 % UWB — indistinguishable from the v41 control group.

## 7. Event log

**Empty, and that is the finding.** No stall, no dying board, no abrupt power loss, no battery death
with preamble, no `NODE_GONE`, no reset of any kind on any of the nine boards across 54 board-hours.
None of §13's measured signatures occurred, so none is classified.

Corpse classification: **not applicable — no corpse.** The classifier was exercised only on the
Stage 2 artificial trigger, where it correctly returned `DIAGNOSTIC_FALSE_POSITIVE` on the trigger
field. It was never given a real fault to name, and no outcome has been forced.

The one board absent all night, **BSF44AD**, never returned from the operator's power cycle and was
quarantined before any write. Its pre-existing wedge is gone with its `.noinit`, as accepted in §12.1.

## 8. What the next round should do

1. **Keep running v43.** The trap works; it needs exposure. 54 board-hours produced nothing at a 13 %
   probability, so the cheapest next step is simply more nights on the same image.
2. **Make the artificial trigger refund the reboot budget.** One line. Stage 2 cost BSFAA61 its
   budget for the whole night (§5.3).
3. **Investigate BSFAA61's IMU** — degraded for six hours with perfect UWB, on a board that took
   three OTAs and two forced resets. Nothing else looks like it.
4. **Do not enable `CONFIG_BT_CONN_TX_NOTIFY_WQ`,** raise `BT_RX_STACK_SIZE`, alter flow control or
   tune buffers. The mechanism is still not on record. §16 stands.

## 9. Evidence index

`EVIDENCE_SHA256.txt`. The host patch is left **applied** to the shared SDK deliberately: the fleet
runs an image that requires it, so any rebuild must match, and `host_patch.sh verify` is the build
gate that enforces it. `host_patch.sh revert` restores the SDK when that is wanted.

---

## 10. Post-run addendum — BSF44AD recovered, and the cold-boot path verified

**08:20–08:45, after the run closed.** The operator fully power-cycled BSF44AD and asked for it to be
brought to v43.

### 10.1 BSF44AD is on v43

It came back and answered an end-to-end PING on `b306-imu-relay-v41`, so it had simply been
unreachable, not bricked. One transaction, source `v41`, target `v43`, `--preflight-require
target-only`:

```
status = PASS   updater_capture = MARKERS_COMPLETE_EARLY_EXIT
BOOT CONFIRM STATUS confirmed=1 required=0 prepared=1 committed=1
PONG name=BSF44AD fw=b306-imu-relay-v43 proto=7
```

No retries. **All ten boards have now taken v43.**

### 10.2 The cold-boot rejection path is verified on hardware

This is the §3.1 item that had to be recorded `INSUFFICIENT` during the run, because testing it means
removing power and §14 forbids that. The operator's power cycle supplied exactly that condition, so
the check was made the moment the board was reachable:

```
CORPSE present=0 seq=0 pages=0 len=812 ... rr=00000000 reboot_owner=0
RING   boot=1 init=cold count=200/200 pages=40 frozen=0 ... writes=5880
STATUS fw=b306-imu-relay-v43 id=44AD up_ms=295292 imu=1/200Hz/N10 verify=PASS
```

Three independent confirmations in those three lines:

* **`CORPSE present=0`** — after a genuine power cycle the retained region holds uninitialised RAM,
  and the magic/schema/length/CRC32 gate rejected it. **No phantom corpse was manufactured**, which
  was the failure mode worth fearing: a plausible-looking record assembled from garbage is worse
  than none.
* **`RING init=cold`** — the ring's own boot verdict is literally `cold`, i.e.
  `bsf_stall_ring_boot()` detected the power-on and reinitialised instead of trusting stale bytes.
  The geometry stamp did its job.
* **`reboot_owner=0`** — the shared reboot budget is fresh, confirming from the other direction that
  the budget really is one **per power cycle** (BSFAA61 still read `owner=2` all night on a board
  that had only ever soft-reset).

**Upgrade: `INSUFFICIENT` → verified.** The one remaining gap in §9 is a *brownout* specifically, as
distinct from a clean power removal; nothing tonight produced one.

### 10.3 The other nine are flat

At the run's last tick (08:12:39) all nine were delivering. By 08:20 none of them would answer, and
they did not return after the DK restore or after spacing was rebuilt. They had been undocked since
roughly 00:30 — about **7.7 h against a stated 6–7 h endurance** — so this is battery depletion, and
it is a measured sample, not a failure. Nine cells from the same charge expiring inside a ~20 min
window is consistent with the precedent in §13 of two boards dying 12 minutes apart on identical
cells.

BSF44AD is the exception only because it spent the night on a **charging** POGO.

**Fleet state as left:** BSF44AD up on v43 with a full charge and a fresh reboot budget; nine boards
flat and needing a dock; Fusion Master on dk-v35 with **spacing rebuilt to ON / 5000 / generation 2**
after the DK restore wiped it again (trap 15.1 fired for the second time tonight, as expected).
