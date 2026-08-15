# Revision D R3D and conditional D0-A/B terminal report

## R3D result

`PASS_R3D_GAUGE_INVARIANT_BROAD_ACTIVITY`

Q2 produces active `R_Ni_Bi` matrices, mapping each board frame into its own gravity-aligned navigation frame. Q2 explicitly reports that absolute heading is unobserved. The destination headings are therefore not established as shared across nodes.

Under independent gauges `R_i' = G_i(alpha_i) R_i`, the historical R3C pair becomes `R_parent^T G_parent^T G_child R_child`. The 1,102-scenario/chain audit found 339 changed records, including parent-only, child-only, opposite-parent/child, and deterministic ten-node injections. Its superseding consumability classification is `FAIL_R3C_PAIRWISE_GAUGE_DEPENDENCE`; this is a frame/model failure, not a subject-motion failure. Historical R3C artifacts and verdict were not rewritten.

R3D uses only `norm(Log(R_i(t-dt)^T R_i(t))) / dt`. The calibration-only replay observed a maximum activity difference of `7.105427357601002e-15 rad/s` and maximum z-score difference of `1.1368683772161603e-13` under deterministic independent yaw injection, below the frozen `1e-12` gates. Chain/node/array/row binding and its corrupt-metadata negative control passed.

All eleven actions have deterministic mask records. Ten have non-empty broad-active rows. `t_pose` has an intentionally empty broad-active mask and a deterministic 20-row static plateau; static actions do not require motion. R3C cycle, direction, sign and zero outputs are not consumed by D0. Exact repetition count is QC-only.

## D0-A result

`D0A_CONTRACTS_FROZEN`

Source accounting corrected the historical 96/56 layout to a minimal-trunk-frame 95/55 layout:

- 29 per-wear coordinates: 20 sensor-to-segment longitudinal-axis coordinates plus 9 effective relative headings;
- 19 subject-functional coordinates: 16 limb-axis coordinates plus one minimal 3-DOF trunk frame;
- 7 non-clinical joint-zero coordinates;
- 40 jointly optimized or mathematically profiled static-pose nuisance coordinates.

The pelvis effective heading is the removed global-yaw gauge. Axial segment twist is not a state or output.

## D0-B result

`FAIL_D0B_SYNTHETIC_NULLSPACE`

One shared synthetic objective includes all eleven actions. All actions have nonzero publishable-parameter Jacobian information. The production residual/Jacobian is finite, the directional Jv error is `8.320616514921442e-11`, every publishable block changes the replay forward output, and two complete synthetic replays are byte-identical with SHA-256 `6eed311858bc872028f7cb4f2347f50d383db5a44c87b3c69f38710bad3e1f82`.

Scientific qualification nevertheless fails. Data-only rank is `72/95`; data plus the separately reported protocol priors is `92/95`. The three zero singular directions are finite combinations of `relative_heading:torso` and `trunk_functional_frame`. The exact blocker is:

`TORSO_EFFECTIVE_HEADING_VS_TRUNK_FUNCTIONAL_FRAME_TRADEOFF`

No threshold was changed and no heading/trunk prior was added after observing this result.

## Plain answers

1. Q2 destination frames shared across nodes? **No.** Gravity axes are aligned, but headings are independently unobserved.
2. Did the old pairwise detector change under independent yaw injection? **Yes.** It is gauge-dependent.
3. Is replacement activity invariant? **Yes, within the predeclared numerical gates; membership is exact.**
4. Are R3C cycle/direction/sign/zero results excluded from D0? **Yes.**
5. Are all eleven actions represented? **Yes as deterministic R3D records.** Ten have non-empty broad-active rows; static `t_pose` is represented by an empty activity mask plus a 20-row plateau.
6. Did D0-A/B start? **Yes, conditionally after R3D PASS.** D0-A contracts were frozen and all eleven actions enter one shared synthetic D0-B objective. No real D0 calculation started.
7. Exact blocker before real D0? **The three-direction torso effective-heading versus trunk-functional-frame tradeoff.**

## Mandatory stop state

```text
REAL_D0_OBJECTIVE = NOT_EVALUATED
REAL_D0_JACOBIAN = NOT_EVALUATED
REAL_D0_SOLVER = NOT_STARTED
MULTISTART = NOT_STARTED
CALIBRATION_FREEZE = NOT_CREATED
REPLAY = NOT_STARTED
RENDER = NOT_STARTED

FINAL_STILL = SEALED
GOLF = SEALED
BOXING = SEALED
WALK = SEALED
UWB/T4/ANCHOR = SEALED
OPERATOR_MEASUREMENTS = SEALED
COMMIT_PUSH = NOT_PERFORMED
```
