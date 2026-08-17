# Phase 2 consolidated capture, anthropometry and metrology package

This is one session request. Do not identify or report BSF hardware IDs. Keep every sensor and strap in the same donning position for the entire session.

Record the subject-facing convention first: the subject faces the room reference `+X` arrow at the start of every neutral and T-pose reference. Send coarse `START` and `STOP` event markers for every repetition; sensor common time remains the measurement clock. Rest eight seconds between repetitions.

Perform three clearly separated repetitions of each required action: 10 s natural neutral still with sway; 10 s T-pose; left-only then right-only shoulder elevation/sweep; left-only then right-only elbow flex/extend followed by forearm pronation/supination; left-only then right-only hip flexion; left-only then right-only knee flex/extend; trunk flex/extend; trunk axial rotation left/right; and two controlled bilateral squats. Use about 15 s for each motion repetition and finish with a 10 s natural still. Keep feet and facing convention consistent except where the action requires movement.

In the same checklist, record in millimetres: barefoot height; footwear and heel height; left/right upper-arm, forearm, thigh and shank joint-centre-to-joint-centre lengths; shoulder breadth; hip breadth; hip-line vertical offset; C7-to-pelvis reference; left/right foot length; and left/right ankle height. Record strap arrow orientation and distance from named landmarks at all ten attachment sites, plus floor surface and level declaration.

Provide independent device metrology for each board: signed IMU-axis-to-UWB-antenna phase-centre transform, fixture or CAD provenance, translation tolerance and rotation covariance. It must not be fitted from this body capture.

This package resolves the measured pelvis/torso and upper-arm top-K ambiguity, supplies explicit left/right facing evidence, creates untouched complete repetitions for bootstrap and predictive qualification, identifies segment/extrinsic/joint modes, and prevents body capture from silently determining device antenna geometry. Until it is completed, mapping and calibration remain conditional and non-authoritative.
