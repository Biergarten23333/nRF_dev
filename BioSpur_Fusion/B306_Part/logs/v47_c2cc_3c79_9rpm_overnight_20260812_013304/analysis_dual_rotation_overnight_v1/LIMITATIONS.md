# Limitations

The motor was already rotating before formal T0. There is no initial stationary baseline, no bracketed motion onset, no `RPM9_READY`/`ON`, no `OVERNIGHT_GO`, and no operator-confirmed long/short mounting token. Both batteries depleted before motor OFF, so no final stationary recovery exists. These are protocol limitations, not reconstructed facts.

There is no encoder, surveyed radius, angle, home, rigid-arm truth, external attitude truth, or external trajectory truth. V4-io positions, orbit centres, planes, radii, phase and RPM are `NO_EXTERNAL_TRUTH_SELF_CONSISTENCY_ONLY`. The soft printed arm can flex. A larger apparent T4 radius is not promoted to a physical arm assignment.

The current-room geometry is capture-bound and was not refit. Sensor axes, `R_V4_N`, mounting extrinsics and lever arms remain unbound. Real acceleration-to-V4 coupling is therefore disabled. `S2R_QUARANTINED_OFFLINE_ONLY` remains in force. Q1's starting roll/pitch is only a rotating-data local gauge; its gyro bias comes from the frozen independent static baseline.

Listener visibility is an RF diagnostic. BSFC2CC's low Listener count cannot invalidate its complete Fusion-side UWB records or prove motion/depletion by itself. Battery degradation is assigned only where disconnect/reconnect plus uptime-reset evidence supports it.
