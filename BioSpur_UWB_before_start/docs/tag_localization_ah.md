# Tag Localization With Fixed A-H Layout

## Setup

- Fixed anchor layout source: `data/anchor_layout_ah_runtime.json`
- Tag device: `760186127`
- Anchor devices:
  - `A=760186071`
  - `B=760185876`
  - `C=760185878`
  - `D=760186081`
  - `E=760185904`
  - `F=760186124`
  - `G=760185889`
  - `H=760186121`

All anchors were flashed in worker mode with `allow_tag_polls=1`.

## Verification

Tag startup log:

```text
Tag app ready tag_id=0 anchor_count=8 anchors=[0,1,2,3,4,5,6,7]
SS-TWR initiator ready tag=0 addr=0xb100 anchor_count=8
```

First complete 8-anchor sweep:

```text
Tag meas anchor=A(0) range=3007 mm q=100%
Tag meas anchor=B(1) range=3920 mm q=100%
Tag meas anchor=C(2) range=2830 mm q=100%
Tag meas anchor=D(3) range=2073 mm q=100%
Tag meas anchor=E(4) range=3355 mm q=100%
Tag meas anchor=F(5) range=3503 mm q=100%
Tag meas anchor=G(6) range=2766 mm q=100%
Tag meas anchor=H(7) range=1932 mm q=100%
Tag pos sweep=1 used=8 lower=4 upper=4 xyz=(1363,2687,525) mm rms=255 mm anchors=[A,B,C,D,E,F,G,H]
```

Second complete 8-anchor sweep:

```text
Tag pos sweep=2 used=8 lower=4 upper=4 xyz=(1364,2684,517) mm rms=258 mm anchors=[A,B,C,D,E,F,G,H]
```

## Current Status

- `Anchor -> Anchor` matrix collection is complete enough for runtime use.
- Fixed anchor coordinates are now consumed by the Tag firmware.
- `Tag 0` can range to all `8` anchors and produce a 3D position estimate on-device.
- Current position output is stable across consecutive sweeps, with residual RMS around `255-258 mm`.

## Robust Solver Update

The runtime tag solver was upgraded from a plain all-anchor linear solve to:

- automatic anchor subset selection
- non-coplanar preference
- iterative residual refinement
- out-of-volume penalty to reject physically implausible solutions

Observed result after the update:

```text
Tag pos sweep=1 used=4 lower=2 upper=2 xyz=(1738,3231,811) mm rms=18 mm max=24 mm anchors=[B,D,E,H]
Tag pos sweep=2 used=4 lower=2 upper=2 xyz=(1735,3231,812) mm rms=17 mm max=23 mm anchors=[B,D,E,H]
```

This is the current best runtime behavior:

- the tag still ranges to all `8` anchors
- the solver automatically keeps the best `4-anchor` non-coplanar subset for this tag pose
- residual RMS dropped from about `255 mm` to about `17-18 mm`
- the selected anchors are `B/D/E/H`, which gives `2` lower-plane and `2` upper-plane constraints
