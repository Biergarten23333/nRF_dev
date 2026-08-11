# v47 Fusion dataset exhaustion

Verdict: `DATASET_EXHAUSTED_S2_READY_FOR_NEW_VALIDATION`; architecture: `S2P_AND_S2R_BOTH_REQUIRED`. S1's exact failure was a conjunctive candidate-shift gate plus mutable lock semantics. S2 removes silent creep by construction and adds auditable suspicion/conflict states. All motion results are `DEVELOPMENT_REPLAY_NOT_GENERALIZATION_EVIDENCE`.

Held-out published-lock RMS is 0.000 mm for S2P and 0.000 mm for S2R; this zero is lock semantics, not absolute accuracy. Background-candidate median RMS is 23.027 mm and 23.027 mm respectively. Both use actual hardware timestamps and complete observation accounting. Persistent false table transitions across both modes: 0. The remainder contains 34 unverified MOVING entries and 38 settling interruptions across both modes; without physical truth these remain ambiguous rather than being called false. Full vector inertial propagation remains blocked. The dataset now supports implementation/internal-consistency closure and a frozen next experiment, not motion generalization or human IK/FK accuracy.

## Reproducibility

Two independent full derivation/replay runs produced byte-identical core JSON, CSV, Markdown and SVG outputs. Runtime metadata is not embedded.
