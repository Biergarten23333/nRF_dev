# Measurements required to resume Fusion V4

Fill the versioned input
`Fusion_Part/config/body_calibration_v4/v47_subject_anthropometry_v1.json`.
Every value must include uncertainty in metres, landmark definition,
measurement method, and `status: MEASURED`.

Required scalar measurements are left/right upper-arm, forearm, thigh and
shank lengths; biacromial width; hip-centre width; signed pelvis-reference to
hip-line vertical offset; C7-to-pelvis separation; left/right foot lengths;
and left/right ankle-centre heights. Record whether the subject was barefoot
or wearing the measured shoes.

Also measure the antenna-phase-centre-to-landmark vector for every BSF node.
Each vector is expressed in that node's sensor board frame. Do not substitute
V3 fitted dimensions, population averages, or UWB node-to-node distances.

Once this file is complete, V4 can run quotient observability, identical-
residual multi-start, interleaved sampling sensitivity, mandatory/optional
action removal, and model-mismatch checks. Held-out data remains sealed until
a centerline freeze passes all those gates.
