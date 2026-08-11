# S2R quarantine

Disposition: `S2R_QUARANTINED_OFFLINE_ONLY`.

The first observed internal position norm above 5 m was approximately 648.318 s; the path later reached 556 m. The obvious cause already established is observation-model inconsistency: per-node/per-anchor median residuals up to about 1.67 m exist in the frozen manifest but S2R does not apply them. Sequential scalar NIS can accept locally plausible biased links; inconsistent accepted corrections create velocity, later links reject, and constant-velocity propagation runs away. The mm→m conversion, one-time hardware-delay subtraction, Jacobian sign/norm, sequential relinearization and Joseph covariance formula were not the primary defect.

No clamp, reset, R inflation, T4 fallback, or raw-range optimization was added. Raw-range coupling returns only after Q1 frame binding and attitude coupling are validated.
