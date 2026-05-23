# Erlangen V1 To V4-io Field Check

- output: `/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/autopos_pipeline/erlangen_20260528_mocap/solver/outputs/v1_to_v4_io_field_check`
- policy: V1/V2/V3-lite/V3-full/V4-io use sweep only for layout; static/roto/wand are validation only.

## Version Summary
| version | label | AutoPos RMS | AutoPos p95 | static med | static p95 | roto dR RMS | turn-center med |
|---|---|---:|---:|---:|---:|---:|---:|
| v1-old | V1 | 124.75454795814217 | 281.1852715126529 |  |  | 6.511534005402038 | 14.92031105374554 |
| v2 | V2 | 101.93307384394491 | 189.25958867561752 |  |  | 10.979274571251256 | 13.521556726360352 |
| v3-lite | V3-lite | 100.85558929202148 | 188.43223312693414 |  |  | 10.047320164441015 | 13.313159885477035 |
| v3-full | V3-full | 150.20801860746292 | 384.22440954790073 |  |  | 16.174461587697653 | 20.254433674108398 |
| v4-io | V4-io | 123.4411896688199 | 279.06307010800384 |  |  | 11.672917705318822 | 16.36238139190744 |

## Anchor Layout Quality
| version | eval set | RMS mm | p95 abs mm | max abs mm |
|---|---|---:|---:|---:|
| v1-old | all1000 | 124.75454795814217 | 281.1852715126529 | 295.3236351598946 |
| v1-old | solve | 124.75454795814217 | 281.1852715126529 | 295.3236351598946 |
| v2 | all1000 | 101.93307384394491 | 189.25958867561752 | 247.90021225846613 |
| v2 | solve | 101.93307384394491 | 189.25958867561752 | 247.90021225846613 |
| v3-full | all1000 | 150.20801860746292 | 384.22440954790073 | 549.5149487261501 |
| v3-full | solve | 150.20801860746292 | 384.22440954790073 | 549.5149487261501 |
| v3-lite | all1000 | 100.85558929202148 | 188.43223312693414 | 254.5468333485178 |
| v3-lite | solve | 100.85558929202148 | 188.43223312693414 | 254.5468333485178 |
| v4-io | all1000 | 123.4411896688199 | 279.06307010800384 | 462.8240292585774 |
| v4-io | solve | 123.4411896688199 | 279.06307010800384 | 462.8240292585774 |

## Delay Sanity
| version | delay min | delay max | delay L2 | near bounds |
|---|---:|---:|---:|---:|
| v1-old | 0.0 | 0.0 | 0.0 |  |
| v2 | 0.0 | 0.0 | 0.0 |  |
| v3-lite | 0.0 | 0.0 | 0.0 |  |
| v3-full | -233.90629515736555 | 176.76794273198334 | 371.36201963638246 |  |
| v4-io | -12.714800063581231 | 59.99999999999747 | 74.65551845219049 |  |

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
