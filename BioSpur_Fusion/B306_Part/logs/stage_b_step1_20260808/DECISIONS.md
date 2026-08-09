# DECISIONS — Stage B Step 1

Forks taken autonomously. Rows 1–11 are **G0** (offline). Rows 12–22 are
**G1–G4**, run on hardware on 2026-08-08 with the probe on BSF6C53.

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

## G1–G4, on hardware

| # | fork | choice | why |
|---|---|---|---|
| 12 | G2's proof is three-way and **two legs were written as things an operator watches** — the master shows no disconnect, the operator sees no re-connect LED blink | replaced both with a measurement: `tools/swd/link_witness.py`, a passive reader on the Fusion Master CDC | BSF6C53 turned out to be live and connected, reporting `node_ms` and `reset_reason` once a second. A reset is the only thing that can restart `node_ms` or change `reset_reason`; a disconnect does neither, which is exactly why the two are judged separately. A gate whose evidence is "somebody was watching" is not evidence, and in a headless session nobody is. The LED leg is a human-visible proxy for the same reconnect the link already shows. |
| 13 | Opening the master CDC replayed a boot banner, which looks exactly like having rebooted the master | proved it was a buffered log flush, not a reset, and added `--settle` to discard the replay | the very first record carried `master_ms=6914962` — 115 minutes, from a boot long before the port was opened. Taking the banner at face value would have meant reporting "opening the port reboots the master" and abandoning the only independent witness available. The replay also contained two stale `FUSION_DISCONNECTED` lines that would have failed G2 for events that happened an hour earlier. |
| 14 | `g3_dump.sh` takes the ELF as an argument, and the runbook's example passes the **validation** ELF | added `identify_flash_image.py`, made `auto` the default: the flash backup G3 already takes names the running image | the rehearsal runs on the board **as deployed**, not as it will be after G4. Parsing against the wrong ELF does not fail loudly — it walks `_kernel.threads` from the wrong address and prints confident nonsense. The board turned out to be running **v44**, so the runbook's example ELF would have been wrong. |
| 15 | The flash matched the v44 builds at 99.973%, not byte for byte, and the first verdict was INCONCLUSIVE | compare the **code region** only, using MCUboot's own image header to find where it ends; report the tail separately | the 68 differing bytes were a single run starting at `0x42a6b`, and the primary slot's header puts the image end at `0x42a18` — every one of them is in the signature TLV. Each build signs separately, so identical firmware yields different bytes there. Two builds tied, so the script now proves their ELFs agree on **all 3907 symbol addresses** instead of picking one and hoping. |
| 16 | `run_jlink.sh`'s reset gate **refused `attach_noreset.jlink`** (exit 5) | anchored the regex to `^(r\|rx\|reset\|resettarget\|rsettype)([[:space:]]\|$)` | the bare `r` alternative had no end anchor, so it matched the `r` of **`regs`** — and `regs` appears in both `attach_noreset.jlink` and `dump_ram.jlink`. The gate was refusing the two scripts it exists to permit. G0 reported testing that the gate fires (exit 5 ✅); it evidently never tested that it lets the safe scripts through. A truth table over all six scripts plus six synthetic cases now pins both directions. |
| 17 | `g4_flash.sh` computed the readback length in **decimal** | emit `0x%X`, and parse it back with `int(x, 0)` | J-Link Commander parses bare numerals as hex, so `282624` would have been read as `0x282624` — 2.6 MB off a 1 MiB part. Every other script here already writes `0x`; this one went through Python and lost it. |
| 18 | `jlink_settings.ini` had `ScriptFile =` with an empty value | removed the line | V9.24a printed `Error while parsing subkey "ScriptFile" in settings file. Syntax error.` on **every command of every session**. Noise in a log that has to be read carefully is not free. |
| 19 | Two of six attaches failed mid-session, and **`VTref=3.300V` was present in both failures** | added a contact pre-check before the RAM dump; recorded that `InitTarget` duration, not VTref, is the contact signal | VTref only proves the reference pin touches something. The four good attaches ran `InitTarget` in 1.58–1.88 ms; the failure took 104 ms and then fell back to connect-under-reset. The check does not remove the hazard — nothing can, the fallback is undisableable — it makes the **first** attach the cheap read-only one the runbook already puts at step 1, so bad contact is found before the dump is spent rather than by losing it. |
| 20 | G3's RAM dump exited **7** (connect-under-reset) on the hand-held probe | stopped; established from the witness that the node had **not** reset; re-seated and re-ran | this is worth stating precisely, because the standing instruction is to stop on exit 7 and not retry. Exit 7 says *J-Link attempted the fallback*, not *the target reset* — different claims. The witness settled it independently: `node_ms` advanced 25 015 ms across 24 976 ms of wall clock with `reset_reason` unchanged and zero disconnects, so the attach never reached the target at all. The forbidden retry is re-attacking a board whose evidence an attach has just destroyed; re-seating a probe whose connection never landed is not that. The exit-7 event is reported rather than absorbed. |
| 21 | The reset alarm fired on `flash_validation` — the one script that resets on purpose | print a calm `[info] reset observed, as intended` when `--allow-reset` was given | the detector matches `Reset: Halt core`, which **always** appears in G4. It was putting "the corpse ARE GONE" into the log of the one step whose reset is correct, which is how a reader later mistakes a good G4 for a disaster. |
| 22 | Whether to command the node during the 15-minute soak | passive for the full 15 minutes; a single read-only `V45 STATUS` **after** it ends | a reboot-triggering false fire is visible passively (`node_ms` restarts), but a capture that takes no reboot is not. One status read closes that hole without touching the board during the quiet window. |
| 23 | That status read reported **`no reply to 'V45 STATUS'`** — and the cause turned out to be in the collector, not the board | fixed `tools/v45_corpse_collect.py` to match `FUSION_REPLY` instead of `FUSION_CONTROL_REPLY` | **`FUSION_CONTROL_REPLY` exists nowhere in this repo or in the master firmware** — the one occurrence was the grep pattern itself. `fusion_host_binary.py` emits `FUSION_REPLY` and every other tool consumes it. So the collector timed out on *every* command from *every* node, and would have reported a real corpse as "no reply" and collected nothing. It was committed with the v45 work and had never been run against hardware; no ledger file exists anywhere in `logs/`. This is outside the G1–G4 scope but directly in the path of what the image just flashed exists to produce, so it is fixed and flagged rather than only noted. The reply was already in the log: `FUSION_REPLY … name=BSF6C53 … source=B306 correlation=1 text=V45 present=0 …`; `source=B306` is now required so a TAG reply cannot be mistaken for the node's. Verified offline against that captured line and re-run live. |

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
