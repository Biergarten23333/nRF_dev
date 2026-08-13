# JY61P MVP production calibration policy

Primary verdict: **INSUFFICIENT_COHORT_EVIDENCE_USE_IDENTITY_MVP**

## Product decision and inference

Use the identity accelerometer matrix and zero shared accelerometer bias for the next MVP stage.  Keep the two device-specific calibrations as oracle/engineering-characterization references.  This is a reversible MVP decision from two devices, not population-wide validation and not evidence about 10,000 units.

Matrix transfer was evaluated separately from bias by retaining each target device's frozen bias.  Product-realistic rows then removed that privilege: neither C2CC nor 31CC accelerometer bias is copied, and the pooled-bias rows are diagnostic-only.  A single startup pose initializes gravity direction and gyro zero rate; it does not uniquely identify accelerometer bias or scale.

The frozen least-complex guard selected identity because only two devices have full multi-pose characterization and identity is adequate for the next frame-binding experiment.  Even a numerical pooled-average win cannot establish manufacturing-cohort variability.  Shared FULL_SPD off-diagonal terms are rejected: their non-training gain over pooled diagonal was only 0.000409 g on C2CC and 0.001075 g on 31CC, so the predeclared 0.001 g benefit did not transfer to both devices.

## Current evidence

With each target retaining its frozen bias (matrix-transfer diagnostic only), non-training mean RMSE was 0.003565/0.006044 g for identity and 0.001972/0.003244 g for pooled diagonal (C2CC/31CC).  Thus diagonal transfer is promising engineering evidence.

The product-realistic zero-bias comparison is less decisive: identity RMSE was 0.006982 g on C2CC original held-out, 0.007403 g on its six-pose revalidation, and 0.006537 g on 31CC held-out; pooled diagonal gave 0.004401, 0.006719, and 0.003766 g respectively.  A pooled accelerometer bias is rejected: on 31CC it worsened identity RMSE to 0.009085 g.  Neither device bias is copied.

## Runtime policy

Use a 1-second stationary startup interval, subject to motion and accelerometer health gates, to initialize per-session gyro bias and gravity-defined roll/pitch.  Yaw remains unresolved.  Q1 retains causal NIS/norm rejection, gyro-bias tracking, quaternion normalization/sign continuity, and positive-definite covariance checks.  No temperature model is inferred from the narrow existing spans.

Across all accepted pose windows, the 1-second gyro estimate had P95 residual norm 0.007900 dps and maximum 0.015126 dps relative to the full-pose mean.  All Q1 policy replays preserved finite, Cholesky-positive covariance (minimum eigenvalue 7.617e-08), quaternion normalization/sign continuity, and produced 0 numerical/systematic-rejection failures.  Every isolated-transient policy replay retained zero accepted transient samples (0 total accepts) and zero false `MOVING` transitions.

## Evidence boundary

The Q1 comparison covers representative, hash-bound windows from the C2CC stationary capture, interactive rotation, C2CC/3C79 overnight rotation, and all ten nodes in the tabletop capture.  It assesses numerical behavior and measurement acceptance, not absolute attitude or trajectory accuracy; V4 frame binding and external truth are still unavailable.

## Unresolved population uncertainty

Two devices do not establish lot-to-lot dispersion, supplier variability, or a 10,000-unit yield distribution.  Existing held-out data remain retrospective cross-transfer evidence, not a new prospective cohort validation set.  A shared diagonal may be reconsidered only after the stratified future-lot sample passes the same frozen per-device regression rule.  Temperature dependence also remains unknown.

## Manufacturing implication

Do not require 18+4 poses per production unit.  Use the short EOL health screen in `MANUFACTURING_EOL_POLICY.md`; reserve full calibration for characterization samples, new lots/process changes, and failed units.  Proceeding to the C2CC frame-binding experiment does not require calibrating the remaining eight devices.
