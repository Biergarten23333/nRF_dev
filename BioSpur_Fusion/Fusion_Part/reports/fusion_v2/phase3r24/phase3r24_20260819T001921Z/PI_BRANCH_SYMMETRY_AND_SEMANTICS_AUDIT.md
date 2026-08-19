# Pi-branch symmetry and semantics audit

The frozen R2.3 reduced objective has `9` independent GF(2) generators and `512` exact S1 representations. Every accepted factor is modulo-pi, so the per-factor matrix is analytically and numerically invariant.

This does **not** prove 512 full-3D physical basins. R2.3 optimized 65 RP1 starts, then programmatically emitted 512 representatives. All 512 remain `MODE_SUPPORT_INDETERMINATE`; none is promoted to an empirically supported physical basin. For a 3D axis with a nonzero vertical component, a heading pi shift is generally not the same as the antipodal RP2 line.
