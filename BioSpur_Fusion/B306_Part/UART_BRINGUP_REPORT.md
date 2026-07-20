# DWM1001C → B306 → Fusion Master bring-up

Date: 2026-07-20

## Result

The B306-to-Fusion-Master half is operational. The DWM1001C-to-B306 half is
silent, and reversing the candidate B306 UART input does not change that
result.

This is not a framing, CRC, BLE, or RX/TX-direction finding: the B306 UARTE
received exactly zero electrical bytes on both candidate nets.

## Real-board A/B evidence

Both B306 images advertised as `BSF3C79`. The DK Fusion Master connected,
negotiated ATT MTU 247, DLE 251, and 2M PHY, discovered the diagnostic service,
and subscribed to both data and telemetry characteristics.

| B306 image | RX pin | Observation |
|---|---|---|
| `b306-uart-bridge-v5`, 0.1.4+0 | P1.01 (`UWB_RX1`) | More than 120 s: `bytes=0`, `frames=0`, all parser/error counters zero |
| `b306-uart-rx-p1.02-v6`, 0.1.5+0 | P1.02 (`UWB_TX1`, reversal test) | More than 45 s: `bytes=0`, `frames=0`, all parser/error counters zero |
| `b306-uart-rx-p1.01-v7`, 0.1.6+0 | P1.01 (`UWB_RX1`, restored) | BLE bridge healthy; repeated telemetry still `bytes=0`, `frames=0` |

The authoritative PCB mapping remains DWM nRF52832 P0.05 TX → B306 nRF52840
P1.01 RX. P1.02 is B306 TX → DWM P0.11 RX and is deliberately left unassigned
by the receive-only B306 overlay.

## Installed B306 and DK state

B306:

```text
marker: b306-uart-rx-p1.01-v7
version: 0.1.6+0
signed.bin SHA-256:
08079df5c4c84ca845fad0a455f95221ff5e673037f6c85d65ccf5abb8fddd94
MCUboot image digest:
ebab8f7fd31c00aa5ad3272c9684e0eee210b74aa20cad874e03376b6f25eaf1
image state: slot 0, active=true, confirmed=true
```

Fusion Master DK:

```text
marker: dk-fusion-uart-bridge-v1
probe: 683234364
merged.hex SHA-256:
9111f053d216d596247b0724ca36d21e2b207574a2a88adeedb20920b23fc3d4
```

Only the explicitly selected DK probe was directly flashed. B306 v7 was
installed through the shared fast BLE OTA core.

## DWM-side evidence

The read-only Master_Tag `tdma show` response listed only BS9336, BS955A, and
BSCCF4. A DK scan-only application then scanned all named advertisements for
30 seconds without connecting to anything. It saw B306 `BSF3C79` and unrelated
nearby devices, but no Fusion DWM `BSxxxx`, Nordic UART Service, default
`Zephyr`, or DWM-named advertisement.

The flashed Task A artifact on disk is intact:

```text
UWB_Part/fusion-link/tag/tag-fusion-link-v2.merged.hex
SHA-256:
53986bc713a5e75dcbe5b1e28d286b692250adff1faee9863041a780e4234758
```

Static inspection confirms that artifact contains marker
`tag-fusion-link-v2`, BLE advertising code, `BSL_STATUS`, the 460800 UARTE
driver, and the UART-link implementation. It does not prove which instruction
the physical DWM reaches at runtime.

## Next gate: human DWM RTT observation

Do not produce or flash another DWM image from this finding. First connect
J-Link OB probe `1050070698` to the Fusion PCB DWM1001C SWD pads, with the board
powered and common ground. Do not connect it to B306. Then collect boot RTT
without erasing or programming:

```bash
cd /mnt/nrf_ssd/nRF_dev/BioSpur_Fusion
mkdir -p UWB_Part/logs/dwm_task_a_boot_20260720
timeout 20s JLinkRTTLogger \
  -Device NRF52832_XXAA \
  -If SWD \
  -Speed 4000 \
  -USB 1050070698 \
  -RTTAddress 0x20000410 \
  -RTTChannel 0 \
  UWB_Part/logs/dwm_task_a_boot_20260720/rtt.log
```

Report the complete log. In particular:

- `Tag firmware marker: tag-fusion-link-v2` proves the intended application
  booted.
- `BioSpur UART link init failed: <rc>` isolates the pre-BLE early return.
- `Tag UWB bringup failed: <rc>` explains absence of sweep/UART generation
  after BLE startup.
- A healthy marker plus increasing `BSLSTAT` counters moves the finding to the
  physical TX/routing path and makes the logic-analyser capture the next gate.

If the marker is absent, treat that as the handover finding. Do not immediately
flash a revised image.
