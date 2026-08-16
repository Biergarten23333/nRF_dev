# Reuse audit

All five controlling identities match. Acquisition infrastructure is reused; old body science is rejected.

| Classification | Artifact | Rationale |
|---|---|---|
| `REUSE_DIRECTLY` | `/mnt/nrf_ssd/nRF_dev/BioSpur_Fusion/Fusion_Part/logs/fusion_v1_reference_20260816_120002/CANONICAL_OBSERVATIONS.csv.gz` | Immutable independently decoded lineage table; SHA matched. |
| `REUSE_AFTER_IDENTITY_CHECK` | `/mnt/nrf_ssd/nRF_dev/BioSpur_Fusion/Fusion_Part/logs/v47_ten_node_body_calibration_20260814_093601/continuous_collector/fusion_host_raw.cobs.bin` | Identity source; not decoded again. |
| `REUSE_AFTER_IDENTITY_CHECK` | `/mnt/nrf_ssd/nRF_dev/BioSpur_Fusion/Fusion_Part/logs/v47_ten_node_body_calibration_20260814_093601/analysis_body_fusion_v2/TIME_ALIGNMENT_RESULT.json` | Exact raw/capture/Listener/TDMA identity matched. |
| `REUSE_AFTER_IDENTITY_CHECK` | `/mnt/nrf_ssd/nRF_dev/BioSpur_Fusion/Fusion_Part/logs/v47_ten_node_body_calibration_20260814_093601/analysis_body_fusion_v2/CLOCK_MODELS.csv` | Byte-identical across deterministic prior replay. |
| `RECOMPUTE_AND_COMPARE` | `/mnt/nrf_ssd/nRF_dev/BioSpur_Fusion/Fusion_Part/logs/v47_ten_node_body_calibration_20260814_093601/analysis_body_fusion_v2/CLOCK_RESIDUALS.csv` | Replay metrics only; not a body input. |
| `REUSE_AFTER_IDENTITY_CHECK` | `/mnt/nrf_ssd/nRF_dev/BioSpur_Fusion/Fusion_Part/logs/v47_ten_node_body_calibration_20260814_093601/analysis_body_fusion_v2/TIME_EVENT_LEDGER.npz` | Lineage-preserving machine mapping; deterministic SHA matched. |
| `REUSE_AFTER_IDENTITY_CHECK` | `/mnt/nrf_ssd/nRF_dev/BioSpur_Fusion/Fusion_Part/logs/v47_ten_node_body_calibration_20260814_093601/listener_capture_5/merged_index.jsonl` | Listener SHA matched prior evidence. |
| `REUSE_DIRECTLY` | `/mnt/nrf_ssd/nRF_dev/BioSpur_Fusion/Fusion_Part/config/captures/v47_ten_node_body_calibration_20260814_093601.json` | Operator/capture identity and TDMA facts. |
| `REUSE_AFTER_IDENTITY_CHECK` | `/mnt/nrf_ssd/nRF_dev/BioSpur_Fusion/Fusion_Part/config/geometry/current_room_autopos_20260811_183541.reference.json` | References frozen V4-io SHA 20320e53... |
| `REUSE_DIRECTLY` | `/mnt/nrf_ssd/nRF_dev/BioSpur_Fusion/Fusion_Part/logs/v47_ten_node_body_calibration_20260814_093601/ACTION_EVENTS.jsonl` | Acquisition annotation; bounds are not exact motion truth. |
| `DIAGNOSTIC_ONLY` | `/mnt/nrf_ssd/nRF_dev/BioSpur_Fusion/Fusion_Part/logs/v47_ten_node_body_calibration_20260814_093601/analysis_body_fusion_v2/Q1_ATTITUDE_TIMELINES.npz` | May initialize/compare; cannot become final truth. |
| `DIAGNOSTIC_ONLY` | `/mnt/nrf_ssd/nRF_dev/BioSpur_Fusion/Fusion_Part/logs/v47_ten_node_body_calibration_20260814_093601/analysis_body_calibration_v1/run_a/T4_POSITION_TIMELINES.npz` | UWB baseline/initialization only; raw ranges remain estimator observations. |
| `REJECT_OLD_SCIENTIFIC_ARCHITECTURE` | `/mnt/nrf_ssd/nRF_dev/BioSpur_Fusion/Fusion_Part/logs/v47_ten_node_body_calibration_20260814_093601/analysis_body_fusion_v2/BODY_MODEL_MANIFEST.json` | Rejected body architecture. |
| `REJECT_OLD_SCIENTIFIC_ARCHITECTURE` | `/mnt/nrf_ssd/nRF_dev/BioSpur_Fusion/Fusion_Part/logs/v47_ten_node_body_calibration_20260814_093601/analysis_body_fusion_v2/CALIBRATION_FREEZE_MANIFEST.json` | Parameters estimated under rejected model. |
| `REJECT_OLD_SCIENTIFIC_ARCHITECTURE` | `/mnt/nrf_ssd/nRF_dev/BioSpur_Fusion/Fusion_Part/logs/d0b_r2_observation_lineage_v2_20260816_025655` | Explicitly prohibited. |
| `REJECT_OLD_SCIENTIFIC_ARCHITECTURE` | `/mnt/nrf_ssd/nRF_dev/BioSpur_Fusion/Fusion_Part/logs/v47_ten_node_body_calibration_20260814_093601/analysis_body_fusion_v3` | Historical evidence only. |
