# Discretization audit

Frozen Q1 used `Phi=I+F*Δt` and `Qd=LΔt` at approximately 50 ms. In the attitude block, `F=-[ω]x` is skew-symmetric, yet the Euler map has singular values `sqrt(1+(|ω|Δt)^2)>1`. At about 9 RPM this injects exponential covariance energy on every covariance step. The result is neither the exact rotational state transition nor a neutral approximation over 7.284 h.

The repair uses Rodrigues' exact transition for the attitude/gyro-bias block. Its `Qd` is the continuous integral of `Phi(s)L Phi(s)^T`; a five-point positive Gauss-Legendre sum preserves its construction as a sum of PSD terms. Full frame-bound mode uses a 30×30 Van Loan exponential. An independent verifier directly calls SciPy's matrix exponential and does not import either production helper. Symmetrization removes only round-off asymmetry. There is no clipping, diagonal loading, restart, process-noise reduction, or tolerance fitted to this run.
