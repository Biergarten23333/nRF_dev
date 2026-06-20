# Task P6 - Static-to-Dynamic Gap

Generated: 2026-06-18T02:08:03

Key finding: approx explained listed proxies 30.0 mm of 45.5 mm

| component | estimated_mm | method | notes |
| --- | --- | --- | --- |
| D_tag mismatch | 22.860 | median \|ROTO Dtag_est - static 49.621\| | Upper-bound proxy, not orthogonal contribution. |
| Motion blur | 6.392 | median Vicon speed * 10 ms | Uses nominal poll window. |
| Time alignment recoverable | 0.716 | R1 before-after median improvement | Recoverable portion from offset sweep. |
| Range aggregation / dynamic single-frame | 0.000 | dynamic median relative to 101.5 reference | Proxy only; static subsampling not rerun here. |
| Unexplained | 15.505 | dynamic-static gap minus listed proxies | gap=45.5 mm |
| TOTAL static-to-dynamic gap | 45.474 | median dynamic track error - static best | dynamic=101.5, static=56.0 |

