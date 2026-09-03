# BioSpur pure-IMU V0 C2 basis-set repair and genuine progressive calibration

Document state: **USER REVIEW DRAFT / NOT ACTIVATED**
Scope: **Capture 2 only, all ten IMUs, raw accelerometer + gyroscope only**
Purpose: prevent another run from silently returning to the invalid old model.

## 1. Outcome and authority

The formal task has one combined outcome:

1. replace the invalid C2 basis/model with a human-worn, uncertainty-aware basis; and
2. use that basis in one genuine chronological progressive calibration whose final
   state agrees with a fresh cumulative batch solution.

The basis repair is a prerequisite phase, not a separate deliverable that can be called
complete without progressive calibration. Likewise, progressive bookkeeping on top of
the old basis is not progress and must not be reported as such.

This contract outranks legacy C2 implementation choices and numeric defaults. Historical
logs remain evidence, but no historical synthetic PASS or real fit qualifies the new
architecture. Any conflict must fail closed and name the conflicting file or behavior.

## 2. Activation rule

Until the user explicitly orders the formal task to start:

- no new C2 payload may be opened or hashed;
- no synthetic or real solver may be run;
- no source implementation work may begin;
- only contract review and metadata-only inspection are allowed.

A question, review comment, or request to show files is not a start command. At start,
the worker must produce an immutable `RUN_START_CONTRACT.json` and a passing executable
contract audit before payload access.

## 3. Open-source-first 60-minute bootstrap gate

The first implementation action after user authorization is not a new solver. It is a
bounded reproduction and adaptation of the official MIT-licensed QMT full-body advanced
example:

- repository: `https://github.com/dlaidig/qmt`;
- upstream example: `examples/full_body_tracking_advanced_example.py`;
- required mechanisms: raw gyro/accelerometer orientation estimation,
  `jointAxisEstHingeOlsson`, time-varying `headingCorrection`, hierarchical heading
  propagation, and the upstream box-model viewer;
- authoritative description: constraint-based magnetometer-free full-body 6D motion
  tracking.

The gate has four timed parts:

1. **0–15 min:** pin source revision/license/environment and run the upstream example
   unmodified on its bundled example data.
2. **15–30 min:** preserve the upstream output and verify that corrected trajectories and
   the viewer execute; record every upstream setting that assumes an exact mount, known
   pose, foot/head sensor, or manually chosen sign.
3. **30–50 min:** create only a thin C2 adapter for the ten authoritative nodes, continuous
   timestamps, parent graph, raw units, and qualitative wear regions. Do not rewrite QMT,
   introduce IK, or copy the example's exact alignment vectors/ROM/signs as C2 truth.
4. **50–60 min:** run one bounded C2 baseline and export direct front/side/top images plus
   QMT ratings, selected rows, corrected trajectory hashes, and named incompatibilities.

“Runs” means the open-source pipeline completes, consumes the intended time-varying
outputs, and produces inspectable trajectories/images. It does not mean scientific PASS.
If C2 adaptation cannot complete within the hour, the required result is a precise first
failing boundary and a preserved upstream-vs-C2 comparison—not a silently extended run.

No bespoke basis/progressive architecture implementation starts until this gate exposes
which upstream mechanisms can be reused unchanged and which C2-specific gaps truly remain.
OpenSense may be used only as an external diagnostic reference: its standard workflow
expects preprocessed orientations, a known calibration pose, and inverse kinematics, so it
cannot become the final raw-IMU/no-IK path.

## 4. Exact scope and input firewall

The only scientific dataset is C2:
`phase2_targeted_calibration_20260817t130918z_capture_2_with_joint_label_c8645eb2`.

All ten authoritative nodes must be present exactly once:

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

Allowed measurement fields are raw accelerometer, raw gyroscope, shared timestamps,
status, and boot epoch. The following are forbidden as scientific inputs or rescue paths:

- magnetometer;
- C1, C3, Hxx, Golf, Boxing, or other captures;
- UWB spatial payload;
- vendor, locked, global, or previously fitted quaternions;
- QMT_OFF, shared IK, old profile, freeze, or candidate lock;
- manual pose truth or action-label pose truth;
- viewer/post-hoc rebase, IK repair, or per-action stitching.

Action labels may route measurement factors to relevant functional motion windows. They
may not assert a pose, angle, axis, sign, or desired answer.

Before any future payload access, a fresh metadata-only preselection must bind the exact
identity, append-only wear amendment, frame amendment, current anthropometry, exact
episode attempts/order, source/config hashes, and independent synthetic qualification.
Historical sealed files are immutable and may not be overwritten.

## 5. Wear-direction semantics

The natural-rest priors are exactly:

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
| BSF6C53 | shank_left | anatomical left; lateral shank, not front | ground |
| BSF8BC4 | shank_right | anatomical right; lateral shank, not front | ground |

These are qualitative human-worn direction regions and soft cone priors. They are never:

- exact vectors or a complete three-axis rotation;
- mechanical fixtures or zero-degree equalities;
- hard bilateral mirrors;
- permission to limit a valid mount to an arbitrary small correction from a nominal
  rotation.

The gross opposite-hemisphere guard is a branch/candidate rejection rule. All remaining
sensor-to-segment rotation degrees of freedom must be estimated from full C2 functional
evidence with uncertainty and multiple legal branches. Sensitivity must cover at least
40°, 55°, and 70° cone interpretations without changing the target after seeing results.

## 6. Ownership model

The implementation must separate the following owners.

### 6.1 Measured body geometry

Measured lengths and surface landmarks, with their human/measurement uncertainty, belong
to geometry. V0 does not ask raw IMUs to rediscover absolute bone lengths. Surface breadths
must not become internal joint-center spacing. No torso length, hip-center spacing,
vertical hip offset, symmetry, or centered sensor placement may be invented and treated as
truth.

### 6.2 Functional sensor-to-segment calibration

Each sensor-to-segment rotation and sensor-to-joint/connection displacement must be able
to occupy full three-dimensional space. An axial-on-bone scalar is insufficient for
front-mounted torso/pelvis/thigh sensors and lateral shank sensors. Joint-axis and
joint-center information must use Seel/Olsson/QMT-style functional constraints where
applicable, with human variability, soft-tissue artifact, and sensor noise represented.

### 6.3 Magnetometer-free orientation and heading

The capture owns one continuous six-axis orientation timeline. Episode boundaries are
labels and factor windows, not filter restarts. VQF or an equivalent 6D orientation filter
must not be re-created per action, and an episode must not acquire a new yaw gauge.

QMT parent-child heading correction must consume and propagate the corrected child
orientation trajectory (`quat2Corr`) and time-varying heading correction (`deltaFilt(t)`).
Reducing them to a single mean seed while replay uses the uncorrected trajectory is
forbidden.

QMT joint-axis sample selection must be result-independent and preregistered. It may use
raw excitation, still-noise covariance, conditioning, and the official algorithm, but it
may not search for a few rows that agree with a desired axis. Exact parallelism or exact
zero cross product is never expected from a real IMU or human joint. Low-information rows
are downweighted with covariance; selected indices, effective sample size, excitation
distribution, and rejected-row reasons are preserved. Strong knee evidence must include
the relevant seated leg raise, squat, and heel-to-butt episodes for each side; rest alone
cannot qualify a knee axis.

### 6.4 Relative-heading graph and gauge

The global graph owns exactly nine parent-child relative headings and one pelvis/root yaw
gauge. Initial still may estimate gravity tilt, bias, noise, stillness, and impossible
branches only. It cannot uniquely determine yaw, joint axes/centers, length, axial mount
twist, sagittal branch, or calibration completion.

### 6.5 Branch manager and physical feasibility

Multiple physically legal mount/heading/sagittal branches must survive until cumulative
evidence distinguishes them. Candidate selection first removes physical impossibilities,
then compares evidence among the remaining legal candidates. A lower residual invalid
body may never displace a legal candidate.

Nontradeable branch rejection includes verified knee front/back split, crossing, mirror
flip, disconnected or inconsistent shared joints, collapse, invalid ROM, implausible
gravity/topology, or loss of proper rotations. These are manifold/candidate gates, not
soft penalties that a lower sensor residual can buy through.

### 6.6 Progressive information state

One persistent state consumes every complete
rest → transition → action → transition → rest episode in chronological order. It retains
all prior evidence, covariance/information, surviving branches, weights, bias/drift state,
and conflicts. Prefixes are posterior diagnostics of that state, not separate fits.

Progress is derived from gauge-reduced information/rank, uncertainty, branch
concentration, physical validity, and held-out evidence. It is not action count, sample
count, elapsed time, or optimizer iterations. Progress may decrease when later evidence
reveals conflict. Inconvenient actions remain; only disproven hypotheses are pruned.

The final progressive state must match a fresh full chronological all-episode batch within
preregistered, synthetic-qualified uncertainty bounds.

## 7. Threshold and consequence policy

Every predicate must declare one of four consequence classes before execution:

### A — architecture/provenance run blocker

Examples: wrong capture, identity/alias conflict, forbidden input, payload access before
seal, active per-episode reset path, missing required ownership, non-immutable authority,
disk/firewall violation, or a corrupt/non-finite computation boundary.

Consequence: stop the **current run**, preserve evidence, diagnose and repair. It does not
terminate the overall task or justify a scientific FAIL.

### B — physical candidate/branch rejection

Examples: anatomical crossing, front/back knee split, collapse, broken topology, invalid
ROM, wrong wear hemisphere, improper rotation, or disconnected shared joint.

Consequence: reject that candidate only and continue other legal branches. If none remain,
preserve the state and perform causal diagnosis/pivot; do not relax the physical rule.

### C — stochastic evidence weighting or uncertainty

Examples: low excitation, QMT rating, static correlation, sensor noise, soft-tissue
artifact, approximate wear direction, uncertain landmarks, or local held-out residual.

Consequence: adjust covariance/information/branch weight or mark uncertainty/conflict.
These observations do not by themselves stop the run or delete the episode.

### D — numerical/sample diagnostic

Examples: isolated near-axis rows, duplicate quantized samples, a local solver warning, or
one uninformative block.

Consequence: log and continue unless a separately declared Class A integrity invariant is
violated.

No local threshold may stop the overall goal. Thresholds must be derived from sensor noise,
covariance, probability, physical manifold membership, or independently randomized
synthetic evidence. Threshold relaxation after observing a failure is forbidden. Boundary
sensitivity must show that small plausible perturbations do not flip the final verdict
without a corresponding uncertainty change.

## 8. Qualification before real C2 fitting

Synthetic truth must be model-independent at the model-class level, not merely stored in
a different file. Qualification must randomize:

- full 3D off-axis sensor positions and broad continuous cone-respecting mounts;
- human dimensions, asymmetry, joint-axis/center variation, imperfect motions, and
  soft-tissue artifact;
- gyro/accelerometer bias, noise, quantization, static correlation, dropped/duplicated
  samples, time-varying yaw drift, and boundary gaps;
- action ordering, degeneracy, full-circle/multibranch starts, and imperfect returns to
  rest.

Mandatory negative mutations must reject or expose:

- front/back knee split despite correct left/right ordering;
- per-action VQF/QMT reset or per-action calibration stitching;
- cross-capture sharing, candidate lock, collapse, leaked truth, exactized wear priors,
  fake count-based progress, and false initial-still completion;
- a lower-residual invalid candidate when a legal higher-residual candidate exists;
- three-dimensional off-axis placements collapsed to axial scalars;
- time-varying QMT correction collapsed to one constant;
- result-directed cherry-picking of a few gyro rows.

Real C2 fitting is forbidden until all architecture mutations pass. A failed mutation is
a diagnostic event requiring a model/parameterization/solver pivot, not a reason to run
the same real solve harder.

## 9. Bounded execution and causal pivot

Every stage declares iteration, wall, multistart, and worker limits before running. On
timeout, zero finite candidates, stall, or failed visual/held-out evidence, the worker must:

1. preserve the exact configuration, traces, candidates, and images;
2. identify the failing owner and earliest causal divergence;
3. visually inspect the available direct-FK output;
4. consult primary sources when the mechanism is unclear;
5. run one bounded hypothesis-discriminating test;
6. make an in-scope architectural, parameterization, or solver pivot;
7. rerun only the smallest gate that tests that pivot.

Blind restart, eight-hour unattended solve, action deletion, threshold relaxation, local
residual tricks, or terminal unsupported FAIL are forbidden.

## 10. Visual evidence is a development gate

The viewer consumes the same raw-path corrected orientation trajectories and fixed
measured geometry as the solver. It may not use IK, repair, smoothing rescue, manual pose
truth, or post-hoc rebase. It must render front, side, and top views for:

- initial standing;
- representative upper-body motion;
- representative left- and right-lower-body motion;
- squat and final standing.

The worker must inspect the actual images, and an independent monitor must inspect them
again. Reported booleans are insufficient. One knee forward and one knee back, crossing,
collapse, disconnected joints, or gross torso folding invalidates the candidate and
requires diagnosis and continuation.

## 11. Required final evidence

A deliverable verdict must include:

- immutable authority, source, configuration, start-contract, and artifact hashes;
- file-access firewall and exact payload-access history;
- code changes and tests actually executed;
- independent synthetic cases and negative mutations;
- progressive information/uncertainty/branch/held-out curves;
- final progressive-versus-fresh-batch comparison;
- all used measured parameters and all fitted parameters with uncertainty;
- named conflicts, rejected branches, timeouts, and causal pivots;
- direct-FK front/side/top viewer paths and explicit human visual findings;
- disk growth and preserved historical FAIL/raw evidence.

PASS requires all hard provenance, architecture, physical, synthetic, held-out, batch
equivalence, and visual obligations. INCONCLUSIVE must name the unresolved evidence and
must not masquerade as PASS. FAIL must be scientific and evidence-backed; an ordinary
timeout, implementation defect, or invalid candidate is not a terminal scientific FAIL.
