# Minimal S2 rotation closure

Frozen S2 required `imu_confirm AND (spatial OR strong_gravity)` for release/interruption and defined quiet as `fast_votes < 3`. Thus gyro-only periodic rotation waited for UWB displacement, and the quiet predicate ignored continuing integrated angle. Decaying dwell timers concatenated separated quiet islands, causing the MEDIUM and CYCLE_2 false relocks and LOW/HIGH premature SETTLING.

The focused successor `MotionVetoGate` makes strong gyro, integrated angle, or acceleration evidence a hard veto. It releases through `STATIONARY→MOTION_SUSPECTED→MOVING` without UWB displacement; renewed veto resets the complete quiet/settling dwell; stable UWB cannot force relock; and the existing published lock is immutable. This is a minimal safety repair, not a new position-only architecture campaign.
