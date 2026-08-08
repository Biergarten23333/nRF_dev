# SWD_BRINGUP_REPORT — Stage B Step 1, BSF6C53

| | |
|---|---|
| board | **BSF6C53** — N7 wedge board, opened, healthy, on the non-power-cutting charging POGO |
| probe | nRF5340 DK onboard J-Link OB, SNR **1050070698**, debug-OUT → TC2030 |
| J-Link | Commander **V9.24a**, DLL V9.24a |
| branch | `feature/b306-bringup`, v45 offline gate PASSED at `9d0869077` |
| **status** | **G0 COMPLETE. G1–G4 NOT RUN — no probe attached, target never touched.** |

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

## G1 — TARGET IDENTIFICATION ⬜ NOT RUN
## G2 — NO-RESET PROOF ⬜ NOT RUN
## G3 — DUMP REHEARSAL AND TIMING ⬜ NOT RUN
## G4 — FLASH THE VALIDATION BUILD ⬜ NOT RUN

*Requires a probe and an operator. Nothing below G0 has been attempted.*

---

## The three questions this report must answer

| question | answer |
|---|---|
| Is the probe configuration safe for a wedged board? | **NOT YET ESTABLISHED — G2 pending.** G0 found that J-Link can reset on its own and made that failure detectable rather than silent. That is a precondition for G2, not a substitute for it. |
| How many seconds of contact does a dump need? | **NOT YET MEASURED — G3 pending.** Estimate 10–15 s at 4 MHz for 256 KiB. |
| Does the offline thread-state parsing work? | **The struct model is proven correct for this build** (self-test PASS against the real ELF). Whether it survives real data is G3. |

**STAGE B STEP 1 — G0 COMPLETE, G1–G4 PENDING PROBE**
