# BioSpur Fusion — Agent Guide

> Workspace: `/mnt/nrf_ssd/nRF_dev/BioSpur_Fusion/`
> Scope: UWB + IMU sensor fusion node. This is **not** the UWB positioning
> project — that one lives at `/mnt/nrf_ssd/nRF_dev/BioSpur_UWB_before_start/`
> and is frozen. See §3.

---

## 1. What this project is

A single-node **UWB + IMU fusion module**: a custom PCB that carries a UWB
ranging radio and a 6-axis IMU under one MCU, timestamps both on **one clock**,
and streams the aligned pair to a host over BLE.

The UWB positioning work (Alt-SS-TWR firmware, anchor layout, Vicon validation)
is **upstream and frozen**. This project consumes its range output; it does not
develop it.

**Target:** beat the pure-UWB dynamic baseline of **102.6 mm** (Erlangen Vicon,
static 72.7 mm) by adding IMU information.

---

## 2. Hardware — authoritative

Do not infer hardware from code comments. This table is ground truth.

### Fusion PCB

| Part | Chip | Role |
|---|---|---|
| DWM1001C | nRF52832 + DW1000 | UWB ranging only. Produces raw ranges. |
| B306 (u-blox NINA-B306) | nRF52840 | **Fusion node core.** IMU ingest, timestamping, BLE egress. |
| JY61P | — | 6-axis IMU, 200 Hz |

### Wiring — exhaustive, there is nothing else

```text
JY61P  --I2C-------------> B306
DWM1001C --UART----------> B306        (raw ranges out)
DWM1001C --READY----------> B306        (sweep timestamp strobe)
DWM1001C SWD               standalone  (NOT routed to B306; flash via J-Link OB)
```

`GPIO19` is the schematic name for **DWM1001 module pin 19**, signal `READY`,
which maps to nRF52832 **P0.26**. A whole-tree audit of the frozen firmware
found P0.26 unused. nRF52832 **P0.19** is the internal DW1000 IRQ input and must
not be repurposed. See §5.

NINA-B3 `GPIO_n` names are module pads, not arithmetic nRF pin numbers:

| PCB net | NINA-B306 pad | B306 nRF52840 pin | Direction/use |
|---|---:|---|---|
| `UWB_RX1` | GPIO_35 | P1.01 | B306 RX from DWM1001C pin 20 `UART_TX` |
| `UWB_TX1` | GPIO_36 | P1.02 | B306 TX to DWM1001C pin 18 `UART_RX`; wired, currently unused |
| `UWB_RDY` | GPIO_37 | P1.03 | B306 strobe input from DWM1001C nRF52832 P0.26 |
| `SDA` | GPIO_42 | P0.26 | JY61P I2C data |
| `SCL` | GPIO_44 | P0.27 | JY61P I2C clock |
| `BUTTON_1` | GPIO_32 | P0.11 | Active-low button |

Always qualify P0.26 by MCU: it is the DWM1001C nRF52832 strobe output and the
B306 nRF52840 I2C SDA pin. The fitted NINA-B306-01B has no 32.768 kHz crystal;
use the calibrated 500 ppm LFRC. The fitted JY61P is the 6-axis part at I2C
address `0x50`; MAX30102, its second I2C path, and the 1.8 V support domain are
not populated.

### Off-board

| Device | Chip | Role |
|---|---|---|
| nRF52840 DK, J-Link `683234364` | nRF52840 | **Fusion Master** — BLE central, native USB CDC to PC; J-Link is RTT/debug only |
| nRF52840 dongle | nRF52840 | Spare/stub candidate. Not the current Fusion Master. |
| nRF54L15 | nRF54L15 | Candidate Fusion Master for multi-node. Not in use yet. |
| 2× B120 | nRF5340 | UWB Tag Master / Anchor Master. **Belongs to the UWB side, not here.** |

B306 development now runs on the real Fusion PCB, not on the DK as a
development twin. SWD access to either Fusion-PCB MCU remains a human handover.

### Not in this project, ever

- **PANS / DRTLS** — never used. All UWB is self-written Alt-SS-TWR.
- **DW3000** — never used.
- B120 / nRF5340 as a fusion component.

---

## 3. Workspace layout

```text
BioSpur_Fusion/
├── AGENTS.md                  ← this file
├── UWB_Part/
│   ├── builds/                ← all generated UWB build trees
│   └── 2026-07-15-FREEZE/
│       └── firmware/          ← READ-ONLY. Stable rollback baseline.
└── B306_Part/
    ├── builds/                ← all generated B306/DK build trees
    └── ...                    ← the actual work
```

### Rules

- **Everything stays inside `BioSpur_Fusion/`.** Never create directories
  directly under `/mnt/nrf_ssd/`.
- **All generated build trees are centralized.** B306/DK builds go only under
  `B306_Part/builds/`; UWB builds go only under `UWB_Part/builds/`. Use
  `builds/<target>-<purpose>`, without another `build-` prefix. A `build/` or
  `build-*` beside source is a layout error and must not be ignored.
- **`UWB_Part/2026-07-15-FREEZE/` is read-only.** It is a rollback baseline,
  not a museum: branch from it freely, but never edit in place.
- If UWB firmware information is missing or incomplete here, look in
  `/mnt/nrf_ssd/nRF_dev/BioSpur_UWB_before_start/` before asking.
- Experiment logs go under a `logs/` subdirectory, never at a component root.

---

## 4. The one invariant: single-clock timestamping

**Everything in this project exists to put IMU and UWB samples on the same time
axis.** If a change breaks that, it is wrong regardless of what else it improves.

- The **B306 hardware timer is the node master clock.**
- IMU samples are timestamped at the **trigger instant** of the 200 Hz hardware
  timer, *not* when the I2C read completes.
- UWB epochs are timestamped by **hardware capture (GPIOTE→PPI→TIMER)** of the
  ready edge, *not* in a software ISR, and *not* by UART arrival time.

### Error budget (why the above matters)

There are two distinct dynamic error classes:

- a time-axis error Δt costs approximately `v·Δt`; and
- SS-TWR motion during the exchange biases a range by approximately
  `v_r·t_round/2`, where `v_r` is radial velocity toward that anchor.

| Source | Timing term | Cost |
|---|---|---|
| Hardware-captured ready edge | <100 µs | <0.2 mm |
| UART arrival-time fallback | ~5 ms | ~7.5 mm |
| Intra-sweep spread (8 anchors, design estimate) | 7.18 ms | ~10.8 mm |
| JY61P internal clock vs B306 timer | 1 sample = 5 ms | ~7.5 mm |
| SS-TWR motion bias | rank-dependent `t_round/2` | **~20–40 mm at limb-tip radial speeds** |

Against a 102.6 mm dynamic baseline, the synchronization terms cost roughly
0.2 %–10 %, while uncorrected SS-TWR motion bias can cost roughly 20 %–40 % and
can dominate the timing budget. The measured `t_round_us[]` exists so the host
can apply the per-anchor correction `Δr_motion = v_r·t_round/2` using
compatible units; never replace the measured interval with nominal guard/rank
spacing.

The 7.18 ms intra-sweep figure is not an extracted frozen-firmware constant.
Frozen source uses 1,200 µs response delay and 1,000 µs ranked response spacing
and estimates the last frame completes near 8.45 ms after poll TX completion.
Measure the actual first-to-last range epochs in P2 before freezing a host model.

**Intra-sweep modelling:** the ready strobe must represent the common broadcast
poll epoch, not sweep completion. Task A places it in the broadcast-poll
TX-done path and carries the exact raw poll-TX timestamp in the paired UART
frame. Their constant offset is absorbed by the B306 clock filter; measured
`t_round_us[]` expresses each anchor-specific response offset. A sweep-end
strobe or UART arrival time cannot express that structure.

---

## 5. Known state of GPIO19 / READY

**DWM1001-side mapping resolved.** The PCB net called `GPIO19 Ready` refers to
DWM1001 module pin 19, datasheet signal `READY`, which maps to nRF52832 P0.26.
A 2026-07-20 whole-tree audit of the frozen firmware found no P0.26 assignment,
reservation, read, configuration, or drive, so it is free for the sweep
strobe.

nRF52832 P0.19 is a different signal: the internal DW1000 `int-gpios` input.
Frozen `uwb_port.c` configures it as `GPIO_INPUT`; never drive or repurpose it.

Before completing P2:

1. Bench-validate the P0.26 implementation in `UWB_Part/fusion-link/`; it
   configures a defined inactive level and pulses in the broadcast-poll
   TX-done path but has not yet been deployed.
2. Capture that pulse on B306 nRF52840 P1.03 through the `UWB_RDY` net.
3. On B306, discard edges until a plausible cadence is established.

The PCB's 0 Ω series resistors on `UWB_RDY`, `UWB_TX1`, and `UWB_RX1` are logic
analyser test points. During bring-up, use those signals as ground truth. Field
telemetry still counts CRC errors, dropped/duplicated sweeps, unpaired strobes
and frames, and clock-filter residuals.

See `UWB_Part/FREEZE_INTERFACE.md` for source citations.

---

## 6. BLE architecture — decision record

**Decided.** Do not re-open without new measurements.

```text
DWM1001C(52832) --UART--> B306(52840) --BLE--> DK(52840) --USB CDC--> PC
   Alt-SS-TWR   --ready->  fuse/stamp        Fusion Master
                              ^
                     JY61P --I2C
```

| Concern | Resolution |
|---|---|
| 52832 firmware | **Unchanged** except a versioned UART range output and ready strobe on a confirmed free pin. Ranging stays adjacent to DW1000. |
| 52832 BLE | Demoted to **OTA-only**. Silent during capture. |
| Fusion location | **Host (PC) first.** Moves into the 52840 only after the measurement model and R matrix are frozen. |
| BLE egress | **One** connection per node, out of B306. |

### Batching — mandatory

**Never send one BLE notify per IMU sample.** 200 Hz = 5 ms production against a
7.5–30 ms connection interval; notifies pile up in the controller queue.

Batch a 100 ms logical window:

- IMU sample: 6×int16 = **12 B**. Samples carry no individual timestamps;
  batch start plus the fixed 5 ms cadence reconstructs them.
- UWB epoch: one v2 `bsl_uwb_t` = **90 B**.
- Logical batch: 20×12 + 90 + batch metadata ≈ **~337 B at 10 Hz**.
- Logical rate: **~3.4 kB/s ≈ 27 kbit/s per node**.

A ~337 B logical batch does **not** fit one BLE DLE payload: the Link Layer data
payload maximum is 251 B before ATT overhead. The logical batch must be
fragmented or encoded more compactly. Freeze the exact fragmentation only after
measurement; do not describe the logical batch as one DLE packet.

Enable **2M PHY** and **DLE**. Connection interval 15–30 ms is the starting
point; the fusion buffer absorbs latency, and P3 must measure the result.

---

## 7. Toolchain

**`B306_Part/` uses nRF Connect SDK (Zephyr).**

Rationale:

- MCUboot + mcumgr supports the required DFU/OTA path (§8).
- **nRF54L15 is NCS-only.** If the Fusion Master later moves to 54L15, a Zephyr
  host stack ports; an nRF5 SDK one does not.
- nRF52840 support is mature.

The UWB side is existing NCS/Zephyr code around the legacy DW1000 driver and
must not be ported into the B306 build. The sides communicate over UART and a
ready strobe and deliberately remain separate applications.

Confirmed installation:

- NCS: `v2.8.0`
- Zephyr: `v3.7.99-ncs1`
- user-environment west: `v1.5.0` at `/home/zekaixiao/.local/bin/west`
- isolated NCS-toolchain west: `v1.2.0`
- west/NCS workspace: `/home/zekaixiao/ncs/v2.8.0`
- toolchain: `/home/zekaixiao/ncs/toolchains/b81a7cd864`
- compiler:
  `/home/zekaixiao/ncs/toolchains/b81a7cd864/opt/zephyr-sdk/arm-zephyr-eabi/bin/arm-zephyr-eabi-gcc`

The user Python site currently contains a partial dependency set that can make
the user-environment west fail board discovery. The verified build command in
`B306_Part/firmware/README.md` isolates Python and uses the NCS toolchain's
complete package set.

---

## 8. DFU / OTA

B306's first-flash image includes signed MCUboot, equal internal-flash slots,
and mcumgr SMP over BLE. The generated partition layout is frozen in
`B306_Part/firmware/pm_static.yml`; key ownership and the exact layout are
recorded in `B306_Part/docs/dfu.md`.

The private signing key is outside the repository. Never replace it or change
the partition layout after the first B306 flash without treating the change as
an SWD recovery event.

Requirements:

- OTA and capture are **mutually exclusive**. Entering DFU cleanly stops IMU
  sampling and UWB ingest; the exit contract leaves the node in RUN.
- The 52832's own OTA path is separate and unchanged. Two independent DFU
  targets on one board — document which tool flashes which.

The first image deliberately contains only MCUboot/SMP, FICR-derived
`BSF%04X` advertising, non-blocking RTT logs, and an LED heartbeat. UART, IMU,
strobe capture, and capture streaming arrive only after a BLE-only DFU cycle
has passed.

---

## 9. Data & logging conventions

- Logs under `logs/`, never at a component root.
- Run directories use `<purpose>_YYYYMMDD_HHMMSS`.
- Host capture files carry: firmware git SHA (both MCUs), connection parameters
  (PHY, interval, MTU/data length), IMU rate, UART interface version, and the
  ready-strobe convention. A capture without provenance is not analysable later.
- Keep raw capture, derived tables, plots, and the human-readable decision
  report distinguishable; do not overwrite raw input.
- Analysis runs on the i7-8700K / 32 GB / 2× GTX 1080 Ti box. Broad independent
  sweeps use 8–10 CPU workers when practical and keep the desktop responsive.

---

## 10. Roadmap

| Phase | Deliverable | Exit criterion |
|---|---|---|
| **P1** | IMU chain on Fusion-PCB B306: hardware-timer-triggered 200 Hz I2C | 30 min, zero dropped samples; JY61P↔B306 drift quantified in **ppm** |
| **P2** | UWB ingest: UART range parse + ready-edge hardware capture | 10 min, zero epoch mispairing; single-peak inter-edge histogram |
| **P3** | BLE egress: batched/fragmented notify + DK central + USB CDC | 1 h zero logical-batch loss; end-to-end latency <150 ms |
| **P4** | Host-side alignment | Shake test: IMU accel peak vs UWB range inflection offset <10 ms **and constant** |
| **P5** | Fusion (ES-EKF) on host | Beats 102.6 mm dynamic baseline |
| **P6** | Vicon validation | Improvement is real **and attributable** (gap-filling vs outlier rejection reported separately) |
| **P7** | Multi-node decision | Measure how many nodes one central holds. ≥8 → ship. <8 → reverse-broadcast. |

**The current checkpoint is the two first-flash human handovers.** Do not begin
dependent feature work until the human reports the observed result from both
MCUs. Stage 1 then proves a complete BLE-only B306 DFU cycle before P1/P2
feature images are accepted.

### Hard sequencing rules

- **Fusion does not move into the MCU until P6 passes.** Porting an ES-EKF
  before the measurement model and R matrix are frozen is wasted work.
- **Nothing is bought and no multi-node code is written before P7.**
- SWD on either Fusion-PCB MCU is a human handover. Do not infer success or
  continue dependent work while a handover is outstanding.

### External dependency

P5 needs the **R matrix**, which comes from the RotoArm dynamic residual
analysis. Freezing the UWB *firmware* did not freeze that analysis — it is now
a P5 prerequisite.

---

## 11. Settled — do not re-litigate

| Proposal | Verdict |
|---|---|
| Make the 52832 a dumb SPI/UART passthrough | **No.** Discards the frozen ranging engine and DW1000 register-level timing for zero gain. |
| Separate IMU master, UWB and IMU on independent BLE links | **No.** Doubles connection count and reconstructs sync across radios instead of one node clock. |
| "BLE can't carry 200 Hz + 10 Hz" | **False.** ~27 kbit/s is modest; scheduling and packetization are the constraints. |
| "Last year we maxed out at 5 partners, so BLE is the ceiling" | **Misattributed.** Measure the current architecture in P7. |
| Fusion Master must be an nRF5340 like the other masters | **No.** The Fusion Master moves bytes from BLE to USB; the current nRF52840 DK is suitable. |
| Buy more dongles to raise node count | **Premature.** Multiple centrals create USB time bases to re-align. Measure P7 first. |

### Multi-node endgame (context, not current work)

Do not scale by adding BLE connections. The intended path is
**reverse-broadcast**: the tag's FINAL frame carries a **preintegrated IMU
delta** alongside the range, the gateway listens passively, and per-node BLE
degrades to a pure configuration channel. Not P1–P6 work.

---

## 12. Confirmed, unknown, and inherited conventions

### Confirmed

- NCS/toolchain/west paths and versions: §7.
- Frozen UWB commit:
  `8b68ee0aafe75b849fca8f36606775e99a9ef3cd`.
- Frozen range record: `TR;2` is a BLE/NUS status record, not a production UART
  record. Exact fields and citations: `UWB_Part/FREEZE_INTERFACE.md`.
- Frozen GPIO state: DWM1001 module pin 19 (`READY`) maps to free nRF52832
  P0.26; nRF52832 P0.19 is the DW1000 IRQ input and is not a free output.
- Frozen nominal range cadence: 10 Hz per tag from a 10×10 ms TDMA schedule,
  runtime-configurable.
- Task A wire contract: `biospur_link.h` v2, fixed 96-byte frame,
  CRC-16/CCITT-FALSE, with measured per-anchor `t_round_us`.
- B306 pins: UWB RX P1.01, unused UWB TX P1.02, UWB ready P1.03, I2C SDA
  P0.26, I2C SCL P0.27, button P0.11.
- JY61P/WT61P-compatible 6-axis subset: address `0x50`, accelerometer and
  gyroscope registers `0x34`–`0x39`, and `RRATE` register `0x03 = 0x000B` for
  nominal 200 Hz. Magnetometer and Euler-angle registers are out of scope.
- NINA-B306-01B has no LFXO. Use LFRC, 500 ppm, with periodic calibration.
- `UWB_RDY`, `UWB_TX1`, and `UWB_RX1` can be observed at their 0 Ω series
  resistors during bring-up.

### UNKNOWN — do not guess

- Whether this individual JY61P acknowledges at `0x50`, applies the 200 Hz
  `RRATE` write, and has the documented axis signs. Verify by ACK, a
  flat/still test, a signed 90° rotation, and duplicate-sample counting.
- Measured per-anchor epoch offsets within a sweep.
- Final BLE logical-batch encoding/fragmentation and UUIDs.

Resolve the remaining sensor facts by bench probe and the protocol details by
implementing and measuring against the versioned Task A interface.

### Conventions inherited from the old workspace

- Names are role/component oriented. Generated trees use
  `B306_Part/builds/<target>-<purpose>` or
  `UWB_Part/builds/<target>-<purpose>`; never place `build/` or `build-*`
  beside source and never create build output at the `BioSpur_Fusion/` root.
- Experiment output uses timestamped purpose directories under `logs/`.
- Build from NCS with west and an explicit board; use pristine builds for
  reproducibility and record the source command/SHA.
- Flash/debug commands use explicit probe identity. Never use `nrfjprog`; use
  west/J-Link workflows appropriate to the target.
- **Never permit an interactive `J-Link Probe Selection` dialog.** Every J-Link
  operation must select the authorized probe in the command itself, using
  `--dev-id` / `--serial-number` for west-compatible runners or
  the tool's explicit serial-number option (`-SelectEmuBySN`, or `-USB` for
  `JLinkRTTLogger`) for SEGGER tools. If a probe-selection dialog appears,
  cancel it and stop; do not choose a probe manually and do not rely on “the
  only probe connected.”
- Tags and anchors are OTA-first. Direct J-Link flashing of deployed UWB devices
  requires explicit authorization; Fusion-PCB B306 and the Fusion Master DK
  are separate targets with separate images and flash authority.
- Never hard-code `/dev/ttyACM<n>` or `cat` nRF CDC devices. Resolve stable
  identity and open serial with DTR/RTS disabled.
- Before destructive OTA/flash, verify device identity and image target.
- Commit subjects are concise, imperative/descriptive, and usually
  `<scope>: <change>`; freeze milestones may use a leading `FREEZE`.
- The parent repository's current branch naming style is
  `feature/<short-hyphenated-topic>`. Do not create a branch unless requested.

### Old conventions deliberately not carried over

- B120 LFRC/dual-core rules, master SNR maps, protected Master_Anchor overrides,
  and B120 boot profiles: UWB-master-specific, not B306 firmware conventions.
- Anchor role/provisioning control, A–H UUID mapping, AutoPos solver workflow,
  and listener/Geiger exclusions: remain authoritative in the frozen UWB
  workspace but are outside B306 development.
- UWB tag/anchor OTA scripts and their command dialect: the DWM1001C keeps that
  path; B306 uses its own future mcumgr target.
- Legacy build artifact names and archived solver variants: retained for UWB
  traceability, not copied into the new scaffold.
