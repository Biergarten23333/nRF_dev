# Fusion PCB board 2/3 first-flash and acceptance checklist

Status: prepared before board 2; annotate any correction learned on board 2,
then run the corrected copy unchanged on board 3.

## Fixed artifacts

| Target | Marker | First-flash image | SHA-256 | Signing-key SPKI fingerprint |
|---|---|---|---|---|
| DWM1001C nRF52832 | `tag-fusion-link-v2-relay3` | `UWB_Part/builds/tag-fusion-link-relay3/merged.hex` | `3c538c787478f86abb0a2eb78c6502ca4bf0d071fe72048939196514b8e11f09` | RSA-2048 `a14bcb1bf9bb821146ba32838217e476f5412621320534ffe490a1890c994660` |
| NINA-B306 nRF52840 | `b306-imu-relay-v26` | `B306_Part/builds/b306-imu-relay-v26/merged.hex` | `474adb8874b3549c1363004998a077819895b35b4a3e8d6a971fe6891a568e25` | ECDSA P-256 `0e525dedaa7f50fb38d3c8f1792cacaa20f70204aa46ef6b50d720479c6ef5a2` |

The DWM and B306 keys and images are different. Never cross them.

DWM partition layout: MCUboot `0x00000..0x0bfff`; primary slot
`0x0c000..0x45fff` with application from `0x0c200`; settings
`0x44000..0x45fff`; secondary slot `0x46000..0x7ffff`.

B306 partition layout: MCUboot `0x00000..0x0bfff`; primary slot
`0x0c000..0x85fff` with application from `0x0c200`; secondary slot
`0x86000..0xfffff`. Its frozen `pm_static.yml` SHA-256 is
`8a0bd54788224848390cf628c38a804e6cf172b7e4ddae7633153d33e213ed09`.

## Entry gate — block until the operator confirms

- [ ] Only the board under test is active; BS065F is stopped with relay3
  `CFG_STOP` or physically off. No second UWB tag is running.
- [ ] Operator names the board number.
- [ ] Operator states whether power is battery or dock supply and confirms the
  AP2112-controlled rail remains live while the pogo probe is seated.
- [ ] Probe `1050070698` has moved away from Master_Tag.
- [ ] Operator identifies the present touchpoint as DWM1001C or B306; never
  infer it from successful probe discovery.
- [ ] Probe and target share ground.

## Step 1 — DWM1001C first

- [ ] Touchpoint confirmed as DWM1001C/nRF52832.
- [ ] Recompute the DWM merged-image SHA and match the table.
- [ ] Confirm full-chip erase is intended for this previously unprovisioned
  board.
- [ ] Run, with an explicit SNR:

```bash
bash B306_Part/tools/flash_new_fusion_board.sh \
  <board2|board3> dwm 1050070698 <absolute-log-directory>
```

- [ ] Record flash/verify result.
- [ ] After boot, obtain the live `VERSION` marker and FICR-derived `BSxxxx`.
- [ ] Reject `BSFFFF`, BS065F, or any fleet collision.
- [ ] Confirm the distinct `BSxxxx` advertising name.
- [ ] Read back actual TX power.
- [ ] Read and record DW OTP antenna delay; do not write it.

## Step 2 — B306 second

- [ ] Operator moves the same probe and explicitly confirms the touchpoint is
  B306/NINA-B306/nRF52840 and that target power remains live.
- [ ] Recompute the B306 merged-image SHA and match the table.
- [ ] Confirm the ECDSA fingerprint and frozen partition layout above.
- [ ] Confirm full-chip erase is intended for this previously unprovisioned
  board.
- [ ] Run, with an explicit SNR:

```bash
bash B306_Part/tools/flash_new_fusion_board.sh \
  <board2|board3> b306 1050070698 <absolute-log-directory>
```

- [ ] Record flash/verify result, MCUboot state, live v26 marker, and
  first-boot `RESETREAS`.
- [ ] Obtain the FICR-derived `BSFxxxx`; reject `BSFFFF`, BSF3C79, or a fleet
  collision.
- [ ] Confirm the distinct `BSFxxxx` advertising name.

## Per-board functional proof

- [ ] I2C ACK at `0x50`.
- [ ] JY61P register `0x2e`; compare with board 1's `0x469b`.
- [ ] Flat/still gravity magnitude near 1 g.
- [ ] DWM UART frames advance; CRC/header/ring-error deltas are zero.
- [ ] READY rise/fall counters advance in step with frames.
- [ ] Configure one-tag TDMA through Master_Tag; prove about 10 Hz.
- [ ] Capture 60 seconds over Fusion Master native CDC.
- [ ] Zero sequence gaps and transport errors.
- [ ] Report strobe-to-frame p50/p90/p99/max and compare with board 1:
  `14.43 / unavailable / 17.13 / 17.19 ms`.
- [ ] `IMU START` reply contains
  `61=0001:P 03=000B:P 1F=0002:P`.
- [ ] Prove 200 Hz stream, then issue `IMU STOP`.
- [ ] Report chip-time delta residual distribution/extremum.
- [ ] Note or photograph hand-assembly differences from board 1.

Do board 2 completely before starting board 3. After board 3, return probe
`1050070698` to Master_Tag and record the operator confirmation. Stop without
creating a multi-tag roster or capacity run.
