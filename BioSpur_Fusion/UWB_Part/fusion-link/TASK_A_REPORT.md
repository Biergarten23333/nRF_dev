# Task A implementation report

Status: **firmware implementation and flash gate complete; bench acceptance
pending; no image flashed**.

## Result

The writable `fusion-link` copy now builds a tag image that emits one fixed
96-byte `bsl_frame_t` per completed sweep over 460800-baud EasyDMA UARTE and
generates a nominal 10 us P0.26 pulse in the broadcast-poll TX-done path. The
frozen baseline was not modified.

The supplied v2 header is installed verbatim in both MCU trees:

```text
B306_Part/include/biospur_link.h
UWB_Part/fusion-link/src/include/biospur_link.h
SHA-256 d832fe9fbaf92ff1d8b82eb1a833566a84c540b863309b18803863ae4de8fd1b
```

Host compilation confirms a 4-byte header, 90-byte body, 96-byte frame, CRC at
offset 94, and a constant 2,083 us UART wire time. The CRC implementation
returns the CRC-16/CCITT-FALSE check value `0x29B1` for `123456789`.

## Read-only gates

- P0.26 is free in the frozen source. No code or DTS entry configures,
  reserves, reads, or drives it. P0.19 remains the DW1000 IRQ input
  (`firmware/src/src/uwb_port.c:23-45,151-174`).
- UART0 input is not a command path. The tag uses it only through
  `uart_poll_out()` (`firmware/src/apps/tag/src/tag_app.c:103-115,295-307`);
  APOS, configuration, mode, capture, and OTA commands enter through BLE NUS.
- Responder rank is derivable from the broadcast mask and rank offset. The
  responder uses the same offset-aware mask walk in
  `firmware/src/src/ss_twr_resp.c:622-647,1203-1219`.
- The Tag Master supplies one future epoch during configuration. Tags then
  self-time from local uptime. A disconnected tag would keep a stale schedule
  while the master rebalances the remainder, so capture retains BLE.
- OTA remains triggerable with `OTA_PREPARE` and `OTA_BEGIN` over BLE NUS.

## Implemented data path

- UART0 is `nordic,nrf-uarte`, 460800 8N1, no flow control, on the existing
  DWM1001 P0.5/P0.11 pins. One static 96-byte DMA buffer is protected by a busy
  flag; a new sweep is counted and dropped rather than overwriting an active
  transfer.
- UART console and logging moved to RTT. The resolved build has
  `CONFIG_SEGGER_RTT_MODE_NO_BLOCK_SKIP=y` and
  `CONFIG_LOG_BACKEND_RTT_MODE_DROP=y`; blocking RTT mode is disabled.
- Every frame has explicit anchor IDs and fixed trailing slots. It reports the
  effective post-settings `identity_code`, independently assigned
  `logical_tag_id`, raw 40-bit broadcast poll-TX timestamp, filtered
  CFO-corrected ranges, the same tracker quality used by `TR;`, CFO in Q8 ppm,
  validity, and strobe/partial/NVS flags.
- `t_round_us[k]` is measured from full 40-bit DW1000 timestamps with masked
  subtraction and nearest-microsecond conversion. Missing responses and
  overflow use `BSL_TROUND_INVALID`.
- P0.26 is configured output-low during initialization. A sweep records
  `BSL_FLAG_STROBE_SENT` only when the pulse helper runs.
- The BLE `TR;` path remains default-on and is switchable with `TR ON|OFF` or
  `CAPTURE ON|OFF`. Capture keeps BLE connected at 437.5 ms; the 4 s
  supervision timeout is more than 9 times that interval.
- `OTA_PREPARE` first requests the fast 7.5 ms interval, then purges BLE
  capture queues, aborts any UART DMA, disables UART/strobe activity, and
  leaves P0.26 low. `OTA_CANCEL` restores RUN and the interval selected by the
  persisted capture state. Reboot naturally initializes RUN.
- `BSL_STATUS` and a periodic `BSLSTAT;1` record expose generated, started,
  completed, dropped, failed, aborted, strobe, and last-error counters over
  BLE. Receive-side CRC errors remain `rxcrc=NA`; they can only be counted by
  the future B306 parser.

The `TR;2` valid mask is keyed by anchor ID, while `bsl_uwb_t.valid_mask` is
keyed by explicit frame slot. Dual-path validation must remap the UART slots by
`anchor_id[]` before comparing masks and values.

## Flash acceptance gate

The pristine NCS v2.8.0 build used:

```text
./scripts/build_tag_ble_unified.sh 0 10 build-tag-fusion-link
```

The image advertises the unique firmware marker `tag-fusion-link-v2`.

| Metric | Frozen baseline | Task A | Delta |
|---|---:|---:|---:|
| `zephyr.bin` | 201,644 B | 208,756 B | +7,112 B |
| Linker FLASH capacity | 228,864 B | 228,864 B | — |
| Linker FLASH usage | 88.11% | **91.21%** | +3.11 pp |
| Signed image | 202,492 B | 209,604 B | +7,112 B |
| ELF text | 198,640 B | 205,688 B | +7,048 B |
| ELF data | 2,985 B | 3,041 B | +56 B |
| ELF BSS | 61,180 B | 62,818 B | +1,638 B |

Result: **PASS**, below the 95% stop/warn threshold, with 20,108 linker FLASH
bytes free.

Artifacts:

```text
tag/tag-fusion-link-v2.signed.bin
  82fe5f6a7a3e779b85af0e5069443e2ed40af73b67fffa1bd2757a49e9508ce6
tag/tag-fusion-link-v2.dfu_application.zip
  4dae721bc09c872c06940b13e126f4876e5473aaa38ac5826d6adb84eb77529c
tag/tag-fusion-link-v2.merged.hex
  53986bc713a5e75dcbe5b1e28d286b692250adff1faee9863041a780e4234758
```

Full build provenance is recorded under
`UWB_Part/logs/task_a_build_20260720_130022/`.

## Guard/spacing finding

The tag and anchors still receive nominal guard/spacing from separate CMake
definitions. Task A now reports measured `t_round_us`, so fusion does not
depend on this nominal split. Unifying it would require a common configuration
or generated header consumed by both app builds, assertions in both roles, and
a matched tag/anchor rebuild and deployment. That touches responder firmware
and was not implemented here.

## Hardware status and pending acceptance

A read-only preflight on 2026-07-20 found the deployed wand tags BS9336,
BS955A, and BSCCF4 held by `Master_Tag` with active `TR;` streaming;
`Master_Anchor` held none. Interrupting that live capture or OTA-deploying to
all three tags was not authorized, and these wand assemblies are not an
identified Fusion PCB/B306 UART measurement target. No direct J-Link flash was
attempted.

The following requested measurements therefore remain pending:

1. at least 1,000 sweeps of UART/BLE agreement, including BLE-name identity;
2. at least 10 minutes of inter-strobe intervals;
3. strobe-to-poll-TX offset mean and standard deviation on a common time base;
4. `first_to_last_us`, `frame_us`, and `poll_count` versus the frozen image;
5. at least one hour of CRC, missing, and duplicate sweep-counter statistics;
6. at least 10 minutes of `t_round_us` median and spread for ranks 0 through 7;
7. at least 10 minutes with no debugger attached to confirm RTT cannot affect
   ranging.

To close these gates safely, identify the Fusion PCB/tag to update, connect its
P0.5/P0.11 UART to a B306 or capture adapter, confirm the B306 pin wired to
DWM1001 module pin 19/P0.26, and schedule a window in which its current firmware
may be replaced.

## Remaining unknowns

- B306 capture-input pin for DWM1001 module pin 19 / nRF52832 P0.26.
- All Task A bench distributions and regression values listed above.
- Receiver-side CRC count until a B306/parser capture path exists.
