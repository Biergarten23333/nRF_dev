# Overnight dual-node trajectory plot method

These figures are an offline visualization of canonical T4 positions in the frozen relative V4-io frame. V4 Z is not asserted to be physical vertical. Fits, radii, centres, planes, RPM and phase are `SELF-CONSISTENCY ONLY`; no absolute accuracy or ground-truth claim is made. BSF3C79 has the larger apparent radius, but the missing mounting token prevents calling it the confirmed long-arm node.

Only `[0, 26222.1428196) s` (`7.283928561 h`) is admitted. The battery-depletion/reconnect tail is excluded. T4 uses the canonical frozen solver, geometry and delay parameters. S2R and Fusion are not run.

The frozen orbit fit is reproduced: finite positions are trimmed only for fitting at the deterministic 99th percentile of distance from the componentwise median; PCA supplies the plane and an algebraic least-squares circle supplies centre/radius. Residual metrics are then evaluated on every finite nominal point, including visible scatter. Fixed one-hour causal windows have no smoothing and the last window is partial. Centre displacement is relative to the first valid window.

The shared comparison plane comes from the combined within-node-centred scatter matrix, preventing centre separation from dominating its normal. Its normal is oriented toward positive relative V4 Z; projected V4 X defines positive u. Both trajectories are projected about one common origin, so relative position and phase are preserved.

All metrics and fits use complete valid nominal data. Rendering alone uses fixed-stride decimation: at most 12,000 points in each individual plot and 9,000 per node in the combined plot. There is no random sampling or trajectory smoothing. Individual 3D panels share the same equal-sided cube, individual plane panels share the same limits, and all 2D orbit panels use equal aspect. PNG is 300 DPI; SVG remains editable.
