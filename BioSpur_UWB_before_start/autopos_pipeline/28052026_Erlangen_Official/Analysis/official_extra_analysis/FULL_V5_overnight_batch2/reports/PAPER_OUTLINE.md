# AutoPos V5: Common-Mode Self-Calibration for UWB Anchor Systems

## 1. Introduction
- Problem: UWB anchor self-calibration has scale-delay identifiability.
- Key finding: common-mode parameterization fixes scale but exposes a cancellation valley.
- Data reference: `reports/KEY_FINDINGS_SYNTHESIS.md`, `tables/n1_solver_verification.csv`.

## 2. System Description
- DWM1001C UWB hardware, broadcast SS-TWR, 8-anchor dual-layer layout.
- Data reference: `solver/outputs/v1_to_v4_io_field_check/*/layout.json`.

## 3. Method: Common-Mode Anchor Delay Parameterization
- V4: bounded `d_i`, `d_A=0` gauge, scale leakage.
- V5: `d_i=c+e_i`, regularized tails, metric-correct scale.
- Data reference: `tables/paper_table_anchor_side.csv`, `figures/fig01_anchor_layout.png`.

## 4. Experimental Setup
- Erlangen MaD Lab, 28 May 2026, Vicon/OptiTrack ground truth.
- 24 static positions, 17 ROTO captures, two ROTO tags.
- Data reference: existing capture metadata and `FULL_V5/tables/*`.

## 5. Results

### 5.1 Anchor-Side Scale Fix
- Use `figures/fig01_anchor_layout.png` and `tables/paper_table_anchor_side.csv`.

### 5.2 Tag Delay Calibration
- Use `figures/fig06_dtag_sweep_curves.png`, `tables/paper_table_dtag_stability.csv`, `tables/n6_percentile_sensitivity.csv`.

### 5.3 Static Positioning Accuracy
- Use `figures/fig02_static_accuracy_trajectory.png` and `tables/paper_table_static_accuracy.csv`.

### 5.4 Cancellation Valley
- Use `figures/fig03_cancellation_valley.png` and `tables/paper_table_transfer_matrix_diagonal.csv`.

### 5.5 NLOS Floor
- Use `figures/fig05_nlos_fingerprint.png`, `tables/paper_table_nlos_per_anchor.csv`, and follow-up `f5_selective_percentile_results.csv`.

### 5.6 Dynamic Tracking
- Use `figures/fig08_roto_floor.png` and `tables/paper_table_roto_summary.csv`.
- Label all dynamic comparisons BEST-FIT-ALIGNED.

## 6. Discussion

### 6.1 V4 wins on this dataset - why
- V4+C_V4 remains the empirical static winner after p30/LOO in `tables/f6_final_comparison.csv` and `tables/n5_transfer_matrix_p30.csv`.

### 6.2 Physical correctness vs empirical accuracy
- V5 fixes anchor-side metric scale; V4 can still benefit from dataset-specific cancellation.

### 6.3 Transferability evidence
- Use corrected MC verification `tables/n1_adversarial_rooms.csv`, not only original P(V5<V4)=1.00.

### 6.4 Practical improvements
- p20/p30 percentile aggregation and inverse-RMS weighting improve static accuracy, but p30 does not transfer to ROTO windows.

## 7. Conclusion
- Common-mode self-calibration improves physical interpretability and scale correctness.
- Residual NLOS/tag-delay structure remains the dominant floor for static and dynamic accuracy.
