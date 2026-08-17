# Gravity and joint-factor equations

For `q_WI`, static positive specific force is
`h(q,b_a)=R_WIᵀ [0,0,g]ᵀ+b_a`. Under a right perturbation
`R_WI exp([δθ]×)`, `∂h/∂δθ=[R_WIᵀg]×`. The ESKF update uses
`y=a-h`, `δx=K y`, and `q←q exp(δθ)`. Independent tests construct truth without
production SO(3) helpers, cover ±5°, ±45°, random orientations, the observed
sensor `+Y` gravity installation, antipodes, permutations, bias and transient
acceleration, and show that reversing the Jacobian sign diverges.

For joint `p→c`, `R_pc=R_WpᵀR_Wc`. Its local perturbation Jacobian has two
nonzero state blocks `[-R_pcᵀ,+I]`. Residuals expressed through `Log` multiply
this by the inverse SO(3) right Jacobian; a finite-difference test covers the
complete parent/child derivative. Temporal articulation penalizes deviation from
the prior relative increment. Elbow/knee factors project that increment through
`I-aaᵀ`; shoulder/hip/trunk use a differentiable soft multi-DOF ROM residual.
Confidence-gated, Huber-robust qmt heading correction adds only the
relative-heading row. Hinge-axis information is likewise confidence-scaled and
soft. Every factor contributes `HᵀWH` to information and is accepted as active
only when its ablation changes the solved pose. A backtracking line search
rejects any nonlinear step that increases the same executable objective.
