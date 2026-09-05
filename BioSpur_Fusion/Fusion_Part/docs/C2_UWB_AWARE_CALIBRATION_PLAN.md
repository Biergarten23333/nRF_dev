# Capture2 UWB-aware calibration and propagation plan

## Objective

Use the nineteen accepted Capture2 calibration episodes (00 and 02--19) to
decide whether UWB should contribute only online dynamic corrections or also
shared calibration parameters.  H01/H02 remain sealed hold-outs until a
calibration manifest is frozen.  UWB is consumed as per-link raw range at the
B306 TIMER2 strobe plus `t_round_us / 2`; the historical solved Position is not
an observation.

This is a shadow study.  It cannot mutate the frozen IMU-only Capture2
trajectory or claim external trajectory accuracy.

## Measured time scales

The accepted calibration episodes are each approximately 40.1 s.  Episode 00
through episode 19 spans approximately 22.0 minutes of action time.  Relative
to the capture `FORMAL_T0`, episode 00 begins at approximately 48.9 minutes,
episode 19 at 70.3 minutes, H01 at 71.8 minutes, and H02 at 72.9 minutes.  The
study therefore separates action-to-action propagation over 22 minutes from
long-session bias and thermal age near 49--73 minutes.

## Research basis

- Zihajehzadeh et al., *UWB-Aided Inertial Motion Capture for Lower Body 3-D
  Dynamic Activity and Trajectory Tracking* (IEEE TIM, 2015) used cascaded
  orientation and position/velocity filters plus anatomical knee constraints:
  https://doi.org/10.1109/TIM.2015.2459532
- UMotion (CVPR 2025) tightly couples six body-worn IMU/UWB units, human shape,
  pose and uncertainty in a UKF.  Its released code is research software, not
  a fixed-anchor raw-range replacement for BioSpur:
  https://arxiv.org/abs/2505.09393 and https://github.com/kk9six/umotion
- Group Inertial Poser (ICCV 2025) combines sparse IMUs, UWB inter-sensor
  distances, structured state-space models and a global-translation stage:
  https://arxiv.org/abs/2510.21654 and
  https://github.com/eth-siplab/GroupInertialPoser
- GTSAM supplies maintained factor-graph and IMU-preintegration machinery:
  https://github.com/borglab/gtsam
- OpenSense supplies a maintained OpenSim IMU-calibration/IK reference path:
  https://doi.org/10.1186/s12984-022-01001-x

Commercial inertial systems likewise expose external position aiding rather
than claiming unrestricted drift-free translation.  Xsens documents HTC Vive
and GNSS positional aiding, while Rokoko documents drift reduction and
foot-contact cleanup.  Their estimators are proprietary and do not expose a
BioSpur-compatible raw-UWB calibration pipeline.

## Parameter ownership

| Quantity | Lifetime | UWB authority |
|---|---|---|
| B306 TIMER2 to common time | Per boot segment | Required Gate 0 input |
| Root `R, p, v` | Dynamic | Direct raw-range factor through body-node FK |
| IMU `b_g, b_a` | Slow state | Only when observable over time |
| Node-anchor NLOS bias | Non-negative slow nuisance state | Link-local; never copied to another link |
| `R_N<-V4` and world chirality | Session/static | Shared across all nodes and actions |
| Antenna lever arm | Static with metrology prior | Node-local and observability gated |
| Sensor-to-segment rotation | Static unless slip is detected | IMU/action semantics primary; UWB auxiliary |
| Segment length/joint centre | Subject-static | Shared, prior-bound, observability gated |
| Per-action correction | Forbidden | Would encode the expected answer |

World/root information may propagate through the articulated body graph.
Hardware bias, antenna delay, NLOS bias and node-specific mounting parameters
may not be copied from a healthy node to an unhealthy node.

## Variants and attribution

1. **A -- frozen IMU calibration:** existing baseline, byte-for-byte input.
2. **B -- dynamic UWB only:** frozen static calibration; UWB updates dynamic
   root/inertial state only.
3. **C -- UWB-aware shared calibration:** B plus an explicit allow-list of
   observable world/extrinsic/body parameters.
4. **D -- C plus kinematic propagation:** a healthy subset of UWB nodes anchors
   one articulated posterior; unavailable nodes are propagated with increasing
   uncertainty.
5. **E -- D plus optional single-foot velocity evidence:** separately
   attributed; no foot-position, floor-height or dual-foot lock.

## Prequential policy

For episode `k`, score the prediction made by the posterior from episodes
`< k` before ingesting episode `k`.  Only after recording that score may the
accepted generic factors update the shared posterior.  No threshold, model,
noise scale or calibration owner may be changed after the calibration freeze
and before H01/H02.

Required internal validation:

- eight leave-one-anchor-out predictions;
- ten leave-one-node-out replays;
- whole-limb and 5/30/120 s UWB dropout;
- persistent positive-bias injection on one node-anchor link;
- episode 00 versus episode 17 still-state closure;
- left/right paired-action consistency;
- singular-value/observability evidence for every promoted parameter;
- direct-versus-propagated state labels and covariance at every output epoch.

## Bounded execution ladder

### Phase 0 -- coverage, clock and propagation-availability audit

Read only the nineteen sealed calibration byte slices.  Bind the sealed
Beacon-only/B306-TIMER2 common-clock table, decode raw UWB, and emit an
episode x node x anchor coverage matrix plus 120 ms propagation-availability
bins.  Historical LBD rows may recover the discrete full superframe integer;
LPD and LRD are forbidden and Listener is not a production fusion dependency.
Raw valid masks are transport/geometric coverage, not LOS truth.

Budget: 10 minutes total, 600 s hard process wall, no optimization and no
Hxx access.  Failure stops the ladder.

### Phase 1 -- mechanism qualification

Use deterministic synthetic perturbations with the real timing/anchor/body
geometry, episode 00 stillness, and leave-one-anchor prediction.  Demonstrate
recovery of injected root velocity/orientation/bias errors before real
multi-action calibration.

Budget: each probe <= 60 s; no parameter sweep larger than six predeclared
variants.  Failure produces a causal report rather than another candidate run.

### Phase 2 -- prequential A/B/C/D/E calibration shadow

Use a maintained fixed-lag/preintegration implementation.  Every episode must
finish in minutes, not hours.  A single-episode solve exceeding 120 s or an
aggregate projection exceeding 30 minutes stops before the next episode.

### Phase 3 -- frozen Hxx replay

Hash the selected calibration and thresholds, then open H01/H02 once.
No post-holdout refit.  Without Vicon, results may establish timing,
numerical integrity and internal predictive consistency, not absolute motion
accuracy.

## Phase-0 exit criterion

Phase 0 passes only if all nineteen action slices are exact and Hxx-free, the
sealed Beacon-only table binds all ten B306 TIMER2 models with residual p95
below 0.5 ms and maximum below 1.0 ms, every decoded UWB node belongs to the sealed body
mapping, anchor IDs are A--H, and the output accounts for every decoded UWB
update and link.  Listener Poll/Response data cannot satisfy or override this
gate.  A degraded node is retained as a propagation candidate with increased
uncertainty, but Phase 0 is reported as complete-with-degradation rather than
PASS.  Coverage alone does not promote any UWB link to LOS or any calibration
parameter to observed.
