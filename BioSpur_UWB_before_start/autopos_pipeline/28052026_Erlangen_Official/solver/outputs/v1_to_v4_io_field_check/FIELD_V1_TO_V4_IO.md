# Erlangen V1 To V4-io Field Check

- output: `/home/zekaixiao/Desktop/28052026_Erlangen_Official/solver/outputs/v1_to_v4_io_field_check`
- policy: V1/V2/V3-lite/V3-full/V4-io use sweep only for layout; static/roto/wand are validation only.

## Version Summary
| version | label | AutoPos RMS | AutoPos p95 | static med | static p95 | roto dR RMS | turn-center med |
|---|---|---:|---:|---:|---:|---:|---:|
| v1-old | V1 | 85.2463578425899 | 182.22076061099776 | 62.90848464100333 | 94.64023875377576 | 42.89735397171129 | 18.08947442085983 |
| v2 | V2 | 52.3579326922222 | 87.23606188418795 | 61.72376751975852 | 89.0684617972022 | 28.801410333821412 | 14.98636835817704 |
| v3-lite | V3-lite | 52.29109680475433 | 87.90975655994784 | 61.929379411895184 | 89.44629158579723 | 28.848852758641982 | 15.118652697719792 |
| v3-full | V3-full | 102.49657601163851 | 258.59489870056893 | 64.30244496121148 | 95.08627484242199 | 36.13499248953618 | 17.119536007953673 |
| v4-io | V4-io | 48.168758821052705 | 108.78846050661649 | 58.603243414574436 | 88.19886400901106 | 32.42899743202175 | 14.314491940159968 |

## Anchor Layout Quality
| version | eval set | RMS mm | p95 abs mm | max abs mm |
|---|---|---:|---:|---:|
| v1-old | all1000 | 85.2463578425899 | 182.22076061099776 | 192.3260979234135 |
| v1-old | solve | 85.2463578425899 | 182.22076061099776 | 192.3260979234135 |
| v2 | all1000 | 52.3579326922222 | 87.23606188418795 | 98.26143377924836 |
| v2 | solve | 52.3579326922222 | 87.23606188418795 | 98.26143377924836 |
| v3-full | all1000 | 102.49657601163851 | 258.59489870056893 | 370.03169685860644 |
| v3-full | solve | 102.49657601163851 | 258.59489870056893 | 370.03169685860644 |
| v3-lite | all1000 | 52.29109680475433 | 87.90975655994784 | 98.89625859365151 |
| v3-lite | solve | 52.29109680475433 | 87.90975655994784 | 98.89625859365151 |
| v4-io | all1000 | 48.168758821052705 | 108.78846050661649 | 141.72779945142702 |
| v4-io | solve | 48.168758821052705 | 108.78846050661649 | 141.72779945142702 |

## Delay Sanity
| version | delay min | delay max | delay L2 | near bounds |
|---|---:|---:|---:|---:|
| v1-old | 0.0 | 0.0 | 0.0 |  |
| v2 | 0.0 | 0.0 | 0.0 |  |
| v3-lite | 0.0 | 0.0 | 0.0 |  |
| v3-full | -5.81207993397561 | 96.56447416541096 | 144.4164035648517 |  |
| v4-io | 0.0 | 59.999999999999986 | 109.95498880807628 |  |

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
