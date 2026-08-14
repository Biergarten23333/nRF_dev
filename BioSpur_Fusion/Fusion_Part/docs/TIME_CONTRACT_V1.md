# Time contract V1

Every typed measurement records `node_id`, `boot_epoch`, `record_type`,
`sequence`, `node_timer_us`, `global_time_ns`, `global_time_sigma_ns`,
`master_arrival_ms`, payload/status, raw record index and byte boundaries.

Authoritative local measurement times are:

- IMU: `base_timer2_us + delta_us`;
- UWB: hardware-captured `strobe_us`;
- `frame_us`: pairing/transport diagnostic only;
- `master_arrival_ms`: receipt diagnostic only;
- `node_ms`: uptime/reset diagnostic only.

For each node and boot segment, `t_global = a_i * TIMER2_i + b_i`. A segment
boundary is mandatory at a TIMER2 reversal, reset evidence or an unresolved
wrap. Within a segment time must be strictly increasing for each source.

The current capture is `COUNT=12`, 10 ms slots and 120 ms Beacon/superframe.
No 110 ms constant from earlier captures is reusable. Listener Beacon records
give an absolute superframe counter; LPD gives poll sequence/source and on-air
phase. Integer selection must be unique against these records and the carried
modulo-16 label. Arrival time may narrow a candidate search but contributes no
fractional timestamp, slope or measurement epoch.

Clock-anchor classifications are recorded as accepted clean, rejected timing
outlier or invalid. The clean set must meet p95 <0.5 ms and maximum <1.0 ms.
All decoded observations are classified as accepted, rejected, invalid,
outside-window, outside-clock-segment or unused-boundary. A future transport
must carry the full Beacon/superframe epoch in every UWB record so integer
selection no longer depends on an external Listener join.
