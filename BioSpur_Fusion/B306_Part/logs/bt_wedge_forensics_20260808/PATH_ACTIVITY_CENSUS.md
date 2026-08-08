# PATH_ACTIVITY_CENSUS — what was provably running when each node froze

§5. The structural fact this exploits: on a peripheral in steady state the
**normal-RX path is nearly idle** (sparse commands; empty LL PDUs allocate no
host RX buffer) while the **TX / TX-completion machinery fires 20–31 times a
second**. Measured here: 142–161 inbound operations per node-hour, i.e. one
every 22–25 s, against 31.4 notifications/s. **Three orders of magnitude.**

## 1. Inbound normal-RX work (measured, not estimated)

Base rate, recomputed from `FUSION_COMMAND_TX` + `FUSION_STALL_READ_START`
addressed to each node:

| run | inbound ops | per node-hour | mean gap per node |
|---|---|---|---|
| N5 | 8 640 | 160.0 | 22.5 s |
| N7 | 963 | 161.2 | 22.3 s |
| N8 | 8 695 | 142.5 | 25.3 s |

Lead time from the last inbound operation to each onset:

| event | last inbound before onset | ops ≤10 s | ops ≤1 s | ops ≤0.05 s |
|---|---|---|---|---|
| N7 BSF6C53 | **44.82 s** (`CORPSE STATUS`) | 0 | 0 | 0 |
| N8 BSF1120 | **20.43 s** (`CORPSE STATUS`) | 0 | 0 | 0 |
| N8 BSF44AD | **40.80 s** (`CORPSE STATUS`) | 0 | 0 | 0 |
| N8 BSFEC35 | **0.034 s** (`STALL STATUS`) | 4 | 4 | 1 |

> **Three of the four wedges happened in an inbound-idle window of 20–45 s.**
> Chance of an operation falling in the last 10 s is 0.33 per event at the
> measured base rate; observing none on three of four is entirely ordinary.

## 2. LL-procedure proxies — nothing there either

Channel-map-update proxy: L1 distance between the normalised
`channels[0..36]` histogram in the 300 s before and after onset, **against
the same-window distribution over every healthy node** (the control set the
brief insists on):

| event | event L1 | control median | control range | n controls |
|---|---|---|---|---|
| N7 BSF6C53 | 0.0906 | 0.0894 | 0.0645 – 0.1098 | 8 |
| N8 BSFEC35 | 0.0999 | 0.0911 | 0.0759 – 0.1102 | 9 |
| N8 BSF1120 | 0.0988 | 0.0837 | 0.0662 – 0.1069 | 9 |
| N8 BSF44AD | 0.1022 | 0.1031 | 0.0713 – 0.1138 | 8 |

Every event value lies inside its control range. All 37 channels remain in
use before and after. **No channel-map update at any onset.**

Connection-interval-change proxy: `reports/s` is flat across each onset
(20.10→20.11 for BSFEC35 and BSF1120; 18.42→18.42 for BSF6C53;
16.18→16.19 for BSF44AD). **No CI change.**

Note the tiers, which are themselves a measurement: BSFEC35 and BSF1120
wedged inside the at-capacity, non-scanning window at 20.1 reports/s;
BSF44AD wedged at **16.18 reports/s against a fleet median of 18.42** — the
low tier of the scan-split. BSF6C53 (N7) sat at 18.42 against a fleet median
of 17.3, i.e. the *high* tier. **The wedge occurs in both tiers**, so anchor
phase / scan tier is not the variable.

## 3. Continuously active paths

| path | rate at onset | evidence |
|---|---|---|
| connection events | 16.2 – 20.1 /s | `FUSION_QOS reports` |
| notifications | 31.4 /s (28.3 delivered as records + 1 Hz ctl records) | `publisher_count` Δ60 s = 1882–1893 → **31.4/s exactly** on all four |
| TX-completion callbacks | ≥ once per connection event | required for `att_pool` to have been full at the last strobe |
| producers | IMU 20.0/s, UWB 8.33/s | `imu_records` Δ60 s = 1201, `frames` Δ60 s = 500–501 on all four |
| system workqueue | 1.00 Hz | `watchdog_feeds` Δ60 s = **60** on all four |

## 4. Per-event conclusion

> **N7 BSF6C53** — in the final 44.8 s the only provably active host paths
> were the notify pipeline (31.4/s), the TX/completion machinery
> (18.4 connection events/s) and the system workqueue (1 Hz watchdog). The
> normal-RX path had nothing to process.
>
> **N8 BSF1120** — same, final 20.4 s, 20.1 events/s.
>
> **N8 BSF44AD** — same, final 40.8 s, 16.2 events/s.
>
> **N8 BSFEC35** — the only exception. A `STALL STATUS` GATT read was
> submitted 189 ms before onset and *completed successfully* 94 ms before
> (`att_err=0 len=232 … terminal=callback elapsed_ms=88`), and a further
> `STALL STATUS` write went out 34 ms before onset. So on this one node the
> normal-RX path was demonstrably active in the final 100 ms **and it
> worked** — the read returned a full 232-byte payload. §7.1 puts that
> coincidence at p ≈ 0.04 per event.

## 5. What this census does and does not license

It shifts weight toward the continuously active TX-completion / notify
machinery (H1, and H2 only through its TX role) and away from a mechanism
that needs inbound work to trigger (H2/H3 as *initiators*). It does **not**
localise the freeze to a thread — three of four events simply had no inbound
work to blame, which is an absence of evidence for H2/H3, not evidence of H1.

The one thing it does rule out cleanly: **no LL procedure (channel map,
connection interval, PHY, DLE) ran at any onset.** Those were the only
inbound HCI events invisible to the command log, and the proxies for all of
them are flat against controls.
