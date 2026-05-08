# AutoPos V1-V5 Complete Solver Progression

Output directory: `/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/autopos_pipeline/outdoor_v4_20260504/v1_to_v5_20260505_124031`

## Table 1: Calibration Quality

| Solver | Fusion | Delay est. | Inter RMS (28) | Inlier RMS (<=30mm) | N inlier | Delay range | Converged |
| --- | --- | --- | --- | --- | --- | --- | --- |
| V1 | simple avg | No | 42.1 | 16.8 | 16 | 0.0 | Yes |
| V2 | IVW | No | 42.4 | 17.2 | 16 | 0.0 | Yes |
| V3-lite | MAD+MVUE | No | 42.4 | 16.0 | 15 | 0.0 | Yes |
| V3-full | MAD+MVUE | Tukey alt | 65.5 | 3.9 | 22 | 138.5 | No |
| V4-io | MAD+MVUE | Huber joint | 48.0 | 13.3 | 24 | 65.9 | Yes |
| V4-roto | MAD+MVUE | Huber joint | 69.6 | 13.6 | 16 | 75.1 | Yes |
| V4-all | MAD+MVUE | Huber joint | 78.0 | 11.0 | 14 | 94.8 | Yes |


## Table 2: ID02 Positioning (Primary Comparison)

| Solver | 8-anc X | Y | Z | 3D | 6-anc (no D/H) 3D |
| --- | --- | --- | --- | --- | --- |
| V1 | 17.6 | 24.0 | 41.9 | 51.4 | 44.7 |
| V2 | 17.6 | 24.0 | 41.7 | 51.2 | 44.6 |
| V3-lite | 17.6 | 23.9 | 41.6 | 51.2 | 44.6 |
| V3-full | 17.8 | 26.5 | 44.4 | 54.7 | 45.8 |
| V4-io | 18.8 | 27.1 | 44.3 | 55.2 | 44.0 |
| V4-roto | 19.5 | 24.5 | 48.4 | 57.6 | 48.3 |
| V4-all | 20.5 | 26.5 | 49.0 | 59.3 | 48.1 |


## Table 3: V3-full Debug

| Metric | Value |
| --- | --- |
| Iterations to converge | 50 |
| Final delay range | 138.5 |
| N Tukey-rejected pairs (weight=0) | 6 |
| Sigma floor used? | Yes (5mm) |
| Final delays A..H | A=+0.0, B=+33.7, C=+101.6, D=-36.8, E=+3.4, F=+58.0, G=+21.2, H=+2.3 |


## Table 4: V4 Delay Comparison

| Anchor | V3-full delay | V4-io delay | V4-roto delay | V4-all delay |
| --- | --- | --- | --- | --- |
| A | 0.0 | 0.0 | 0.0 | 0.0 |
| B | 33.7 | 22.3 | 50.5 | 60.0 |
| C | 101.6 | 20.4 | 33.9 | 38.5 |
| D | -36.8 | 11.6 | 11.3 | 12.3 |
| E | 3.4 | -5.9 | -12.5 | -34.8 |
| F | 58.0 | 3.8 | 10.4 | 53.7 |
| G | 21.2 | 60.0 | 31.9 | 41.9 |
| H | 2.3 | 9.6 | -24.7 | -26.3 |


## Table 5: V5 Per-Anchor Uncertainty

| Anchor | sigma_x | sigma_y | sigma_z | sigma_3D | sigma_d |
| --- | --- | --- | --- | --- | --- |
| A | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 |
| B | 22.952 | 0.000 | 0.000 | 22.952 | 19.426 |
| C | 30.128 | 36.527 | 0.000 | 47.349 | 19.562 |
| D | 23.245 | 20.974 | 62.088 | 69.535 | 18.552 |
| E | 23.073 | 32.640 | 22.965 | 46.099 | 18.924 |
| F | 29.709 | 44.043 | 30.990 | 61.504 | 19.190 |
| G | 58.640 | 50.038 | 65.945 | 101.445 | 62.784 |
| H | 24.670 | 32.487 | 58.120 | 71.007 | 18.530 |


V4-interonly FIM condition number: `1.223e+03`. GDOP/static-std Pearson r: `-0.053`. Estimated sigma_range: `62.1 mm`.


## Table 6: Progression Summary

| Step | What changed | ID02 3D std (8-anc) | ID02 3D std (6-anc) | Delta |
| --- | --- | --- | --- | --- |
| V1 | baseline | 51.4 | 44.7 | ref |
| V2 | +IVW fusion | 51.2 | 44.6 | -0.1 |
| V3-lite | +MAD+MVUE | 51.2 | 44.6 | -0.2 |
| V3-full | +delay est | 54.7 | 45.8 | 3.3 |
| V4-io | +Huber joint | 55.2 | 44.0 | 3.9 |
| V4-roto | +tag ranges (roto) | 57.6 | 48.3 | 6.3 |
| V4-all | +tag ranges (all) | 59.3 | 48.1 | 8.0 |


## Key Findings

- The progression is re-derived from raw sweep/TR data in one standalone script; no previous layouts are used as solver inputs.

- V1, V2, and V3-lite are effectively tied on ID02: 51.4 mm, 51.2 mm, and 51.2 mm. On this outdoor 500-set sweep, the raw bidirectional pair data is already clean enough that IVW and MAD+MVUE fusion only make sub-millimeter-to-few-millimeter layout changes.

- V3-full is not valid as a deployable calibration in this run. The 5 mm Tukey sigma floor prevents the previous sigma collapse, but the alternating delay update still escapes to a 138.5 mm delay range, with C=+101.6 mm and F=+58.0 mm. That is a model/optimization failure, not a physical antenna-delay estimate.

- V4-interonly converges, but its delay range is still large at 65.9 mm, with G hitting +60.0 mm. That says the joint Huber model is using delay to absorb geometric/range inconsistency.

- Adding tag ranges from roto or all captures does not improve ID02; it degrades 8-anchor 3D std from 55.2 mm to 57.6/59.3 mm and increases inter-anchor RMS. The tag range factors are currently pulling the layout away from the inter-anchor solution.

- The strongest repeatable improvement is excluding D and H during positioning. Across the progression, 6-anchor no-D/H evaluation is about 44-48 mm, while all-8 evaluation is 51-59 mm. This points to anchor/pair quality handling as the immediate practical lever.

- V5 FIM marks G as the weakest anchor in the V4-interonly solve: sigma_3D=101.4 mm and sigma_d=62.8 mm. D/H also have high spatial uncertainty. This agrees with the empirical no-D/H positioning improvement and reinforces that the next solver should include per-anchor/pair reliability rather than blindly trusting all 8 anchors.

- GDOP does not explain the static-capture error variation here (Pearson r=-0.053). The observed floor is more consistent with range quality / anchor-specific bias than pure geometry dilution.

- Recommended next step: freeze V1/V2/V3-lite as the honest outdoor baseline, do not push V3-full/V4-joint layouts from this run, and build the offline solver around robust per-anchor/per-pair quality scoring plus optional D/H exclusion or downweighting.
