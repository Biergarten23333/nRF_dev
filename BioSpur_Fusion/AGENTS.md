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
DWM1001C --GPIO19 Ready--> B306        (sweep timestamp strobe)
DWM1001C SWD               standalone  (NOT routed to B306; flash via J-Link OB)
```

`GPIO19` is the schematic name for **DWM1001 module pin 19**, signal `READY`,
which maps to nRF52832 **P0.26**. A whole-tree audit of the frozen firmware
found P0.26 unused. nRF52832 **P0.19** is the internal DW1000 IRQ input and must
not be repurposed. The B306 capture-input mapping is still unconfirmed. See §5.

### Off-board

| Device | Chip | Role |
|---|---|---|
| nRF52840 dongle | nRF52840 | **Fusion Master** — BLE central, USB CDC to PC |
| nRF52840 DK | nRF52840 | B306 development twin (same silicon) |
| nRF54L15 | nRF54L15 | Candidate Fusion Master for multi-node. Not in use yet. |
| 2× B120 | nRF5340 | UWB Tag Master / Anchor Master. **Belongs to the UWB side, not here.** |

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
│   └── 2026-07-15-FREEZE/
│       └── firmware/          ← READ-ONLY. Stable rollback baseline.
└── B306_Part/                 ← the actual work
```

### Rules

- **Everything stays inside `BioSpur_Fusion/`.** Never create directories
  directly under `/mnt/nrf_ssd/`.
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

At a body speed of ~1.5 m/s, a sync error of Δt costs ≈ `v·Δt`:

| Source | Δt | Cost |
|---|---|---|
| Hardware-captured ready edge | <100 µs | <0.2 mm |
| UART arrival-time fallback | ~5 ms | ~7.5 mm |
| Intra-sweep spread (8 anchors, design estimate) | 7.18 ms | ~10.8 mm |
| JY61P internal clock vs B306 timer | 1 sample = 5 ms | ~7.5 mm |

Against a 102.6 mm dynamic baseline these are 0.2 %–10 %. **None is fatal;
all are cheap to remove.** Do not trade architecture cleanliness for them, but
do not leave them on the table either.

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

1. Confirm the B306 capture input from the PCB schematic/netlist.
2. Bench-validate the P0.26 implementation in `UWB_Part/fusion-link/`; it
   configures a defined inactive level and pulses in the broadcast-poll
   TX-done path but has not yet been deployed.
3. On B306, discard edges until a plausible cadence is established.

See `UWB_Part/FREEZE_INTERFACE.md` for source citations.

---

## 6. BLE architecture — decision record

**Decided.** Do not re-open without new measurements.

```text
DWM1001C(52832) --UART--> B306(52840) --BLE--> dongle(52840) --USB CDC--> PC
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

- IMU sample: 4 B timestamp + 6×int16 = **16 B**
- UWB epoch: 4 B timestamp + 8×4 B ranges = **36 B**
- Logical batch: 20×16 + 36 + header ≈ **~360 B at 10 Hz**
- Logical rate: **~3.6 kB/s ≈ 29 kbit/s per node**

A ~360 B logical batch does **not** fit one BLE DLE payload: the Link Layer data
payload maximum is 251 B before ATT overhead. The logical batch must be
fragmented or encoded more compactly. Freeze the exact fragmentation only after
measurement; do not describe 360 B as one DLE packet.

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

## 8. DFU / OTA — to be built

B306 currently has **no DFU path**. This is a required deliverable, not a nice
to have: the module goes on a body, and SWD access during a capture session is
not realistic.

Plan: **MCUboot + mcumgr over BLE (SMP)**, dual-slot.

Requirements:

- OTA and capture are **mutually exclusive**. Entering DFU cleanly stops IMU
  sampling and UWB ingest; the exit contract leaves the node in RUN.
- The 52832's own OTA path is separate and unchanged. Two independent DFU
  targets on one board — document which tool flashes which.

The planned configuration and 1 MiB internal-flash partition map are documented
in `B306_Part/docs/dfu.md`. Do not enable MCUboot in the minimal P1 scaffold.

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
| **P1** | IMU chain on DK: hardware-timer-triggered 200 Hz I2C | 30 min, zero dropped samples; JY61P↔B306 drift quantified in **ppm** |
| **P2** | UWB ingest: UART range parse + ready-edge hardware capture | 10 min, zero epoch mispairing; single-peak inter-edge histogram |
| **P3** | BLE egress: batched/fragmented notify + dongle central + USB CDC | 1 h zero logical-batch loss; end-to-end latency <150 ms |
| **P4** | Host-side alignment | Shake test: IMU accel peak vs UWB range inflection offset <10 ms **and constant** |
| **P5** | Fusion (ES-EKF) on host | Beats 102.6 mm dynamic baseline |
| **P6** | Vicon validation | Improvement is real **and attributable** (gap-filling vs outlier rejection reported separately) |
| **P7** | Multi-node decision | Measure how many nodes one central holds. ≥8 → ship. <8 → reverse-broadcast. |

**P1 is the current phase.** It is fully decoupled from the ready-strobe issue;
DWM1001C does not need power. Resolve the pin map and add the strobe during P2,
alongside the UART output change.

### Hard sequencing rules

- **Fusion does not move into the MCU until P6 passes.** Porting an ES-EKF
  before the measurement model and R matrix are frozen is wasted work.
- **Nothing is bought and no multi-node code is written before P7.**
- **The custom PCB is not on the critical path.** P1–P6 run on the 52840 DK.

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
| "BLE can't carry 200 Hz + 10 Hz" | **False.** ~29 kbit/s is modest; scheduling and packetization are the constraints. |
| "Last year we maxed out at 5 partners, so BLE is the ceiling" | **Misattributed.** Measure the current architecture in P7. |
| Fusion Master must be an nRF5340 like the other masters | **No.** The Fusion Master moves bytes from BLE to USB; a 52840 dongle is suitable. |
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

### UNKNOWN — do not guess

- JY61P I2C address, register map, and 200 Hz configuration.
- B306 PCB pins for I2C SCL/SDA, DWM UART TX/RX, and ready capture.
- B306 capture-input mapping for the DWM1001 module-pin-19/P0.26 READY signal.
- Measured per-anchor epoch offsets within a sweep.
- Final BLE logical-batch encoding/fragmentation and UUIDs.

Resolve JY61P facts from the exact module datasheet/bench probe; resolve pins
from the PCB schematic/netlist; resolve protocol fields by implementing and
testing a versioned branch from the freeze.

### Conventions inherited from the old workspace

- Names are role/component oriented; build directories use
  `<component>/build-<target>-<purpose>`. B306 build output stays under
  `B306_Part/`, never at the `BioSpur_Fusion/` root.
- Experiment output uses timestamped purpose directories under `logs/`.
- Build from NCS with west and an explicit board; use pristine builds for
  reproducibility and record the source command/SHA.
- Flash/debug commands use explicit probe identity. Never use `nrfjprog`; use
  west/J-Link workflows appropriate to the target.
- Tags and anchors are OTA-first. Direct J-Link flashing of deployed UWB devices
  requires explicit authorization; the B306/DK remains a separate target.
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
