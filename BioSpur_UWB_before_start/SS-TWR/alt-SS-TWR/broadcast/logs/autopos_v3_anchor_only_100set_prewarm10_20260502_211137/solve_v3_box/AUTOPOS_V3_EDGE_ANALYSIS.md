# AutoPos V3 Bidirectional Edge Analysis

source: `SS-TWR/alt-SS-TWR/broadcast/logs/autopos_v3_anchor_only_100set_prewarm10_20260502_211137`

rms_edges=113.3 mm, rms_inlier=70.9 mm

## Worst Residual Edges

- B-D: resid=+472.9 mm, w=0.05, fused=5385, pred=5857.9, dir_delta_mean=-27.3, sigma=21.5, high_bias=False
- B-E: resid=-246.2 mm, w=0.38, fused=5185, pred=4938.8, dir_delta_mean=-2.1, sigma=14.6, high_bias=False
- A-F: resid=-109.8 mm, w=0.85, fused=4812, pred=4702.2, dir_delta_mean=+44.0, sigma=100.8, high_bias=False
- E-F: resid=+101.2 mm, w=0.88, fused=4248, pred=4349.2, dir_delta_mean=-18.1, sigma=20.4, high_bias=False
- A-E: resid=-90.4 mm, w=0.90, fused=1729, pred=1638.6, dir_delta_mean=+11.3, sigma=15.1, high_bias=False
- A-G: resid=+81.3 mm, w=0.92, fused=5540, pred=5621.3, dir_delta_mean=-12.1, sigma=35.7, high_bias=False
- C-D: resid=-75.7 mm, w=0.93, fused=4611, pred=4535.3, dir_delta_mean=+34.9, sigma=15.8, high_bias=False
- C-H: resid=+70.9 mm, w=0.94, fused=4840, pred=4910.9, dir_delta_mean=+20.4, sigma=23.6, high_bias=False
- B-F: resid=+67.9 mm, w=0.94, fused=1554, pred=1621.9, dir_delta_mean=-27.7, sigma=13.4, high_bias=False
- D-F: resid=+64.9 mm, w=0.95, fused=5701, pred=5765.9, dir_delta_mean=-3.8, sigma=15.7, high_bias=False
- A-B: resid=+51.1 mm, w=0.97, fused=4686, pred=4737.1, dir_delta_mean=+34.1, sigma=15.0, high_bias=False
- G-H: resid=-49.5 mm, w=0.97, fused=4570, pred=4520.5, dir_delta_mean=+32.2, sigma=23.2, high_bias=False
- F-H: resid=-41.6 mm, w=0.98, fused=5651, pred=5609.4, dir_delta_mean=+7.1, sigma=13.5, high_bias=False
- B-C: resid=-37.4 mm, w=0.98, fused=3817, pred=3779.6, dir_delta_mean=-28.4, sigma=23.4, high_bias=False
- D-E: resid=-36.1 mm, w=0.98, fused=3327, pred=3290.9, dir_delta_mean=+3.7, sigma=30.4, high_bias=False
- F-G: resid=-35.7 mm, w=0.98, fused=3761, pred=3725.3, dir_delta_mean=+15.6, sigma=23.8, high_bias=False
- C-F: resid=+33.6 mm, w=0.99, fused=3954, pred=3987.6, dir_delta_mean=+28.9, sigma=18.3, high_bias=False
- C-G: resid=-33.1 mm, w=0.99, fused=1651, pred=1617.9, dir_delta_mean=+25.5, sigma=17.9, high_bias=False
- D-H: resid=+33.0 mm, w=0.99, fused=1555, pred=1588.0, dir_delta_mean=+13.9, sigma=27.1, high_bias=False
- E-H: resid=+31.1 mm, w=0.99, fused=2823, pred=2854.1, dir_delta_mean=+6.1, sigma=20.4, high_bias=False
- C-E: resid=+29.5 mm, w=0.99, fused=5658, pred=5687.5, dir_delta_mean=+40.7, sigma=21.2, high_bias=False
- A-D: resid=-24.4 mm, w=0.99, fused=2786, pred=2761.6, dir_delta_mean=-10.0, sigma=27.2, high_bias=False
- E-G: resid=-23.0 mm, w=0.99, fused=5414, pred=5391.0, dir_delta_mean=-20.4, sigma=23.9, high_bias=False
- A-C: resid=-20.9 mm, w=0.99, fused=5469, pred=5448.1, dir_delta_mean=-34.8, sigma=19.4, high_bias=False
- B-H: resid=+17.9 mm, w=1.00, fused=6124, pred=6141.9, dir_delta_mean=+73.3, sigma=17.0, high_bias=False
- A-H: resid=-17.8 mm, w=1.00, fused=3202, pred=3184.2, dir_delta_mean=+19.6, sigma=28.2, high_bias=False
- B-G: resid=+13.3 mm, w=1.00, fused=4171, pred=4184.3, dir_delta_mean=+31.2, sigma=19.9, high_bias=False
- D-G: resid=+6.0 mm, w=1.00, fused=4691, pred=4697.0, dir_delta_mean=-18.4, sigma=20.2, high_bias=False

## Noisy Directions sd > 80 mm

- F->A: sd=241.5, mean=4779.9, med=4829.5, p05/p95=4459/5119, min/max=4411/5794
- A->F: sd=210.8, mean=4823.9, med=4769.5, p05/p95=4384/5088, min/max=4361/5099
- B->G: sd=105.0, mean=4195.8, med=4185.0, p05/p95=4109/4277, min/max=4033/5048
