# BioSpur pure-IMU V0 — C2 basis repair and genuine progressive calibration

Document state: **USER REVIEW DRAFT / NOT ACTIVATED**
Planned duration after activation: **12–18 hours maximum**
Scientific scope: **Capture 2 only; ten IMUs; raw accelerometer + gyroscope**
Execution scope: **one English sole-writer WORK task plus one Chinese read-only monitor**

## 1. Required outcome

The task has one combined outcome:

1. replace the invalid over-free/over-idealized C2 basis and parameterization;
2. produce one capture-wide, magnetometer-free, human-worn calibration state;
3. update that state genuinely and chronologically over every complete C2 episode;
4. match a fresh full chronological all-episode batch within independently
   qualified uncertainty;
5. pass preregistered held-out evidence and direct front/side/top pixel inspection.

Basis repair is not complete without the genuine progressive result. Progressive
bookkeeping on the old basis is not progress. A plausible-looking viewer is not a
scientific result, and a low residual is not permission to accept an invalid body.

## 2. Activation and authority

Until the user explicitly starts the formal task:

- do not create the WORK or monitor tasks;
- do not implement or modify solver/source code for the task;
- do not run synthetic or real calibration;
- do not open or hash any new C2 payload;
- do not render new C2 outputs;
- only review and amend this contract package.

A discussion, question, approval of one clause, or request to show these files is
not a start command. On activation, the WORK task must create a fresh immutable
`RUN_START_CONTRACT.json`, bind every reviewed contract hash, and pass the
contract audit before payload access.

Contract amendments after activation are append-only. Historical seals, reports,
FAIL evidence, raw evidence, and previous contracts are never overwritten,
deleted, renamed to hide history, or repaired in place.

## 3. Workspace, writer, and resource gates

The only authorized working tree is:

`/mnt/nrf_ssd/nRF_dev/BioSpur_Fusion/Fusion_Part`

Rules:

- no branch, worktree, nested worktree, checkout copy, repository copy, or raw-data copy;
- all implementation occurs in the canonical path;
- the English WORK task is the sole writer during the formal run;
- the Chinese monitor is strictly read-only in `Fusion_Part` and may only send
  direct STEER messages to WORK;
- no periodic monitoring messages are posted into the present planning chat;
- generated evidence belongs under one new timestamped `logs/` run directory;
- source/config/test changes remain in their established canonical directories;
- before start: at least 100 GB free on `/mnt/nrf_ssd`, 40 GB free on root, and
  projected total growth no greater than 5 GB;
- preserve desktop responsiveness; broad CPU work uses at most 8–10 workers;
- no single blind/uninspected compute call may run longer than 30 minutes;
- no stage may exceed its declared phase budget without an append-only causal report.

## 4. Exact C2 dataset scope and file-access firewall

The only scientific capture is:

`datasets/phase2_calibration/phase2_targeted_calibration_20260817t130918z_capture_2_with_joint_label_c8645eb2`

All ten nodes must be present exactly once:

| Hardware ID | Segment |
|---|---|
| BSFEC35 | forearm_left |
| BSFB165 | forearm_right |
| BSFAA61 | upper_arm_left |
| BSF1120 | upper_arm_right |
| BSF31CC | torso |
| BSFC2CC | pelvis |
| BSF44AD | thigh_left |
| BSF3C79 | thigh_right |
| BSF6C53 | shank_left |
| BSF8BC4 | shank_right |

Legacy aliases or conflicting left/right assignments fail closed and name the
conflicting authority. C1, C3, Hxx, Capture3, Golf, Boxing, and any other capture
are forbidden. No cross-capture fitted state, parameter, prior, candidate, or
warm start may enter the task.

Allowed scientific fields are raw accelerometer, raw gyroscope, timing, boot
epoch, sequence/status, and the minimum transport metadata required to decode
those fields. Forbidden fields and rescue paths include:

- magnetometer;
- UWB ranges, anchors, or spatial payload;
- vendor/locked/global/prior quaternions or Euler angles;
- QMT_OFF, shared IK, old fitted profile, candidate lock, freeze, or latent warm start;
- manual pose truth or action-label pose/angle/axis/sign truth;
- per-action profile stitching;
- viewer-derived rebase, IK, repair, or pose correction fed back to fitting.

Before any payload open or hash, WORK must create:

1. immutable `RUN_START_CONTRACT.json`;
2. executable contract-audit PASS;
3. fresh metadata-only C2 preselection;
4. exact action-directory allowlist;
5. exact payload byte/range access plan;
6. authority, source, config, anthropometry, wear-amendment, and frame-amendment hashes.

The allowlist is constructed from the authorized action directories only. It may
not scan, open, stat for content, hash, or enumerate `holdout/` paths to construct
exclusion ranges. The following are forbidden by default even as metadata:

- `holdout/00_walk`;
- `holdout/H01_boxing`;
- `holdout/H02_golf`.

Any external holdout access requires a later, exact, separate user authorization
after the fitted state and all tuning choices are frozen. Default final held-out
evidence must instead be preregistered result-independent withheld rows/blocks
inside the allowed C2 action episodes. Held-out evidence is opened only after fit
freeze and may not trigger refitting.

## 5. Authorized episodes and chronology

One persistent C2 state consumes every complete authorized episode in recorded
chronological order. The expected action identities are:

`00_initial_still`, `02_t_pose`, `03_pelvis_hula_circle`,
`04_shoulder_left`, `05_shoulder_right`, `06_elbow_left`,
`07_elbow_right`, `08_hip_left`, `09_hip_right`,
`10_knee_left_seated`, `11_knee_right_seated`,
`12_heel_raise_left`, `13_heel_raise_right`,
`14_trunk_flex_extend`, `15_trunk_axial_rotation`, `16_squat`,
`17_final_still`, `18_heel_to_butt_left`, `19_heel_to_butt_right`.

Each complete episode retains rest → transition → action/hold → transition → rest.
Episode labels may route functional factors but may not state the desired pose,
angle, axis, sign, branch, or answer. Inconvenient episodes are never removed.
Only hypotheses may be eliminated.

Left knee evidence includes left seated raise, squat, and left heel-to-butt.
Right knee evidence includes right seated raise, squat, and right heel-to-butt.
Rest alone cannot identify a knee axis or heading.

## 6. C2 → QMT input contract

The future run binds, rather than rediscovers, these verified facts:

- Capture 2 reports the same production `b306-imu-relay-v47` lineage on all ten nodes;
- B306 reads JY61P at I2C address `0x50` from register `0x34`;
- AX/AY/AZ/GX/GY/GZ are decoded as signed little-endian values;
- B306 performs no raw-axis permutation or sign flip;
- return rate is configured and read back as 200 Hz;
- bandwidth is configured and read back as 98 Hz;
- accelerometer conversion is `raw / 2048 * 9.80665` m/s²;
- gyroscope conversion is `raw / 16.384` deg/s, then converted to rad/s;
- JY61P acceleration range switches internally from 2 g to 16 g when needed,
  while the output register convention remains the documented ±16 g formula;
- gyroscope output convention is fixed ±2000 deg/s;
- accelerometer specific force including gravity is retained;
- vendor quaternion/Euler output is not consumed;
- QMT quaternion storage is `wxyz`; exact active/passive direction is bound by a
  source-level numerical round-trip test before first trajectory consumption.

These facts are not new hard qualification experiments. No new six-face or ±90°
hardware capture is required. Existing C2 still-gravity, gyro-bias/noise, finite
values, timestamp monotonicity, and saturation checks are diagnostics. They block
only if they reveal actual severe corruption or clipping, not ordinary sensor noise.

Time handling:

- every node owns one capture-wide streaming 6D orientation state;
- episode boundaries never instantiate a new VQF/QMT state or yaw gauge;
- recorded timestamps are authoritative; a nominal 5 ms grid is not permission
  to fabricate samples;
- missing rows are no-update events with propagated uncertainty;
- long gaps are never linearly filled;
- interpolation is forbidden by default and may be enabled only after independent
  synthetic qualification, preregistration, covariance inflation, and sensitivity;
- within a contiguous no-gap hardware-trigger run, the QMT equidistant time input
  may use the verified 200 Hz trigger cadence without inventing a sample; measured
  timestamps still own span boundaries and gap detection. If cadence consistency
  fails, the adapter must report it and use a qualified resampling/wrapper path;
- gap diagnostics are evaluated only where the relevant factor/view needs data;
- a gap outside an authorized factor/view window cannot invalidate the entire capture.

## 7. Wear-direction authority

Natural-rest priors are exactly:

| Node | Segment | sensor -Z approximately | sensor -Y approximately |
|---|---|---|---|
| BSFEC35 | forearm_left | anatomical left | ground |
| BSFB165 | forearm_right | anatomical right | ground |
| BSFAA61 | upper_arm_left | left-rear, posterior-dominant | ground |
| BSF1120 | upper_arm_right | right-rear, posterior-dominant | ground |
| BSF31CC | torso | forward | ground |
| BSFC2CC | pelvis | forward | ground |
| BSF44AD | thigh_left | forward | ground |
| BSF3C79 | thigh_right | forward | ground |
| BSF6C53 | shank_left | anatomical left; lateral shank | ground |
| BSF8BC4 | shank_right | anatomical right; lateral shank | ground |

The two non-collinear directions may construct a right-handed **nominal** frame
through orthonormalization/cross product. This is allowed and avoids inventing
axis ambiguity already resolved by the user and register semantics.

However, the directions are qualitative cones, not exact vectors. The nominal
frame must retain angular uncertainty and multiple legal branches. It is never:

- a mechanical fixture;
- an exact sensor-to-segment rotation;
- a hard bilateral mirror;
- a small ±25° correction lock around a fabricated standard mount;
- a reason to reject an otherwise legal cone-respecting functional estimate.

The prior is a broad, non-compact directional distribution over physically plausible
human-worn orientations, not a hard numeric cone. Broadness sensitivity is frozen
without real residuals and must include a near-uninformative hemisphere-supported
case; the favorable setting may not be selected after viewing the answer. Only a
grossly wrong wear hemisphere is a nontradeable branch rejection.

## 8. Architecture and state ownership

The dependency order is mandatory:

1. decode and propagate one continuous six-axis orientation per node;
2. estimate pairwise functional hinge axes/centers and sensor-to-segment branches;
3. express the joint constraints in the frames required by official QMT;
4. run time-varying parent-child heading correction;
5. propagate the nine corrected relative headings through the kinematic tree;
6. update the one chronological progressive posterior and finally compare it with
   a fresh full batch.

Heading correction may not run on invented anatomical axes merely to get ahead of
functional calibration. Conversely, functional calibration may consume raw
accelerometer/gyroscope and capture-wide sensor orientations without waiting for a
completed anatomical viewer.

This is an integration of mature mechanisms, not an invitation to invent a new
motion-capture theory. The implementation hierarchy is pinned official QMT public
functions and advanced-example data flow; Olsson plug-and-play hinge-axis selection
and uncertainty; Seel/Olsson joint-position constraints where their assumptions
hold; then only the smallest transparent adapter required by C2 timing/gaps. A new
global nonlinear solver is forbidden unless a bounded test first demonstrates a
specific capability absent from those mechanisms, names that capability, and keeps
the reference path as a comparator.

### 8.1 Persistent six-axis orientation owner

One streaming VQF/equivalent 6D state per node spans the capture. It owns tilt,
gyro bias/noise, orientation propagation, and named no-update uncertainty. It does
not own anatomical axes, joint centers, absolute yaw, body geometry, or progress.

The stochastic input model must not presume an ideal chip. Besides additive noise
and bias, it accounts for or performs preregistered sensitivity to quantization,
scale-factor error, cross-axis/non-orthogonality, slow bias/temperature drift,
timestamp jitter, dropped/duplicated rows, and clipping. Ordinary values widen
covariance or reduce information; only directly observed severe corruption is a
current-run blocker.

### 8.2 Gap-aware QMT heading-observation owner

After the functional joint/frame inputs for an edge are available, pinned official
QMT is used unchanged inside preregistered dense contiguous valid spans. QMT
`headingCorrection` outputs — `deltaFilt(t)`, corrected child quaternion, rating,
state, and selected-row/debug evidence — are preserved and consumed.

"Official" applies to algorithm implementation, not to blindly copying the example
subject's parameters. Every upstream exact alignment, manual flip/sign, heading
offset, ROM table, `startRating`, `stillnessRating`, stillness threshold, sample
window, and optimizer setting is audited and registered. Example-specific values
are not C2 truth. In particular, upstream startup/stillness rating 1 cannot turn the
initial rest into a known heading; it is mapped to C2 uncertainty and the initial
still remains low-information.

Because the upstream batch function has no missing-row mask, the C2 adapter must
not force one illegal compressed timeline and must not omit heading correction.
Instead:

1. continuous VQF orientations remain in the capture-wide sensor gauges;
2. result-independent dense valid spans are supplied to official QMT;
3. QMT time-varying outputs become timestamped relative-heading observations;
4. one persistent edge state retains/filter-weights them chronologically;
5. gaps produce no-update plus covariance growth, not a new calibration;
6. no per-span output is concatenated as an independent per-action profile;
7. the final corrected trajectory is generated by parent-to-child tree propagation
   of the persistent edge corrections, matching the official advanced-example
   `delta[child] = delta[parent] + deltaFilt[child]` ownership;
8. factors, refinement, progressive state, held-out evaluation, and viewer consume
   that same corrected trajectory.

If the pinned API cannot support a required mechanism, WORK must run a bounded
source-audited hypothesis test and implement the smallest stateful wrapper or use a
primary-source-equivalent magnetometer-free relative-heading mechanism. It may not
silently disable heading, compress gaps, average `deltaFilt` to one seed, or stop the
overall task on this ordinary integration problem.

### 8.3 Functional sensor-to-segment owner

Each of ten sensor-to-segment rotations is a full SO(3) state/branch constrained by
functional C2 evidence and broad directional wear distributions. Each sensor-to-joint/connection vector is
three-dimensional in the correct sensor/segment frame. Axial-on-bone scalar models
are forbidden for front-mounted torso/pelvis/thigh and lateral shank sensors.

These states may remain uncertain or partially unidentifiable; the estimator must
report gauge and weak directions instead of manufacturing exact mounts. The human
model likewise includes soft-tissue motion, slow strap slippage, non-ideal or
slowly varying hinge axes, joint-center migration, imperfect movement, and
anatomical asymmetry as covariance, robust likelihood, or sensitivity—not as
per-action mount profiles and not as exact-equality failures.

Use mature mechanisms first with attribution:

- QMT/Olsson functional hinge-axis estimation;
- Seel-style joint-axis and joint-center constraints;
- functional connection/center ideas where applicable;
- uncertainty for imperfect human joints, soft-tissue artifact, and sensor noise.

Sample selection is preregistered, result-independent, noise/covariance aware, and
fully recorded. Searching 600 rows for a few desired rows, exact axis parallelism,
exact zero cross product, or result-directed threshold choice is forbidden.

### 8.4 Measured and functional geometry owner

Raw anthropometric observations remain unaveraged provenance. V0 does not ask IMUs
to rediscover absolute bone lengths. It also does not silently equate:

- external biacromial breadth with glenohumeral-center spacing;
- bicristal/bitrochanteric breadth with internal hip-center spacing;
- pelvis-sensor-to-chest-sensor distance with anatomical torso length;
- surface segment chords with exact internal joint-center lengths.

Any mapping from surface landmark to internal geometry must cite a primary method,
declare every required input, propagate human/observer uncertainty, and retain
asymmetry. Missing geometry remains uncertain; it is never filled by a hidden
standard adult, hard symmetry, centered sensor, fixed 6 cm offset, or zero spacing.

The exact available limb-scale observations are not optional background context:

| Quantity | Left observations | Right observations | Meaning |
|---|---:|---:|---|
| Forearm | 245 mm | 245 mm | lateral epicondyle → styloid midpoint surface distance |
| Upper arm | 310, 325 mm | 310, 325 mm | acromion → lateral epicondyle surface distance |
| Thigh | 480 mm | 480 mm | greater trochanter → lateral femoral epicondyle surface distance |
| Shank | 430 mm | 430 mm | lateral femoral epicondyle → malleolar midpoint surface distance |

The original anthropometry file records a second forearm observer's 260–265 mm
range without a side label. The user subsequently clarified in
`USER_ANTHROPOMETRY_AMENDMENT_001.json` that this same reported range applies to
both left and right forearms. Therefore each side retains its original 245 mm
observation plus the bilateral second-observer 260–265 mm range. The range may
not be collapsed to a convenient midpoint, used to erase the 245 mm readings,
or relabeled as exact internal bone length. "Both sides" resolves provenance;
it does not impose hard equality on latent internal joint-center lengths.

Other exact external observations are biacromial breadth 400/425 mm, chest sensor
to acromion line 140/150 mm, pelvis-sensor to chest-sensor 280 mm, bicristal
breadth 335/315 mm, bitrochanteric breadth 335 mm, pelvis AP depth 200 mm, and
chest-sensor to head vertex 470 mm. Their landmark definitions and permitted roles
are fixed in `GEOMETRY_AND_PARAMETER_CONTRACT.json`.

The authority currently supplies neither instrument resolution nor a numerical
measurement uncertainty. Zero uncertainty, hard equality, or a convenient guessed
sigma is forbidden. Before first real fit, a nonzero measurement-plus-landmark-
mapping uncertainty model must be frozen without viewing fit residuals. Absolute
link length cannot be a free IMU-fit coordinate. Raw readings remain separate; a
surface-to-internal mapping or explicitly labeled landmark-proxy skeleton anchors
scale and receives sensitivity analysis.

Internal shoulder centers, internal hip centers, anatomical torso length, and foot/
floor geometry are not directly measured. In particular, bicristal breadth is not
ASIS breadth, so the available record is insufficient to execute a Harrington hip
regression as if all inputs existed. The former 0.06 m hip vertical offset, 0.22 m
internal hip spacing, 0.18/0.22/0.26 m hip sensitivity treated as truth, and eight
axial sensor offsets are not authorized parameters.

Every value affecting the real fit or viewer must appear in a sealed
`ACTIVE_PARAMETER_REGISTRY.json` with owner, unit, source, status, uncertainty,
bounds/manifold, consumers, consequence class, and sensitivity/identifiability
test. A source/config/default scan must report zero unregistered parameters. An old
numeric value does not become authorized merely because it exists in
`config/biospur_fusion_v0_c2_basis/config_v1.json` or historical source.

### 8.5 Relative-heading tree owner

The connected kinematic graph is exactly a rooted tree. It owns nine parent-child
relative-heading time series and one pelvis yaw gauge. The primary implementation
is official-QMT-style edge correction plus parent-to-child propagation, not a
bespoke global nonlinear heading solve. Initial still provides gravity tilt, bias/noise/stillness, and impossible
branch rejection only. It cannot uniquely determine yaw, mount twist, joint axes,
joint centers, body length, sagittal branch, or completion.

The nine directed parent → child edges are exactly:

1. pelvis → torso;
2. torso → upper_arm_left;
3. upper_arm_left → forearm_left;
4. torso → upper_arm_right;
5. upper_arm_right → forearm_right;
6. pelvis → thigh_left;
7. thigh_left → shank_left;
8. pelvis → thigh_right;
9. thigh_right → shank_right.

No extra latent edge, duplicated side edge, direct pelvis-to-distal shortcut, or
disconnected per-limb gauge is allowed.

### 8.6 Branch and physical-feasibility owner

Multiple physically legal heading/mount/sagittal branches survive until cumulative
evidence prunes them. Physical feasibility filters candidates before residual
ranking. A lower-residual invalid candidate cannot displace a legal candidate.

Nontradeable candidate rejection includes:

- one verified standing knee forward and the other backward;
- left/right crossing or mirror flip;
- disconnected or inconsistent shared joints;
- limb or torso collapse;
- invalid ROM or improper rotation;
- implausible gravity/topology;
- branch geometry incompatible with the declared uncertainty manifold.

Human ROM is a probabilistic, subject-variable manifold, not a precise mechanical
stop copied from the QMT example. Small or plausible boundary excursions change
weight/uncertainty. Only grossly impossible motion after declared uncertainty and
sensitivity can reject a candidate; topology failures such as knee front/back split,
crossing, collapse, improper rotation, or disconnection remain hard candidate gates.

These reject a candidate, not the overall task. If no legal candidate remains, the
worker preserves evidence, finds the causal owner, pivots, and continues.

### 8.7 Genuine progressive information owner

One persistent state retains all prior episodes, information/covariance, biases,
drift, branches, conflicts, and weights. Prefixes are posterior snapshots of that
same state, not separate fits or stitched profiles.

Progress is based on gauge-reduced information/rank, uncertainty contraction,
branch concentration, physical validity, and prequential prediction scored before
the next episode is ingested. It is never sample count, action count, elapsed time,
or optimizer iteration. Progress may decrease when later evidence exposes conflict.
The final sealed held-out blocks are not opened or included in progress until the
fit and all choices are frozen; afterward they only evaluate the frozen result and
cannot trigger refitting.

The final progressive state must agree with an independently initialized fresh full
chronological batch within synthetic-qualified uncertainty bounds.

## 9. Open-source diagnostic status and reuse boundary

The completed bounded diagnostic established:

- official QMT v0.2.4 at revision
  `0fa8d32eb461e14d78e9ddbd569664ea59bcea19` is pinned under MIT;
- the literal advanced example runs, with upstream default heading correction off;
- a source-literal wrapper with heading enabled produces time-varying
  `deltaFilt(t)` and corrected child trajectories;
- C2 conversion, raw-axis order, `wxyz` round trip, continuous VQF finiteness,
  still gravity, and absence of saturation are usable;
- the diagnostic did not execute C2 heading correction and its zero-width viewer
  was anatomically invalid;
- the diagnostic accessed forbidden holdout metadata and is not strict scientific evidence.

Therefore no additional standalone one-hour open-source task is required. The new
main task begins with a reusable QMT integration/synthetic phase, not another
throwaway baseline and not a blind restart of the old solver.

Historical diagnostic code/results may be read only after the new seal and only to
identify mechanisms or act as an isolated A/B display comparator. They cannot become
fit truth, warm start, candidate lock, accepted geometry, or qualification evidence.

## 10. Controlled A/B/C causal visual test

Before interpreting real images, the task must stop changing algorithm and renderer
simultaneously. One renderer and one declared geometry/gauge configuration compare:

- A: historical failed-profile trajectory before display retarget/rebase;
- B: continuous capture-wide 6D VQF trajectory;
- C: genuinely time-varying QMT/global-graph corrected trajectory.

All three use identical timestamps, frames, geometry, root gauge, projection, and
camera. No measured-proportion retarget, torso rebase, shoulder-line yaw rescue,
per-action correction, IK, or smoothing repair is allowed. A is diagnostic only and
cannot influence fitting.

The renderer exposes two clearly separated products:

1. **sensor/segment-frame diagnostic:** axes and graph connectivity without claiming
   anatomical joint centers;
2. **scientific anatomical FK:** posterior functional joint centers/connections and
   fixed measured geometry with uncertainty, using the same corrected trajectories
   as the solver.

Zero shoulder/hip branch spacing is forbidden because it causes collapse by
construction. An invented 24 cm internal hip spacing is also forbidden. Before
internal geometry is qualified, display-only external-landmark spans may be shown
with explicit proxy labels and uncertainty, identically for A/B/C, but cannot count
as PASS evidence.

Required real views include front/side/top for initial standing, representative upper
motion, left and right lower motion, squat, and final standing. WORK and monitor must
inspect actual pixels. Reported booleans are not evidence.

## 11. Threshold and consequence policy

Every predicate is preregistered as one of four classes.

### Class A — current-run architecture/provenance blocker

Examples: wrong capture/node mapping, alias conflict, forbidden source/field, payload
access before seal, holdout firewall violation, per-episode state reset, immutable
authority mismatch, disk gate failure, actual severe parse corruption, non-finite
state boundary, or missing required owner.

Consequence: stop the current run only, preserve exact evidence, repair the cause,
create a new sealed run if necessary, and continue the overall task. Class A alone is
not a terminal scientific FAIL.

### Class B — physical candidate/branch rejection

Examples: crossing, knee front/back split, mirror flip, collapse, invalid ROM,
improper rotation, disconnected shared joint, or wrong wear hemisphere.

Consequence: reject that candidate only. Continue legal branches. Never soften a
physical impossibility into a residual tradeoff.

### Class C — stochastic evidence/uncertainty

Examples: low excitation, QMT rating, soft-tissue artifact, static correlation,
landmark uncertainty, ordinary sensor noise, or local held-out conflict.

Consequence: modify covariance/information/branch weight or report uncertainty. Do
not stop, delete the episode, or tune the threshold after seeing the answer.

### Class D — numerical/sample diagnostic

Examples: isolated near-axis row, quantized duplicate, local warning, one
uninformative block, a failed multistart, or a temporary visual failure.

Consequence: log and continue unless a separately declared Class A invariant fails.

Thresholds must derive from covariance, sensor noise, probability, physical manifold
membership, or independently randomized synthetic evidence. Exact thresholds are
fixed before real fitting and receive sensitivity checks. Threshold relaxation,
action removal, result-dependent sample choice, or repeated restart is forbidden.

Human/IMU imperfection is never itself a threshold failure. Near-axis samples,
low excitation, imperfect rest return, soft-tissue artifacts, mount micro-motion,
quantization, bias drift, timestamp jitter, and non-ideal hinge behavior are normal
stochastic/model-error evidence. They reduce weight, widen uncertainty, or preserve
multiple branches. Only provenance corruption or a demonstrated physical
impossibility has a hard consequence, and that consequence remains scoped to the
current run or candidate as defined above.

## 12. Synthetic qualification before real fitting

The synthetic oracle must be independent at the model-class level; it may not call
the estimator's geometry/residual implementation to generate truth.

Positive cases randomize:

- broad cone-respecting SO(3) mounts and arbitrary 3D sensor offsets;
- human dimensions, asymmetry, joint-axis/center variation, imperfect motion, and
  soft-tissue artifact, slow strap slippage, axis migration, and non-ideal hinges;
- accelerometer/gyro bias, scale-factor and cross-axis error, slow drift, noise,
  quantization, static correlation, timestamp jitter, clipping edge cases,
  dropped/duplicated rows, timing gaps, and yaw drift;
- episode order, imperfect rest return, degeneracy, and full-circle/multibranch starts.

Mandatory negative mutations detect/reject:

- one-knee-forward/one-knee-back despite correct left/right identity;
- per-action VQF/QMT reset or profile stitching;
- cross-capture sharing, old-profile warm start, candidate lock, or leaked truth;
- exactized wear directions or hard bilateral mirrors;
- three-dimensional offsets collapsed to axial scalars;
- time-varying heading collapsed to one mean seed;
- result-directed selection of a few gyro rows;
- lower-residual invalid candidate displacing a legal candidate;
- zero-width/collapsed viewer geometry and post-hoc rebase rescue;
- fake count-based progress and false initial-still completion;
- progressive final state disagreeing with a fresh full batch.

All architecture mutations pass before the first real C2 fit. A failure triggers a
bounded causal pivot inside the task; it does not justify running real data harder.

## 13. 12–18 hour phase and wall-budget contract

| Phase | Maximum elapsed budget | Required result |
|---|---:|---|
| P0 seal/input/firewall | 0.5 h | immutable start contract, validators, allowlists, resource gates |
| P1 continuous frontend + stochastic/synthetic harness | 2.0 h | one state/node, non-ideal sensor/human models, no-update tests, mature-method applicability map |
| P2 functional SO(3)/3D geometry | 4.0 h | pairwise axes/centers/connections and segment-frame branches plus independent mutations |
| P3 QMT heading + nine-edge tree/branches | 4.0 h | official time-varying edge correction, tree propagation, legal branches, synthetic PASS |
| P4 controlled A/B/C | 1.5 h | identical-renderer causal views and earliest divergence report |
| P5 real C2 progressive | 4.0 h | one chronological state, traces, prefixes, conflicts, corrected real views |
| P6 fresh batch/held-out/final QA | 2.0 h | equivalence, frozen held-out, pixel QA, hashes, final verdict |

Total planned maximum is 18 hours. A phase finishing early donates time to later
phases; a phase failure does not end the task. Before every solver call, record its
iteration, wall, multistart, and worker limits. Preserve convergence traces at useful
intervals and inspect at least every 30 minutes. No eight-hour blind solve is allowed.

On timeout, stall, zero finite candidates, failed mutation, failed image, or held-out
conflict, WORK must:

1. preserve configuration, traces, candidates, and images;
2. identify the owner and earliest causal divergence;
3. inspect the direct output;
4. check primary source/official documentation if unclear;
5. run one bounded hypothesis-discriminating test;
6. make an in-scope model/parameterization/solver pivot;
7. rerun only the smallest gate that tests the pivot;
8. continue until the total task budget or genuine terminal condition.

## 14. Final evidence and verdict

Final evidence includes:

- authority/source/config/start-contract/artifact hashes;
- complete file-access audit and firewall report;
- exact code changes and tests executed;
- input-contract proof and all unit/axis/time/quaternion semantics;
- independent synthetic cases, seeds, positive metrics, and negative mutations;
- per-stage limits and convergence traces;
- all measured parameters, mappings, fitted parameters, covariance/uncertainty;
- exact raw anthropometry, frozen scale/mapping likelihood, and sealed active
  parameter registry with a zero-unregistered-parameter scan;
- nine-edge heading trajectories/ratings and corrected-trajectory hashes;
- progressive information/rank/uncertainty/branch/physical/prequential curves,
  followed separately by post-freeze held-out evidence;
- final progressive-versus-fresh-batch comparison;
- named conflicts, rejected branches, timeouts, causal analyses, and pivots;
- front/side/top paths plus explicit WORK and monitor pixel findings;
- disk growth, free space, and preserved historical evidence.

Permitted terminal labels:

- **PASS:** every provenance, architecture, synthetic, physical, progressive,
  batch-equivalence, held-out, and visual obligation passes.
- **INCONCLUSIVE:** total budget ends with named unresolved scientific evidence or a
  dependency needing new user authority. It must state what is working and what is not.
- **FAIL:** only a valid, qualified pipeline produces evidence that scientifically
  falsifies the target. Timeout, implementation defect, invalid candidate, missing
  wrapper, bad viewer, or ordinary solver failure is not terminal scientific FAIL.

No false PASS and no unsupported terminal FAIL are acceptable.

## 15. Explicitly forbidden shortcuts and repeated mistakes

- patching/restarting the old 17D/axial solver as the final architecture;
- treating a human or IMU as a perfect mechanical fixture;
- exact-vector wear priors or hard bilateral symmetry;
- selecting a few desired rows from hundreds;
- treating static samples as independent votes;
- assuming initial still identifies yaw/axes/centers/twist;
- per-action VQF/QMT restart, calibration, profile, or gauge;
- discarding `quat2Corr` or averaging `deltaFilt(t)` to a seed;
- zero shoulder/hip spacing, hidden standard-adult geometry, or surface-to-internal substitution;
- old 0.06 m hip vertical offset, 0.22 m internal hip spacing, axial-only sensor
  offsets, or any unregistered historical numeric default;
- viewer retarget, torso rebase, shoulder-line yaw rescue, IK, or repair;
- using old viewer appearance as pose truth;
- allowing residual ranking before physical feasibility;
- relaxing thresholds or deleting actions after failure;
- scanning forbidden holdout metadata to build exclusions;
- outsourcing pixel inspection to booleans or another task;
- stopping the overall task on a routine diagnostic failure;
- claiming data/setup failure without a validated pipeline and direct evidence;
- claiming progressive completion before fresh-batch and held-out agreement.

## 16. Communication and independent monitoring

After explicit activation only:

- create one new English WORK task as sole writer;
- create one separate Chinese monitor/steer/correction task as strict read-only;
- WORK and monitor bind this exact contract package and start time;
- monitor uses compact snapshots and does not narrate unchanged state;
- monitor sends direct STEER to WORK on any material deviation;
- monitor independently opens actual generated images and checks pixels;
- periodic messages remain inside the monitor task, not this planning chat;
- attention is requested from the user only for new authority, destructive action,
  true external dependency, or a material scientific choice not fixed here.

This contract package remains reviewable and editable until the user explicitly
activates it. Activation freezes hashes; later changes are append-only.
