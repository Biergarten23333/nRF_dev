# Frame and common-heading contract

`Q_i` is the official VQF scalar-first active rotation `R_EiI`: it maps an IMU-local vector into that node's own 6D earth/reference frame. The missing alignment is a world/reference-side transform, `R_GI = Rz(hbar_i) R_EiI`; it is not a fixed right sensor/segment extrinsic.

The operator protocol frame `P` is related by the nuisance `R_GP = Rz(psi_GP)`. Pelvis heading is fixed to zero only to define relative coordinates. That convention does not supply evidence for `psi_GP`, and a surviving `psi_GP` null therefore counts against nine-dimensional identifiability.

Official qmt hinge axes are retained as paired sensor-local RP2 nuisances with antipodal symmetry and cross-covariance. This stage does not call qmt heading correction, does not estimate full `R_IS`, and does not start OpenSense or Phase 4.
