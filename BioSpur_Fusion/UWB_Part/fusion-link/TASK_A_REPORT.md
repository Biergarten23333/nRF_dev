# Task A implementation and RAM-budget report

Status: **300 s absolute-deadline validation passed; the attempted one-hour
confirmation failed at a rollover-associated B306/RDY outage; validation
instrumentation removed; production image built but not deployed**.

## Result

The writable `fusion-link` copy emits one fixed 96-byte `bsl_frame_t` per
completed sweep over 460800-baud EasyDMA UARTE and generates a nominal 10 us
P0.26 pulse in the broadcast-poll TX-done path. The frozen rollback baseline
was not modified.

The original `tag-fusion-link-v2` image was human-flashed on 2026-07-20 and
immediately reset-looped with an imprecise bus fault before printing the
application marker. Address resolution placed the fault in `sys_heap_init()`.
That deployment never ran its UART transmitter or strobe, so the B306 P1.01
and P1.02 zero-byte observations do not determine UART direction.

The RAM-fix marker was `tag-fusion-link-v2-ramfix1`. The hardware validation
ran `tag-fusion-link-v2-absdeadline3`; the stripped production marker is
`tag-fusion-link-v2-absdeadline-final`.

## Fusion relay derivative: v2-relay1

The Fusion-only lineage is now:

```text
tag-fusion-link-v2-absdeadline3 (installed validation image)
  -> tag-fusion-link-v2-absdeadline-final (stripped, never deployed)
  -> tag-fusion-link-v2-clean1 (honest raw-range naming/filter purge)
  -> tag-fusion-link-v2-relay1 (APOS removal + UART command/ACK transport)
```

`v2-relay1` removes the host-owned APOS parser and storage implementation under
the accepted v2-clean1 zero-reader audit. The retained source comment points
back to the frozen fork, which still owns APOS. Its second isolated change adds
EasyDMA UART RX, an ISR-to-ring handoff, CRC/frame parsing in one worker thread,
and source-aware reply routing. BLE/NUS commands still enter the same parser and
reply through the unchanged BLE payload path; UART commands return a type-2
relay ACK with the original correlation. No tag command word was added.

The first build correctly failed the production RAM gate at 87.76% because two
new persistent relay thread stacks had been provisioned. RX parsing and ACK
transmission were then combined in one worker without moving work into the UART
ISR or ranging window. The second pristine build passed:

```text
FLASH 207004 / 228864 B = 90.45% (95% gate: PASS)
RAM    55136 /  65536 B = 84.13% (85% gate: PASS)
malloc arena = 0 B (explicit finite gate: PASS)
```

Build-only artifacts (not yet deployed):

```text
UWB_Part/builds/tag-fusion-link-relay1/merged.hex
  6b3b0d62ecf23b681b4a6d91aafa84a02e37c07564c315fc4e4c9a52dffb57b6
UWB_Part/builds/tag-fusion-link-relay1/dfu_application.zip
  63b8127638c972a5551d8c007e0386de270cba72ea02446fcd07ca357361a8ce
UWB_Part/builds/tag-fusion-link-relay1/tag/zephyr/zephyr.signed.bin
  3175f6b5b72258fe6da73ac89b72cfd839bba7443f2028f2b1418cf77429e97b
```

Both `biospur_link.h` copies are byte-identical at SHA-256
`792db4819ec320b586ac47b0a0a22e799c119b81bfb74ede3d8e0b40f06230f5`.
The unchanged static assertions still require a 90-byte `bsl_uwb_t` and
96-byte `bsl_frame_t`. The linked ELF contains the UART RX, ring-buffer, relay
worker, and ACK symbols and contains no APOS/layout symbol; the sole remaining
source occurrence is the required frozen-reference comment.

Before deployment, the Phase-B hardware predictions are:

1. Path M PING/STATUS/CFG/TR behavior and its 437.5 ms CI remain unchanged.
2. Path R PING/STATUS/VERSION returns a source=TAG reply with the matching
   correlation before B306's 2 s timeout.
3. `CFG_OK ... LIVE=1` is followed by independently rising strobe and valid
   96-byte frame counters at approximately 10 Hz; the ACK alone is not proof.
4. M-configure -> R-override -> M-reconfigure keeps replies on their originating
   paths and adds zero data TX drop/fail/abort delta.
5. Direct Path-M OTA duration will be measured end to end; the old 50–60 s
   number remains an estimate until that run completes.

## Slot-loss diagnosis and exact acceptance gate

The cause was **integer-millisecond truncation in the scheduling arithmetic**,
not RTC tick quantization. In the pre-fix `src/src/broadcast_tdma.c`, both
acceptance checks were:

```c
phase_ms >= target_ms &&
phase_ms <= target_ms + late_tolerance_ms
```

They were at baseline lines 203–204 and 226–227. `phase_ms` came from
`k_uptime_get_32()` modulo the cycle (baseline line 185). With the deployed
9 ms active slot and 12 ms minimum-remaining constant, the old tolerance
function returned zero (baseline lines 113–115), so the effective test was
`phase_ms == target_ms`. That accepted every real instant in the truncated
millisecond bucket `[target, target + 1 ms)`, while the zero-duration path then
called `k_yield()` (baseline line 230) and could cross out of that bucket.

The measured pre-fix lateness mean of 496 us and standard deviation of 299 us
match a uniform `[0, 1 ms]` distribution (500 us and 288.7 us ideally). Raising
the RTC tick rate would not repair that arithmetic.

The corrected implementation uses one absolute RTC-tick axis for phase,
candidate target, remaining time, post-sleep rejection, and final acceptance:

- `src/src/broadcast_tdma.c:138-142` derives the physical late budget;
- `src/src/broadcast_tdma.c:172-178` forms and accepts an absolute target;
- `src/src/broadcast_tdma.c:194-218` sleeps until 3 ms remains, busy-waits to
  the deadline, and applies the same tick-domain acceptance test;
- `src/src/broadcast_tdma_math.h:11-16` lifts the 32-bit synchronized epoch
  onto the nearest 64-bit uptime axis, including wrap;
- `src/src/broadcast_tdma_math.h:36-49` limits lateness to
  `min(2000 us, 10000 us - 8500 us) = 1500 us`.

The zero-duration `k_yield()` is gone. The configured 2 ms tolerance is only a
ceiling; the 8.5 ms sweep budget leaves 1.5 ms of real slot headroom.

## Pre-registered prediction

Before the final validation, the recorded prediction was:

1. residual loss below 0.5%, from the previous 6/3505 = 0.171% sparse tail as
   the conservative starting point; and
2. the old approximately uniform 1 ms lateness body would disappear, leaving
   the busy-wait/RTC resolution plus sparse preemption outliers.

## Formal 437.5 ms validation

The formal run is under
`UWB_Part/logs/absdeadline_437500us_20260721_193951/formal2/`.
Before capture, the Anchor Master sent the Responder command three times and
received 8/8 acknowledgements each time. The Tag Master then sent a fresh
explicit `BS065F motion` roster (`CFG_OK ... GEN=5`), and read back:

```text
VERSION fw=tag-fusion-link-v2-absdeadline3
ci=350; sup=400; reqci=350; reqsup=400; ciok=1; supok=1; cpmode=CAP
```

The 300 s independent fixed windows passed both recorders:

| Recorder | Observed | Missing multi-period gaps | Loss |
|---|---:|---:|---:|
| B306 UART/RDY | 3001 records; one boundary-phase extra | 0 | 0.000% |
| DSView RDY | 3000 pulses | 0 | 0.000% |

All 3000 B306 inter-record intervals mapped to one 100 ms slot. Every B306
record was `verdict=healthy`; `edge_qdrop`, `orphan_strobe`, `orphan_edge`, and
`orphan_frame` had zero delta. DSView independently measured 3116/3116 complete
pulses over its full 311.55 s recording, no interval above 150 ms, no estimated
missing slot, and 10.6–10.8 us pulse width.

The frozen lateness observation covered the formal window plus unchanged CAP
time immediately around it. It contained 5320 samples: 2236 at 0 tick and 3039
at 1 tick (99.15% combined), 99th percentile 1 tick, mean 19.475 us, standard
deviation 26.424 us, maximum 18 ticks/549.316 us, and no samples in the
33-tick tail buffer. `spinlate`, `slplate`, `pollfail`, and `polllast` all had
zero delta. The 1 ms uniform body disappeared and the measured loss beat the
pre-registered <0.5% prediction.

The 3.5 s recurrence remains recorded but unchanged: `35 x 100 ms = 8 x
437.5 ms`, ratio 35/8. Its denominator is small, so the phase repeats every
eight connection events. The earlier 6/3505 events did not justify changing
the connection interval without RF event timestamps.

## One-hour extended confirmation: failed

The attempted 36,000-slot confirmation is under
`UWB_Part/logs/absdeadline_1h_20260721_205638/`; the complete analysis is in
`analysis/1h-summary.md`. The predictions were committed to
`PREDICTIONS.md` before setup. The installed marker and settings remained
`tag-fusion-link-v2-absdeadline3`, CI/SUP 350/400, and CAP mode. Anchors again
acknowledged Responder 8/8 three times, and the fresh roster reported
`CFG_OK ... GEN=6`.

This run must **not** be reported as `0/36,000`. B306 and the first DSView
segment agreed on a clean prefix of 17,235 normal slots with no multi-period
gap and all B306 records `healthy`. B306 then stopped producing both UWB and
one-Hz telemetry. Its last captured edge was `4,294,873,329 us`, only
`93,967 us` before `2^32 us`; the next 100 ms slot crosses the 32-bit TIMER2
boundary. The RTT recorder itself continued normally for 3,905.032 s, so the
terminal outage was not a host logging exit.

The B306 source contains an intended compare-at-zero epoch extension in
`firmware/src/strobe_capture.c:146-205,519-525`, but the observed rollover path
is not validated. A later operator-reported detached RDY probe makes the
physical signal after the cutoff non-authoritative and prevents assigning the
terminal event solely to firmware from this run. The controlled reproduction
must secure and continuity-check the probe while directly crossing TIMER2
wrap.

DSView segment 1 contained 17,235 normal pulses followed by a 1 us terminal
artifact and then a flat trace. Segment 2 was unavailable until the probe was
reattached; its approximately 51 s reconnection interval was explicitly
excluded as external intervention, after which 5,932 pulses were gap-free.
The sequential DSView save/restart seam was 162.411 s and had no overlap.
Because B306 had already stopped, it could not bound that seam; DSView degraded
to initial and final spot evidence rather than a full-hour authority.

Lateness instrumentation was accidentally frozen at 3,302.660 s, so it covers
33,286 samples (about 55 minutes). Aggregate p99 remained 1 RTC tick and mean
was 14.465 us, but maxima of 37 ticks/1,129.150 us and 34 ticks/1,037.598 us
failed the pre-registered `<750 us` prediction while remaining inside the
1,500 us physical budget. All available bin means were below 39 us. Complete
histogram differences for 0–10, 10–20, and 40–50 minutes had p99=1 tick; a
missing page at 30 minutes prevents proving the two adjacent bins separately,
and the early freeze invalidates the final 10-minute bin.

Persistent Tag counters `spinlate`, `slplate`, `pollfail`, and `polllast`
remained zero. The two lateness-tail samples had modulo-35 phases 10 and 32,
with no 35/8 clustering. Temperature trailers changed from `T,120,177` to
`T,121,178`, but endpoint-only raw fields do not support a temperature
correlation.

Pre-registered verdicts were: P1 **FAIL**, P2 **FAIL**, P3 **FAIL**, P4
**PASS**, and P5 **PASS (narrow: no 35/8 cluster)**. No one-hour loss upper
bound is claimed. The clean 17,235-slot prefix is useful evidence, but it does
not replace the required continuous 36,000-slot result.

## Instrumentation removal and production build

After acceptance, the lateness histogram, summary, tail buffers, per-wait
sample arrays, and `BSL_LATE_*` commands were removed. The corrected deadline
logic and the production `spinlate`, `slplate`, `pollfail`, and `polllast`
counters remain. Wrap and physical-budget host tests pass.

The pristine stripped build reports:

```text
FLASH 212560 / 228864 B = 92.88% (95% gate: PASS)
RAM    52648 /  65536 B = 80.33% (85% gate: PASS)
```

Compared with the instrumented validation build, this recovers 2204 B FLASH
and 2352 B RAM. Current artifacts are:

```text
UWB_Part/builds/tag-fusion-link-absdeadline-final/merged.hex
  d79f8cb9cd82b9b0b5ad01bf53fd2edfeb850f7bbfd00e574f7b96e0a551649d
UWB_Part/builds/tag-fusion-link-absdeadline-final/dfu_application.zip
  8b67333cb9c36c63b4d0250136bb5df14ae9f8d816cb70d3d2f06bdfb7a193e9
UWB_Part/builds/tag-fusion-link-absdeadline-final/tag/zephyr/zephyr.signed.bin
  1fe31ae1e0f3ed8988665814654b96bd6b1205a6c112eee5ce72752d9071d684
```

No final OTA was initiated. The installed DWM1001C therefore still runs the
validated `tag-fusion-link-v2-absdeadline3` image.

## Diagnosis confirmation

The untouched v2 build resolved:

```text
CONFIG_COMMON_LIBC_MALLOC_ARENA_SIZE=-1
20010000 N _end
```

Its complete Zephyr application memory report was:

```text
Memory region         Used Size  Region Size  %age Used
           FLASH:      208756 B     228864 B     91.21%
             RAM:         64 KB        64 KB    100.00%
        IDT_LIST:          0 GB        32 KB      0.00%
```

The configured remaining-RAM arena tried to initialize beyond usable SRAM.
This explains both the successful link and the deterministic pre-application
fault.

## RAM audit and forward fix

There are no `malloc`, `calloc`, `realloc`, `free`, `k_malloc`, `k_free`, or
`k_heap_alloc` callers in the Task A sources. The replacement ELF has neither
`malloc_prepare`/`z_malloc_heap` nor `kheap__system_heap`.

MCUmgr's `CONFIG_MCUMGR_GRP_IMG_USE_HEAP_FOR_FLASH_IMG_CONTEXT` is disabled,
so it already uses a static `struct flash_img_context` (556 bytes in this
build). The 6,144-byte kernel heap had no consumer.

| Allocation/configuration | Invalid v2 | RAM fix |
|---|---:|---:|
| C malloc arena | remaining RAM (`-1`) | `0` |
| Kernel heap | 6,144 B | `0` |
| MCUmgr net buffers | 1,536 B x 4 | 512 B x 2 |
| ACL RX count, size | 4 x 502 B | 3 x 502 B |
| ACL TX count, size | 6 x 502 B | 3 x 502 B |
| L2CAP TX buffers | 6 | 3 |
| L2CAP TX MTU | 498 B | 498 B, unchanged |

The OTA master permits exactly one SMP request in flight. Its nominal 448-byte
chunk still fits the retained 498-byte MTU; only the first upload request
downshifts to 224 bytes because it also carries the SHA. Reducing queue depth
therefore does not reduce the steady-state chunk size.

For a comparable 204,496-byte tag image, the inherited fast uploader previously
took about 21.1 seconds from `OTA upload starting` to `OTA upload complete`.
Scaling to this 211,792-byte image gives about **22 seconds of upload time**.
Allow roughly **50–60 seconds end to end** for discovery, connection, erase,
image-state checks, scheduling, and reboot. This is an estimate until the
replacement completes one real OTA; the RAM trade removes burst queueing, not
the one-request-at-a-time transfer path.

The conservative stacks were not cut without measurement:

```text
MAIN                         3584 B
SYSTEM_WORKQUEUE             3072 B
ISR                          3072 B
BT_RX                        1600 B
MCUMGR_TRANSPORT_WORKQUEUE   3072 B
```

`CONFIG_THREAD_ANALYZER` now reports high-water marks to non-blocking RTT every
60 seconds. B306 has the same runtime reporting requirement and compiles with
it enabled.

## Hard production gate

Both build wrappers call `tools/zephyr_memory_gate.py`. A build fails if FLASH
exceeds 95%, RAM exceeds 85%, the malloc arena is unresolved, or the arena is
negative/remaining-RAM sized.

All active application `prj.conf` files in `B306_Part/` and the writable
`fusion-link` tree now set the C arena explicitly; none inherits `-1`.

The pristine replacement was built with:

```bash
cd /mnt/nrf_ssd/nRF_dev/BioSpur_Fusion/UWB_Part/fusion-link/src
./scripts/build_tag_ble_unified.sh 0 10 tag-fusion-link-ramfix1
```

Its generated tree is
`UWB_Part/builds/tag-fusion-link-ramfix1/`; the source sidecar is
`UWB_Part/fusion-link/src/tag-fusion-link-ramfix1.source`. The application
reports:

```text
Memory region         Used Size  Region Size  %age Used   Limit   Gate
          FLASH:       210944 B     228864 B     92.17%  95.00%   PASS
            RAM:        52640 B        64 KB     80.32%  85.00%   PASS
malloc arena: 0 B (explicit finite gate)
```

Compared with invalid v2, linked RAM falls by 12,896 bytes while adding the
1,024-byte analyzer stack and its metadata. `_end` is now `0x2000cda0`,
leaving 12,896 bytes before `0x20010000`. FLASH has 17,920 bytes free.

A pristine B306 compile-check also passes the same policy:

```text
FLASH 178024 / 499200 B = 35.66% PASS
RAM    61672 / 262144 B = 23.53% PASS
malloc arena = 0 B
```

## Connection-interval verification

The GAP preferred fallback is now 350 units rather than the known-bad 6-unit
value. Capture mode requests exactly 350 x 1.25 ms = 437.5 ms. The
`le_param_updated` callback logs the achieved values, and every `BSLSTAT;1`
record now appends:

```text
ci=<1.25-ms units>;lat=<events>;sup=<10-ms units>;
reqci=<1.25-ms units>;ciok=<0|1>;cpmode=<CAP|FAST>
```

In capture mode, acceptance requires `reqci=350`, `ci=350`, `ciok=1`, and
`cpmode=CAP`. A rejected or unapplied request is therefore visible.

## Signing-key compatibility

Task A keeps the key inherited from the DWM freeze:

```text
NCS file: bootloader/mcuboot/root-rsa-2048.pem
PEM SHA-256:
1fc912d30251b821f251e127d4daf7ba9338dd5c04e5af100abfb5b7c7d4c022
public SPKI DER SHA-256:
a14bcb1bf9bb821146ba32838217e476f5412621320534ffe490a1890c994660
```

It is the NCS sample key. It was not changed because doing so would break
compatibility with existing DWM bootloaders. Any future replacement is a
fleet-wide SWD event.

## Wire contract retained

The supplied v2 header remains byte-identical in both MCU trees:

```text
B306_Part/include/biospur_link.h
UWB_Part/fusion-link/src/include/biospur_link.h
SHA-256 d832fe9fbaf92ff1d8b82eb1a833566a84c540b863309b18803863ae4de8fd1b
```

Host compilation confirms a 4-byte header, 90-byte body, 96-byte frame, CRC at
offset 94, and a fixed 2,083 us UART wire time. CRC-16/CCITT-FALSE returns
`0x29B1` for `123456789`.

The tag preserves explicit anchor IDs, raw 40-bit poll-TX time, per-sweep
instantaneous CFO clock-offset-corrected ranges with no smoothing, tracker
quality, CFO Q8 ppm, validity and flags, and
measured `t_round_us[]`. `TR;2` masks use anchor IDs while the v2 UART valid
mask uses explicit frame slots; a dual-path comparator must remap by
`anchor_id[]`.

## Historical RAM-fix artifacts

Final SHA-256 values are frozen in `TASK_A_SHA256SUMS.txt` and the human
procedure in
`B306_Part/handover/dwm1001c-task-a-v2-ramfix1/README.md`.

```text
tag/tag-fusion-link-v2-ramfix1.signed.bin
  d8600871f402b4d5a7d0fb4df97e52d02a18f2eccf886934f3b48af70949750e
tag/tag-fusion-link-v2-ramfix1.dfu_application.zip
  1cdf9cca2e1629d09bb7f9c44de0fecd0e780c9ef269643a3f1bd460483a6017
tag/tag-fusion-link-v2-ramfix1.merged.hex
  12d4c587c2fae44b1469baf5260961522119eeeffa9c74e25d330f1b0523b869
```

Rollback remains:

```text
tag/tag-freeze-clean-20260716.merged.hex
8405cf0506400bc7085c3498d4413fe06ea2eb2e7c5836e75bc2f81ceba53186
```

## Post-reflash gates

Do not reuse the old P1.01/P1.02 comparison. After the replacement prints its
marker and remains alive:

1. collect the first 60-second thread-analyzer report;
2. verify `BSLSTAT` connection parameters and increasing generated/completed
   counters;
3. retest P1.01 first, then reverse only if the live transmitter still yields
   zero electrical bytes;
4. capture READY/UART on the PCB 0-ohm test points with the logic analyser;
5. complete the sweep agreement, cadence, offset, CRC, duplicate/missing, and
   `t_round_us[]` measurements from the original Task A acceptance list.

The UART direction remains open until those live-transmitter measurements.

## v2-clean1 firmware hygiene (build only, 2026-07-23)

Lineage:

```text
tag-fusion-link-v2-absdeadline3
  installed, instrumented, 270k-slot validated
    -> tag-fusion-link-v2-absdeadline-final
       instrumentation stripped, never deployed
         -> tag-fusion-link-v2-clean1
            honest range naming + legacy filter/solver purge, not deployed
```

Names on this evolving line now use incrementing numeric suffixes. A `-final`
suffix is prohibited because the never-deployed `absdeadline-final` was already
superseded.

The tracker field is now `range_mm`, assigned directly from the current
CFO-corrected SS-TWR result. There is no mean, median, low-pass or Kalman stage
on the DWM1001C; smoothing belongs on the fusion host so the upstream node does
not corrupt the fusion noise model. The write-only three-sample window was
removed. `struct uwb_range_tracker` changed from 40 B to 28 B, so eight
trackers changed from 320 B to 224 B and reclaim 96 B.

The tag build no longer compiles or calls the old EKF, position solver, motion
estimator or legacy LIS2DH IMU path. A whole-tree build-hook audit found no
other CMake target referencing those four source modules. The base ELF carried
`uwb_ekf_state_data` (160 B RAM) and `uwb_ekf_reset` (16 B FLASH), plus the
legacy motion and IMU state. None of their symbols occur in the clean1 ELF or
map.

The prior last-value outlier comparison is gone; no magic 120,000 mm threshold
or previous-range read remains. Failed/absent measurement semantics are
unchanged:

```c
static bool ss_twr_init_range_measurement_valid(uint32_t range_mm)
{
    /* Validity marking of failed measurements -- NOT range filtering. */
    return range_mm != 0U;
}
```

This check still drives rejection and therefore the UART `valid_mask`; zero is
not forwarded as a real range.

Memory comparison:

| image | FLASH | RAM | FLASH gate | RAM gate |
|---|---:|---:|---:|---:|
| absdeadline3 (instrumented reference) | 214,764 B (93.84%) | 55,000 B (83.92%) | PASS | PASS |
| absdeadline-final (cleanup base) | 212,560 B (92.88%) | 52,648 B (80.33%) | PASS | PASS |
| v2-clean1 | 210,640 B (92.04%) | 52,296 B (79.80%) | PASS | PASS |

Relative to the pinned base, clean1 reclaims 1,920 B FLASH and 352 B RAM.
The RAM delta includes 160 B EKF state, 96 B tracker shrink, 24 B legacy
motion state, 24 B legacy IMU state/spec, and alignment/linker effects. The C
malloc arena remains explicitly 0 B.

Build-only artifacts:

```text
UWB_Part/builds/tag-fusion-link-clean1/merged.hex
  f776381c2c68c0ca76057a2af5ce5de460b34304f0d2a32a3ce6d610810c1702
UWB_Part/builds/tag-fusion-link-clean1/dfu_application.zip
  1cc342488ab77d096423b64ca81861cc86ab74c013193a9adc04401203edb2c0
UWB_Part/builds/tag-fusion-link-clean1/tag/zephyr/zephyr.signed.bin
  ee6a810e0b3a5b23dd4b0278bced65c0d6cc687be8cb1fa843da87c09198f615
```

The UART v2 contract remains 4 B header + 90 B body + 2 B CRC = 96 B.
Compile-time assertions now also pin `poll_tx_ts=4`, `identity_code=9`,
`anchor_id=16`, `range_mm=32`, `t_round_us=48`, `valid_mask=88`,
frame body=4 and CRC=94. Both MCU copies of `biospur_link.h` are byte-identical.

### APOS disposition and reader audit

The v2-clean1 audit licensed removal: layout coordinates had zero
measurement-path readers. The IMU/relay batch executed that removal in its
first isolated tag commit: the APOS BLE parser/help entries, startup load, NVS
handler, runtime/default coordinate arrays, public header, and source module
are gone from the fusion fork.

Fork divergence: layout is host-side in the fusion fork, so APOS is no longer
part of its tag command surface. This is intentional protocol divergence and
merge debt +1. The freeze fork keeps APOS as the production receiver for
`push_apos_layout_verified.py`; a source pointer to
`uwb_tag_ble.c / uwb_anchor_layout.c @ freeze-clean-20260716` remains beside
the removed parser location.

`tag-fusion-link-v2-clean1` was built and verified only. No OTA, flash or
configuration push was performed; installed firmware remains
`tag-fusion-link-v2-absdeadline3`.
