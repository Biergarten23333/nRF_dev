# SWD WEDGED-BOARD RUNBOOK — one page, hand-held session

**Status: TIMINGS NOT YET MEASURED.** Every `<G3>` below is filled in at G3 with
a real number from a real dump. Do not take this card to a wedged board until
they are.

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

| # | command | contact time |
|---|---|---|
| 1 | `tools/swd/run_jlink.sh id_target.jlink $LOG` | ~`<G3>` s |
| 2 | `tools/swd/run_jlink.sh dump_ram.jlink $LOG RAMDUMP_PATH=$PWD/$LOG/ram.bin` | ~`<G3>` s |

**That is the whole hand-held part.** Everything after step 2 is offline and the
probe can be released.

Step 1 exists because it is cheap and it fails loudly on the wrong pad set. If
you are certain of the pads and the board is on borrowed time, step 2 alone is
sufficient — it names the device itself and will refuse the nRF52832.

## Release the probe, then

```bash
tools/swd/parse_ram_dump.py $LOG/ram.bin \
  --elf builds/b306-imu-relay-v45-val-corpse/firmware/zephyr/zephyr.elf \
  | tee $LOG/threads.txt
```

Read the `pended_on` column. That one column is the answer to the round:

| `MPSL Work` pended on | verdict |
|---|---|
| `sync_evt_pool.free.wait_q` | the singleton is held — candidate 1 confirmed |
| `hci_rx_pool.free.wait_q` | true RX-pool exhaustion |
| `-` (not pended), state RUNNING/READY | the inlet is alive; look at `BT RX WQ` and the corpse |

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
