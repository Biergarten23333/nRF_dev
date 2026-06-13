# Professor L2/L16/L20 Spiky Track Comparison

Generated UTC: `2026-06-11T10:00:02.168219+00:00`

Selected track: `R01/BS2DCE` because B0 pure UWB has the most X-Z jump spikes in the ROTO set.

Presentation convention: this report labels the height axis as `Vertical Z`; internally this uses the official aligned table's `y_vertical` height column.

- B0 X-Z `jump > 200 mm`: `178` samples
- B0 X-Z `jump_p99`: `425.8 mm`
- B0 X-Z `max_jump`: `506.5 mm`
- B0 3D P95: `595.5 mm`
- B0 vertical Z P95: `545.1 mm`

## Experiments

### `X_A0_U4_P4_L2_I5_T3`

- IMU: L2 MPU6050/JY61P-like
- Fusion 3D P95: `188.4 mm`
- P95 improvement vs B0: `407.1 mm`
- Fusion X-Z jump >200mm count: `0`
- Fusion vertical Z P95: `174.8 mm`

### `X_A0_U4_P4_L16_I6_T4`

- IMU: L16 ICM-45686
- Fusion 3D P95: `183.3 mm`
- P95 improvement vs B0: `412.2 mm`
- Fusion X-Z jump >200mm count: `0`
- Fusion vertical Z P95: `174.0 mm`

### `X_A0_U4_P4_L20_I3_T2`

- IMU: L20 Xsens MTi-3
- Fusion 3D P95: `166.6 mm`
- P95 improvement vs B0: `428.9 mm`
- Fusion X-Z jump >200mm count: `0`
- Fusion vertical Z P95: `157.0 mm`

## Figures

- `figs/professor_l2_l16_l20_spiky_track/01_R01_BS2DCE_same_track_XZ_L2_L16_L20.png`
- `figs/professor_l2_l16_l20_spiky_track/02_R01_BS2DCE_err3d_time_L2_L16_L20.png`
- `figs/professor_l2_l16_l20_spiky_track/03_R01_BS2DCE_vertical_Z_error_time_L2_L16_L20.png`

## Table

- `tables/professor_l2_l16_l20_spiky_track/R01_BS2DCE_L2_L16_L20_professor_metrics_verticalZ.csv`