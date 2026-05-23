# V4-io Field Check

- output: `/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/autopos_pipeline/erlangen_20260528_mocap/solver/outputs/v4io_field_check`

## Summary
- layout residual RMS: `32.48142062170568` mm
- layout residual p95 abs: `73.24996506851636` mm
- static captures used: `3`
- static 3D median: `46.393529644498756` mm
- static 3D p95: `46.41324404484319` mm
- roto dR RMS: `31.390975046319536` mm
- roto turn-center median: `17.965046306057136` mm

## Anchor Layout Quality
| eval set | RMS mm | p95 abs mm | max abs mm |
|---|---:|---:|---:|
| all1000 | 32.48142062170568 | 73.24996506851636 | 104.75772841393336 |
| solve | 32.48142062170568 | 73.24996506851636 | 104.75772841393336 |

## Static Captures
| ID | X std | Y std | Z std | 3D std |
|---|---:|---:|---:|---:|
|  | 34.79433233922871 | 16.99325254954536 | 25.55275714535827 | 46.393529644498756 |
|  | 33.402467555278186 | 16.372377492102416 | 27.093390964670213 | 46.019902406347924 |
|  | 33.6284092335296 | 17.005910901394707 | 27.098369873385508 | 46.41543453377035 |

## Roto Physical Consistency
| captures | dR mean | dR RMS | turn-center median |
|---:|---:|---:|---:|
| 3 | -29.824064965788693 | 31.390975046319536 | 17.965046306057136 |

## Wand Static Summary
| ID | pair bias RMS mm | max abs bias mm |
|---|---:|---:|
|  |  |  |
|  |  |  |
|  |  |  |
|  |  |  |
|  |  |  |
|  |  |  |
|  |  |  |

## Important Caveat
This is a field sanity check. Without OptiTrack alignment and final metadata review, do not treat these numbers as final absolute accuracy.

## Ultrasound Height-Aligned Layout
- `/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/autopos_pipeline/erlangen_20260528_mocap/solver/outputs/v4io_field_check/v4-io/layout_us_height.json`
- Uses Anchor H ultrasound median antenna-center height as the z-up frame reference.
