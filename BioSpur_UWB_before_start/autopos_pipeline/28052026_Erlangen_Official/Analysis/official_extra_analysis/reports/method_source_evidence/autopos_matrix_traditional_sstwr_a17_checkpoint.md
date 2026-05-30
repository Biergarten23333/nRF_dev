# AutoPos Matrix Restore Checkpoint - a17

Date: 2026-05-02

## Goal

Keep the broadcast ranging baseline intact, but restore AutoPos Matrix sweep to
traditional anchor-to-anchor unicast SS-TWR. Matrix mode does not need Alt
SS-TWR broadcast timing because there is no tag motion error during inter-anchor
sweep.

## Firmware

Anchor marker:

```text
alt-bcast-a17-matrixslow-g1200-r1000
```

Main changes:

- Matrix poll matching is separated from Tag/broadcast responder matching.
- Matrix accepts only anchor-origin unicast polls addressed to the local anchor.
- Tag polls still use the existing broadcast/Alt SS-TWR responder path.
- Matrix response delay is `APP_ANCHOR_MATRIX_RESP_DELAY_UUS=1200`.
- Matrix master response RX timeout is `APP_ANCHOR_MATRIX_RESP_RX_TIMEOUT_UUS=3500`.
- Generic poll builder no longer clears the full broadcast frame length when used
  for legacy 13-byte Matrix polls.

## Deployment

Master_Anchor B120 carrier flashed with explicit SNR:

```text
SNR 960148546
```

LFRC assert passed before flash.

A-H Anchor OTA:

```text
logs/alt_bcast_a17_matrixslow_anchor_ota_20260502_190701
```

OTA upload succeeded for A-H. Post responder runtime passed:

```text
ready=8/8
```

VERSION readback still returned `actual=-` for A-H. This is the known
Master_Anchor version readback issue and was not treated as OTA failure because
runtime responder verification passed.

## A/B Probe

Log:

```text
autopos_pipeline/logs/a17_matrixslow_AB_probe_20260502_191512
```

Result:

```text
Session guard: matrix ready=8/8
Session finalizer: responder ready=8/8
SW-A: sw=10/10, no zero peers
SW-B: sw=10/10, no zero peers
```

Representative rows:

```text
SW-A,B,4603,100,C,5585,100,D,2784,100,E,1674,100,F,4419,95,G,5619,95,H,3378,100
SW-B,A,4640,95,C,3826,100,D,5380,100,E,4397,100,F,1502,85,G,4166,90,H,6233,95
```

Warning:

```text
Anchor F low quality as Matrix in round B, minq=81
```

## Full A-H Sweep

Log:

```text
autopos_pipeline/logs/a17_matrixslow_full_sweep_20260502_191653
```

Result:

```text
Session guard: matrix ready=8/8
Session finalizer: responder ready=8/8
All rounds A-H: sw=10/10
Zeros: none
Slow switch rounds: none
Reconnect retry rounds: none
```

Final rows:

```text
SW-A,B,4614,100,C,5575,95,D,2731,95,E,1681,100,F,4348,100,G,5457,95,H,3305,95
SW-B,A,4585,95,C,3842,95,D,5390,95,E,4458,100,F,1526,95,G,4206,95,H,5618,95
SW-C,A,5558,94,B,3874,100,D,4589,100,E,5666,95,F,3918,100,G,1682,95,H,4882,100
SW-D,A,2803,95,B,5378,100,C,4513,100,E,3349,90,F,5695,100,G,4710,100,H,1595,100
SW-E,A,1717,90,B,4389,94,C,5626,100,D,3342,95,F,4227,95,G,5377,100,H,2805,100
SW-F,A,5398,100,B,1521,90,C,3939,95,D,5709,100,E,4241,95,G,3767,95,H,5700,100
SW-G,A,5514,100,B,4171,100,C,1623,100,D,4663,90,E,5440,95,F,3769,95,H,4525,100
SW-H,A,3210,95,B,6291,95,C,4796,100,D,1631,100,E,2824,100,F,5663,95,G,4525,100
```

Warnings:

```text
Anchor D low quality as Matrix in round G, minq=85
Anchor E low quality as Matrix in round D, minq=81
```

## Conclusion

a17 restores Matrix/AutoPos sweep behavior under the broadcast branch. The root
issue was sharing the broadcast responder matching/scheduling path with Matrix.
Matrix now uses traditional anchor-to-anchor unicast SS-TWR timing and is no
longer tied to broadcast mask/rank response scheduling.

Remaining low-quality pairs are local RF/layout/placement candidates rather than
the previous all-zero Matrix failure.

## Next Steps

1. Run AutoPos solve/calibration using the full a17 Matrix sweep data.
2. Recalibrate/update the stale anchor layout.
3. Keep broadcast ranging baseline b55/a13 behavior frozen unless creating a new
   version marker.
