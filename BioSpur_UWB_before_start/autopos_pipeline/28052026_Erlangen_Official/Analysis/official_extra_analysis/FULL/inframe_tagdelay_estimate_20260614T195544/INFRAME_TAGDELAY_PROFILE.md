# In-Frame Tag-Delay Profile Sweep

- Common-mode layout: `/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/autopos_pipeline/28052026_Erlangen_Official/solver/outputs/v1_to_v4_io_field_check/v4-io-commonmode/layout.json`
- Output directory: `/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/autopos_pipeline/28052026_Erlangen_Official/Analysis/official_extra_analysis/FULL/inframe_tagdelay_estimate_20260614T195544`
- Production C-core solver unchanged: replay uses `static_tag_raw_replay_matrix.py --tag-methods T4`.
- Range score is post-hoc plain sigma-weighted SSR: `sum(((||x-A_i|| + d_anchor_i + d_tag - range_i) / sigma_i)^2)`.
- The C-core can additionally apply quality/residual-history penalties in low-anchor frames; this profile uses plain sigma weighting to approximate the range objective.

## Sweep Table

| d_tag_mm | Vicon median 3D mm | range SSR | SSR/term | sigma RMSE | vertical slope mm/m | vertical R2 |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 0.000 | 109.515 | 5777347.213 | 25.309825 | 5.031 | 196.435 | 0.7693 |
| 10.000 | 98.382 | 5576265.540 | 24.428912 | 4.943 | 170.399 | 0.7359 |
| 20.000 | 86.124 | 5410581.712 | 23.703072 | 4.869 | 145.266 | 0.6894 |
| 30.000 | 72.607 | 5283757.571 | 23.147471 | 4.811 | 121.180 | 0.6235 |
| 35.000 | 71.446 | 5235644.456 | 22.936694 | 4.789 | 109.526 | 0.5812 |
| 37.000 | 72.806 | 5219349.309 | 22.865307 | 4.782 | 104.936 | 0.5622 |
| 39.000 | 73.848 | 5204746.777 | 22.801335 | 4.775 | 100.388 | 0.5419 |
| 40.000 | 74.039 | 5198107.634 | 22.772250 | 4.772 | 98.131 | 0.5313 |
| 41.000 | 74.238 | 5191922.984 | 22.745156 | 4.769 | 95.885 | 0.5204 |
| 43.000 | 74.650 | 5180881.834 | 22.696786 | 4.764 | 91.425 | 0.4976 |
| 45.000 | 72.540 | 5171531.779 | 22.655824 | 4.760 | 87.007 | 0.4736 |
| 47.000 | 70.405 | 5163925.249 | 22.622501 | 4.756 | 82.629 | 0.4483 |
| 49.000 | 68.297 | 5158120.058 | 22.597069 | 4.754 | 78.288 | 0.4218 |
| 50.000 | 68.055 | 5155918.274 | 22.587424 | 4.753 | 76.131 | 0.4082 |
| 51.000 | 68.583 | 5154176.501 | 22.579793 | 4.752 | 73.984 | 0.3943 |
| 53.000 | 68.750 | 5152122.633 | 22.570795 | 4.751 | 69.720 | 0.3658 |
| 55.000 | 67.180 | 5151935.734 | 22.569977 | 4.751 | 65.494 | 0.3366 |
| 57.000 | 65.555 | 5153543.859 | 22.577022 | 4.752 | 61.307 | 0.3068 |
| 59.000 | 63.849 | 5156962.012 | 22.591996 | 4.753 | 57.162 | 0.2768 |
| 60.000 | 63.511 | 5159382.766 | 22.602601 | 4.754 | 55.104 | 0.2618 |
| 61.000 | 63.186 | 5162275.005 | 22.615272 | 4.756 | 53.057 | 0.2468 |
| 63.000 | 61.423 | 5169524.342 | 22.647030 | 4.759 | 48.993 | 0.2171 |
| 65.000 | 58.922 | 5178712.945 | 22.687284 | 4.763 | 44.970 | 0.1881 |
| 70.000 | 59.439 | 5210460.015 | 22.826364 | 4.778 | 35.067 | 0.1208 |
| 80.000 | 60.066 | 5313493.237 | 23.277740 | 4.825 | 15.865 | 0.0258 |
| 90.000 | 58.485 | 5471817.651 | 23.971339 | 4.896 | -2.601 | 0.0007 |
| 91.153 | 58.591 | 5493761.695 | 24.067473 | 4.906 | -4.688 | 0.0021 |
| 95.000 | 60.739 | 5572751.268 | 24.413516 | 4.941 | -11.591 | 0.0126 |
| 100.000 | 64.964 | 5688821.242 | 24.922004 | 4.992 | -20.433 | 0.0368 |
| 110.000 | 73.152 | 5968418.752 | 26.146885 | 5.113 | -37.724 | 0.1081 |
| 120.000 | 80.494 | 6314255.295 | 27.661951 | 5.259 | -54.524 | 0.1911 |
| 130.000 | 89.806 | 6729233.384 | 29.479918 | 5.430 | -70.779 | 0.2712 |
| 140.000 | 99.379 | 7214793.473 | 31.607095 | 5.622 | -86.493 | 0.3425 |
| 150.000 | 112.165 | 7771460.053 | 34.045780 | 5.835 | -101.649 | 0.4037 |

## Summary

- `d_tag_range*`: `55.000` mm, range SSR `5151935.734`.
- `d_tag_vicon*`: `90.000` mm, Vicon median `58.485` mm.
- Minima separation: `-35.000` mm.
- Honest oracle-free deployable Vicon median at `d_tag_range*`: `67.180` mm.
- Delta vs frozen v4-io `72.69` mm: `-5.510` mm.
- Delta vs sampled 91.153-mm stand-in `58.591` mm: `+8.589` mm.
- SSR at 0 / min / 150: `5777347.213` / `5151935.734` / `7771460.053`.
- SSR ratios at 0 and 150 vs min: `1.121393` / `1.508454`.
- +5% SSR delay span: `30.000` to `80.000` mm (`50.000` mm wide).

**Verdict:** DOES NOT RECOVER: the range-SSR profile prefers a different/flat delay region, so in-frame profiling does not recover the oracle static accuracy.
## Scale-Coupling Diagnostic

This diagnostic reruns the same production C-core T4 path used by the profile sweep, then scores the resulting 24 session-mean positions three ways. The official sanity column is still anchor-locked rigid no-scale and reproduces the sweep table. The scale columns are tag-cloud diagnostics: they fit the solved 24-point tag cloud to Vicon truth and are therefore not headline accuracy metrics.

- Output CSV: `/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/autopos_pipeline/28052026_Erlangen_Official/Analysis/official_extra_analysis/FULL/inframe_tagdelay_estimate_20260614T195544/tables/scale_coupling_profile.csv`
- Anchor-locked reproduction max absolute delta versus the existing sweep table: `0.000000` mm.
- At `d_tag_range* = 55 mm`: tag-cloud scale `s = 0.951875`, internal pair scale `s_internal = 1.040121`.
- At `d_tag_vicon* = 90 mm`: tag-cloud scale `s = 1.000488`, internal pair scale `s_internal = 1.001719`.
- At oracle stand-in `91.153 mm`: tag-cloud scale `s = 1.002062`, internal pair scale `s_internal = 1.000448`.
- Anchor-locked no-scale 55-vs-90 gap: `+8.695` mm. Tag-cloud allow-scale 55-vs-90 gap: `-0.245` mm. Collapse in absolute gap: `8.450` mm.
- Best tag-cloud allow-scale median occurs at `d_tag = 63.000` mm with `49.725` mm.

| d_tag_mm | vicon median rigid/no-scale mm | tag-cloud scale s | tag-cloud median with scale mm | s_internal median |
| ---: | ---: | ---: | ---: | ---: |
| 0.000 | 109.515 | 0.865970 | 72.992 | 1.138774 |
| 10.000 | 98.382 | 0.882336 | 71.153 | 1.119183 |
| 20.000 | 86.124 | 0.898533 | 68.315 | 1.098746 |
| 30.000 | 72.607 | 0.914377 | 62.300 | 1.077987 |
| 35.000 | 71.446 | 0.922137 | 59.830 | 1.067561 |
| 37.000 | 72.806 | 0.925205 | 59.297 | 1.065275 |
| 39.000 | 73.848 | 0.928250 | 59.477 | 1.063491 |
| 40.000 | 74.039 | 0.929764 | 58.845 | 1.061819 |
| 41.000 | 74.238 | 0.931272 | 58.222 | 1.060153 |
| 43.000 | 74.650 | 0.934273 | 58.222 | 1.056664 |
| 45.000 | 72.540 | 0.937252 | 58.340 | 1.055892 |
| 47.000 | 70.405 | 0.940212 | 57.658 | 1.052220 |
| 49.000 | 68.297 | 0.943154 | 56.778 | 1.049244 |
| 50.000 | 68.055 | 0.944619 | 56.347 | 1.047681 |
| 51.000 | 68.583 | 0.946078 | 55.922 | 1.045213 |
| 53.000 | 68.750 | 0.948985 | 54.553 | 1.042887 |
| 55.000 | 67.180 | 0.951875 | 53.354 | 1.040121 |
| 57.000 | 65.555 | 0.954748 | 52.335 | 1.038206 |
| 59.000 | 63.849 | 0.957603 | 51.381 | 1.034567 |
| 60.000 | 63.511 | 0.959024 | 50.923 | 1.033785 |
| 61.000 | 63.186 | 0.960440 | 50.479 | 1.033413 |
| 63.000 | 61.423 | 0.963260 | 49.725 | 1.031987 |
| 65.000 | 58.922 | 0.966066 | 49.902 | 1.027868 |
| 70.000 | 59.439 | 0.973033 | 50.472 | 1.021732 |
| 80.000 | 60.066 | 0.986810 | 51.998 | 1.010270 |
| 90.000 | 58.485 | 1.000488 | 53.599 | 1.001719 |
| 91.153 | 58.591 | 1.002062 | 53.684 | 1.000448 |
| 95.000 | 60.739 | 1.007317 | 52.564 | 0.994881 |
| 100.000 | 64.964 | 1.014156 | 52.090 | 0.987201 |
| 110.000 | 73.152 | 1.027920 | 60.800 | 0.974876 |
| 120.000 | 80.494 | 1.041872 | 65.088 | 0.961419 |
| 130.000 | 89.806 | 1.055885 | 69.765 | 0.951345 |
| 140.000 | 99.379 | 1.069881 | 80.580 | 0.940063 |
| 150.000 | 112.165 | 1.083773 | 88.298 | 0.927045 |

**Verdict:** The low range-optimal delay is explained by a delay-scale trade. At 55 mm the solved tag cloud is expanded (`s_internal` about 1.049, tag-cloud fit scale about 0.956 to contract it back to Vicon), while at 90-91.153 mm the scale is near metric (`s_internal` about 1.01, tag-cloud fit scale about 0.991). Allowing scale in the Vicon fit collapses most of the official 55-vs-90 gap, showing that range SSR can trade tag delay against geometric scale and therefore cannot identify the physically correct tag delay in-frame.
## One-Distance Scale Break

This section tests whether the delay-scale coupling can be broken by one externally known inter-anchor distance. Positions are regenerated with the unchanged production C-core T4 path. The correction then uses one anchor-pair distance ratio at a time and applies `1/ratio` to the common-mode anchors and the `d_tag=55 mm` solved tag cloud about the solved-anchor centroid before reusing the same anchor-locked rigid/no-scale scoring semantics.

- Ratio stats CSV: `/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/autopos_pipeline/28052026_Erlangen_Official/Analysis/official_extra_analysis/FULL/inframe_tagdelay_estimate_20260614T195544/tables/one_distance_scale_ratio_stats.csv`
- Long pair-ratio CSV: `/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/autopos_pipeline/28052026_Erlangen_Official/Analysis/official_extra_analysis/FULL/inframe_tagdelay_estimate_20260614T195544/tables/one_distance_pair_ratios_long.csv`
- One-distance correction CSV: `/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/autopos_pipeline/28052026_Erlangen_Official/Analysis/official_extra_analysis/FULL/inframe_tagdelay_estimate_20260614T195544/tables/one_distance_anchor_pair_correction.csv`

### Registration Semantics

`static_tag_absolute_accuracy.py` loads the solved layout anchors as `src` and Vicon anchor truth as `dst`, fits `fit_similarity(src, dst, allow_reflection=True, allow_scale=False)`, then applies that rigid transform to the solved tag positions. Therefore no scale is fitted by the official score; scale only enters here through the manually corrected solved coordinates supplied before the rigid registration.

### Per-Pair Scale Uniformity

| object | d_tag_mm | n_pairs | median | IQR | std | min | max |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| anchor_anchor | 55.000 | 28 | 0.990143 | 0.026825 | 0.020193 | 0.951249 | 1.020840 |
| tag_position_position | 55.000 | 276 | 1.040121 | 0.079676 | 0.724890 | 0.700826 | 9.615553 |
| anchor_anchor | 90.000 | 28 | 0.990143 | 0.026825 | 0.020193 | 0.951249 | 1.020840 |
| tag_position_position | 90.000 | 276 | 1.001719 | 0.084195 | 0.712435 | 0.311854 | 8.427062 |

Anchor-anchor scale does not carry the `+4%` tag-cloud expansion. The common-mode anchor layout is already near metric and has a median anchor-pair ratio near 0.991, while the `d_tag=55 mm` tag position cloud has median pair ratio near 1.040. At `d_tag=90 mm`, the tag position-pair scale collapses to near metric.

### One Anchor-Distance Correction at d_tag = 55 mm

- Uncorrected `d_tag=55` median: `67.180` mm.
- Optimal tag-cloud scale diagnostic floor at `d_tag=55`: `53.354` mm.
- Oracle `d_tag=90` median: `58.485` mm.
- Across all 28 single anchor-pair corrections: median corrected median `67.000` mm, IQR `2.544` mm, std `2.821` mm, min/max `62.829`/`75.517` mm.

| anchor pair choice | pair | true dist mm | ratio | applied scale | corrected median 3D mm |
| --- | --- | ---: | ---: | ---: | ---: |
| shortest | B-F | 1392.383 | 0.994699 | 1.005329 | 66.903 |
| median-length | A-B | 2665.064 | 1.017248 | 0.983045 | 65.098 |
| longest | B-H | 3745.512 | 0.984093 | 1.016164 | 68.964 |
| best of all 28 | A-C | 3414.109 | 1.020840 | 0.979586 | 62.829 |
| worst of all 28 | A-E | 1403.637 | 0.951249 | 1.051250 | 75.517 |

**Verdict:** One anchor baseline does not recover most of the oracle gain in this common-mode + tag-delay setting. The `+4%` scale is a tag-solution scale induced by the wrong tag delay, not a uniform anchor-layout scale: anchor pairs are near metric already, while the `d_tag=55 mm` tag cloud is expanded. A single anchor distance therefore estimates the wrong scale for the tag cloud and leaves the corrected median around the uncorrected 67 mm level rather than the 58.5 mm oracle or the 53.4 mm scale-free floor.

