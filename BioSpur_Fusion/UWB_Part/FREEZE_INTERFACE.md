# Frozen UWB interface audit

This document audits `2026-07-15-FREEZE/firmware/` as a read-only rollback
baseline. It distinguishes current behavior from the interface BioSpur Fusion
still needs.

## Provenance

- Annotated tag: `freeze-clean-20260716`
- Firmware commit: `8b68ee0aafe75b849fca8f36606775e99a9ef3cd`
- Archive manifest: `firmware/src/MANIFEST.md:3-10`
- Tag build recipe: `firmware/src/BUILD.md:28-30`

The annotated tag object itself is
`f0d19222ebb385686520c9d09db11ddf5bcb99ae`; it points to the firmware commit
above.

## Range-message transport: current fact

There is no production UART range-message contract in this freeze.

The range summary is formatted as:

```text
TR;<version>;<sweep>;<plan>;<pmode>;<active_mask>;<valid_mask>;<raws>;<ranges>;<qualities>;<statuses>[;T,<temp_raw>,<vbat_raw>]
```

The literal format string and argument order are in
`firmware/src/src/ss_twr_init.c:1338-1357`. With the frozen production settings,
`version` is `2`; DIAG-off behavior is documented in the source at
`firmware/src/src/ss_twr_init.c:1345-1350`. The optional temperature/VBAT trailer
is appended at `firmware/src/src/ss_twr_init.c:1444-1450`.

Field meanings:

1. `TR`: record marker.
2. `version`: `2` in the frozen production profile.
3. `sweep`: monotonically increasing sweep counter.
4. `plan`: one-character sweep-plan code.
5. `pmode`: runtime positioning mode.
6. `active_mask`: hexadecimal anchor-ID bitmap.
7. `valid_mask`: hexadecimal valid-range bitmap.
8. `raws`: signed comma-separated raw ranges in millimetres.
9. `ranges`: unsigned comma-separated per-sweep instantaneous ranges in
   millimetres. CFO clock-offset correction is part of the SS-TWR formula;
   no smoothing/filtering is applied.
10. `qualities`: comma-separated tracker quality percentages.
11. `statuses`: one character per active anchor: `O` ok, `R` rejected, `T`
    timeout, `E` error, or `P` pending. The mapping is defined at
    `firmware/src/src/ss_twr_init.c:1089-1103`.
12. Optional `T` trailer: raw 8-bit DW1000 temperature and VBAT SAR codes, not
    engineering units.

The record does not carry an anchor ID beside each array entry. The formatter
emits entries in `measurements[]` order
(`firmware/src/src/ss_twr_init.c:1283-1317`), while the frozen host parser
reconstructs entry order as increasing anchor ID from `active_mask`
(`2026-07-15-FREEZE/scripts/ops/run_recv_tdma_capture.py:368-383`). Dynamic
selection can construct an interleaved lower/upper-plane active list
(`firmware/src/src/ss_twr_init.c:1802-1840,3088-3116`). Consequently, an
increasing-ID ordering guarantee is not established by the formatter itself.
This ambiguity must be resolved before reusing the record as a DWM-to-B306
protocol.

The line is handed to `uwb_tag_ble_publish_status()`, not `printk()` or a UART
writer (`firmware/src/src/ss_twr_init.c:1453`). That function queues/bundles BLE
status at `firmware/src/apps/tag/src/uwb_tag_ble.c:2107-2181`. The host sees a
B120 prefix such as `BLE[...] notify: `; that prefix is added by the master and
is not part of the tag record.

The tag console is enabled and the NCS board definition selects UART0 at 115200
baud, but `APP_TAG_VERBOSE_RANGING=0` and `APP_TAG_USB_MIRROR_BLE_STATUS=0` in
`firmware/src/apps/tag/CMakeLists.txt:66-70`. Therefore B306 cannot parse frozen
range summaries from UART without a new, versioned firmware output path.

UART framing for future DWM1001C-to-B306 range records: **UNKNOWN**.

## Sweep structure

- Anchor count: eight, from
  `firmware/src/apps/tag/src/tag_app.c:31-64` and the eight-entry table at
  `firmware/src/apps/tag/src/tag_app.c:346-355`.
- Broadcast shape: one poll carries the active anchor mask; all responders
  answer in ranked slots (`firmware/src/src/ss_twr_init.c:4720-4726`).
- Response delay: 1,200 us.
- Inter-anchor response spacing: 1,000 us.
- Last response completion: approximately 8.45 ms for rank 7, from the timing
  comment at `firmware/src/src/ss_twr_init.c:449-457`.
- Nominal build schedule: TDMA slot index 0, 10 slots, 10 ms per slot, from the
  build recipe and `firmware/src/scripts/build_tag_ble_motion.sh:20-22,231-235`.
  That is a 100 ms sweep period, or 10 Hz, unless runtime BLE configuration
  replaces it.

Exact per-anchor measurement epochs within the broadcast response window:
**UNKNOWN** until measured. The 7.18 ms value in the Fusion guide is inconsistent
with the frozen source's approximately 8.45 ms last-frame completion.

The 1,200 us guard and 1,000 us response spacing are not live BLE-configurable
fields in this freeze. They are CMake/build inputs:

- Tag defaults and compile definitions:
  `firmware/src/apps/tag/CMakeLists.txt:139-140,273-274`
- Frozen build-script inputs:
  `firmware/src/scripts/build_tag_ble_motion.sh:32-33,183-184,212-213`
- Initiator window calculation:
  `firmware/src/src/ss_twr_init.c:2497-2499`
- Responder transmit-delay calculation:
  `firmware/src/src/ss_twr_resp.c:611-619`

Neither `struct uwb_tdma_schedule` nor `struct uwb_tag_runtime_params` contains
guard/spacing fields (`firmware/src/include/uwb_tdma.h:45-68`), and the BLE
command parser has no guard/spacing command. A future UART frame can truthfully
carry the effective build values used by both initiator and responders, but
calling them "live runtime values" or claiming that BLE currently changes them
would be inaccurate.

## DWM1001 READY / P0.26 audit

The schematic label `GPIO19 Ready` names **DWM1001 module pin 19**, whose
datasheet signal name is `READY`. Module pin 19 maps to nRF52832 **P0.26**. It
does not mean nRF52832 P0.19.

P0.26 is free in the frozen source. A whole-source-tree search on 2026-07-20
found no `P0.26`, `NRF_GPIO_PIN_MAP(0, 26)`, `gpio0 26`, GPIO pin-26
assignment, reservation, read, configuration, or drive. The only standalone
decimal `26` matches are DW1000 range-table values/constants and comments:

- `firmware/src/drivers/dw1000/src/deca_range_tables.c:20,144,239,321,413,478,593`
- `firmware/src/drivers/dw1000/src/deca_device.c:2970`
- `firmware/src/drivers/dw1000/include/deca_device_api.h:947`
- `firmware/src/drivers/dw1000/include/deca_regs.h:558,910`
- `firmware/src/src/ss_twr_init.c:5048`

P0.19 remains the DW1000 IRQ and must not be repurposed. The NCS v2.8.0
`decawave_dwm1001_dev` board DTS assigns P0.19 to the DW1000 `int-gpios`.
Frozen code requires that property, obtains it as `uwb_irq`, and configures it
as an input:

- `firmware/src/src/uwb_port.c:23-45`
- `firmware/src/src/uwb_port.c:151-174`

The DWM1001-side strobe pin is therefore resolved as P0.26. The Fusion PCB
mapping subsequently resolved the B306 capture input as nRF52840 P1.03 on
`UWB_RDY`.

## TDMA alignment audit

The Tag Master is configuration-only for TDMA alignment; it does not emit a
periodic timing beacon that tags track.

- The master computes one future deadline, converts it to a per-message
  relative `EPOCH` delay, and sends it in a BLE `CFG` command during a TDMA
  rebalance: `firmware/src/apps/master/src/master_multi_app.c:1621-1649`.
- The tag parses that `EPOCH` from the BLE command:
  `firmware/src/apps/tag/src/uwb_tag_ble.c:929-990`.
- When the configuration is applied, the tag converts the relative delay once
  to `k_uptime_get_32() + epoch_ms`:
  `firmware/src/src/ss_twr_init.c:6518-6548` and
  `firmware/src/src/uwb_tdma.c:63-80`.
- Thereafter slot phase is derived only from the tag's local uptime and stored
  local epoch: `firmware/src/src/uwb_tdma.c:83-100,125-175,230-285`.

Thus a BLE connection is not part of the tag's ongoing TDMA timebase. However,
dropping one tag's link causes the current master to rebalance the remaining
connected tags (`firmware/src/apps/master/src/master_multi_app.c:3100-3125`);
the disconnected tag would keep its old local schedule. A deliberate
disconnect capture mode is unsafe without a coordinated master change. Retain
the control connection and use a long connection interval for capture.

## Task A UART preflight

The nRF52832 exposes only `uart0`
(`/home/zekaixiao/ncs/v2.8.0/zephyr/dts/arm/nordic/nrf52832.dtsi:114-121`).
The DWM1001 board selects that instance for the console and configures it as a
non-EasyDMA `nordic,nrf-uart` at 115200 baud:

- `/home/zekaixiao/ncs/v2.8.0/zephyr/boards/qorvo/decawave_dwm1001_dev/decawave_dwm1001_dev.dts:16-25,103-110`
- `/home/zekaixiao/ncs/v2.8.0/zephyr/boards/qorvo/decawave_dwm1001_dev/decawave_dwm1001_dev-pinctrl.dtsi:6-19`
- `firmware/src/apps/tag/prj.conf:3-11`

Meeting Task A's 460800-baud EasyDMA requirement therefore requires changing
`uart0` to `nordic,nrf-uarte` and giving up the UART console/log backend (or
moving diagnostics to RTT). There is no second UART instance.

Task A's v2 shared contract was supplied separately on 2026-07-20 and installed
verbatim at `B306_Part/include/biospur_link.h` and
`UWB_Part/fusion-link/src/include/biospur_link.h`. The attachment and both
installed copies have SHA-256
`d832fe9fbaf92ff1d8b82eb1a833566a84c540b863309b18803863ae4de8fd1b`.

The v2 frame is fixed at 96 bytes: 4-byte header, 90-byte body, and 2-byte
CRC-16/CCITT-FALSE. Separate static assertions guard all three sizes. At
460800 8N1, its constant wire time is 2,083 us by the contract's integer
helper.

The working `fusion-link` implementation reports the effective 16-bit
`identity_code` and independently assigned 8-bit `logical_tag_id`. It sets
`BSL_FLAG_IDENTITY_NVS` when the identity was loaded from the settings record.
`BSFFFF` is legal; new code must use the flag and never treat `0xFFFF` as an
identity sentinel.

Identity collisions remain a system-level hazard. At session start the host
must reject duplicate `identity_code` values before capture or TDMA assignment.
The existing nonzero NVS `identity_code` override is the remedy.

The v2 frame carries measured `t_round_us[]`, computed as the masked 40-bit
DW1000 response-RX timestamp minus the broadcast poll-TX timestamp and rounded
to the nearest microsecond. Missing responses and values outside the
representable range use `BSL_TROUND_INVALID`. The nominal `guard_us`,
`spacing_us`, and `rank[]` fields are diagnostic only.

Tag and anchor guard/spacing values still come from separate CMake definitions.
Unifying them would require one shared build configuration or generated header
consumed by both tag and anchor application builds, build-time assertions that
both roles used it, and deployment of a matched tag/anchor release. That work
touches responder firmware and is deliberately outside Task A.

The original Task A v2 build occupied 208,756 of 228,864 linker FLASH bytes
(91.21%) but 65,536 of 65,536 RAM bytes (100%). Its resolved
`CONFIG_COMMON_LIBC_MALLOC_ARENA_SIZE=-1` expanded the C arena to all remaining
RAM; `_end` resolved to `0x20010000`. On hardware it faulted in
`sys_heap_init()` before the application marker and reset continuously. That
image is invalid and its UART/strobe tests are void.

The forward RAM fix explicitly sets both unused heaps to zero, reduces only
OTA queue counts/pools, retains the 498-byte L2CAP MTU and 502-byte ACL sizes,
and enables runtime thread analysis. The replacement build occupies 52,640 of
65,536 RAM bytes (80.32%) and 210,944 of 228,864 FLASH bytes (92.17%), passing
the fixed RAM <=85% and FLASH <=95% gates. Exact artifact hashes are in
`fusion-link/TASK_A_REPORT.md`.

`BSLSTAT;1` now reports achieved BLE connection parameters:
`ci` and `reqci` in 1.25 ms units, `lat`, `sup` in 10 ms units, `cpmode`
(`CAP` or `FAST`), and `ciok`. Capture requires `reqci=350` (437.5 ms);
`ciok=1` proves the controller accepted that exact interval rather than merely
receiving a request.

## Inherited DWM1001C MCUboot signing key

The freeze and Task A images inherit the NCS v2.8.0 sample RSA-2048 key:

```text
bootloader/mcuboot/root-rsa-2048.pem
PEM SHA-256: 1fc912d30251b821f251e127d4daf7ba9338dd5c04e5af100abfb5b7c7d4c022
public SPKI DER SHA-256: a14bcb1bf9bb821146ba32838217e476f5412621320534ffe490a1890c994660
```

This is a known inherited weakness, not authorization to change the key in a
routine build. Replacing it makes existing OTA payloads/bootloaders
incompatible and must be planned as a fleet-wide DWM SWD event.

## Frozen OTA behavior

The DWM1001C tag advertises BLE NUS and SMP DFU. The B120 OTA master arms the tag
with `OTA_PREPARE`, then `OTA_BEGIN`
(`firmware/src/apps/master_ota/src/main.c:958-967`).

`OTA_PREPARE` sets both `ota_ready` and `ota_active`, clears queued capture
records, cancels bundle flushing, and purges the BLE TX queue
(`firmware/src/apps/tag/src/uwb_tag_ble.c:2007-2017`). While active, the ranging
loop forces the DW1000 transceiver off and sleeps
(`firmware/src/src/ss_twr_init.c:6019-6027`). The master uploads the secondary
image, marks it pending, and requests a remote reset
(`firmware/src/apps/master_ota/src/main.c:2054-2086`).

At boot, the image confirms itself
(`firmware/src/apps/tag/src/tag_app.c:403-409`) and the normal runtime defaults
to RUN. `OTA_CANCEL` clears the two flags and allows the loop to resume
(`firmware/src/apps/tag/src/uwb_tag_ble.c:2039-2043`). A disconnect also clears
the flags. Thus the intended terminal state is RUN after reboot or cancellation,
subject to any separately persisted runtime TDMA/mode settings.

## Fusion blockers carried forward

- Task A bench acceptance results, including the measured per-rank
  `t_round_us` distributions: **PENDING**. The original v2 image was deployed
  but never reached application startup; the RAM-fixed replacement still
  awaits the human reflash handover.
- The v2 UART record carries the same per-sweep instantaneous range and quality
  used by `TR;2`. The range is CFO clock-offset corrected as part of the SS-TWR
  formula, with no smoothing/filtering applied. Smoothing is deliberately left
  to the fusion host because upstream smoothing would corrupt its noise model.
