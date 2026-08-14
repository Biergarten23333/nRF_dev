# Constrained body-calibration continuation

Top-level verdict: `BODY_MAPPING_CONSTRAINED_PASS_FUSION_READY`.

This result preserves the earlier anonymous analysis unchanged. Operator evidence fixes segment classes; only three binary left/right swaps were optimized over all eight combinations. Inferred sides are not operator-confirmed.

Best bits (elbow,wrist,ankle; 0=expected ordering): `000`; best cost `-95.8044892`, second `-62.5917579`, margin `33.2127312`. Pair leave-one-action stability: `{'elbow': True, 'wrist': True, 'ankle': True}`.

Canonical `UWB_TAG_T4` uses `/mnt/nrf_ssd/nRF_dev/BioSpur_Fusion/B306_Part/deployments/current_room_autopos_20260811_183541/V4IO_LAYOUT.json` SHA `20320e53d48b171c016a0e8d1d93b3cb10e979cf4c21c15c21647d5c0b9878b1` and AutoPos binding commit `87d9027cc368cd05e707dd3a564e4c28b9c505ee`. A single proper `R_body←V4` was estimated from frozen T-Pose/static geometry; anchors were not refit and trajectories were not independently rotated. B0, Q0 and F1 ran separately. F2 remains conditional without approved lengths. F3 is preliminary only for observable relative joints; shoulders are approximated through Central→Elbow and are neither directly measured nor clinically valid. Held-out walk/final_still opened only after freeze `c1337d2a3e556b04ca94fcf4082ddcd918a910a98dcba575d285ed036c94605f`. All reported metrics are self-consistency, not external accuracy.

Raw SHA verified before and after; no hardware accessed.
