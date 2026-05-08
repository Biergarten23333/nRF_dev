# Wand T-Structure Ground Truth Analysis

Ground truth: `A --285mm-- T --385mm-- B`, and `T --595mm-- C` perpendicular.

| Pair | Truth mm | Fwd median | Rev median | Bidir median | Bidir mean | Bidir bias mean |
|---|---:|---:|---:|---:|---:|---:|
| AB | 670.00 | 767 | 775.5 | 771 | 773.32 | 103.32 |
| AC | 659.73 | 787.0 | 824.5 | 797.5 | 804.08 | 144.34 |
| BC | 708.70 | 842.5 | 846.0 | 844.0 | 841.89 | 133.2 |

Notes:
- `AC = sqrt(285^2 + 595^2) = 659.73 mm`.
- `BC = sqrt(385^2 + 595^2) = 708.70 mm`.
- Bias is measured raw UWB distance minus this hand-measured geometry.
