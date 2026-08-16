# Invariants I-01–I-13

1. **I-01:** usable frozen IMU-only baseline before production UWB fusion.
2. **I-02:** target is usable articulated pose with all UWB absent; only absolute position/global yaw may drift if Phase 3 proves no additional segment/subtree heading modes.
3. **I-03:** IMU is high-rate motion/orientation authority; UWB is low-rate world constraint.
4. **I-04:** estimate orientation and gyro/accelerometer bias dynamics; Q1 is never hard state, truth or FK driver.
5. **I-05:** final state is articulated body, never pelvis XYZ only.
6. **I-06:** final estimator has true-time gyro propagation, accelerometer specific-force/gravity likelihood, bias/random walk, stillness/contact evidence and evidence-gated soft ZUPT.
7. **I-07:** anatomical bone lengths fixed within calibrated session; joint centres/axes/extrinsics/lever arms have finite uncertainty and soft constraints.
8. **I-08:** UWB never overwrites orientation/joint angles, hard-resets state or uses a fixed percentage blend.
9. **I-09:** bad UWB is gated by current IMU/articulated prediction and uncertainty; dropout/relock is graceful.
10. **I-10:** all IMU/range measurements retain true asynchronous time; TIMER2/common clock is precise, host arrival is not.
11. **I-11:** freeze/report IMU-only first, then prove UWB improvement with isomorphic A/B; convergence is not validation.
12. **I-12:** source/config/tests live in auditable tracked Fusion_Part, never only logs.
13. **I-13:** no perfect hinges/still/contact/extrinsics/joint centres and no PCB-to-PCB pseudo bone lengths.
