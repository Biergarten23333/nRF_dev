# DWM1001C → B306 → Fusion Master bring-up

Date: 2026-07-20

## Result

The B306-to-Fusion-Master half is operational. The DWM1001C-to-B306 half is
not yet tested with a running Task A transmitter.

The human-flashed `tag-fusion-link-v2` image reset-looped in
`sys_heap_init()` before application startup because linked RAM usage was
100%. It never ran the UART/strobe code. Therefore the earlier P1.01/P1.02
comparison was guaranteed to receive zero bytes and is **void as a
RX/TX-direction test**.

## Real-board observations (direction conclusion void)

Both B306 images advertised as `BSF3C79`. The DK Fusion Master connected,
negotiated ATT MTU 247, DLE 251, and 2M PHY, discovered the diagnostic service,
and subscribed to both data and telemetry characteristics.

| B306 image | RX pin | Observation |
|---|---|---|
| `b306-uart-bridge-v5`, 0.1.4+0 | P1.01 (`UWB_RX1`) | B306 path healthy; zero bytes while DWM application was not running |
| `b306-uart-rx-p1.02-v6`, 0.1.5+0 | P1.02 (`UWB_TX1`, reversal test) | B306 path healthy; zero bytes while DWM application was not running |
| `b306-uart-rx-p1.01-v7`, 0.1.6+0 | P1.01 (`UWB_RX1`, restored) | BLE bridge healthy; zero bytes while DWM application was not running |

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

## DWM-side root cause

The read-only Master_Tag `tdma show` response listed only BS9336, BS955A, and
BSCCF4. A DK scan-only application then scanned all named advertisements for
30 seconds without connecting to anything. It saw B306 `BSF3C79` and unrelated
nearby devices, but no Fusion DWM `BSxxxx`, Nordic UART Service, default
`Zephyr`, or DWM-named advertisement.

The human-flashed Task A v2 artifact on disk was intact:

```text
UWB_Part/fusion-link/tag/tag-fusion-link-v2.merged.hex
SHA-256:
53986bc713a5e75dcbe5b1e28d286b692250adff1faee9863041a780e4234758
```

RTT then showed a deterministic imprecise bus fault at about 2 ms followed by
reset. The resolved build had:

```text
CONFIG_COMMON_LIBC_MALLOC_ARENA_SIZE=-1
20010000 N _end
RAM: 65536 / 65536 B = 100.00%
```

Address resolution maps the fault path to `sys_heap_init()`. The image never
printed `Tag firmware marker: tag-fusion-link-v2`, so its UART and READY paths
were never reached.

## Next gate: human DWM forward-fix reflash

The fresh human-only handover is:

```text
B306_Part/handover/dwm1001c-task-a-v2-ramfix1/README.md
```

It installs marker `tag-fusion-link-v2-ramfix1`, whose pristine build passes
FLASH 92.17% and RAM 80.32%. Codex has not flashed it.

After the human reports a stable marker, collect RTT with the explicitly
selected probe:

```bash
cd /mnt/nrf_ssd/nRF_dev/BioSpur_Fusion
mkdir -p UWB_Part/logs/dwm_task_a_ramfix1_boot_20260720
timeout 75s JLinkRTTLogger \
  -Device NRF52832_XXAA \
  -If SWD \
  -Speed 4000 \
  -USB 1050070698 \
  -RTTAddress 0x20000410 \
  -RTTChannel 0 \
  UWB_Part/logs/dwm_task_a_ramfix1_boot_20260720/rtt.log
```

Report the complete log. In particular:

- `Tag firmware marker: tag-fusion-link-v2-ramfix1` proves the intended application
  booted.
- `BioSpur UART link init failed: <rc>` isolates the pre-BLE early return.
- `Tag UWB bringup failed: <rc>` explains absence of sweep/UART generation
  after BLE startup.
- A healthy marker plus increasing `BSLSTAT` counters makes P1.01 the first
  real direction test. Reverse to P1.02 only if that live-transmitter test
  still yields zero electrical bytes.

If the marker is absent, treat that as the handover finding. Do not immediately
flash a revised image.
