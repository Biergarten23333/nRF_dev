# Task M10 - Comprehensive Consistency Audit

Generated: 2026-06-18T01:59:35

Key finding: V5 baseline consistency max delta 0.00 mm

| claim_id | claim_text | confidence | contradicting_evidence |
| --- | --- | --- | --- |
| 1 | V5 fixes scale | strong |  |
| 2 | V4 wins on this dataset | strong | p30 fixed-delay can be lower but is not same deployable calibration |
| 3 | Cancellation valley exists | strong |  |
| 4 | D/F are NLOS-heavy but geometrically essential | moderate | M4 tests whether e_i absorbs NLOS |
| 5 | p30 improvement is cancellation-sensitive | moderate |  |
| 6 | NLOS detectable from range statistics | moderate | feature leakage checks required |
| 7 | Student-t is correct noise model | moderate |  |
| 8 | AA-AT asymmetry is small | moderate |  |
| 9 | Per-tag D_tag varies materially | moderate | static mechanism previously estimated smaller delta |
| 10 | V5 transferability supported by MC | moderate | original P=1.00 was questionable |

