# BioSpur system hardware and identity registry

Live registry updated 2026-07-26. This is the writable successor to
`UWB_Part/2026-07-15-FREEZE/HARDWARE_STATE.md`; that file is the immutable
2026-07-15 rollback snapshot and must not be edited.

Stable identities are hardware SNRs and FICR-derived `BSxxxx` / `BSFxxxx`
codes. `/dev/ttyACM<n>` is never an identity.

## Fusion PCBs

Each Fusion PCB contains two independently identified MCUs:

- DWM1001C/nRF52832: UWB identity `BSxxxx`
- NINA-B306/nRF52840: Fusion BLE identity `BSFxxxx`

All five rows below were independently observed from live radio/CDC evidence.
No value is `FFFF`, and no identity collides within the five-board set.

| Fusion PCB | DWM1001C identity | B306 identity | Firmware state | Evidence |
|---|---|---|---|---|
| Board 1 (original) | `BS065F` | `BSF3C79` | DWM relay3; B306 v26 | Established system baseline and relay3 OTA report |
| Board 2 | `BSE88E` | `BSFC2CC` | DWM relay3; B306 v26 | `B306_Part/logs/relay3_bringup_20260726/board2/PROVENANCE.md` |
| Board 3 | `BS6F3A` | `BSF44AD` | DWM relay3; B306 v26 | `B306_Part/logs/relay3_bringup_20260726/board3/PROVENANCE.md` |
| Board 4 | `BSF8E0` | `BSF6C53` | DWM relay3; B306 v26 | `B306_Part/logs/relay3_bringup_20260726/board4_identity/IDENTITY.md` |
| Board 5 | `BSEFD2` | `BSF8BC4` | DWM relay3; B306 v26 | `B306_Part/logs/relay3_bringup_20260726/board5/PROVENANCE.md` |

`BSF8E0` is a valid DWM `BS` identity whose four-hex suffix begins with `F`;
it must not be confused with the B306 `BSFxxxx` namespace. The paired B306 on
that board is `BSF6C53`.

## Fusion host

| Role | Hardware | Stable identity | Current use |
|---|---|---|---|
| Fusion Master | nRF52840 DK | J-Link SNR `683234364` | BLE central; native USB CDC to PC; RTT diagnostic backup |

## UWB masters

| Role | Hardware | J-Link SNR | Stable application CDC |
|---|---|---:|---|
| Tag Master | B120/nRF5340 | `1050070698` | `usb-Master_Tag_Master_Tag_Control_6918E0384172A49F-if00` |
| Anchor Master | B120/nRF5340 | `960148546` | `usb-Master_Anchor_Master_Anchor_Control_87EA2F4A526C5A02-if00` |

Current probe-location record: on 2026-07-26 the operator confirmed probe
`1050070698` was returned to Tag Master after the five-board flashing batch.
The Anchor Master remains protected.

## Anchors

Eight OTA-managed anchors, addressed by UUID/letter `A` through `H`.
`A–D` are the lower layer and `E–H` are the upper layer. Per-anchor J-Link
SNRs are not operational identities in this system; Anchor Master owns their
BLE control and OTA path.

## Retired wand tags

`BS9336`, `BS955A`, and `BSCCF4` belong to the previous-generation wand line
and are retired. They are not Fusion nodes and must not be powered or included
in current fleet acceptance.

## Passive listeners

| SNR | Registered position |
|---:|---|
| `760184753` | A–E midpoint |
| `760184548` | B–F midpoint |
| `760181725` | C–G midpoint |
| `760184784` | D–H midpoint |
| `760184964` | vertical profile LOW |
| `760184767` | vertical profile MID |
| `760184545` | vertical profile HIGH |
| `760181879` | AEDH face, upper layer |
| `760186115` | BFCG face, lower layer |

SNR `760185886` is the legacy Geiger air monitor, not a listener available for
flashing. Never target it with a listener image.

## Operational identity rules

1. Resolve all J-Link operations by explicit SNR; never accept a probe
   selection dialog.
2. Resolve CDC devices by `/dev/serial/by-id`; never store a ttyACM number as
   stable identity.
3. Reject any new DWM or B306 whose derived identity is `FFFF` or collides
   with this registry.
4. DWM `BSxxxx` and B306 `BSFxxxx` are separate namespaces and separate
   firmware/keys, even though both MCUs occupy one Fusion PCB.
