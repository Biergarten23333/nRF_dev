# AutoPos V1-V4 Solver Explanation For Field Discussion

This note is written for explaining the current Erlangen / Garage / Outdoor field workflow to another researcher.

## One-Sentence Summary

We do not directly measure anchor XYZ. We first collect pairwise UWB distances between anchors, then solve a 3D distance-geometry problem. V4-io is the current production solver: it robustly fuses directed inter-anchor ranges, estimates anchor coordinates plus bounded per-anchor range-delay terms, and uses static / roto / wand captures only as validation unless an explicitly experimental solver variant says otherwise.

## What We Measure

The AutoPos sweep rotates the master role through anchors A-H.

For each sweep row, one anchor is the master:

- SW-A means A is master.
- SW-B means B is master.
- ...
- SW-H means H is master.

During SW-A, the system records ranges from A to the other anchors. During SW-B, it records ranges from B to the other anchors, and so on. This gives directed pair measurements such as A -> B and B -> A.

The raw sweep is staged into:

```text
solver/work/field_dataset_staged/sweep1000/pairs_all.csv
```

The solver uses this staged sweep table as its main layout input.

## What The Main Solver Does Not Use

For the standard versions:

- V1
- V2
- V3-lite
- V3-full
- V4-io

static / roto / wand capture data is not used to generate the anchor layout. Those captures are used afterward to validate whether the solved layout produces reasonable tag positions.

This distinction is important:

```text
sweep data -> anchor layout
static/roto/wand -> validation of that layout
```

Only experimental branches such as `v4-io-roto` or `v4-io-wand` inject roto/wand constraints into the layout, and those must be reported separately.

## Directed Pair Fusion

For each anchor pair, for example A-B, the data can contain two directions:

```text
A -> B
B -> A
```

These are not always identical because of antenna direction, local geometry, signal quality, multipath, and radio/clock bias. Therefore the solver first reduces the directed measurements into one robust pair distance.

The progression is:

| Version | Pair fusion | Delay model | Meaning |
|---|---|---|---|
| V1 / `v1-old` | simple bidirectional mean | no delay | earliest baseline |
| V2 / `v2` | inverse-variance weighted pair fusion | no delay | better weighting of noisy directions |
| V3-lite / `v3-lite` | median + MAD/MVUE robust fusion | no delay | robust pair fusion |
| V3-full / `v3-full` | MAD/MVUE robust fusion | per-anchor delay, alternating Tukey-style update | first delay-aware version |
| V4-io / `v4-io` | MAD/MVUE robust fusion | bounded per-anchor delay, joint Huber solve | current production inter-anchor solver |

## Layout Optimization

After pair fusion, the solver has a set of distances:

```text
d_AB, d_AC, ..., d_GH
```

It then solves for 3D anchor positions:

```text
p_A, p_B, ..., p_H
```

For no-delay versions, the residual is essentially:

```text
residual_ij = ||p_i - p_j|| - d_ij
```

For V4-io, the residual is:

```text
residual_ij = ||p_i - p_j|| + b_i + b_j - d_ij
```

where:

- `p_i` is the 3D coordinate of anchor `i`.
- `d_ij` is the fused measured distance between anchors `i` and `j`.
- `b_i` and `b_j` are bounded per-anchor range-delay / bias terms.

In the current code, V4-io uses:

- Huber robust loss.
- Residual scale around 15 mm.
- Delay regularization around 20 mm.
- Delay bounds of `[-60, +60] mm`.
- A soft two-layer physical prior.

The soft two-layer prior means:

- A/B/C/D are expected to be the lower layer.
- E/F/G/H are expected to be the upper layer.
- D is allowed to deviate from the A/B/C gauge plane.
- E/F/G/H are not forced to be exactly coplanar.
- The solver enforces physically plausible lower/upper ordering and rough layer separation.

## Gauge Freedom And Why Coordinates Need Convention

Distance-only geometry cannot determine absolute world coordinates. It can only determine shape up to:

- translation,
- rotation,
- and mirror reflection.

Therefore the code fixes a coordinate gauge:

- A is placed at the origin.
- B defines the local X direction.
- A/B/C define the initial gauge plane.

This does not mean A/B/C are physically guaranteed to be exactly level in the real world. It is a coordinate convention needed to remove mathematical ambiguity.

Because distance-only data cannot know the true left/right mirror, we must use physical conventions and external information to make the displayed layout match reality.

## Ultrasound Height Post-Process

Ultrasound is not part of the normal V4-io inter-anchor solve.

The pure UWB solver writes:

```text
layout.json
```

Then the ultrasound post-process writes:

```text
layout_us_height.json
```

The current ultrasound post-process uses F/G/H ultrasound antenna-center heights when available. It finds a rigid z-up coordinate alignment:

```text
z_corrected = dot(raw_xyz, fitted_z_axis) + z_shift
```

This does three things:

- Chooses the physically correct z direction.
- Keeps ABCD below EFGH.
- Aligns the selected upper anchors to measured ultrasound heights.

It does not change the raw sweep measurements and does not improve the inter-anchor residual by fitting extra range data. It is a coordinate-frame and height alignment step.

## What The Validation Metrics Mean

### AutoPos RMS / P95

These are inter-anchor residuals:

```text
predicted anchor-anchor distance - fused measured anchor-anchor distance
```

They measure how well the solved anchor layout explains the AutoPos sweep.

They are not tag-position error.

### Static Median / Static P95

These come from stationary tag captures. The solver computes tag positions frame by frame using the fixed anchor layout. The spread of those positions shows repeatability.

Static validation answers:

```text
If the tag is not moving, does the solved position stay stable?
```

### Roto dR RMS / Turn-Center Median

RotoArm validation is a kinematic consistency test. For two tags mounted on the rotating arm, the expected radius difference is known mechanically.

This validation answers:

```text
Do the solved tag trajectories behave like a physical rotating rigid setup?
```

It is not the same as absolute motion-capture ground truth.

### Wand Validation

Wand validation checks whether multiple tags with known rigid-body distances stay consistent after solving their positions.

This validation answers:

```text
Do solved tag positions preserve known rigid-body geometry?
```

## Why A Good RMS Can Still Be Wrong

A low AutoPos RMS only means the solved layout fits the measured UWB distances. It does not automatically prove the physical orientation is correct.

Possible failure modes:

- mirrored layout,
- wrong z-up direction,
- tilted frame due to weak z observability,
- biased distances from multipath,
- bad pair measurements from antenna blind directions,
- delay variables absorbing geometry error.

This is why we also check:

- ultrasound height alignment,
- lower/upper layer ordering,
- split first-half / second-half layout stability,
- static repeatability,
- roto consistency,
- wand rigid-body consistency.

## How To Explain The Full Workflow Tomorrow

Use this short explanation:

> We first run an AutoPos sweep where each anchor A-H becomes master once. This gives directed inter-anchor UWB ranges. The solver robustly fuses A->B and B->A style measurements into one pair distance, then solves a 3D distance-geometry problem. V4-io is our current production version: it jointly estimates anchor coordinates and bounded per-anchor range-delay terms using a Huber robust loss and soft physical two-layer constraints. The static, roto, and wand captures are not used to fit the standard V4-io layout; they are used afterward as validation. Ultrasound height is a post-process to align the solved UWB shape into a physical z-up frame, not a replacement for the UWB solve.

## If Asked "How Did You Know The Layout Is Good?"

Answer with three levels:

1. The sweep residuals are small:
   `AutoPos RMS` and `AutoPos p95` show the layout explains inter-anchor sweep distances.

2. The layout generalizes:
   split the sweep into first half and second half, solve separately, align by rigid transform, and compare anchor differences.

3. Independent capture data behaves physically:
   static tags are repeatable, roto tags preserve the expected radius difference, and wand tags preserve rigid-body distances.

## If Asked "What Is The Weak Point?"

Be explicit:

> Pure UWB inter-anchor distances alone cannot determine absolute coordinate orientation or mirror direction. Z is also weaker than XY in a two-layer rectangular setup. Therefore, the solver needs physical conventions and optional ultrasound height alignment to choose the correct physical frame. Garage multipath or bad antenna-facing pairs can distort the range graph, so outdoor LOS data is the cleaner validation case.

## Relevant Code Files

- `autopos_pipeline/erlangen_20260528_mocap/solver/scripts/run_v1_to_v4_io.py`
- `autopos_pipeline/erlangen_20260528_mocap/solver/scripts/run_v4io_field_check.py`
- `autopos_pipeline/erlangen_20260528_mocap/solver/scripts/apply_ultrasound_height_to_layout.py`
- `autopos_pipeline/outdoor_20260513/run_clean_full_compare.py`
- `autopos_pipeline/outdoor_20260513/analysis_20260513_182053/run_full_evaluation_same_pipeline_20260513.py`

