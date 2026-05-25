# T4 Variant Comparison

Date: 2026-05-25

Dataset: `autopos_pipeline/outdoor_20260513`

Main comparison source:

- baseline T1/T2/T3 MC500: `outdoor_20260513_failure_modes_500/`
- T4 v1 MC500: `outdoor_20260513_failure_modes_T4_v1_500/`
- T4 v5 MC500: `outdoor_20260513_failure_modes_T4_v5_500/`
- post-v5 MC50 probes:
  - `outdoor_20260513_failure_modes_T4_tukey_c4685_50/`
  - `outdoor_20260513_failure_modes_T4_tukey_refine_c4685_50/`
  - `outdoor_20260513_failure_modes_T4_tukey_refine_c8000_50/`
  - `outdoor_20260513_failure_modes_T4_blend_prior_50/`

## Final T4 Choice

The current best T4 candidate is **T4 v5**:

```text
if valid anchors >= 8:
    use T1 robust WLS without previous-position prior
else:
    use T3 dynamic-stable weighting and weak previous-position prior
```

This is not globally best for every metric. It is the best tested compromise for
dynamic/Roto robustness: it improves Roto NLOS center consistency versus T3
while mostly preserving T3's low-redundancy behavior.

## Variant Notes

- v1: signed residual memory / anchor-health soft penalty. Static p95 improved,
  but Roto center and thickness degraded.
- v2: hard leave-one-out rejection with loose thresholds. Rejected far too many
  frames and damaged both static and Roto metrics.
- v3: conservative leave-one-out rejection. Safer than v2, but still degraded
  Roto and did not beat T3/T4 v5.
- v4: full-anchor frames removed temporal prior but kept residual-memory
  weighting. Roto NLOS still degraded, likely because residual-memory weighting
  remained unstable under persistent positive bias.
- v5: full-anchor T1, low-redundancy T3. Best current dynamic compromise.
- v6: motion-gated full-anchor T1/T3 switching with 120 mm step threshold.
  Static median improved slightly in MC50, but Roto NLOS was worse than v5.
- Tukey full-anchor probes: tested direct Tukey c=4.685, Huber-initialized
  Tukey c=4.685, and Huber-initialized Tukey c=8.0. All degraded clean
  full-anchor keep-8 and Roto metrics, so Tukey is not integrated into the
  current T4 default.
- continuous redundancy-blended prior: tested a low-redundancy prior weight
  that weakens from n=4 to n=7. It degraded keep-7/6/5 static and Roto metrics,
  so the hard v5 split remains the default.

## MC500 Key Results

Values are medians over 500 Monte Carlo repeats.

### Roto Turn-Center RMS

| Condition | T1 | T3 | T4 v5 | T4 v5 - T3 |
|---|---:|---:|---:|---:|
| keep-4 | 139.10 | 82.72 | 82.79 | +0.07 |
| keep-5 | 91.23 | 58.73 | 58.99 | +0.26 |
| keep-6 | 52.89 | 42.04 | 41.96 | -0.07 |
| keep-8 | 22.03 | 22.64 | 22.47 | -0.17 |
| burst H 1.0s | 19.67 | 20.20 | 20.09 | -0.11 |
| NLOS +100 mm | 20.32 | 21.81 | 21.03 | -0.78 |
| NLOS +200 mm | 21.86 | 24.64 | 23.16 | -1.48 |
| NLOS +300 mm | 24.33 | 28.18 | 25.59 | -2.59 |

### Roto Circle Thickness RMS

| Condition | T1 | T3 | T4 v5 | T4 v5 - T3 |
|---|---:|---:|---:|---:|
| keep-4 | 262.45 | 200.00 | 200.17 | +0.16 |
| keep-5 | 210.20 | 177.71 | 177.69 | -0.02 |
| keep-6 | 175.06 | 158.99 | 158.88 | -0.11 |
| keep-8 | 127.09 | 122.84 | 124.28 | +1.44 |
| burst H 1.0s | 124.11 | 122.36 | 122.67 | +0.31 |
| NLOS +100 mm | 128.55 | 126.04 | 126.38 | +0.34 |
| NLOS +200 mm | 135.52 | 133.38 | 134.97 | +1.59 |
| NLOS +300 mm | 144.34 | 141.26 | 142.41 | +1.14 |

### Static Tradeoff

T4 v5 is not the best static-tail solver. T3 and T4 v1 remain better for some
static repeatability and static NLOS tail conditions. The largest observed T4 v5
cost is under static persistent NLOS:

| Condition | Static 3D median T3 | Static 3D median T4 v5 | Static 3D p95 T3 | Static 3D p95 T4 v5 |
|---|---:|---:|---:|---:|
| NLOS +100 mm | 49.69 | 50.18 | 69.68 | 77.83 |
| NLOS +200 mm | 51.44 | 53.67 | 73.67 | 87.02 |
| NLOS +300 mm | 53.06 | 58.77 | 76.01 | 101.13 |

## Interpretation

T4 v5 should be treated as a dynamic candidate, not a universal replacement for
T3. For Roto/future body-motion captures, it reduces center bias under NLOS
while keeping low-redundancy behavior close to T3. For static reports, T3 or the
static-tail-oriented T4 v1 may still be preferable unless dynamic behavior is
the priority.

The strongest rejected lesson is important: lowering residual RMS by rejecting
anchors is not the same as improving trajectory consistency. Hard leave-one-out
looked plausible numerically but failed the Roto metrics.

## Post-v5 Probe Results

Values below are MC50 medians and should be read only as screening evidence.
They were sufficient to reject the probes before running MC500.

### Tukey Full-Anchor Loss

Tukey was tested only on the `n >= 8` full-anchor path. Low-redundancy behavior
was unchanged from T4 v5.

| Probe | Static keep-8 p95 | Static NLOS +300 p95 | Roto keep-8 center | Roto NLOS +300 center | Decision |
|---|---:|---:|---:|---:|---|
| T4 v5 MC50 | 70.01 | 98.34 | 22.47 | 25.50 | baseline |
| direct Tukey c=4.685 | 248.22 | 156.85 | 102.62 | 115.64 | reject |
| Huber-init Tukey c=4.685 | 132.66 | 107.32 | 32.93 | 42.12 | reject |
| Huber-init Tukey c=8.0 | 108.88 | 114.98 | 32.80 | 42.83 | reject |

Tukey did not recover the static NLOS tail and it damaged clean full-anchor
behavior. The likely cause is that the bisquare cutoff is too sensitive to the
current UWB residual/sigma scale, and the non-convex objective creates unstable
full-anchor solutions even with a same-frame Huber initializer.

### Continuous Prior Blend

The blend kept the v5 full-anchor path unchanged. For `n < 8`, it scaled the
weak previous-position prior by redundancy:

```text
w_prior = (8 - n) / 4
```

The result was worse than v5 under low redundancy:

| Condition | T4 v5 static p95 | Blend static p95 | T4 v5 Roto center | Blend Roto center |
|---|---:|---:|---:|---:|
| keep-7 | 100.87 | 108.78 | 29.93 | 32.27 |
| keep-6 | 173.85 | 184.52 | 41.68 | 47.09 |
| keep-5 | 218.46 | 225.94 | 58.58 | 63.83 |

This indicates that the existing T3-style prior is useful once redundancy drops
below 8. The hard v5 switch is therefore not just a coding artifact; it is the
best tested engineering compromise so far.
