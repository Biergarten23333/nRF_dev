from __future__ import annotations


def to_canonical_human_state(estimator_output: dict, identities: dict, calibration_ref: str) -> dict:
    if estimator_output.get("active_modality") != "IMU_ONLY" or estimator_output.get("uwb_factor_count") != 0:
        raise ValueError("Phase 3 semantic adapter accepts IMU-only state")
    return {
        "schema": "canonical-human-state-v0.1",
        "identity": {k: identities[k] for k in ("subject_id", "capture_id", "session_id", "donning_id")},
        "mapping_binding_id": estimator_output["mapping_binding_id"], "conditional_calibration_ref": calibration_ref,
        "estimate_kind": estimator_output["estimate_kind"], "measurement_cutoff_time_s": estimator_output["measurement_cutoff_time_s"],
        "output_time_s": estimator_output["output_time_s"], "algorithmic_latency_s": estimator_output["algorithmic_latency_s"],
        "frame_realization": estimator_output["frame_realization"], "segments": estimator_output["segments"],
        "root_local_position_m": estimator_output["root_local_position_m"], "root_local_velocity_m_s": estimator_output["root_local_velocity_m_s"],
        "world_absolute_state": "UNAVAILABLE", "head_hands": "MODEL_INFERRED", "feet": "UNAVAILABLE",
        "gauges": estimator_output["gauges"], "contact": "CONTACT_UNOBSERVABLE",
        "active_modality": "IMU_ONLY", "validity_semantics": "conditional local state; zero values never encode unavailable DOF",
        "external_accuracy_claim": False,
    }
