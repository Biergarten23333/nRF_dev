# VISUALIZATION_CENTERLINE_V1 — facts still required

No capture payload is needed yet. Supply filled **copies** of the referenced forms; do not edit the frozen V4.1 templates or the blank shared-wearing template in place.

## Direct surface measurements

Use the frozen V4.1 palpable-landmark definitions and record all three repeats in millimetres in a copy of `v47_visualization_subject_measurements.csv`. Enter the date the new measurement was actually taken; do not reuse the capture date unless that is true:

- acromion to lateral humeral epicondyle, left and right;
- lateral humeral epicondyle to radial/ulnar wrist-styloid midpoint, left and right;
- greater trochanter to lateral femoral epicondyle, left and right;
- lateral femoral epicondyle to medial/lateral malleolar midpoint, left and right;
- biacromial breadth;
- ASIS breadth;
- C7 to mid-PSIS;
- pelvis anterior-posterior depth.

The visualization form contains no Meskers, height, mass or internal joint-centre rows. Meskers remains blocking for the separate strict `SCIENTIFIC_CENTERLINE` product but is not an input to `VISUALIZATION_CENTERLINE_V1`.

## One shared hardware/enclosure description

Follow `HARDWARE_MEASUREMENT_GUIDE.md` and fill a copy of `v47_visualization_hardware_measurements.csv` with directly observable distances and directions:

- confirm whether all ten assemblies have identical PCB-to-enclosure registration;
- enclosure outer long, short and thickness dimensions;
- U4 reference distances to the two named short faces and two named long-side faces;
- PCB-top-plane distance to the body-facing enclosure face;
- the named PCB edge toward the antenna end, PCB/enclosure edge relationship and component-face direction;
- PCB mechanical play in x/y/z;
- strap width;
- evidence status and one reference for the shared hardware facts. Use `MEASURED_CURRENT_HARDWARE` or `PHOTO_DERIVED`.

Do not calculate XYZ or Euler angles. The compiler derives the complete rigid transform and records its face, axis, sign and handedness choices. The repository-verified U4 location and DWM1001C package/printed-antenna envelope are already bound by SHA-256 and do not need to be re-entered ten times.

## One shared historical wearing description

In the same form provide once, for all nodes:

- body-facing enclosure face;
- enclosure long-axis direction relative to the worn segment;
- antenna-end direction;
- attachment convention relative to each graphical landmark;
- evidence status and one reference or explicit recollection.

The ±50 mm translation and ±30° rotation bounds are fixed `DEFAULT_ENGINEERING_PRIOR` rows. Do not tighten them or relabel them as capture-day measurements.

Allowed evidence statuses are `PHOTO_DERIVED`, `OPERATOR_RECOLLECTION`, and `MEASURED_CAPTURE_DAY`. Use `MEASURED_CAPTURE_DAY` only when the reference names contemporary capture-day evidence. A per-node override is needed only where a node is remembered or evidenced as different; mark its `override_present` row `YES`, give the evidence status/reference there, and add only the overridden bound rows.

## Optional shoe facts

The shoe form is optional at this stage. Missing foot length, floor-to-malleolus height, and heel/forefoot stack measurements block detailed feet only; they do not block the torso-and-limb graphical centerline.

Until the required direct and shared facts are supplied, the deterministic readiness result is:

```text
SCIENTIFIC_CENTERLINE = UNCHANGED_FROZEN_V4_1_BLOCKED
VISUALIZATION_CENTERLINE = BLOCKED_OPERATOR_INPUTS_MISSING
CALIBRATION_LEDGER = SEALED
WALK_HELDOUT_STATUS = SEALED
FINAL_STILL_STATUS = SEALED
```
