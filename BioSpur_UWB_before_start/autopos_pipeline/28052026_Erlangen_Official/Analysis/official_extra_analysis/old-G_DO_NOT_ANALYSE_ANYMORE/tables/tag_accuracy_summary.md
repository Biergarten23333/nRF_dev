# Static Tag Absolute Accuracy

Transform source: anchor alignment only, reflection allowed, no scale. No tag truth was used for fitting.

Tag truth source: corrected static OptiTrack `Iantenna`. ID01 and ID05 use deterministic I1..I5 relabeling plus a rebuilt ball-local consensus antenna; all other captures use Motive `Iantenna` as exported.

This is the production tag-solver output. The full 5 Vx x 4 Tx raw replay matrix is reported separately.

Frame-locking rule: the official values below use method C only, where the UWB->OptiTrack transform is fixed by anchors. Methods A/B are written separately in `tag_alignment_method_comparison.csv` as failure-mode demonstrations.

| version | eval_set | n | median_3d_mm | p95_3d_mm | rms_3d_mm | median_horizontal_mm | median_vertical_mm | median_dist_to_array_mm | median_scale_bias_expected_mm |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| v1-old | all8 | 24 | 167.6 | 314.8 | 193.4 | 54.8 | 144.5 | 757.0 | 32.6 |
| v1-old | noG | 24 | 154.5 | 330.1 | 193.7 | 54.1 | 140.8 | 775.5 | 34.1 |
| v2 | all8 | 24 | 81.5 | 233.1 | 134.0 | 45.5 | 68.2 | 757.0 | 45.0 |
| v2 | noG | 24 | 80.5 | 242.9 | 135.9 | 47.9 | 69.7 | 775.5 | 48.0 |
| v3-full | all8 | 24 | 123.1 | 280.1 | 160.7 | 52.3 | 110.0 | 757.0 | 25.7 |
| v3-full | noG | 24 | 123.8 | 290.4 | 161.3 | 46.8 | 113.7 | 775.5 | 29.1 |
| v3-lite | all8 | 24 | 81.7 | 233.5 | 134.2 | 45.1 | 68.8 | 757.0 | 45.0 |
| v3-lite | noG | 24 | 81.1 | 243.2 | 136.1 | 48.4 | 70.2 | 775.5 | 47.9 |
| v4-io | all8 | 24 | 77.4 | 270.3 | 138.3 | 43.8 | 63.1 | 757.0 | 30.0 |
| v4-io | noG | 24 | 81.3 | 278.6 | 141.1 | 46.9 | 63.5 | 775.5 | 31.2 |

## A/B/C Frame-Locking Sanity

- A fits the transform to tag truth and is circular; it should not be used as an accuracy claim.
- B aligns only centroids; orientation and handedness remain free, so it is reported as an error range over swept rotations/reflections.
- C locks the transform from anchors only and is the official value.

## Iantenna Ground-Truth Correction

| ID | corrected | permutation | shift_from_motive_mm | fingerprint_as_is_max_mm | fingerprint_corrected_max_mm |
| --- | --- | --- | ---: | ---: | ---: |
| ID01 | True | 0,1,4,2,3 | 54.1 | 29.1 | 1.5 |
| ID05 | True | 3,4,2,0,1 | 2.1 | 5.5 | 0.8 |
