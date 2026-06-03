# CIR BSF66F 200s quick analysis

- File: `crx_samples.csv`
- Time span: `2026-06-01T23:50:27.135000+02:00` to `2026-06-01T23:53:47.144000+02:00` (200.009s)
- Samples: 5140

## Overall by anchor

| Anchor | N | raw median mm | raw std mm | FP median | peak median | noise median | good % | watch % | weak % |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| A | 996 | 2739 | 131.8 | 14953 | 1874 | 44 | 99.9 | 0.1 | 0.0 |
| B | 673 | 3232 | 119.8 | 8162 | 376 | 24 | 85.6 | 14.4 | 0.0 |
| C | 845 | 2649 | 111.0 | 10051 | 600 | 36 | 86.2 | 13.8 | 0.0 |
| D | 651 | 2703 | 255.2 | 8771 | 478 | 24 | 80.2 | 19.7 | 0.2 |
| E | 771 | 2711 | 255.0 | 9121 | 427 | 24 | 76.3 | 21.0 | 2.7 |
| F | 219 | 2968 | 82.4 | 19248 | 1952 | 60 | 100.0 | 0.0 | 0.0 |
| G | 767 | 2466 | 84.6 | 14734 | 2022 | 48 | 100.0 | 0.0 | 0.0 |
| H | 218 | 2251 | 242.2 | 20298 | 1990 | 44 | 96.8 | 3.2 | 0.0 |

## Main event

- UI-quality weak/red samples appear almost only on anchor E.
- The strongest weak interval is around 23:51:32-23:51:47 local time.
- B and H briefly drop to watch/yellow in the same neighborhood; A/F/G stay clean.
- Around the user-marked ~23:52 point, many links improve rather than degrade, so the event likely started before the rough mark or the body changed the multipath geometry instead of only blocking LOS.

## 5s non-good bins

| bin start | Anchor | N | median score | label | raw median | FP median | peak median | noise median |
|---|---|---:|---:|---|---:|---:|---:|---:|
| 23:51:32.135000 | B | 16 | 0.716 | watch | 3368 | 2444 | 160 | 16 |
| 23:51:32.135000 | E | 20 | 0.375 | weak | 3305 | 1072 | 99 | 12 |
| 23:51:32.135000 | H | 4 | 0.720 | watch | 3692 | 4236 | 355 | 24 |
| 23:51:37.135000 | E | 23 | 0.481 | watch | 3401 | 1476 | 150 | 12 |
| 23:51:37.135000 | H | 2 | 0.508 | watch | 2510 | 1306 | 1365 | 20 |
| 23:51:42.135000 | E | 12 | 0.681 | watch | 3412 | 2262 | 122 | 16 |
