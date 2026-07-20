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
9. `ranges`: unsigned comma-separated filtered ranges in millimetres.
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

## GPIO19 audit

The claim that nRF52832 GPIO19 is "wired but not configured" is false for P0.19
in the frozen build.

The NCS v2.8.0 `decawave_dwm1001_dev` board DTS assigns P0.19 to the DW1000
`int-gpios`. Frozen code requires that property, obtains it as `uwb_irq`, and
configures it as an input:

- `firmware/src/src/uwb_port.c:23-45`
- `firmware/src/src/uwb_port.c:151-174`

No frozen code configures P0.19 as a sweep-ready output or strobes it. Reusing
P0.19 as an output would disconnect/conflict with the DW1000 IRQ assignment.
The schematic phrase "GPIO19 Ready" may refer to a connector pin rather than
nRF P0.19, but that mapping is **UNKNOWN** and must be resolved before P2.

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

- UART range framing/termination and a versioned DWM-to-B306 output path:
  **UNKNOWN / not implemented**.
- Correct physical ready-strobe signal and both MCU pin assignments:
  **UNKNOWN**.
- Measured sweep-start-to-anchor response offsets: **UNKNOWN**.
- Whether the future UART record carries raw only, filtered only, or both:
  **UNKNOWN**.
