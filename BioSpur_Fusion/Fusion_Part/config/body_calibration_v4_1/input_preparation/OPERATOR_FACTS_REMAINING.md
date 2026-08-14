# V4.1 input facts still required

Status: `INPUT_PREPARATION_WAITING_FOR_OPERATOR_FACTS`

This list was produced without opening the raw capture, calibration ledger, `walk`, `final_still`, or any historical V3/V4/V4.1 analysis payload. Blank means unknown; it does not mean zero.

## 1. Direct subject measurements

For every item below, provide three independently repositioned readings, the instrument name, and its smallest scale division. The operator supplies readings, not uncertainty.

- acromion → lateral humeral epicondyle, L/R;
- lateral humeral epicondyle → radial/ulnar styloid midpoint, L/R;
- greater trochanter → lateral femoral epicondyle, L/R;
- lateral femoral epicondyle → malleolar midpoint, L/R;
- biacromial breadth;
- ASIS breadth;
- C7 → mid-PSIS;
- horizontal pelvis anterior-posterior depth.

Optional sanity-only facts: barefoot height and body mass. They are excluded from the Harrington/Meskers derivations and calibration residuals.

## 2. Shoulder joint centres

Meskers 1998 requires three complete, common-frame 3D digitization passes of AC, AA, TS, AI and PC on both shoulders. These are specialist surface-landmark observations, not internal centre values. If a digitizer and trained palpation are unavailable, report that fact; do not estimate the internal shoulder centre by eye or replace it with a fixed offset.

## 3. Internal segment-centre lengths

The required tape distances terminate at surface landmarks. They do not uniquely determine shoulder-centre→elbow-centre, elbow-centre→wrist-centre, hip-centre→knee-centre, or knee-centre→ankle-centre lengths. To prepare those frozen B scalars without relabelling surface chords, provide evidence locating the medial/lateral elbow, wrist, knee and ankle centres in a common geometry, or an independently versioned functional/joint-centre acquisition. No population-average replacement will be made.

## 4. Capture shoes

Provide three readings each for foot length L/R, floor→malleolar midpoint L/R while wearing the capture shoes, rear heel stack L/R and forefoot stack L/R. Also provide:

- brand, model, size and identifying features of the actual capture shoes;
- capture-shoe photo references, or an explicit statement that no suitable photographs exist.

Heel-minus-forefoot elevation is calculated from paired repeats; do not type a nominal “7 cm heel.”

## 5. Historical placement of each node

For every node below, fill evidence status, anatomical surface/landmark relation, enclosure long-axis direction, antenna-facing direction, strap width/provenance, likely slip bounds, and photo/video reference or bounded recollection:

`BSF31CC`, `BSFC2CC`, `BSFAA61`, `BSF1120`, `BSFB165`, `BSFEC35`, `BSF44AD`, `BSF3C79`, `BSF6C53`, `BSF8BC4`.

Also state whether any surviving strap is the actual capture strap. If yes, provide three width readings and provenance. Recollection is at most `CALIBRATION_ESTIMATED`; only contemporary evidence can support `MEASURED_CAPTURE_DAY` or `PHOTO_DERIVED`.

## 6. Shared hardware facts — measure once, not ten times

- confirm whether all ten assemblies share the same PCB-to-enclosure registration and antenna-end orientation;
- versioned enclosure CAD/source drawing, or three outer dimensions plus inner registration geometry;
- U4 reference-to-enclosure-frame registration and mechanical play along three axes;
- an evidence-backed effective RF phase-centre convention inside the DWM1001C antenna area;
- a non-zero assembly-tolerance distribution from the enclosure fit.

The verified PCB source proves U4/DWM1001C and U7/IMU placement in `PCB17`/`PCB17_1`, and the official module datasheet bounds the DWM1001C package. Neither source marks the RF phase centre; no enclosure geometry is present. Therefore the current shared transform is correctly `BLOCKED_SHARED_TRANSFORM_INCOMPLETE`, not zero.
