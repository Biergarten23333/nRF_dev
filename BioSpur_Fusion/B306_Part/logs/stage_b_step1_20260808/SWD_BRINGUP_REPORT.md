# SWD_BRINGUP_REPORT — Stage B Step 1, BSF6C53

| | |
|---|---|
| board | **BSF6C53** — N7 wedge board, opened, healthy, on the non-power-cutting charging POGO |
| probe | nRF5340 DK onboard J-Link OB, SNR **1050070698**, debug-OUT → TC2030 |
| J-Link | Commander **V9.24a**, DLL V9.24a |
| branch | `feature/b306-bringup`, v45 offline gate PASSED at `9d0869077` |
| session | `logs/stage_b_step1_20260808/session_20260808T194513/` |
| **status** | **G0–G4 ALL PASS**, plus a clean 15-minute post-G4 soak. Board is programmed with the validation+corpse image and back on the fleet. |

---

## G0 — PREPARE OFFLINE ✅ COMPLETE

### G0.1 Validation build, and the switch that was not connected

Building the validation image for the first time exposed a defect in the v45
work: fault injection had **two** switches — a Kconfig symbol
`BSF_V45_FAULT_INJECT` and a CMake variable of the same name exported as a
compile definition — and the C code guarded on the CMake one. Setting
`CONFIG_BSF_V45_FAULT_INJECT=y` in a conf file therefore did **nothing**,
silently, while looking exactly like it had worked.

Collapsed to the Kconfig symbol alone. The CMake variable and its compile
definition are gone; the build-time warning now reads the Kconfig value.

**The Kconfig diff the brief asks for, in full:**

```
$ diff <(grep -v '^#' prod/.config | sort) <(grep -v '^#' val/.config | sort)
328a329
> CONFIG_BSF_V45_FAULT_INJECT=y
```

**Exactly one line.** Production and validation differ by nothing else.

Verified in the linked image rather than only in the config — `nm` on the two
ELFs:

| symbol | production | validation |
|---|---|---|
| `bsf_v45_sync_evt_leak` | **6 bytes** (`return -ENOTSUP` stub) | **80 bytes** (the real thing) |

FLASH 231 976 → 232 088 B, **+112 bytes**, which is the injection code and
nothing else.

### G0.2 Validation + flash corpse partition

Third build: `overlay-validation.conf` **and** `BSF_CORPSE_FLASH_ENABLED=1`
**and** `pm_static_v45_corpse.yml`.

| | prod | val | val-corpse |
|---|---|---|---|
| `.config` differences vs val | 1 line | — | **0 lines** |
| `app` partition | `0x79e00` | `0x79e00` | **`0x77e00`** |
| `bsf_v45_flash_persist` | 2 B stub | 2 B stub | **416 B real** |
| FLASH | 46.47 % | 46.49 % | **47.40 %** of the shrunken slot |
| RAM | 52.88 % | 52.88 % | **56.01 %** (the 8 KiB staging buffer) |

Worth stating plainly: **val and val-corpse have identical `.config` files.**
The corpse variant is selected by a CMake compile definition and a partition-map
file, neither of which is a Kconfig symbol, so `diff` over `.config` cannot see
it. The differences above are read from the ELF and the generated partition map
instead, which is why they are listed that way.

Generated map from the real build:

```
bsf_corpse_partition:  address 0xfc000  end 0x100000  size 0x4000  (16 KiB)
app:                   0xc200 .. 0x84000   (0x77e00)
mcuboot_secondary:     0x84000 .. 0xfc000  (0x78000)
```

`test_v45_partition_overlap.py` → **PASS** against that generated map, not just
against the overlay text. (The checker's glob was widened; it had silently
stopped matching anything the moment the build was renamed.)

**Slot margin:** signed image 233 419 B into a 491 008 B slot — **47.5 % used,
251 KiB spare**. The 8 KiB haircut is free.

**And the fact that makes SWD mandatory rather than preferred:** MCUboot's own
hex changed.

| build | `mcuboot/zephyr/zephyr.hex` |
|---|---|
| prod, val | `3ce8194b94b3f89f…` |
| **val-corpse** | **`e73de639d43d9287…`** |

The bootloader compiles its flash map in. Bootloader and application must go on
together or they disagree about where the secondary slot ends. `merged.hex`
carries both (spans `0x00000`–`0x44fcc`) and is what G4 programs.

### G0.3 Scripts — committed before use, under `B306_Part/tools/swd/`

| file | resets? | halts? | writes? |
|---|---|---|---|
| `id_target.jlink` | no | no | no |
| `attach_noreset.jlink` | no | yes | no |
| `dump_ram.jlink` | no | yes | no |
| `dump_flash.jlink` | no | no | no |
| `verify_flash.jlink` | no | no | no |
| `flash_validation.jlink` | **yes, deliberately** | yes | **yes** |

All go through `run_jlink.sh`, which always passes
`-NoGui 1 -ExitOnError 1 -SelectEmuBySN 1050070698` — never a bare `JLinkExe`,
which would open a GUI probe picker.

Three gates in the wrapper, **all tested without a probe**:

| gate | trigger | exit |
|---|---|---|
| reset refusal | a script containing `r`/`reset`/`RSetType` without `--allow-reset` | **5** ✅ |
| placeholder | an unsubstituted `RAMDUMP_PATH` etc. reaching J-Link | **4** ✅ |
| reset-fallback detector | `connect under reset` in the session log | **7** ✅ |

Also fixed before the bench: `flash_validation.jlink` used `verifybin` on a hex
file (wrong argument type — it would have failed with a probe in hand), and
`-JLinkSettingsFile` is not an option on V9.24a; the correct spelling is
`-SettingsFile`.

### G0.4 Offline analysis path — proven now, not at the bench

`tools/swd/parse_ram_dump.py` walks `_kernel.threads` from a raw 256 KiB dump
using symbols and DWARF from the ELF, and prints per thread: state, priority,
**`pended_on` resolved to a named wait object**, PSP, and stack used/size. It
then locates every `.noinit` landmark (`stall_ring`, `bsf_v45_ch`,
`bsf_v45_core`, `bsf_v45_bank`, `retained_corpse`, `retained_stall`) and checks
their magics.

`tools/swd/test_parse_ram_dump.py` synthesises a RAM image at the **real**
struct offsets from the **real** ELF and checks the whole path → **PASS**.
Two model errors it caught:

- `prio` is inside an anonymous union, so DWARF exposes no member and the
  lookup raised `KeyError`. Now falls back to `thread_state + 1`.
- the net_buf wait-object offset had been written as "+8, and maybe +12". Now
  derived: `offsetof(net_buf_pool, free) + offsetof(k_queue, wait_q)` = **8**,
  pinned by the self-test.

It also asserts that `--expect-healthy` **fails** on a wedged-shaped dump, so
G3 cannot pass on a board that is actually stuck.

### G0.5 Runbook

`SWD_WEDGED_BOARD_RUNBOOK.md` is written, with the timing fields left as
`<G3>` rather than filled with a guess.

---

## ⚠ The G0 finding that changes how G1–G3 must be read

**J-Link resets the target on its own when the first attach fails.** From the
wrapper's log, with no target attached:

```
Connecting to target via SWD
Failed to attach to CPU. Trying connect under reset.
```

This happens **despite `ConnectUnderReset = 0`** in the settings file, and there
is **no way to disable it** — `-ConnectUnderReset`, `-NoConnectUnderReset`,
`-CUR` and `-AutoConnectUnderReset` were each tried and are all rejected as
unknown options on V9.24a.

It cannot be prevented, so it is detected: `run_jlink.sh` greps every log and
exits 7. That matters because on a wedged board a silent connect-under-reset
destroys the evidence and then returns a clean-looking session — the run would
report "no corpse present" and the next round would go hunting for a detector
that had worked perfectly.

In practice the fallback fires only when the first attach fails, and on a
wedged-but-running Cortex-M the first attach succeeds. **G2 is the measurement
that turns that from an expectation into a fact.**

## Pre-checked for G1/G2

- **APPROTECT.** `CONFIG_NRF_APPROTECT_LOCK is not set` in both the app and
  MCUboot, and the merged hex writes **zero** UICR bytes — so the image does not
  lock the debug port. If G1's connect nonetheless fails, that is a STOP:
  **never run `recover`**, which mass-erases the part and everything on it.
- **Watchdog during halt.** `wdt_setup(watchdog, WDT_OPT_PAUSE_HALTED_BY_DBG)`
  is present in `watchdog_start()`, `firmware/src/main.c`. Verified in the
  current tree, as the brief requires, so a halt does not become a watchdog
  reset. `WATCHDOG_TIMEOUT_MS` is 30 000.

---

## How G2's two operator-watched legs became measurements

G2's proof is three-way, and `g2_noreset.sh` wrote two legs as things a person
watches: the master shows no disconnect, and the operator sees no re-connect LED
blink. In a headless session nobody is watching, and a gate whose evidence is
"somebody was looking" is not evidence.

BSF6C53 turned out to be **live and connected to the Fusion Master**, reporting
once a second:

```
FUSION_TELEMETRY proto=7 name=BSF6C53 node_ms=4071252 ... reset_reason=1 ...
```

`node_ms` is the node's own uptime; `reset_reason` is latched at boot. **A reset
is the only thing that can restart the one or change the other, and a disconnect
does neither** — which is what lets the two be judged separately. So both legs
became `tools/swd/link_witness.py`, a passive reader that holds dtr/rts low
exactly as `fusion_session.py` does and only reads.

One trap on the way: the first open replayed the master's boot banner, which
looks exactly like having rebooted it. It had not — the very first record
carried `master_ms=6914962`, 115 minutes, from a boot long before the port was
opened. It was a buffered CDC flush. `--settle` discards it; without that, two
hour-old `FUSION_DISCONNECTED` lines from the replay would have failed G2 for
events that predated the session.

---

## G1 — TARGET IDENTIFICATION ✅ PASS

```
FICR.INFO.PART    0x52840   nRF52840 — B306 / NINA-B306, CORRECT PAD SET
INFO.VARIANT      0x41414430  AAD0      INFO.RAM 256 KiB   INFO.FLASH 1024 KiB
FICR.DEVICEID     0xe17c1c19 0x310f1ec9
derived identity  0x6C53  ->  BSF6C53   BOARD MATCH
```

**138 ms**, read-only, no halt, no reset. The DEVICEID fold turned "an nRF52840"
into "this one" exactly as intended.

## G2 — NO-RESET PROOF ✅ PASS

`attach_noreset.jlink` — connect, halt, read DEVICEID while halted, `go`.

| leg | measurement |
|---|---|
| J-Link session | **155 ms**, `InitTarget` 1.58 ms, **no** connect-under-reset fallback |
| node uptime | `node_ms` 4 949 791 → 4 967 802 = **+18 011 ms across 18 049 ms of wall clock** |
| `reset_reason` | **1 → 1, unchanged** |
| `watchdog_feeds` | 4 946 → 4 964, monotonic |
| master link | **0 disconnects, 0 reconnects** — the link never even dropped |

**This is the measurement the whole of G0 was a precondition for.** The probe
configuration does not reset a running Cortex-M on attach, and that is now a
fact about this bench rather than an expectation.

## G3 — DUMP REHEARSAL AND TIMING ✅ PASS

**Flash backup** 1 MiB in 7.156 s → `0fd13b36ba340d1d…` (the restore path G4
refuses to run without).

**The board was running v44, not v45.** Identified from the backup, which is why
`g3_dump.sh` no longer takes the ELF on trust:

```
MCUboot primary slot 0x00c000, image v0.1.44+0, code ends 0x042a18
99.973%  255423 B  b306-imu-relay-v44-b   [68 B differ, all in the signature TLV]
99.973%  255423 B  b306-imu-relay-v44-a   [68 B differ, all in the signature TLV]
  the tied builds agree on all 3907 symbol addresses
IMAGE_ID PASS -- code-identical (signature TLV differs)
```

The 68 differing bytes start at `0x42a6b`, past the `0x42a18` image end — every
one is signature, none is code. The secondary slot still held a stale **v43**
OTA image, which is where the second `FW_MARKER` string in the dump came from.

**RAM dump: `RAM_DUMP_SECONDS=1.9`** for all 256 KiB at 4 MHz. The brief
estimated 10–15 s. **The hand-held part of a wedged-board session is about two
seconds**, 0.14 s of it the contact check.

Parse against the v44 ELF: **15 threads walked, G3 healthy-board check PASS.**
`pended_on` resolved to named objects (`uart_data_sem`, `publisher_sem`,
`notify_job_sem`), and the `.noinit` landmarks read back with correct magics —
`stall_ring` `0x52334236` OK, `retained_stall` `0x56333852`. `bsf_v45_*` are
absent, correctly: they do not exist in a v44 image.

Witness across the dump: `node_ms` +12 008 ms over 11 932 ms wall,
`reset_reason` unchanged, **one** disconnect (`reason=0x08`, supervision
timeout) and no reset. The halt drops the link and the node survives it —
exactly the distinction the witness exists to make. Uptime tracked wall clock
straight through the halt, confirming it is RTC-driven.

## G4 — FLASH THE VALIDATION BUILD ✅ PASS

`merged.hex` `b403c458dfc89bea…` — MCUboot and app together, which is the whole
reason this is SWD and not OTA.

| | |
|---|---|
| erase + program | 266 240 B in **8.958 s**, 90 KB/s |
| J-Link download verify | O.K. |
| **independent readback, separate session** | 282 624 B (`0x45000`) read back off the part, **0 mismatches** |
| reboot → reconnect | `FUSION_CONNECTED` **0.23 s** after the session ended, `FUSION_BRIDGE_READY` at 1.5 s |
| new-image confirmation | `node_ms` restarts at 3 725 and tracks 1001 ms/s; `reset_reason` **1 → 4** (SREQ, i.e. J-Link's `r`) |

## POST-G4 — 15-MINUTE QUIET SOAK ✅ PASS

*(Not a gate in the brief, which defines G0–G4. Run because a validation image
carrying fault-injection hooks has to be shown not to fire on its own.)*

The board was not touched. `link_witness.py` watched the master link for the
whole window.

| | |
|---|---|
| telemetry | **900 records over 899.5 s** — exactly 1 Hz, no gaps |
| `node_ms` | 53 753 → 953 261 = **+899 508 ms across 899 497 ms of wall clock** (11 ms of skew in 15 minutes) |
| `reset_reason` | **4 → 4, unchanged** — no reboot, so no trigger took the reboot budget |
| `watchdog_feeds` | 53 → 952, monotonic, one per second |
| link | **0 disconnects, 0 reconnects** |
| `ring_drop`, `sweep_drop`, `crc`, `header`, `drop_err`, `notify_errno`, `malformed`, `duplicate`, `reorder`, `imu_missed_deadlines`, `logger_drop` | **identically 0 across all 900 samples** |

No `FUSION_DISCONNECTED`, no `FUSION_FAIL`, no stall or corpse announcement —
only the normal periodic line set.

A trigger that reboots is visible in the above. A trigger that *captures without
rebooting* is not, so one read-only status query was made after the window
closed:

```
FUSION_REPLY name=BSF6C53 source=B306 correlation=1
  text=V45 present=0 seq=0 cause=0 len=944 pages=5 core=944 ch=4 ring=510 flash=1
```

**`present=0`. No corpse, no false trigger, in either form.** The validation
image's fault injection is reached only through the `V45 LEAK` vendor command
(`main.c:3086`) and was never issued.

### That query also found a bug outside this step's scope

The first attempt returned `no reply to 'V45 STATUS'`. The board was not at
fault — `tools/v45_corpse_collect.py` greps for **`FUSION_CONTROL_REPLY`, a
string that exists nowhere in this repo or in the master firmware.** The decoder
emits `FUSION_REPLY` and every other tool consumes it.

The collector therefore timed out on every command from every node, and **would
have reported a real corpse as "no reply" and collected nothing.** It was
committed with the v45 work and had never been run against hardware; there is no
ledger file anywhere under `logs/`. Fixed to match `FUSION_REPLY` with
`source=B306`, verified offline against the captured reply and re-run live.

This is the tool that collects the deliverable the flashed image exists to
produce, so it is called out here rather than left as a footnote.

---

## What G1–G4 found that G0 could not

Six defects, five of them in code G0 had reported as tested.

| # | defect | how it would have failed at a wedged board |
|---|---|---|
| 1 | **The reset gate refused `attach_noreset.jlink`** — bare `r` in the regex matched the `r` of **`regs`**, which is in both that script and `dump_ram.jlink` | exit 5 on the two scripts the gate exists to permit. G0 tested that the gate *fires*; it never tested that it *lets through*. |
| 2 | `g4_flash.sh` computed the readback length in **decimal** | J-Link parses bare numerals as hex: `282624` → `0x282624`, 2.6 MB off a 1 MiB part |
| 3 | `jlink_settings.ini` had `ScriptFile =` empty | a parse error printed on every command of every session |
| 4 | `g3_dump.sh` took the ELF on trust, and the runbook's example is the **validation** ELF | the rehearsal board ran v44; the wrong ELF walks `_kernel.threads` from the wrong address and prints confident nonsense instead of failing |
| 5 | The reset alarm fired on `flash_validation` | "the corpse ARE GONE" in the log of the one step whose reset is correct |
| 6 | **Contact, not configuration, is the real hazard** | see below |

### The finding that should change how a wedged board is approached

Six attaches were made. **Two failed** — one before the probe was pressed, one
mid-session with it already held. A failed attach is exactly what triggers the
undisableable connect-under-reset fallback.

**`VTref` does not tell you contact is good: it read `3.300V` in both failures.**
The discriminator is `InitTarget` duration — **1.58–1.88 ms** on all four
successes, **104 ms** on the failure before it gave up.

G3 now runs the cheap read-only `id_target` first and refuses the dump if it
fails (exit 8). That does not remove the hazard, since the fallback cannot be
disabled; it makes the *first* attach the cheap one, so bad contact is found
before the dump is spent rather than by losing it. **For a real wedged board the
TC2030 should be clamped, not hand-held** — a 2-in-6 failure rate is fine for a
rehearsal and unacceptable for a one-shot corpse.

### The exit-7 that was not a reset

G3's first RAM dump exited **7**. Standing instruction is to stop there, and it
did: G4 did not run. The witness then settled what had actually happened —
`node_ms` +25 015 ms over 24 976 ms wall, `reset_reason` unchanged, zero
disconnects. **The attach never reached the target.** Exit 7 means *J-Link
attempted the fallback*, which is not the same claim as *the target reset*, and
on a wedged board that distinction is the difference between "the evidence is
gone" and "re-seat the probe".

---

## The three questions this report must answer

| question | answer |
|---|---|
| Is the probe configuration safe for a wedged board? | **YES, measured.** Attach + halt cost 155 ms, uptime advanced 18 011 ms across 18 049 ms of wall clock, `reset_reason` unchanged, and the link did not even drop. The remaining risk is **contact, not configuration** — clamp the probe. |
| How many seconds of contact does a dump need? | **1.9 s** for 256 KiB at 4 MHz, plus 0.14 s for the contact check. The brief's 10–15 s estimate was 5–8× high. |
| Does the offline thread-state parsing work? | **YES, on real data.** 15 threads walked from a real dump, `pended_on` resolved to named wait objects, `.noinit` magics correct, healthy-board check PASS. |

**STAGE B STEP 1 — G0–G4 COMPLETE, ALL PASS. 15-minute soak clean, no false trigger.**
