# Frame contract V1

The estimator keeps five non-interchangeable frames:

- `B_i`: sensor/PCB frame for node i;
- `S_i`: anatomical segment frame;
- `N`: physical gravity-aligned navigation frame;
- `V4`: frozen AutoPos relative-geometry frame;
- `H`: session body/display frame.

The attitude relation is `R_NB_i = R_NS_i R_SB_i`. The UWB antenna observation
is `p_ant_N = p_segment_N + R_NS_i lever_arm_segment_i`. There is one shared,
proper `R_N<-V4`; individual T4 trajectories are never rotated independently.

`H` supports anatomical interpretation and display only. It cannot declare
gravity or qualify inertial propagation. `R_N<-V4`, per-session `R_SB_i`, lever
arms and joint offsets must be jointly observable from gravity plus natural
multi-node calibration motion. Unobservable yaw remains an explicit gauge with
uncertainty. A display T-Pose never substitutes for a surveyed navigation
frame.
