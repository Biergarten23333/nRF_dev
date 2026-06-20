# Task G1 - Locked Headline Table

This is the authoritative paper table. Rows D/E are post-selected apparent results and must carry the caveat. Rows F/G are the corrected OOB-bootstrap headlines.

| Row | Variant | Description | median_3d | P95 | RMSE | evaluation_type | paper_location |
| --- | --- | --- | --- | --- | --- | --- | --- |
| A | V4 production | p50, uniform, D=0 | 71.875 | 175.996 | 110.373 | in-sample, all 24 | main_text |
| B | V4 + D_LOO | p50, uniform, D_LOO | 57.921 | 110.585 | 74.372 | LOO-CV | main_text |
| C | V5 baseline | p50, uniform, D_LOO=49.6 | 67.809 | 160.509 | 86.400 | LOO-CV | main_text |
| D | V5 apparent best | p30, invRMS, D_recal=33.0 | 56.011 | 143.120 | 79.482 | in-sample post-selected | main_text_with_caveat |
| E | V4 apparent best | p30, invRMS, D_recal=18.2 | 54.918 | 154.784 | 79.586 | in-sample post-selected | main_text_with_caveat |
| F | V5 corrected | winner's curse adjustment | 65.579 |  |  | OOB-bootstrap | main_text |
| G | V4 corrected | winner's curse adjustment | 64.486 |  |  | OOB-bootstrap | main_text |
| H | V5 bootstrap CI | 95% CI [54.3, 63.7] |  |  |  | bootstrap 95% CI | main_text |
| I | Nested CV (height) | best variant selected on train | 82.925 |  |  | held-out test | appendix |
| J | Nested CV (quadrant) | best variant selected on train | 88.042 |  |  | held-out test | appendix |
| K | Nested CV (spatial6) | best variant selected on train | 94.250 |  |  | held-out test | appendix |
| L | ROTO V5 per-frame | anchor-bridge best-fit | 101.485 | 214.369 | 126.226 | BEST-FIT-ALIGNED | main_text |
| M | ROTO SE(3) aligned | per-capture SE(3) | 82.516 | 185.207 | 103.746 | diagnostic | appendix |
| N | ROTO Sim3 aligned | per-capture Sim3, scale 0.906 | 74.264 | 160.793 | 94.811 | diagnostic only | appendix |
NaN anchor-side metrics were filled from the scale and delay tables:

- V4 rigid RMSE: 105.4 mm
- V5 rigid RMSE: 63.0 mm
- V5 common-mode c: 112.0 mm
