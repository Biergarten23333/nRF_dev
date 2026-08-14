# Fusion architecture V2

The only qualified data path is:

```text
immutable raw
  -> typed event ledger
  -> Listener-backed strict common clock (Gate 0)
  -> Q1 IMU frontend + canonical UWB_TAG_T4 frontend
  -> functional/session calibration and immutable freeze
  -> one joint articulated fixed-lag estimator
  -> observable IK/FK records
  -> downstream UI (out of scope here)
```

## Ownership and gates

`Fusion_Part` owns every stage after B306 transport decoding. The transport
envelope decoder may remain a B306 interface dependency, but it cannot assign
scientific time, frames, covariance, body topology or acceptance. The canonical
T4 solver binding and its delay/Anchor identity guards live in Fusion; only the
frozen solver implementation itself remains under the read-only UWB baseline.
No algorithm implementation newly owned by Fusion is added under `B306_Part`.

Gate 0 maps each explicit boot segment's TIMER2 domain to a single Listener
Beacon epoch. It uses on-air Beacon/LPD evidence, poll sequence, modulo-16
superframe evidence and the capture's measured 120 ms superframe. Host or
Master arrival time is association diagnostics only. Gate 0 requires a unique
integer epoch, clean p95 residual below 0.5 ms, clean maximum below 1.0 ms, no
valid-segment reversal and exact accounting. Failure stops real-data execution
before UWB position, calibration, held-out access or body estimation.

After Gate 0, Q1 publishes orientation, bias, preintegration and causal motion
evidence; it does not publish ten independent final positions. UWB_TAG_T4
publishes position observations with geometry/Jacobian/residual/time-derived
covariance. Every update is predicted and gated before mutation. Rejection is
byte-stable.

The body state is one kinematic tree. Static sensor extrinsics, antenna lever
arms, joint-centre offsets and segment lengths are calibration parameters;
after the calibration freeze they are immutable by construction. Dynamic
states are root pose/velocity and segment orientations or equivalent joint
coordinates. UWB antenna factors, IMU preintegration, orientation/gravity,
joint-centre coincidence, hinge/ball-joint, ZUPT/contact and physical
smoothness factors meet in the same fixed-lag solve. A correction at one node
therefore propagates through shared body coordinates rather than renderer
lines.

Walk and final-still are held out. Their files are not opened until the
calibration manifest has been serialized and hashed. No threshold, covariance,
frame, assignment, extrinsic or body dimension may change afterwards.

The capture has no external motion truth. A PASS can establish provenance,
timing, numerical integrity, fixed geometry and internal held-out consistency;
it cannot establish absolute trajectory accuracy or clinical joint angles.
