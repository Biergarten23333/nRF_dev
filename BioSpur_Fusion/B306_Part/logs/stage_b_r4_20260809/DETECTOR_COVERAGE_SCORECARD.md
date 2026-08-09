# Detector coverage scorecard — what v45 catches, and the hole it cannot close

Written 2026-08-09, after a `V45 HANG` re-run on BSF6C53 reset the board with
`reset_reason=0x00000002` (RESETREAS.DOG) and left no corpse.

## The hole, stated first because it is structural and permanent

**A wedge that stalls the system workqueue is invisible to the v45 detector, and
no change to the detector can fix it.**

The detector tick is `k_work_reschedule()` on the system workqueue, and
`v45_capture()` runs from the same handler. The watchdog is fed from that same
queue (`watchdog_feed_count`, "system-workqueue heartbeat" — `main.c`). So when
that queue dies:

| | |
|---|---|
| the detector | stops ticking — the code that would react is the code that stopped |
| the capture | never runs, so `.noinit` holds no corpse |
| the watchdog | stops being fed, and resets the board at `WATCHDOG_TIMEOUT_MS` = 30 s |
| the node | comes back reporting `V45 present=0 armed=1` |

That last line is the dangerous part. **`present=0 armed=1` after a syswq death
is byte-for-byte identical to a healthy node on which nothing ever happened.**
An entire class of wedge could run through the fleet every night and the
telemetry would show a clean sheet.

This is not new — `bsf_v45.c`'s own header and `DECISIONS.md` both said the
class was excluded from the detector's reach. What was new on 2026-08-09 is
that it stopped being theoretical: we produced one, on the bench, and the node
lied about it exactly as predicted.

**Moving the detector off the system workqueue is the only thing that would
close this, and it is not done.** A dedicated thread would need its own
priority, its own stack, and its own argument about what *it* can be blocked by
— the same argument that has already been got wrong three times (v43 stage
semantics, v44 multi-writer stage, the 1 Hz pool sampler). It is a real design
task, not a patch, and it is open.

## What the three 2026-08-09 changes actually do

None of them close the hole. Stated individually so nobody reads them as a fix:

| change | what it buys | what it does NOT do |
|---|---|---|
| dwell 20 s → 12 s (`BSF_V45_FREEZE_MS`) | capture starts ~8 s earlier, so it can finish inside a 30 s watchdog period that is already partly spent | does not make the detector tick when the queue is dead |
| one `wdt_feed()` inside `v45_capture()` | a capture that HAS started gets a full 30 s to finish writing | only runs if the detector already fired; on a syswq death it never runs |
| `.noinit` watchdog witness (`bsf_v45_dog`) | the RESET becomes readable: `dog=`, `dog_dwell=`, `dog_age_ms=`, `dog_tick_ms=` on `V45 STATUS` | records that the board died, not what killed it |

The witness is the one that matters for the fleet. It does not detect the
wedge; it removes the disguise. After it, a node that ate a watchdog says
`dog=1` and reports how far into a dwell the last detector tick had got —
so "our detector has a hole" and "there was nothing to detect" stop looking
the same from the outside.

**The watchdog timeout was deliberately NOT extended.** The periodic feed stays
on the system workqueue at 30 s, because that feed *is* the diagnostic: a
longer timeout would blind the one mechanism that currently notices a syswq
death at all.

## Coverage table

| wedge class | detector | witness | notes |
|---|---|---|---|
| notify worker parked, syswq alive | **covered** (arm A) | n/a | the original v45 target |
| submissions never complete | **covered** (arm B) | n/a | ncp watermark |
| delivery frozen, calls still return | **covered** (arm C) | n/a | the 2026-08-09 spontaneous wedge; host-tested only as of today, see below |
| app connected, host conn released | **covered** (CONN_RELEASED) | n/a | no dwell, fires on the contradiction |
| **system workqueue dead** | **NOT COVERED** | reset is readable | this scorecard's subject |
| power-on / brownout | n/a | **NOT COVERED** | `.noinit` does not survive; witness dies with it |

## A second gap found while fixing the first

`test_bsf_v45_detector.c` had been **failing since R4** and nobody had noticed.
The harness modelled only watermarks A and B, so `notify_ok_total` never moved
and arm C fired 20 s into every test — including the healthy-traffic one. Ten
checks were red at the old dwell and the new one alike.

**Arm C and `CAUSE_CONN_RELEASED` therefore shipped into r5/r6 with no passing
host coverage at all.** Both are now covered (`test_notify_ok_arm`), the
timings are expressed against `BSF_V45_FREEZE_MS` instead of literals, and
`test_dwell_is_pinned` keeps the constant from drifting silently now that
nothing else hardcodes it.

## What would actually close the hole

Ranked, for whoever picks this up:

1. **Move the detector to its own thread**, above the system workqueue in
   priority, and have it watch the syswq heartbeat as an input rather than
   riding it. This is the real fix and the real work.
2. Feed the watchdog from that thread instead, so the watchdog measures the
   detector's liveness rather than the workqueue's — but only *after* 1, and
   only with a separate syswq-liveness watermark, or the current diagnostic is
   lost.
3. Until either lands, `dog=` on `V45 STATUS` is the fleet's only signal for
   this class. **It must be collected and trended, not just present.** A field
   that nobody reads is the same as the silence it replaced.
