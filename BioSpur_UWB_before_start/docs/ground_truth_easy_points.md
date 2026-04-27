# Easy Ground-Truth Points

If it is hard to invent `XYZ` points manually, use these suggested points derived from the current runtime anchor layout.

Source:

- [ground_truth_points_suggested.json](/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/data/ground_truth_points_suggested.json)

## Recommended First 6 Points

1. `P1_center_floor`
   - `XYZ = (1912, 1942, 0) mm`
   - Place the Tag at the floor center: the intersection of diagonal `A-C` and diagonal `B-D`.

2. `P2_center_mid`
   - `XYZ = (1912, 1942, 776) mm`
   - Same XY as room center, Tag held roughly halfway between lower and upper planes.

3. `P3_center_high`
   - `XYZ = (1912, 1942, 1302) mm`
   - Same XY as room center, Tag held below the upper anchor plane.

4. `P4_AB_mid_floor`
   - `XYZ = (1884, 0, 0) mm`
   - Midpoint of floor edge `A-B`.

5. `P5_BC_mid_floor`
   - `XYZ = (3824, 1852, 0) mm`
   - Midpoint of floor edge `B-C`.

6. `P6_CD_mid_floor`
   - `XYZ = (1940, 3884, 0) mm`
   - Midpoint of floor edge `C-D`.

## Extra Useful Points

- `P7_DA_mid_floor = (0, 2032, 0) mm`
- `P8_under_E_floor = (60, 363, 0) mm`
- `P9_under_G_floor = (3998, 3797, 0) mm`
- `P10_left_face_center_approx = (34, 2102, 776) mm`
  - Physical definition: the approximate crossing/center of the two 3D face diagonals `A-H` and `D-E`
  - This is a very good mid-air reference point if you can hold the Tag with a small frame or rack.
- `P11_right_face_center_approx = (3849, 1857, 776) mm`
  - Physical definition: the approximate crossing/center of the two 3D face diagonals `B-G` and `C-F`
- `P12_front_face_center_approx = (1895, 72, 776) mm`
  - Physical definition: the approximate crossing/center of the two 3D face diagonals `A-F` and `B-E`
- `P13_back_face_center_approx = (1988, 3887, 776) mm`
  - Physical definition: the approximate crossing/center of the two 3D face diagonals `D-G` and `C-H`
- `P14_top_face_center = (1971, 2017, 1552) mm`
  - Physical definition: the approximate crossing/center of the upper face diagonals `E-G` and `F-H`
- `P15_volume_center_approx = (1942, 1980, 776) mm`
  - Physical definition: approximate room volume center from the calibrated anchor layout

## Practical Advice

- You do not need millimeter-perfect physical placement for the first pass.
- Floor points are the easiest because you can mark them with tape.
- A physically defined 3D point like the `A-H / D-E` face-diagonal center is better than an arbitrary free-space point.
- If you can place all four vertical face centers, that is better than many random air points.
- Start from the `6` points above.
- Run one `180 s` capture per point.
- Then compare error patterns before touching the UWB logic again.

## Best 3D Order

If your rack lets you place face-diagonal centers reliably, test in this order:

1. `P10_left_face_center_approx`
2. `P11_right_face_center_approx`
3. `P12_front_face_center_approx`
4. `P13_back_face_center_approx`
5. `P15_volume_center_approx`

This sequence is better than many arbitrary mid-air points because it probes multiple 3D faces and the room center with clear geometry.

## One Example Command

```bash
python3 scripts/run_ground_truth_point.py \
  760186127 \
  /dev/serial/by-id/usb-SEGGER_J-Link_000760186127-if00 \
  --label P1_center_floor \
  --truth-x 1912 \
  --truth-y 1942 \
  --truth-z 0 \
  --duration 180 \
  --skip-sweeps 2
```
