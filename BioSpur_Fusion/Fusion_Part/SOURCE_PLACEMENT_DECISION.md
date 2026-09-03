# Root-R3 source placement decision

- Canonical repository: `/mnt/nrf_ssd/nRF_dev`
- Recorded base commit: `5bd0a3aa5803921d39f1e7d317762a8f6d7bc3bb`
- Recorded branch: `feature/b306-bringup`
- Development checkout: `/mnt/nrf_ssd/nRF_dev`
- Package: `BioSpur_Fusion/Fusion_Part/src/biospur_fusion/root_r3/`
- Tests: `BioSpur_Fusion/Fusion_Part/tests/root_r3/`
- Generated evidence: `/tmp/biospur_c1_uwb_imu_root_r3_20260824T060622Z/`

The current `BioSpur_Fusion/AGENTS.md` contains a newer workspace rule than
the Root-R3 attachment: Fusion algorithm development must run directly in the
canonical `Fusion_Part`, and a per-attempt worktree is forbidden by default.
This phase therefore uses an isolated new package in the canonical checkout,
does not edit an existing algorithm module, and does not change an import,
runner, configuration, or product default outside that package.

The subpackage name `root_r3` follows the existing component-oriented package
layout. It owns only strict-causal common-root estimation, fault/degradation
policy, C1 replay orchestration, and scientific diagnostics. It reuses the
existing typed-event, common-clock, IMU quaternion, UWB frontend, and frozen
M1/body-geometry contracts rather than duplicating those owners.

The implementation is experimental. Its real-C1 paths fail closed when the
required navigation-to-V4 frame binding is not authoritative. A diagnostic
assumption or yaw sensitivity run must never be relabelled as a qualified
frame transform.
