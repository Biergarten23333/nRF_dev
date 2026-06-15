# Static Tag Absolute Accuracy

Transform source: anchor alignment only, reflection allowed, no scale. No tag truth was used for fitting.

Tag truth source: corrected static OptiTrack `Iantenna`. ID01 and ID05 use deterministic I1..I5 relabeling plus a rebuilt ball-local consensus antenna; all other captures use Motive `Iantenna` as exported.

This is the production tag-solver output. The full 5 Vx x 4 Tx raw replay matrix is reported separately.

Frame-locking rule: the official values below use method C only, where the UWB->OptiTrack transform is fixed by anchors. Methods A/B are written separately in `tag_alignment_method_comparison.csv` as failure-mode demonstrations.

| version | eval_set | n | median_3d_mm | p95_3d_mm | rms_3d_mm | median_horizontal_mm | median_vertical_mm | median_dist_to_array_mm | median_scale_bias_expected_mm |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| v4-io-commonmode | all8 | 24 | 74.7 | 160.1 | 87.8 | 34.4 | 57.9 | 743.7 | 7.3 |

## A/B/C Frame-Locking Sanity

- A fits the transform to tag truth and is circular; it should not be used as an accuracy claim.
- B aligns only centroids; orientation and handedness remain free, so it is reported as an error range over swept rotations/reflections.
- C locks the transform from anchors only and is the official value.

## Iantenna Ground-Truth Correction

| ID | corrected | permutation | shift_from_motive_mm | fingerprint_as_is_max_mm | fingerprint_corrected_max_mm |
| --- | --- | --- | ---: | ---: | ---: |
| ID01 | True | 0,1,4,2,3 | 54.1 | 29.1 | 1.5 |
| ID05 | True | 3,4,2,0,1 | 2.1 | 5.5 | 0.8 |
