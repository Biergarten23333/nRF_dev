# COUNTER_BOUNDARY_ANALYSIS — §10

Values reconstructed as **cold-boot cumulative**, not run-relative. Boot
segments come from `node_ms` going backwards in the 1 Hz telemetry;
`reset_reason` is constant (1) on every wedged board through its whole
segment, i.e. none of them rebooted before wedging.

## 1. State at each onset

| quantity | width | N7 BSF6C53 | N8 BSFEC35 | N8 BSF1120 | N8 BSF44AD |
|---|---|---|---|---|---|
| IMU `seq` | 16 bit | 48 843 | 3 643 | 18 209 | 3 443 |
| distance to 2¹⁶ wrap | | 16 693 | 61 893 | 47 327 | 62 093 |
| UWB `sweep` | 32 bit | 21 162 | 16 550 | 49 925 | 139 425 |
| `publisher_count` | 32 bit | 79 269 | 61 951 | 187 787 | 524 719 |
| `notify_ok` | 32 bit | 79 269 | 61 951 | 187 787 | 524 719 |
| `watchdog_feeds` | 32 bit | 2 537 | 1 984 | 5 987 | 16 719 |
| node uptime at onset | | 2 540.2 s | 1 986.6 s | 5 991.8 s | 16 731.9 s |
| TIMER2 low-32 at onset | 32 bit µs | 2 540 284 132 | 1 986 647 163 | 1 696 848 917 | 3 846 988 141 |
| distance to 2³² µs wrap | | 1 754.7 s | 2 308.3 s | 2 598.1 s | 448.0 s |
| `timer_wraps` so far | | 0 | 0 | 1 | 3 |
| phase on the 327.68 s IMU-seq-wrap grid | | 246.5 s | 20.6 s | 93.6 s | 20.2 s |

## 2. Tests and outcomes

| test | outcome |
|---|---|
| 16-bit IMU `seq` wrap | nearest approach is BSF6C53 at 16 693 counts (≈83 s) away. **No hit.** |
| 32-bit `sweep` / `notify_ok` / `publisher_count` wrap | all 4–5 orders of magnitude from 2³². **No hit.** |
| TIMER2 low-32 wrap (71.58 min) | nearest is BSF44AD at 448 s before a wrap — 10 % of the period, and it had already survived 3 wraps. **No hit**, and no onset is within 60 s of a multiple of 71.58 min since cold boot. |
| `timer_wraps` increment immediately before onset | BSF44AD's third wrap was 4 293 s before onset; BSF1120's only wrap 4 295 s before. **No proximity.** |
| 327.68 s IMU-seq-wrap grid alignment | phases 246.5 / 20.6 / 93.6 / 20.2 s. Three are spread; **BSFEC35 and BSF44AD sit 0.4 s apart on a 327.68 s circle**, and their IMU `seq` values at onset (3 643 and 3 443) are 200 counts = 1.0 s apart. See §2a. |
| powers of two in any counter | none within 1 % of any listed value. |
| **shared cumulative value across independent boards** | `publisher_count` 79 269 / 61 951 / 187 787 / 524 719; `sweep` 21 162 / 16 550 / 49 925 / 139 425; uptime 1 987 / 2 540 / 5 992 / 16 732 s. **All four distinct, no near-coincidence.** |

## 2a. The one near-coincidence, with its arithmetic

BSFEC35 and BSF44AD wedged at IMU `seq` 3 643 and 3 443 — one second apart on
the 327.68 s sequence-wrap cycle, and correspondingly 0.4 s apart in grid
phase. Taken alone that looks like something.

The arithmetic says otherwise. With four events and `seq` effectively uniform
over 2^16, the chance that *some* pair lands within ±200 counts is
6 x 400/65536 = **3.7 %**. Against the other two events at 18 209 and 48 843,
against four different boards, two different runs and two different firmware
versions, and against 25x spread in cumulative notifications, this is a
one-in-twenty-seven accident and is recorded as such.

It is nonetheless the only numeric coincidence in the whole boundary scan, so
it is written down: if a fifth wedge ever lands near `seq` ~3 500 again, this
paragraph stops being a footnote.

## 3. Verdict

**No counter or timer boundary is implicated.** The §3 falsifier "a shared
cumulative value at onset on independent boards ⇒ deterministic bug, all six
hypotheses subordinate" is **not triggered** — the four onsets are spread
over an 8.4× range of node uptime and a 25× range of cumulative
notifications.

The one weak pattern worth recording, because it is the only structure in the
table: all four boards wedged on their **first** boot segment, with
`reset_reason` unchanged, i.e. no wedge has ever followed a reboot within a
run. With n=4 and almost every board-hour being first-segment, that carries
no weight.
