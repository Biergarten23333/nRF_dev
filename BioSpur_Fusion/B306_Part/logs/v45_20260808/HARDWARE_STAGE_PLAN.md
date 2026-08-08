# HARDWARE_STAGE_PLAN — v45

**Plan only. No hardware was touched in producing this: no J-Link, no SWD, no
flashing, no OTA, no BLE commands, no serial ports.** Nothing below has been
executed.

---

## Stage A — what already exists (offline, done)

Two clean builds, `b306-imu-relay-v45-a` and `-b`, byte-identical in the unsigned
app and MCUboot. A third, `b306-imu-relay-v45-flash`, exists only to prove the
§9 code and the partition overlay compile and link; **it is not a deployment
candidate** (see the blocker below).

Artifact for OTA: `builds/b306-imu-relay-v45-a/firmware/zephyr/zephyr.signed.bin`,
marker `b306-imu-relay-v45`.

---

## Stage B — single canary, the four §12 injections

One board, isolated, on the bench. Build with `BSF_V45_FAULT_INJECT=1` — that
flag emits a build-time warning on purpose, and an image carrying it must never
reach the fleet.

### B1. CORPSE FORCE — the whole pipeline, on a healthy board

```
<NODE> V45 STATUS          -> present=0
<NODE> V45 FORCE           -> armed
   ... node captures, jitters 0-4 s, cold-resets, rejoins (~20.7 s + jitter)
tools/v45_corpse_collect.py --nodes <NODE> --outdir <dir>
```

PASS criteria, all of them:
- `V45 STATUS` after rejoin reports `present=1`, `cause=4` (FORCED),
- all 135 pages read, every page CRC16 valid, aggregate CRC32 recorded,
- the decoder accepts the image and every one of the five banks,
- `V45 ACK=<seq>` returns `ok`, and a subsequent `V45 STATUS` reports `present=0`,
- the node's `reboot_taken=1`, `reboot_owner=3`.

The corpse must be labelled FORCED everywhere it appears. **A forced capture is
not a wedge and must never be counted as one in the ledger.**

### B2. Notify-worker hang on a private semaphore

Expect: primary trigger at 20 s via the `notify_exit` arm, `cause=1`. The corpse
must show `APP_NOTIFY` ENTER > EXIT and the notify worker's `pended_on` naming
the injected object (unnamed address is acceptable — it is not in the wait-object
table — but the channel's ENTER-without-EXIT is not optional).

### B3. sync_evt leak — the one that matters

```
<NODE> V45 LEAK            -> rc=0
```

Takes the singleton `sync_evt_pool` buffer and never returns it.

**Expected if candidate 1 is right: the FULL phenotype reproduces.** Check all
eight invariants, in this order, and record each as PASS/FAIL rather than
summarising:

| # | invariant | how to check |
|---|---|---|
| 1 | UWB and IMU production continue; all B306 export stops, three streams within ~1.4 ms | master `delivered_*` freeze; node `producer_heartbeat` / `valid_frames` keep advancing |
| 2 | BLE Link Layer stays alive | master `FUSION_QOS` `crc_ok ≈ 20/s`, `nak = 0` |
| 3 | ATT requests accepted on air, never answered | a `STALL READ` from the master times out at ~25 s, then `-ENOMEM` |
| 4 | a controller-executed disconnect produces no advertising recovery | disconnect from the master; the node must not re-advertise |
| 5 | node system workqueue and watchdog stay alive | no reset; `wdt_feed_count` still climbing in the corpse |
| 6 | the DWM tag keeps ranging | listener post/pre poll ratio in 0.94–1.10 |
| 7 | power cycle fully restores | it does |
| 8 | binary latch: no partial degradation, no near-miss | export goes from normal to zero, not to reduced |

And the corpse must show, specifically:
`MPSL Work.pended_on = sync_evt_pool.free`, `sync_evt avail = 0`, `ref = 1`,
`last_owner = INJECTED`. `bsf_v45_corpse_decode.verdict()` should print the
`SINGLETON sync_evt BUFFER HELD` row.

> **SCOPE, AND DO NOT LET THIS DRIFT IN THE WRITE-UP.** B3 proves the
> *starvation → phenotype* consequence chain: that holding this one buffer
> produces exactly what the fleet exhibits. It does **not** prove that real
> wedges begin this way. Those are different claims. If B3 passes, the honest
> statement is "the singleton is sufficient to cause the observed phenotype",
> and the fleet run is what tests whether it is what actually happens.

Release with `V45 LEAK OFF` and confirm the board recovers without a reset.

### B4. Collection-failure retry

Interrupt the retrieval mid-way (kill the host script between pages) and
reconnect. The corpse must still be `present=1` with the same `seq`, and the full
retrieval must succeed on the second attempt. Then ACK with the **wrong** seq and
confirm it is refused and the corpse retained.

---

## Stage C — fleet

### C1. OTA

**All ten boards as ONE continuous batch.** No per-board diagnostic gates
interleaved: the v44 round showed that stopping between boards to inspect one
leaves the rest in mixed states for hours. Every board `confirmed=1` before the
batch is called done.

Image: the Stage A signed binary, `BSF_V45_FAULT_INJECT=0`,
`BSF_CORPSE_FLASH_ENABLED=0`.

### C2. One full-fleet power cycle — mandatory, not optional

After all ten confirm, power-cycle the **whole fleet** once.

This is not hygiene. The trajectory ring's geometry stamp changed (capacity
200 → 510), and the four channel structs plus the CORE are new `.noinit` regions
at new addresses. A retained v44 ring is correctly rejected as
`BSF_RING_BOOT_GEOMETRY`, but the cleanest possible start is a cold RAM, and the
alternative is a fleet where half the boards are carrying rejected junk that
somebody will later have to explain.

### C3. Run

Exposure arithmetic, from `CROSS_RUN_NECESSITY.md` §5 — pooled rate 1 per 26.8
delivered board-hours, 95 % CI 1-per-9.8 to 1-per-98:

| assumption | delivered board-hours for P(≥2 events) ≥ 0.9 | full-fleet 10-node runs at ~6 h |
|---|---|---|
| N8-only rate (optimistic) | 61 bh | 1 |
| pooled point estimate | 104 bh | 2 |
| pessimistic 95 % bound | 381 bh | 6–7 |

**Plan 3–6 runs, not one.** Battery caps a single 10-node run at about 6 h at
full rate (N8's first depletion was at 5 h 35 m). The harness must be restartable
across runs without losing a corpse, and must tolerate a run in which nothing
happens — a null run is a legitimate outcome and must not be treated as a
harness failure.

### C4. Host-side rules, enforced by `tools/v45_corpse_collect.py`

- **On every reconnect, query corpse status first.** With flash disabled,
  `.noinit` is the only copy, and a power cycle destroys it. The N8 run lost
  three corpses to exactly this window.
- **ACK-clear only after verified export.** Every page CRC16, the aggregate
  CRC32, and a successful decode — then the evidence file on disk — then the ACK.
- **A `V45_WEDGE` self-reset is EXPECTED for ≥60 s.** No quarantine, no removal
  from the expected set, no operator alarm beyond a log line. Measured rejoin is
  ~20.7 s plus 0–4 s of jitter; 60 s is the grace window.
- **Every trigger goes into `v45_trigger_ledger.jsonl`** with cause and both
  watermark ages.

### C5. The invariant that makes the rate comparable

**v45 adds no periodic inbound traffic and changes nothing before a trigger.**
The detector is a 1 Hz work item reading atomics; the markers are RAM stores; no
new packets are sent, no new reads are solicited, and the notify path is
unchanged apart from two counters around the existing call. So the v45 wedge rate
is directly comparable to v43's and v44's, and the pooled estimate above stays
valid. State this in the run report; if anything about it stops being true, the
rate statistics stop being poolable.

---

## The deployment blocker, stated once more where it will be read

`BSF_CORPSE_FLASH_ENABLED=0` in every Stage C image, because the deployed
partition map has **zero free bytes** and the only clean carve needs MCUboot
rebuilt and **SWD-reflashed on all ten boards** (`CONTEXT_AUDIT.md` item 11).

Consequence, so nobody is surprised by it:
- a corpse captured and then power-cycled before collection is **lost**;
- a **second** corpse in one power cycle — captured, no reboot, board stays up
  wedged — is lost when the operator power-cycles it.

The self-reboot narrows the first window from hours to about 40 s, which is why
this is acceptable for now rather than blocking. If a run loses a corpse to a
power cycle, that is the trigger to schedule the SWD campaign and switch to
`pm_static_v45_corpse.yml`.
