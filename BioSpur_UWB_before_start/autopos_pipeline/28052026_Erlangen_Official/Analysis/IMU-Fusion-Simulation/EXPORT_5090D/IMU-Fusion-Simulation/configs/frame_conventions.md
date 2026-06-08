# Frame Conventions For Phase 0/1

Generated for the first IMU-fusion vertical slice.

## World Frame

The OptiTrack/Vicon and official ROTO sample tables use:

```text
x_mm
y_vertical_mm
z_mm
```

`y_vertical_mm` is treated as the vertical-up axis.

Gravity in world coordinates:

```text
g_world_mm_s2 = [0, -9806.65, 0]
```

## Body / IMU Frame

Phase 1 uses the fitted wand-body frame when TRC body markers are available.
For the first vertical-slice solver rows, position-domain IMU diagnostics are
computed on the already aligned OptiTrack antenna-point trajectory. This keeps
the time/alignment contract fixed while the full attitude/gyro strapdown path is
being brought up.

## Accelerometer Convention

Specific force is defined as:

```text
f_world = acceleration_world - g_world
```

When attitude is used, the body-frame value is:

```text
f_body = f_world expressed in the IMU/body frame
```

## Phase 1 Limitation

`T11` drift diagnostics in the first vertical slice are position-domain
diagnostics derived from the fixed Vicon/OptiTrack antenna-point trajectory.
They validate drift generation and time/frame consistency, but they are not yet
the final full attitude/gyro strapdown implementation.
