# Task R5 - Dynamic NLOS Solver

Generated: 2026-06-18T01:47:24

Uses rolling range-std NLOS proxy; not the static MLP because dynamic raw windows have different feature support.

| method | median_3d | p95 | rmse | n_frames |
| --- | --- | --- | --- | --- |
| uniform | 104.918 | 277.487 | 156.732 | 40661 |
| soft_nlos | 104.238 | 268.145 | 149.946 | 40661 |
| hard_reject | 105.366 | 278.430 | 155.400 | 40661 |
