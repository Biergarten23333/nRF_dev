# Phase 3-R2 final result — revision 2

Primary verdict remains `STAGE_COMPLETE_NEEDS_CURRENT_SESSION_TIME_EVIDENCE`.

Forward candidate `ae2941501317fec4c1f8ba944e193599885583d0` supersedes only the observability
qualification interpretation from revision 1. It now reports convention-fixed
information separately from gauge-free information. The declared common global
yaw direction is null with rank 29/nullity 1 at every frozen relative tolerance
from 1e-4 through 1e-8. Detached qualification passed 40/40 tests and the pose
core hash stayed byte-identical.

No real FIT, VALIDATION, final-still, B0/B1/P, static-wobble, H, or animation
claim changes: those remain unavailable because the current-session ten-node
strict time gate failed before IMU numeric decode. UWB semantic consumption
remains zero; the co-located transport exposure count remains one.
