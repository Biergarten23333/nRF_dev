# VISUALIZATION_CENTERLINE_V1 product contract

Status: `NON_CLINICAL_DIAGNOSTIC_VISUALIZATION_ONLY`

This track is separate from V3, V4 and V4.1. It does not alter, supersede, rehabilitate or weaken any historical scientific verdict.

## Products

### A. SCIENTIFIC_CENTERLINE

`SCIENTIFIC_CENTERLINE` continues to use the frozen V4.1 requirements. Missing Meskers five-landmark 3D digitization, strict internal joint-centre evidence, or evidence-bounded sensor placement remains blocking. Nothing in this directory may be used to change that result.

### B. VISUALIZATION_CENTERLINE

`VISUALIZATION_CENTERLINE` is a graphical proxy skeleton for inspecting the historical capture. It may use direct palpable-landmark chords, acromion endpoints as graphical shoulders, and Harrington population-regression hip centres with published residual error. It is not clinical ground truth.

Every frame must display:

> Non-clinical visualization centerline. Axial segment twist and clinical joint centres/angles are not validated.

## Geometry vocabulary

The following names are mandatory and cannot be replaced by anatomical joint-centre labels:

- `rendering_upper_arm_length_L/R`: acromion to lateral humeral epicondyle surface chord;
- `rendering_forearm_length_L/R`: lateral humeral epicondyle to wrist-styloid midpoint surface chord;
- `rendering_thigh_length_L/R`: greater trochanter to lateral femoral epicondyle surface chord;
- `rendering_shank_length_L/R`: lateral femoral epicondyle to malleolar midpoint surface chord;
- `AcromionProxy_L/R`: graphical shoulder endpoint, not glenohumeral centre;
- `ElbowProxy_L/R`, `WristProxy_L/R`, `KneeProxy_L/R`, `AnkleProxy_L/R`: palpable-landmark graphical nodes;
- `HipRegression_L/R`: Harrington 2007 pelvis-only population-regression result, including published model error;
- `C7Proxy` and `PelvisProxy`: direct palpable-landmark graphical nodes.

Meskers 1998 is not used by this proxy path unless real five-landmark 3D digitization is supplied. Its absence blocks only `SCIENTIFIC_CENTERLINE`, not this visualization product.

Bone proxy lengths are compiled once, hashed and immutable through calibration and replay. A renderer or optimizer that changes a length, swaps left/right identity, disconnects the topology, or silently accepts a placement state at its bound fails.

## Hardware and placement

The U4 reference point and verified PCB documents are reused from the immutable V4.1 input-preparation evidence. The effective UWB RF phase centre is a bounded set inside the DWM1001C printed-antenna/module envelope, never an exact point. The operator measures named physical face distances, visible edge/face orientation, enclosure dimensions and mechanical play; the deterministic compiler derives the shared full rigid U4-board-to-enclosure registration. The operator never supplies XYZ or Euler angles. The compiler records all face, axis, sign and handedness choices and distinguishes the board-frame antenna region from its transformed enclosure-frame region.

Historical enclosure-to-landmark offsets remain bounded calibration-only nuisance variables. The fixed ±50 mm and ±30° bounds are labelled `DEFAULT_ENGINEERING_PRIOR`, never capture-day measurements. Shared wearing rules apply fleet-wide; a categorical per-node override requires remembered or contemporary evidence and an explicit reference.

## Data firewall and release order

1. Compile geometry and freeze the gates hash.
2. Open only the calibration ledger.
3. Run quotient observability, placement posterior/profile, multistart, interleaved sampling sensitivity and model-mismatch checks.
4. If calibration passes, render calibration-action previews only.
5. Only after explicit preview acceptance may `walk` be opened once. At that transition record `WALK_HELDOUT_STATUS = CONSUMED_FOR_VISUALIZATION`.
6. `final_still` remains sealed. No threshold, covariance, length, transform or placement prior may change after `walk` is opened.

Missing shoe geometry blocks only detailed foot/shoe rendering. The torso and limb proxy centerline remains independently evaluable.
