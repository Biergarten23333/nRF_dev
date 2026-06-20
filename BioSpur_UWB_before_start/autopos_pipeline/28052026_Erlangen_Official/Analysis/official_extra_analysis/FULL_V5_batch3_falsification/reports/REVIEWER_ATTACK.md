# Reviewer Attack Memo

1. The main static improvements are tuned on only 24 positions. Nested CV and bootstrap optimism must be treated as primary evidence, not auxiliary checks.
2. The Vicon-oracle result does not by itself prove cancellation. It is consistent with cancellation, but phase-center anisotropy and NLOS can also explain part of the gap.
3. The NLOS detector PR-AUC is vulnerable to anchor/position leakage. Leave-one-anchor and anchor-ID-only baselines are the decisive tests.
4. ROTO results are BEST-FIT-ALIGNED, so they cannot support absolute timing or dynamic tracking claims.
5. V5 transferability remains a hypothesis. Batch-2 adversarial rooms showed V4 can win under low vertical spread and high common-mode conditions.
6. The D_tag narrative mixes physical device delay, range percentile choice, and NLOS absorption; the paper must separate these.
