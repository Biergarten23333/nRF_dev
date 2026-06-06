# Static Tag Absolute Accuracy

Transform source: anchor alignment only, reflection allowed, no scale. No tag truth was used for fitting.

Tag truth source: corrected static OptiTrack `Iantenna`. ID01 and ID05 use deterministic I1..I5 relabeling plus a rebuilt ball-local consensus antenna; all other captures use Motive `Iantenna` as exported.

This is the production tag-solver output. The full 5 Vx x 4 Tx raw replay matrix is reported separately.

Frame-locking rule: the official values below use method C only, where the UWB->OptiTrack transform is fixed by anchors. Methods A/B are written separately in `tag_alignment_method_comparison.csv` as failure-mode demonstrations.

| version | eval_set | n | median_3d_mm | p95_3d_mm | rms_3d_mm | median_horizontal_mm | median_vertical_mm | median_dist_to_array_mm | median_scale_bias_expected_mm |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| v1-old | all8 | 24 | 157.4 | 326.1 | 192.6 | 51.8 | 139.8 | 743.7 | 33.5 |
| v2 | all8 | 24 | 81.1 | 248.0 | 135.7 | 44.3 | 74.0 | 743.7 | 45.8 |
| v3-full | all8 | 24 | 120.6 | 293.8 | 160.7 | 48.8 | 113.7 | 743.7 | 26.9 |
| v3-lite | all8 | 24 | 81.8 | 248.5 | 135.9 | 44.6 | 74.6 | 743.7 | 45.7 |
| v4-io | all8 | 24 | 74.0 | 282.1 | 139.6 | 42.3 | 65.3 | 743.7 | 31.0 |

## A/B/C Frame-Locking Sanity

- A fits the transform to tag truth and is circular; it should not be used as an accuracy claim.
- B aligns only centroids; orientation and handedness remain free, so it is reported as an error range over swept rotations/reflections.
- C locks the transform from anchors only and is the official value.

## Iantenna Ground-Truth Correction

| ID | corrected | permutation | shift_from_motive_mm | fingerprint_as_is_max_mm | fingerprint_corrected_max_mm |
| --- | --- | --- | ---: | ---: | ---: |
| ID01 | True | 0,1,4,2,3 | 54.1 | 29.1 | 1.5 |
| ID05 | True | 3,4,2,0,1 | 2.1 | 5.5 | 0.8 |
