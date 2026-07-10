# Anchor B — health history since 2026-05-28

Question: after the Erlangen test (before 5.28) the rig was put back **without moving location** —
has B been bad the whole time?

**Answer: No.** B was a normal, healthy anchor from early June through **June 24**, then broke in
**July** (first flicker 07-01, severe & persistent by 07-10).

Method: every inter-anchor sweep since 05-28 (valid ones, core RMS ≤ 120 mm = same settled rig).
`B/core` = B's multilat RMS ÷ the A,C,D,E,G core's own self-consistency (normalizes global drift).
HEALTHY = B/core < 2.5 and |worst residual| < 250 mm. Script: `code/b_history.py`.

| date | core RMS | B RMS | B/core | worst link | resid mm | verdict |
|---|---:|---:|---:|---|---:|---|
| 2026-05-28 10:45 | 25.6 | 76.7 | 3.0 | B-C | +106 | ~ok |
| 2026-06-01 22:53 | 53.6 | 310.4 | 5.8 | B-D | +682 | BROKEN (isolated blip) |
| 2026-06-02 11:38 | 41.3 | 53.7 | 1.3 | B-E | +68 | HEALTHY |
| 2026-06-02 11:42 | 42.0 | 76.9 | 1.8 | B-D | −127 | HEALTHY |
| 2026-06-02 11:55 | 54.5 | 82.6 | 1.5 | B-D | −126 | HEALTHY |
| 2026-06-02 12:02 | 48.2 | 65.4 | 1.4 | B-D | +109 | HEALTHY |
| 2026-06-02 22:51 | 41.0 | 51.7 | 1.3 | B-A | −77 | HEALTHY |
| 2026-06-03 00:16 | 38.2 | 50.4 | 1.3 | B-A | −76 | HEALTHY |
| 2026-06-07 23:37 | 47.4 | 73.8 | 1.6 | B-D | +133 | HEALTHY |
| 2026-06-07 23:45 | 52.0 | 75.9 | 1.5 | B-D | +122 | HEALTHY |
| 2026-06-08 | 55.6 | 72.2 | 1.3 | B-D | +117 | HEALTHY |
| 2026-06-08 | 59.5 | 74.7 | 1.3 | B-A | −113 | HEALTHY |
| 2026-06-23 14:28 | 44.9 | 68.8 | 1.5 | B-A | −107 | HEALTHY |
| 2026-06-23 14:49 | 38.2 | 77.8 | 2.0 | B-A | −127 | HEALTHY |
| 2026-06-23 23:19 | 62.5 | 101.5 | 1.6 | B-D | −181 | HEALTHY |
| 2026-06-24 00:34 | 54.5 | 40.1 | 0.7 | B-E | +53 | HEALTHY |
| **2026-07-01 19:33** | 44.7 | 394.1 | **8.8** | B-E | **+839** | **BROKEN** |
| **2026-07-01 19:51** | 95.3 | 428.8 | **4.5** | B-A | **−834** | **BROKEN** |
| 2026-07-02 19:51 | 45.1 | 65.7 | 1.5 | B-D | −104 | HEALTHY (recovered) |
| 2026-07-04 03:09 | 78.6 | 213.0 | 2.7 | B-A | +457 | marginal |
| **2026-07-10 12:15** | 11.6 | 483.5 | **41.8** | B-A | **−1060** | **BROKEN** |
| **2026-07-10 13:28** | 11.2 | 518.7 | **46.3** | B-A | **−1012** | **BROKEN** |
| **2026-07-10 14:01** | 7.1 | 539.2 | **76.4** | B-A | **−1198** | **BROKEN** |
| 2026-07-10 eve (pre-move) | 6.5 | 552.1 | 84.7 | B-A | −1155 | BROKEN |
| 2026-07-10 eve (post off-wall) | 25.8 | 324.1 | 12.5 | B-A | −710 | BROKEN (halved) |

## Reading
- **June (05-28 → 06-24): B was fine** — RMS ~40–100 mm, only ~1.3–2× the core, worst residuals
  ~100–180 mm (just noise). The systematic multi-hundred-mm B-A error did **not** exist yet.
  (One isolated 06-01 reading is broken but every capture before and after it is healthy.)
- **July: B breaks.** First large error on **07-01** (~830 mm), *recovered* 07-02, marginal 07-04,
  then **severe and persistent from 07-10** (B-A −700 to −1200 mm).
- The on/off behaviour (07-01 bad → 07-02 good → 07-10 bad) = a **mechanical/positional** fault
  that developed in early July, matching the recorded events (stepped-on cable → antenna rotation,
  wall proximity). It is **not** consistent with damage from the Erlangen transport — that would
  have shown in early June, but June was clean.

## Implication
B's **module was healthy as recently as 2026-06-24**, so the current fault is most likely the
cable/antenna/mount disturbed in early July (partly recoverable — the off-wall move already halved
it) rather than a long-dead module. The residual noisy B-A does indicate real recent
connector/antenna damage. Re-seat/replace B's antenna+cable (or the module) and re-sweep.
