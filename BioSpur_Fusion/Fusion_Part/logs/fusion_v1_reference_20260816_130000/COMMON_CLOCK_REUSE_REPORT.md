# Common-clock reuse report

Verdict: `COMMON_CLOCK_PASS`.

Exact raw, canonical, Listener, timing-result, and ledger hashes matched. The 120 ms superframe, ten node IDs, and explicit boot epochs match. Stored coefficients were loaded without refitting. Re-evaluating each in-domain ledger timestamp from `a_ns_per_us * node_timer_us + b_ns` differs from the stored integer timestamp by at most 0 ns. Prior and reproduced timing gates therefore remain worst clean P95 280.852 us and maximum 408.279 us.

The sidecar `/mnt/nrf_ssd/nRF_dev/BioSpur_Fusion/Fusion_Part/logs/fusion_v1_reference_20260816_130000/COMMON_TIME_SIDECAR.npz` contains 7,295,015 IMU and 2,271,712 individual-range rows. Each UWB time applies the affine clock to `strobe_us + t_round_us/2`. Non-accepted clock rows remain present with status. Sidecar SHA-256: `ced0b929cec90c48bdbe7b4049afa880c21572b41bd57100b75eb7532f40f8ea`.
