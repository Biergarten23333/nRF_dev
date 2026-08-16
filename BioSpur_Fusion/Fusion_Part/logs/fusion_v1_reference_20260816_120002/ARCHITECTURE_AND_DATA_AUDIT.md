# Fusion v1 architecture and data audit

Status: **Stage A partially complete; no scientific fitting performed.**  
Generated: 2026-08-16. New implementation: `Fusion_Part/fusion_v1`.  
Machine evidence: `STAGE_A_MACHINE_AUDIT.json`.

## 1. Repository state

The parent worktree is `/mnt/nrf_ssd/nRF_dev`, branch
`feature/b306-bringup`, starting HEAD
`412233adcb0a5a8551f2a5d1085c79b8c2c26ae5`. The relocated git directory on
`/mnt/DatenBankHDD` was mounted and resolved. The worktree was already heavily
dirty with unrelated modifications, deletions, and untracked files. This work
does not modify or import the historical Fusion estimator or its logs.

## 2. Raw-data inventory

One full-body raw recording was found:

| Recording | Time evidence | Duration | Raw source | Integrity |
|---|---:|---:|---|---|
| `v47_ten_node_body_calibration_20260814_093601` | formal T0 monotonic 159270.593388 s; end 160662.971235 s | 1392.378 s after T0 | `continuous_collector/fusion_host_raw.cobs.bin` | 224,739,075 B; SHA-256 `a491520739400064db520377ec87a9331feb6274cd42a7e6d9aad57a2b93d56a` |

The file contains 1,234,999 complete host records, one complete record with a
CRC mismatch, and 129 unterminated EOF bytes. The EOF tail is quarantined and
does not create an observation. There are no separate repeated recording files;
repetitions are actions inside this continuous capture. Listener JSONL/raw logs
are independent passive-listener diagnostics, not the body-node IMU/range
source. Historical `.npz`, CSV, plots, and derived ledgers are excluded as
scientific inputs.

## 3. Node and anchor inventory

All ten operator-facing nodes appear in both raw modalities:
`BSF1120`, `BSF31CC`, `BSF3C79`, `BSF44AD`, `BSF6C53`, `BSF8BC4`,
`BSFAA61`, `BSFB165`, `BSFC2CC`, and `BSFEC35`.

The capture binding reports: Central `BSF31CC`; Pelvis `BSFC2CC`; left/right
elbow `BSFAA61`/`BSF1120`; left/right wrist `BSFB165`/`BSFEC35`; left/right
knee `BSF44AD`/`BSF3C79`; left/right ankle `BSF6C53`/`BSF8BC4`. Several side
assignments were data-inferred rather than directly operator-confirmed and must
be rechecked against action evidence.

Eight fixed anchors A--H have IDs 0--7. The independently frozen V4-io
coordinates, in metres after conversion from the source millimetres, are:

| ID | x | y | z |
|---:|---:|---:|---:|
|0|0|0|0|
|1|4.301493|0|0|
|2|4.194083|2.989279|0|
|3|0.152116|2.684975|0.129386|
|4|0.197543|-0.064244|1.625698|
|5|4.291337|-0.090289|1.603431|
|6|4.151368|3.098146|1.748947|
|7|0.180817|2.665645|1.847950|

The geometry is relative-world geometry and has a reported inter-anchor-pair
RMS of 56.49 mm. It is not re-fit from this body capture.

## 4. Raw-schema verification

The new decoder independently implements the transport facts and imports no
old Fusion module. Complete records are COBS-delimited and decode to a
little-endian `<HBBHHIQ` envelope with magic `0x5342`, envelope version 1, and
CRC-16/CCITT-FALSE. The CRC implementation passes the standard `123456789 ->
0x29B1` vector. Selected decoded binary fields were compared directly with the
readable CDC representation.

IMU kind 3 has a 14-byte header `<BBHQh>` followed by 1--16 samples of
`<Hhhhhhh>`. Physical sample time is `base_us + delta_us`; axes remain raw
signed int16 values because scale and sensor/body conventions have not yet been
independently established.

UWB kind 1 has exactly 184 payload bytes. It preserves sweep, DW40 poll TX,
identity, anchor IDs/ranks, ranges in mm, measured `t_round_us`, quality,
CFO-q8, validity mask, frame time, and hardware strobe time. The table emits
one observation per anchor and retains invalid observations.

## 5. Timing model

Each node's IMU and UWB share that B306's free-running TIMER2 clock. Median IMU
spacing is exactly 5,000 us for all nodes. Long positive gaps exist, up to
1.755 s in the current simple adjacency audit; gap causes and boot epochs still
need exact accounting. No USB `master_arrival_ms` value is used as physical
measurement time.

For an individual SS-TWR range the provisional physical epoch is
`strobe_us + t_round_us/2`, retaining the measured anchor-specific interval.
The cross-node common-clock mapping is not yet accepted. It will unwrap each
node's carried modulo-16 superframe label, use the 120 ms superframe and
assigned 10 ms TDMA slot as common events, and robustly fit each TIMER2 epoch
to that schedule. IMU times then inherit the node fit. Clean P95 <0.5 ms and
maximum <1.0 ms are gates; otherwise fitted uncertainty is propagated. The
canonical `common_time_us` column is deliberately blank until this passes.

## 6. Available calibration actions

Manual/action-log bounds exist for initial still (two attempts), T-pose, arm
raises, left/right elbow flexion plus forearm rotation, left/right knee raise,
left/right heel raise, squats, multi-planar trunk motion, walk, golf swing,
final still, and boxing. Bounds are manual or automatic upper bounds, not exact
motion labels; dynamic intervals require refinement from raw IMU evidence.
Initial-still attempt 1 and right-elbow attempt 1 are superseded/excluded.

## 7. Anthropometric information

No independently measured subject anthropometry was found in the factual
capture binding or raw manifest. Existing generated calibration objects are
prohibited inputs. Segment lengths therefore require measured priors or
identifiability analysis from development actions; unsupported lengths must be
held to broad documented priors rather than reported as observed.

## 8. Proposed skeleton topology

Ten instrumented segments: pelvis, torso, bilateral upper arms, forearms,
thighs, and shanks. The pelvis is the root. Soft joints connect pelvis--torso,
torso--upper arms, upper arms--forearms, pelvis--thighs, and thighs--shanks.
Hands, feet, and head are uninstrumented and cannot be claimed as directly
reconstructed. Shoulders are virtual because no direct shoulder-centre sensor
exists.

## 9. Proposed state vector

At time knot k: root SE(3) pose and linear velocity; segment-relative SO(3)
coordinates for the nine child segments; optional joint angular velocities;
and per-node gyro/accelerometer biases. Node positions are generated only by
forward kinematics and capture-level sensor extrinsics. There are no ten free
XYZ trajectories.

## 10. Proposed capture-level parameters

Segment lengths with uncertainty; left/right-specific joint-centre offsets;
nominal segment-to-sensor SE(3) transforms with finite mounting covariance;
dominant elbow/knee functional axes with dispersion; node-specific IMU noise
and bias random-walk scales; pair-specific UWB base scales/bias diagnostics;
and floor height only if contact evidence supports it.

## 11. Proposed factors

Fused batch initialization uses gravity-consistent accelerometer evidence,
gyro low-motion statistics, articulated geometry, multiple robust raw ranges,
and broad pose priors. Dynamic estimation starts with direct asynchronous range
factors, transparent gyro-integrated orientation evidence, soft joint-centre
and physiological priors, and temporal motion factors. Direct gyro factors and
then a sensor-location accelerometer model are staged additions. Contact is not
enabled until the basic estimator works.

## 12. Uncertainty strategy

All quantities use SI units internally. No numerical scientific weights are
frozen yet. IMU covariances will be node-specific and estimated from development
low-motion intervals. UWB scales will be node-anchor-specific and conditioned
on range, geometry, and health, with robust heavy-tail loss. Joint centres,
functional axes, mounting transforms, static sway, and contacts all have finite
covariance. Sensitivity analyses must accompany selected priors.

## 13. UWB health strategy

Each node-anchor pair has a visible confidence state driven by standardized
innovation, missingness, jump evidence, and sustained signed residual. Strong
inconsistency reduces confidence rapidly; recovery requires repeated consistent
measurements and is slower. Invalid/rejected rows remain in diagnostics with a
reason. No single range can overwrite a state.

## 14. Human soft-constraint strategy

Capture-level segment lengths are structurally constant. Joint-centre closure,
dominant elbow/knee axes, ranges of motion, sensor attachment, floor, and
contact are soft probabilistic terms. Secondary elbow/knee motion, multi-DOF
shoulder/hip/trunk motion, breathing, sway, and skin motion are not clamped to
zero. Per-frame sensor extrinsics and bone stretching are disallowed.

## 15. Development, validation, held-out split

The frozen manifest is `fusion_v1/config/data_split.json`. Development uses
static/T-pose and functional limb/trunk actions; validation uses walk and final
still; golf swing and boxing are action-level held-out. This is weaker than an
independent recording split and must be reported as such. Held-out rows will
not be selected or inspected by scientific code before model/metric freeze.

## 16. Implementation stages

Stage A parser/canonical lineage is implemented. Remaining Stage A work is the
accepted cross-node clock fit, exact interval refinement, gap/epoch accounting,
and raw-scale verification. Then: pairwise UWB characterization; independently
unit-tested articulated FK; static fused initialization; minimal range plus
orientation smoother; direct gyro factors; accelerometer dynamics; contacts;
and calibration refinement. Complexity is retained only after ablation.

## 17. Identified risks

Only one subject/capture; action-level leakage risk; no measured anthropometry;
some left/right mappings inferred; weak vertical anchor geometry; relative
world gauge; 474,585 invalid range rows; one CRC failure and an incomplete EOF
tail; pronounced node-dependent IMU gaps; possible clock epochs; manual action
bounds; no external motion-capture reference; and absent JAX/GTSAM. SciPy is
the initial framework because NumPy/SciPy are installed and sparse support is
available, while JAX and GTSAM are absent.

## 18. Exact open questions resolvable from data

1. Which node/stream epochs explain each timing and sequence discontinuity?
2. Does the modulo-16 TDMA fit meet the 0.5/1.0 ms clock gate per node/epoch?
3. Are inferred left/right wrist, elbow, and ankle mappings supported by raw
   action-specific angular energy without using held-out actions?
4. What are the raw accelerometer/gyro scale, axis signs, gravity direction,
   bias, correlation time, and sampling jitter per node?
5. Which manual action bounds contain usable functional motion and still tails?
6. What pair-specific UWB bias, spread, autocorrelation, tail rate, and recovery
   are supported by development intervals?
7. Which segment/extrinsic/axis parameters are identifiable without manual
   anthropometry, and which remain prior-dominated?
8. Can the minimal articulated range-plus-orientation estimator remain world
   anchored without copying UWB high-frequency jitter?

## Canonical evidence

`CANONICAL_OBSERVATIONS.csv.gz` contains 9,566,727 rows: 7,295,015 IMU
samples and 2,271,712 individual ranges. There are 474,585 invalid observations
preserved with reasons. Its SHA-256 is
`836ee43e3a86f818ff4bc954a7111e4f4111a3f7693047b84811571cb48332cd`.
Every row carries source path, record index, byte range, encoded-record hash,
node, anchor when applicable, raw values/units, native time, sequence, validity,
parser version, and transport-arrival diagnostic.

