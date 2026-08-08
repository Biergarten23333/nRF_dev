# DECISIONS — Stage B Step 1

Forks taken autonomously during **G0**. G1–G4 have not run: no probe was
attached and nothing touched the target.

| # | fork | choice | why |
|---|---|---|---|
| 1 | Fault injection had TWO switches — a `Kconfig` symbol `BSF_V45_FAULT_INJECT` and a CMake variable of the same name exported as a compile definition — and the C code guarded on the **CMake** one | collapsed to the Kconfig symbol alone; removed the CMake variable and its compile definition | `CONFIG_BSF_V45_FAULT_INJECT=y` in a conf file did **nothing**, silently, while looking exactly like it had worked. Found while building the validation image, which is the first thing that ever set it. Keeping the Kconfig half rather than the CMake half also makes the brief's "diff the Kconfig fragments" a real diff instead of a claim. |
| 2 | How to express the validation variant | `overlay-validation.conf`, one line, applied with `-DEXTRA_CONF_FILE=` | an env var would not appear in `.config`, so the production-vs-validation difference would not be diffable — which is exactly what the brief asks to see. |
| 3 | `id_target.jlink` device name | generic `CORTEX-M4`, not `NRF52840_XXAA` | the board has two SWD contact sets and the whole point of G1 is not knowing which one the probe is on. Naming the nRF52840 on the DWM1001C's pads returns a device mismatch instead of the answer. Every other script names the part and will correctly refuse the wrong pads. |
| 4 | G1 asks only for `INFO.PART` | also read `FICR.DEVICEID[0..1]` and fold them with the firmware's own `bsl_identity_from_ficr()` | `INFO.PART` identifies the *chip*; there are ten nRF52840s in this fleet. The fold yields the BSFxxxx name the board advertises, so G1 answers "**this** board" rather than "an nRF52840". Same read, two extra words. The Python fold is checked against the C, compiled natively, on four vectors. |
| 5 | `flash_validation.jlink` originally used `verifybin <hex>, 0x0` | removed it; verification is now a **separate J-Link session** that reads the flash back and compares against the hex, plus J-Link's own download verify | `verifybin` takes a binary and an address, not a hex file — it would have failed at the bench, or worse, appeared to pass. A same-session verify can also be satisfied from a download cache; a fresh session cannot. |
| 6 | `-JLinkSettingsFile` was rejected as an unknown option by V9.24a | the correct spelling on this version is `-SettingsFile`; found by probing the binary | this is the sort of thing that costs five minutes at a desk and a wasted bench session with a probe in someone's hand. |
| 7 | **J-Link falls back to connect-under-reset on its own** | cannot be prevented — so `run_jlink.sh` DETECTS it and exits 7 with a loud message | see the box below. This is the most consequential thing G0 found. |
| 8 | `parse_ram_dump.py` read `prio` from DWARF | fall back to `thread_state + 1` | `prio` lives in an anonymous union with `preempt`, so DWARF exposes no member for it and the lookup would have raised `KeyError` — at the bench, on the one dump that matters. |
| 9 | The net_buf wait-object offset was written as "+8, and maybe +12" | derived it from DWARF: `offsetof(net_buf_pool, free) + offsetof(k_queue, wait_q)` = 8 | a hedge in a decoder is how you get plausible nonsense. The self-test pins the value so a Zephyr bump fails here rather than silently un-naming every `pended_on`. |
| 10 | Whether to write a self-test for the dump parser with no real dump available | synthesised a RAM image using the **real** struct offsets from the **real** ELF | it cannot prove the parser handles real data, but it does prove its model of `k_thread`, `net_buf_pool` and `k_queue` matches this build — which is the part that would otherwise be discovered wrong with a probe in hand. It also asserts `--expect-healthy` FAILS on a wedged-shaped dump, so G3 cannot pass on a board that is actually stuck. |
| 11 | `test_v45_partition_overlap.py` globbed `b306-imu-relay-v45-flash*` | widened to any v45 build whose generated map contains the corpse partition, and made "no such map found" a failure | the glob stopped covering the new build the moment it was named `-val-corpse`. A checker that silently matches nothing is worse than no checker. |

---

## The finding that matters, stated on its own

**J-Link V9.24a resets the target by itself when the first attach fails.**

Observed in G0, with no target attached, from the wrapper's own log:

```
Connecting to target via SWD
Failed to attach to CPU. Trying connect under reset.
```

It does this **regardless of `ConnectUnderReset = 0`** in the settings file —
that setting controls the deliberate mode, not the fallback. There is no
command-line option to disable it either; `-ConnectUnderReset`,
`-NoConnectUnderReset`, `-CUR` and `-AutoConnectUnderReset` are all rejected as
unknown by this version. Each was tried.

So it cannot be prevented. It can only be detected, and detecting it is worth a
lot: on a wedged board a silent connect-under-reset destroys `.noinit`, the ring
and the corpse, and then hands back a clean-looking session. The run would
report *"no corpse present"* and the next round would go hunting for a detector
that had in fact worked perfectly.

`run_jlink.sh` therefore greps every session log for it and exits **7** with:

```
[error] J-Link FELL BACK TO CONNECT-UNDER-RESET in session '...'
[error] If this was a wedged board, .noinit / the ring / the corpse ARE GONE.
[error] Do not report this run as 'no corpse present'.
```

In practice the fallback only fires when the first attach fails, and on a
wedged-but-running Cortex-M the first attach succeeds. **G2 is what measures
that.** Until G2 passes, this is a known hazard with a detector, not a solved
problem — and that is exactly why the brief put G2 before any wedged board.

---

## Not decided here, because G0 cannot decide it

- **Whether the probe configuration is safe for a wedged board.** That is G2's
  verdict and it needs hardware. G0's contribution is that the failure mode is
  now instrumented rather than invisible.
- **How many seconds of probe contact a dump needs.** G3 measures it. The
  estimate in the brief is 10–15 s at 4 MHz; the runbook's timing fields are
  left as `<G3>` rather than filled with a guess.
- **Whether the offline thread-state parsing works on real data.** The self-test
  proves the struct model is right for this build. Only a real dump proves the
  rest.
