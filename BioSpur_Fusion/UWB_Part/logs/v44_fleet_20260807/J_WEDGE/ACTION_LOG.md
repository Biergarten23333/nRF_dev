# J_WEDGE — live wedge intervention, N8 run

Two boards wedged during the N8 fleet run with their BLE links alive. This
directory records the intervention, which is **outside the N8 brief** and was
authorised explicitly by the user at 17:27 after they confirmed they would be
away for the following two days.

## Why intervene at all

The N8 brief says run to depletion and do not intervene. That instruction was
written on the assumption that a wedge would either resolve itself or be
readable later. Neither holds here:

- Both boards refuse every command with `-ENOMEM`. There is no software path in.
- The only non-destructive read of their live state is SWD, which needs hands on
  the hardware. The user is away for two days.
- `.noinit` does not survive power loss, and the batteries will not last two
  days. **Every hour of waiting is pure loss with no compensating gain.**

So the choice was not "preserve vs destroy". It was "attempt recovery vs
guarantee total loss".

## Timeline (wall clock, 2026-08-07)

| time | event |
|---|---|
| 15:14:57 | N8 run opens, 10/10 delivering at 8.333 Hz |
| 15:46:10 | **BSFEC35 goes silent.** Link stays up. |
| 15:46:35 | Its GATT read times out at 25001 ms — no ATT response ever produced |
| 15:51:08 | First `-ENOMEM`; every command after this fails the same way |
| 16:53:10 | **BSF1120 goes silent**, identical signature |
| 17:28:20 | `BSFEC35 RECONNECT` sent (authorised) |
| 17:28:20 | `FUSION_RECONNECT_START err=0` → `FUSION_RECONNECT_DISCONNECTED` +100 ms |

BSF1120 deliberately left untouched as a second, uncontaminated sample.

## What the wedge looks like

Identical on both boards, and sharp:

- Last data record is entirely normal (BSF1120: IMU `seq=18209`, UWB
  `sweep=49925`). The next millisecond produces nothing.
- **No precursor.** All eight net_buf pools read full — `avail == low_water ==
  max` — on the last record before silence, and never dipped at any point in
  the board's life. This is not a buffer leak.
- The peer's controller stays alive. Master-side QoS keeps reporting
  `reports=20, crc_ok≈17-20, crc_error=0, nak=0, event_gaps=0` for the entire
  wedge — 90+ minutes of a healthy link carrying no application data.
- An ATT request reaches the node and never gets a response. This is more than
  N7 showed: not only do notifications stop, the **inbound** ATT path is dead.
- Then the master's per-connection TX credits never return, so every subsequent
  write fails `-ENOMEM`.

## The v44 trap did not fire

No corpse, no reboot, no `reboot_owner` change, on either board, after 90 and 20
minutes respectively. The eight healthy boards all read `present=0`.

Three candidate explanations, in the order I would bet on them:

1. **The BT RX WQ is genuinely quiescent and the block is below it.** Empty LL
   PDUs generate no host work, so `rx_work_handler()` has nothing to run, the
   stage stays at `RX_WORK_EXIT`, and the monitor is *correct* to stay silent.
   The wedge would then be in the controller / RX-buffer layer, where v44 has no
   instrumentation at all — the DRGN-23518 class the K1 audit flagged.
2. **The monitor's predicate is wrong.** It measures dwell in a non-quiescent
   stage, not absence of forward progress. A thread that cycles through
   `rx_work_handler()` quickly while never completing useful work looks healthy.
3. The monitor thread itself is starved. Least likely — it is an independent
   `K_PRIO_COOP` thread.

Hypothesis 1 fits every observation including the monitor's silence, which is
the one thing hypothesis 2 has to explain away.

## Outcome — hypothesis 2 refuted

```
17:28:20  FUSION_RECONNECT_START         err=0
17:28:20  FUSION_RECONNECT_DISCONNECTED  (+100 ms)
17:30:20  FUSION_RECONNECT_DONE  outcome=timeout  connect_ms=0  total_ms=120001
```

The node never re-advertised, so it never processed the HCI Disconnection
Complete — an event that is handled on the BT RX WQ. A thread that were merely
cycling would have consumed it. The block is below `rx_work_handler()`.

Full reasoning chain, the converged mechanism, and what v45 must instrument:
**[WEDGE_LOCALISATION.md](WEDGE_LOCALISATION.md)**.

## Files

- `pre_reconnect_snapshot.json` — QoS history, full STALL_READ lifecycle, last
  data record and every failing command, for both boards, captured before the
  RECONNECT.
- `ACTION_LOG.md` — this file.
