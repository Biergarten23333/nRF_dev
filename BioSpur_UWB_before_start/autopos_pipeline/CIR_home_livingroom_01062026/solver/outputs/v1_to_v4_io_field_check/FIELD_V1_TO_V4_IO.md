# Erlangen V1 To V4-io Field Check

- output: `/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/autopos_pipeline/CIR_home_livingroom_01062026/solver/outputs/v1_to_v4_io_field_check`
- policy: V1/V2/V3-lite/V3-full/V4-io use sweep only for layout; static/roto/wand are validation only.

## Version Summary
| version | label | AutoPos RMS | AutoPos p95 | static med | static p95 | roto dR RMS | turn-center med |
|---|---|---:|---:|---:|---:|---:|---:|
| v1-old | V1 | 238.81635583026096 | 379.41630481673894 |  |  |  |  |
| v2 | V2 | 111.17486645231409 | 217.43498723046767 |  |  |  |  |
| v3-lite | V3-lite | 113.0796468807706 | 219.49578656655729 |  |  |  |  |
| v3-full | V3-full | 136.07643772044972 | 312.98722606230575 |  |  |  |  |
| v4-io | V4-io | 132.3072539539271 | 205.09163896635562 |  |  |  |  |

## Anchor Layout Quality
| version | eval set | RMS mm | p95 abs mm | max abs mm |
|---|---|---:|---:|---:|
| v1-old | all1000 | 238.81635583026096 | 379.41630481673894 | 879.0346497390797 |
| v1-old | solve | 238.81635583026096 | 379.41630481673894 | 879.0346497390797 |
| v2 | all1000 | 111.17486645231409 | 217.43498723046767 | 356.4394639938382 |
| v2 | solve | 111.17486645231409 | 217.43498723046767 | 356.4394639938382 |
| v3-full | all1000 | 136.07643772044972 | 312.98722606230575 | 532.4605705690647 |
| v3-full | solve | 136.07643772044972 | 312.98722606230575 | 532.4605705690647 |
| v3-lite | all1000 | 113.0796468807706 | 219.49578656655729 | 364.94005010869023 |
| v3-lite | solve | 113.0796468807706 | 219.49578656655729 | 364.94005010869023 |
| v4-io | all1000 | 132.3072539539271 | 205.09163896635562 | 613.9688849909926 |
| v4-io | solve | 132.3072539539271 | 205.09163896635562 | 613.9688849909926 |

## Delay Sanity
| version | delay min | delay max | delay L2 | near bounds |
|---|---:|---:|---:|---:|
| v1-old | 0.0 | 0.0 | 0.0 |  |
| v2 | 0.0 | 0.0 | 0.0 |  |
| v3-lite | 0.0 | 0.0 | 0.0 |  |
| v3-full | -89.25546112949678 | 113.71956055969122 | 150.9371671125939 |  |
| v4-io | -30.32549589856762 | 33.780428884702836 | 55.74464407424798 |  |

## Main Files
- `tables/version_summary.csv`
- `tables/autopos_quality_summary.csv`
- `tables/delay_sanity.csv`
- `v1-old/layout.json`
- `v2/layout.json`
- `v3-lite/layout.json`
- `v3-full/layout.json`
- `v4-io/layout.json`

## Important Caveat
This is a field sanity check. Final OptiTrack alignment and metadata review still happen later.
