# AutoPos V5 - Key Numbers Reference Card

## Anchor Calibration
- layout: Sim3 scale 0.958, rigid RMSE nan mm, common-mode nan mm, delay spread 60.0 mm.
- layout: Sim3 scale 1.010, rigid RMSE nan mm, common-mode nan mm, delay spread 27.7 mm.

## Tag Delay
- V5 LOO tag delay: 49.621 mm.

## Static Accuracy (24 positions)
- pipeline: median 71.9 mm, P95 176.0 mm, RMSE 110.4 mm.
- pipeline: median 67.8 mm, P95 160.5 mm, RMSE 86.4 mm.
- pipeline: median 56.0 mm, P95 143.1 mm, RMSE 79.5 mm.
- pipeline: median 54.9 mm, P95 154.8 mm, RMSE 79.6 mm.
- pipeline: median 56.3 mm, P95 147.9 mm, RMSE 81.8 mm.
- p30 V5_p50_layout_p50_ranges_DLOO: median 67.8 mm, D_tag 49.6 mm.
- p30 V5_p50_layout_p30_ranges_Dtag_p50: median 47.5 mm, D_tag 49.6 mm.
- p30 V5_p50_layout_p30_ranges_Dtag_p30_LOO: median 59.8 mm, D_tag 33.0 mm.
- p30 V5_p50_layout_p30_ranges_invRMS_Dtag_p30_LOO: median 56.0 mm, D_tag 33.0 mm.
- Percentile sensitivity best tested percentile: 25.0 with median nan mm.

## Dynamic (ROTO)
- A_none_beta0: overall median 557.9 mm, RMSE 606.1 mm.
- B_translation_existing_beta: overall median 83.7 mm, RMSE 108.5 mm.
- C_SE3_existing_beta: overall median 81.7 mm, RMSE 103.8 mm.
- D_Sim3_existing_beta: overall median 74.3 mm, RMSE 94.8 mm.

## NLOS
- Anchor F: rho RMS/std marker 123.0 mm, >100 mm 0.32.
- Anchor D: rho RMS/std marker 106.8 mm, >100 mm 0.19.
- Highest Shapley anchors: H=1246.2, D=1242.9.

## Identifiability / Noise
- Noise-model evidence table winner row: {'model': 'M0_global_gaussian', 'n_params': 1, 'log_likelihood': -1170.5537724438675, 'aic': 2343.107544887735, 'bic': 2346.3650402597627, 'loo_score': -1171.5537724438675}.
- N1: adversarial P(V5<V4)=0.30.
- N2: best 82.6 mm.
- N3: Student-t 95% coverage 0.46.
