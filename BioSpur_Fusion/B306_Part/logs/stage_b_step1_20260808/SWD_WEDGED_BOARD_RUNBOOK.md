# SWD WEDGED-BOARD RUNBOOK — one page, hand-held session

**Status: TIMINGS MEASURED 2026-08-08 on BSF6C53** (healthy, v44, 4 MHz SWD).
The numbers below are from real sessions, not estimates. The brief guessed
10–15 s for the dump; it is **1.9 s**.

---

## Before you touch anything

```bash
cd /mnt/nrf_ssd/nRF_dev/BioSpur_Fusion/B306_Part
LOG=logs/stage_b_step1_20260808/session_$(date +%Y%m%dT%H%M%S)
```

The board must be **still wedged** — check the master first. If it has already
rebooted itself, the corpse is in `.noinit` and you want
`tools/v45_corpse_collect.py`, not a probe.

## The sequence

| # | command | measured |
|---|---|---|
| 1 | `tools/swd/run_jlink.sh id_target.jlink $LOG` | **0.14 s** (134–139 ms, n=3) |
| 2 | `tools/swd/run_jlink.sh dump_ram.jlink $LOG RAMDUMP_PATH=$PWD/$LOG/ram.bin` | **1.9 s** (256 KiB) |

**That is the whole hand-held part: about two seconds.** Everything after step 2
is offline and the probe can be released.

Step 1 exists because it is cheap and it fails loudly on the wrong pad set — and
because of what step 1 is now also for, below. Do **not** skip it to save time:
it costs 0.14 s of the 2 s total.

### Step 1 is the contact check, and contact is the real risk

Six attaches were made during G1–G4. **Two failed**, one of them mid-session
with the probe already held. A failed attach makes J-Link fall back to
connect-under-reset, and on a wedged board that is the corpse gone.

`VTref` does **not** tell you whether contact is good — it read `3.300V` in both
failures. The tell is `InitTarget` duration in the session log:

| `InitTarget() end - Took` | meaning |
|---|---|
| **1.6–1.9 ms** | good contact (all four successful attaches) |
| **~104 ms**, then `Failed to attach to CPU` | marginal contact — re-seat, do not proceed |

`g3_dump.sh` now runs step 1 and **refuses to attempt the dump** if it fails
(exit 8). That does not remove the hazard, since the fallback cannot be
disabled — it makes the first attach the cheap read-only one, so bad contact is
discovered before the dump is spent rather than by losing it.

**For a board that actually matters, do not hand-hold the TC2030.** A 2-of-6
failure rate is fine for a rehearsal on a healthy board and unacceptable for a
one-shot corpse. Clamp it.

## Release the probe, then

```bash
# The ELF must be the one the board is RUNNING. Let the flash backup say which:
tools/swd/identify_flash_image.py $LOG/BSF6C53_flash_backup.bin \
  --cache $LOG/build_fingerprints.json --json $LOG/image_id.json
tools/swd/parse_ram_dump.py $LOG/ram.bin \
  --elf "$(python3 -c 'import json;print(json.load(open("'"$LOG"'/image_id.json"))["elf"])')" \
  | tee $LOG/threads.txt
```

Read the `pended_on` column. That one column is the answer to the round:

| `MPSL Work` pended on | verdict |
|---|---|
| `sync_evt_pool.free.wait_q` | the singleton is held — candidate 1 confirmed |
| `hci_rx_pool.free.wait_q` | true RX-pool exhaustion |
| an address ~`0xd0` past its own tid | **this is the healthy baseline** — its own work-queue. Look at `BT RX WQ` and the corpse |

**Measured healthy baseline (BSF6C53, v44, 2026-08-08)** — keep it next to the
wedged dump, because "PENDING" alone is not abnormal. 15 threads walked; every
one of them was PENDING or SUSPENDED except `idle`:

```
MPSL Work    0x20004890  PENDING  -10  0x20004960   <- its own workq, healthy
BT RX WQ     0x20003720  PENDING   -8  0x200037f0   <- its own workq, healthy
idle         0x20004978  RUNNING/READY
```

Full baseline: `session_*/BSF6C53_ram_rehearsal.threads.txt`. A pool name in
that column is the finding; a bare address just past the thread's own tid is
the thread sitting on its own queue with nothing to do.

## OPTIONAL — attach an RTT reader for the observation window

**Probe-gated. RTT runs over J-Link, so this needs the probe attached and is
subject to the same `PROBE GO` rule as every other SWD action. It is not a
"free" observation.**

```bash
tools/jlink_rtt_transport.py            # see its --help for SNR/device args
```

**What it can get that nothing else can.** The host log is where the BLE stack
names its own failures, and two of those messages decide a whole round:

| message | source | what it settles |
|---|---|---|
| `Fatal error (%d). Disconnecting %p` | `conn.c` `tx_processor()` | an async send failure tore the connection down, **and the errno** |
| `ATT Timeout for device %s` | `att.c:3184` | the 30 s ATT bearer timeout fired instead |

On 2026-08-09 neither could be recovered. The build is `LOG_MODE_DEFERRED` with
`CONFIG_LOG_BUFFER_SIZE=1024`, drained continuously to `CONFIG_LOG_BACKEND_RTT`
— and with **no host attached, RTT fills once during boot and skips everything
after**. Reading `_acUpBuffer` out of the wedge RAM dump recovers only the boot
banner, ending in `--- 11 messages dropped ---`. So the one record that would
have named the teardown path was never stored.

**Attach it before the wedge, not after.** After the fact there is nothing to
read. If the probe is going to be clamped for an observation window anyway, an
RTT reader costs nothing extra and may name the mechanism before the corpse
does.

It is a **second source**, not the primary one: the R4 markers
(`BSF_V45_TX_SEND_FAIL`, `BSF_V45_TX_FATAL_DISCONNECT`, and the
`bt_conn_set_state` site ids) live in `.noinit` and survive without any host
attached. Do not let a missing RTT reader stop a session.

## RULE — a new reading script is hand-checked against raw data once, before it is believed

**Any script written to interpret evidence must have its first verdict checked
by hand against the raw bytes it read. Once. Every new script, no exceptions.**

This is not caution, it is a measured defect rate. Six reading tools in this
campaign have produced a confident, wrong conclusion:

| # | tool | false verdict | actual defect |
|---|---|---|---|
| 1 | `test_v45_partition_overlap.py` | PASS | stale glob — validated a build nobody was running |
| 2 | `test_bsf_v45_decoder.py` | PASS | new decoder checked against old ELFs |
| 3 | `test_v45_lifetime_contract.py` | PASS | passed on the known-broken build; no check that the mechanism was in *that* ELF |
| 4 | `bsf_v45_conn_sites.h` generator | "16 sites", then "23" | truncated enumeration; the real count is 24 |
| 5 | `rtt_t2_capture.py` | `T2_REPRODUCED=NO` | compared `up_ms` (ms) against `window + 15` (s) — `23409 < 55` |
| 6 | `seg_flash.sh` `report_attach()` | `InitTarget=n/a`, every press | grepped the **`id_target`** log, which connects as generic `CORTEX-M4` and so structurally never contains `InitTarget`. The regex was correct; the file could not answer. |

Every one of them failed in the safe-looking direction: they said *nothing is
wrong*. A reading tool that is broken does not crash, it agrees with you.

**Case 5 is the cheapest illustration.** The script printed `NOT REPRODUCED`
while the RTT buffer it had just written to disk contained an MCUboot banner and
a `boot_id` increment — the reset was in the evidence, and the tool that read
the evidence denied it. Thirty seconds of reading `rtt_capture.txt` by hand
would have caught it; nobody did, because the tool had already answered.

The check that satisfies this rule is small and concrete:

- take **one** record the script classified, find it in the raw file, and
  confirm the classification by eye;
- confirm every numeric comparison's **units** on both sides — the value's, and
  the threshold's;
- confirm the script read the artefact you think it read (path, build, ELF, and
  mtime), not a same-named one from an earlier round.

A script may be trusted unattended only after it has been checked once this way,
and again after any change to what it parses.

## Contact statistics — hand-held TC2030, cumulative

`InitTarget` now comes from `contact_probe.jlink` (read-only, `NRF52840_XXAA`,
no reset), because `id_target.jlink` cannot report it — see row 6 above.

| date | press | contact on attempt | InitTarget | fallbacks (pre-flash) | outcome |
|---|---|---|---|---|---|
| 08-08 | G1–G4 | 4 of 6 attaches good | 1.6–1.9 ms | 2 | 2 marginal at ~104 ms |
| 08-09 | flash r7-prod | 1 | 1.79 ms | 0 | flash 9941 ms, readback PASS |
| 08-09 | flash r7-val | 4 | 1.99 ms | 2 during contact check | `Failed to preserve target RAM`, nothing written |

**The band is necessary, not sufficient.** The failed press measured 1.99 ms
against the good press's 1.79 ms — on n=2 that is the same number, and no band
narrow enough to separate them would be anything but fitted noise. What
separated them is that the failed press **needed four attempts to attach**.

So the rule `seg_flash_gated.sh` enforces is: a band check to catch a genuinely
bad seat (the two regimes are 50× apart, ~1.8 ms vs ~104 ms), plus — once any
attempt in a hold has failed — **two consecutive clean measurements before
flashing**. A hand that has slipped twice is still marginal, and the attempt
that finally attaches is not evidence to the contrary. That is the strongest
claim tonight's data supports.

**Never flash on the first success after a failure.** That is exactly the
sequence that produced `Failed to preserve target RAM @ 0x20000000-0x2003FFFF`,
`Failed to prepare for programming` and `SYSRESETREQ has confused core`.

## Three things that will ruin the session

**1. A reset destroys everything.** `.noinit`, the trajectory ring, the corpse
and the wedge state all die on reset, and the board then looks healthy and
innocent. `run_jlink.sh` refuses any script containing a reset command unless
`--allow-reset` is passed, and **only `flash_validation.jlink` ever gets that**.

**2. J-Link can reset you without being asked.** V9.24a prints
`Failed to attach to CPU. Trying connect under reset.` whenever the first attach
fails, and there is no setting or command-line option that disables it — this
was verified, not assumed (`-ConnectUnderReset`, `-NoConnectUnderReset`, `-CUR`
and `-AutoConnectUnderReset` are all rejected as unknown options). `run_jlink.sh`
therefore **greps every log for it and exits 7**. If you see

```
[error] J-Link FELL BACK TO CONNECT-UNDER-RESET in session '...'
```

the evidence is gone. Say so in the report. **Do not** write "no corpse
present" — that is a different and much worse conclusion, and it is the one
that would send the next round chasing a detector that actually worked.

**3. Do not leave the core halted.** Every dump script ends in `go`. The
watchdog is paused while halted (`WDT_OPT_PAUSE_HALTED_BY_DBG`,
`firmware/src/main.c` in `watchdog_start()`), so a long halt is survivable — but
a halted core with the probe unplugged is a board that will not feed the
watchdog and will reset itself in `WATCHDOG_TIMEOUT_MS` = 30 s, taking the
`.noinit` you came for. Re-verify that Kconfig line before every campaign.

## Power

BSF6C53 lives on the **non-power-cutting** charging POGO, so it stays powered
indefinitely and there is no race. Never dock it on a standard dock: that dock's
signal pin pulls the regulator enable low, the board loses power, and `.noinit`
goes with it.

## If step 1 says the wrong thing

| `INFO.PART` | meaning | do |
|---|---|---|
| `0x52840` | B306 — correct pads | continue |
| `0x52832` | DWM1001C — wrong pads | move to the other contact set |
| connect fails outright | possibly APPROTECT | **STOP.** Do not run `recover`: it mass-erases the part, and with it the firmware, the settings and the corpse. Report and stop. |

`id_target.jlink` also prints `FICR.DEVICEID[0..1]`; run
`tools/swd/decode_target_id.py` on them and confirm it says `BSF6C53`. That
turns "an nRF52840" into "**this** nRF52840", which matters when ten of them are
on the bench.
