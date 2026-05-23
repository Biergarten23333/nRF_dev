# Erlangen V1 To V4-io Field Check

- output: `/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/autopos_pipeline/erlangen_20260528_mocap/solver/outputs/v1_to_v4_io_field_check`
- policy: V1/V2/V3-lite/V3-full/V4-io use sweep only for layout; static/roto/wand are validation only.

## Version Summary
| version | label | AutoPos RMS | AutoPos p95 | static med | static p95 | roto dR RMS | turn-center med |
|---|---|---:|---:|---:|---:|---:|---:|
| v1-old | V1 | 65.09677273224183 | 111.31040501391756 | 53.3504336523024 | 53.507032196804026 | 40.649251552889936 | 18.59850001959954 |
| v2 | V2 | 41.18967485959779 | 77.49561821247842 | 46.981560528307895 | 46.98557966999212 | 30.456865690522623 | 17.764918250410595 |
| v3-lite | V3-lite | 40.711824781323784 | 76.96849382108829 | 47.08852119398813 | 47.095497947205224 | 30.416634251464643 | 17.77335240621311 |
| v3-full | V3-full | 66.33529076611543 | 140.12047783048146 | 45.097019166427756 | 45.407464331041794 | 28.696167973329615 | 18.23498540348296 |
| v4-io | V4-io | 32.48142062170568 | 73.24996506851636 | 46.393529644498756 | 46.41324404484319 | 31.390975046319536 | 17.965046306057136 |

## Anchor Layout Quality
| version | eval set | RMS mm | p95 abs mm | max abs mm |
|---|---|---:|---:|---:|
| v1-old | all1000 | 65.09677273224183 | 111.31040501391756 | 139.08566969846333 |
| v1-old | solve | 65.09677273224183 | 111.31040501391756 | 139.08566969846333 |
| v2 | all1000 | 41.18967485959779 | 77.49561821247842 | 88.61949902171636 |
| v2 | solve | 41.18967485959779 | 77.49561821247842 | 88.61949902171636 |
| v3-full | all1000 | 66.33529076611543 | 140.12047783048146 | 259.06005506313386 |
| v3-full | solve | 66.33529076611543 | 140.12047783048146 | 259.06005506313386 |
| v3-lite | all1000 | 40.711824781323784 | 76.96849382108829 | 86.42692416366617 |
| v3-lite | solve | 40.711824781323784 | 76.96849382108829 | 86.42692416366617 |
| v4-io | all1000 | 32.48142062170568 | 73.24996506851636 | 104.75772841393336 |
| v4-io | solve | 32.48142062170568 | 73.24996506851636 | 104.75772841393336 |

## Delay Sanity
| version | delay min | delay max | delay L2 | near bounds |
|---|---:|---:|---:|---:|
| v1-old | 0.0 | 0.0 | 0.0 |  |
| v2 | 0.0 | 0.0 | 0.0 |  |
| v3-lite | 0.0 | 0.0 | 0.0 |  |
| v3-full | -28.030207467621267 | 83.88543213281082 | 103.8344238691898 |  |
| v4-io | 0.0 | 59.99999999999881 | 103.82360655441178 |  |

## Main Files
- `tables/version_summary.csv`
- `tables/autopos_quality_summary.csv`
- `tables/delay_sanity.csv`
- `v1-old/layout.json`
- `v1-old/layout_us_height.json`
- `v2/layout.json`
- `v2/layout_us_height.json`
- `v3-lite/layout.json`
- `v3-lite/layout_us_height.json`
- `v3-full/layout.json`
- `v3-full/layout_us_height.json`
- `v4-io/layout.json`
- `v4-io/layout_us_height.json`

## Ultrasound Height-Aligned Layout
- `layout_us_height.json` files use Anchor H ultrasound median antenna-center height as the z-up frame reference.
- This is a coordinate-frame post-process; it does not change inter-anchor solve residuals.

## Important Caveat
This is a field sanity check. Final OptiTrack alignment and metadata review still happen later.
