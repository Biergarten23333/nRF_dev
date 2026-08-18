# Literal staging allowlists

Implementation commit `d238d925a049c68cbce7ff745a8db6645be6a12d` staged exactly
the 44 files shown by `git show --name-only --format= d238d925...`: six frozen
configs, two architecture documents, 21 `imu_pose_v1` source files, 11 Phase3-R
test/support files and four Phase3-R tools. The staged audit reported 2,786
insertions. No glob, `git add .`, `git add -A`, raw, trajectory, GIF, dependency
checkout or cache was staged.

Forward-only strict-gap repair implementation commit
`4f35bbfce16b6efd6ec5fc793d13ee85d17423a5` used the literal allowlist at
`config/fusion_v2/phase3r/STAGING_ALLOWLIST_REPAIR_IMPLEMENTATION.txt`. Its
detached exact-SHA run passed 44 Phase3-R tests and the complete qualification.

The attestation commit allowlist is exactly the files in this report directory:

```text
DATA_ACCESS_SUMMARY.json
FACTOR_ACTIVATION.json
FINAL_RESULT.md
HANDOFF.md
OPEN_SOURCE_COMPARISON.md
QUALIFICATION_SUMMARY.json
REPRESENTATIVE_B0_B1_P.png
STAGING_ALLOWLISTS.md
TEST_RESULTS.json
```

The final forward-only repair attestation uses exactly the literal paths in
`STAGING_ALLOWLIST_REPAIR_ATTESTATION.txt` in this directory.
