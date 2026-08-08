# DOWNTIME_LEDGER — §12: why there has never been a stable 10/10 run

Every departure from full delivery in N5/N6/N7/N8, attributed. Node-minutes,
counted from onset to recovery, or to run end for a terminal event.

## 1. Totals

| cause | N5 | N6 | N7 | N8 | total | share |
|---|---|---|---|---|---|---|
| `HOST_WEDGE` | 0.0 | 0.0 | 10.2 | 684.7 | **694.9** | **82.9 %** |
| `BATTERY_DEPLETION` | 0.0 | 0.0 | 0.0 | 138.7 | 138.7 | 16.5 % |
| `BROWNOUT_CYCLE` | 0.0 | 0.0 | 0.0 | 4.5 | 4.5 | 0.5 % |
| `DOCK_CONTACT_CHARGE_FAILURE` | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 | 0 % |
| `OPERATIONAL` | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 | 0 % |
| `UNKNOWN` | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 | 0 % |
| **all** | **0.0** | **0.0** | **10.2** | **827.8** | **838.1** | |

**Four wedges cost 695 node-minutes. Everything else in four runs cost 143.**
The wedge is not one problem among several — it is 83 % of all lost node-time,
and it is the reason a 10/10 run has never held.

Two structural notes on the totals:

- **N5 and N6 have literally zero downtime.** Nine boards, six hours, no
  stall of any kind ≥2 s. A stable multi-hour run *is* achievable with this
  hardware and this firmware; N5 is the existence proof.
- **`DOCK_CONTACT_CHARGE_FAILURE` is zero because no such event occurs
  inside these four runs.** BSF31CC's known dock-contact problem is a
  between-runs charging fault; it shows up here only as BSF31CC having less
  battery at N8 start, and it is not attributable from the run logs. Recorded
  as out of scope rather than as zero.

## 2. Per event

| run | node | start | minutes | cause |
|---|---|---|---|---|
| N7 | BSF6C53 | 12:16:23 | 10.2 | HOST_WEDGE *(terminal — run ended)* |
| N8 | BSFEC35 | 15:46:08 | **327.8** | HOST_WEDGE |
| N8 | BSF1120 | 16:53:08 | **267.9** | HOST_WEDGE *(terminal)* |
| N8 | BSF44AD | 19:51:58 | **89.0** | HOST_WEDGE *(terminal)* |
| N8 | BSF3C79 | 20:49:31 | 8.8 | BATTERY_DEPLETION |
| N8 | BSF6C53 | 20:47:51 – 20:49:40 | 1.6 (10 episodes) | BROWNOUT_CYCLE |
| N8 | BSF6C53 | 20:50:45 | 30.2 | BATTERY_DEPLETION *(terminal)* |
| N8 | BSF31CC | 20:51:43 | 29.3 | BATTERY_DEPLETION *(terminal)* |
| N8 | BSF3C79 | 20:59:31 | 16.1 | BATTERY_DEPLETION |
| N8 | BSFC2CC | 21:01:19 – 21:02:14 | 0.9 (6 episodes) | BROWNOUT_CYCLE |
| N8 | BSFC2CC | 21:02:25 / 21:03:00 | 18.6 | BATTERY_DEPLETION *(terminal)* |
| N8 | BSFAA61 | 21:06:44 | 14.2 | BATTERY_DEPLETION *(terminal)* |
| N8 | BSF8BC4 | 21:10:10 | 10.8 | BATTERY_DEPLETION *(terminal)* |
| N8 | BSFEC35 | 21:14:00 – 21:14:38 | 0.6 (4 episodes) | BROWNOUT_CYCLE |
| N8 | BSFEC35 | 21:14:47 – 21:15:19 | 6.3 | BATTERY_DEPLETION *(terminal)* |
| N8 | BSF3C79 | 21:16:32 | 4.5 | BATTERY_DEPLETION *(terminal)* |

## 3. Boards written off and then heard again

`RAW_DATA_INVENTORY` records three boards that came back after being written
off. What actually happened to them, from the air timeline and the master log:

- **BSFEC35** — wedged 15:46, force-disconnected by the operator at
  17:28:20 (`reason=0x16`, locally initiated), then *unreachable by BLE* for
  3 h 46 min while its UWB tag kept transmitting. At **21:13:56** it appeared
  in the master's scan again and connected. It had by then **power-cycled
  itself via brownout**, which is the only thing that clears the wedge — and
  the same brownout is why it then failed GATT discovery 14 times in 90
  seconds and finally went off air. So: wedge → operator disconnect →
  invisible-but-alive → battery brownout → brief reappearance → death.
  **The wedge did not clear on its own in 3 h 46 min.**
- **BSF6C53 (N8)** — never wedged in N8; its 20:47–20:50 cascade is brownout
  cycling with reboots, and it returned at 23:12 on the air interface only.
  Battery recovery under no load, not a BLE event.
- **BSF44AD** — wedged 19:51:58, held its link until **20:49:18** when the
  supervision timeout fired (`reason=0x08`) — the same minute the fleet-wide
  depletion cascade began. Its link died of battery, not of the wedge.

## 4. Boards that hit `delivering = 0` at full charge

Three: BSFEC35 (31 min into the run), BSF1120 (98 min), BSF44AD (277 min).
**All three are wedges.** No board in any run stopped delivering at full
charge for any other reason.

## 5. Operator actions

One, and it is recorded in the source log as prohibited by that run's own
brief: `BSFEC35 RECONNECT` at 17:28:20 during N8. It removed BSFEC35
permanently and produced no node-side information (`outcome=timeout`,
`connect_ms=0`, no re-advertise in 120 s). Its analytical cost is that
BSFEC35's post-onset link observation stops at +6 132 s instead of running to
depletion like BSF1120's. Its analytical value is the no-re-advertise
observation, which is one half of the §0.3-retracted discriminator and is
retained here only as a description, not as a localisation.
