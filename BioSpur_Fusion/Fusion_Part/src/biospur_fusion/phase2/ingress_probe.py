"""Minimal read-only compatibility probe for Phase 1 handoff artifacts."""
NODES={"BSF31CC","BSFC2CC","BSFAA61","BSF1120","BSFB165","BSFEC35","BSF44AD","BSF3C79","BSF6C53","BSF8BC4"}
def probe_phase1_run(run):
 states=run.get("states",[]);nodes={s.get("hardware_node_id") for s in states}
 if nodes!=NODES or len(states)!=10:raise ValueError("ten-node coverage")
 for s in states:
  if s.get("logical_role") is not None or s.get("mapping_status")!="UNASSIGNED":raise ValueError("mapping contract")
  if not str(s.get("yaw_gauge_id","")).startswith("YAW_GAUGE_"):raise ValueError("yaw gauge")
  if len(s.get("bg_radps",[]))!=3 or len(s.get("ba_mps2",[]))!=3:raise ValueError("bias ABI")
  if s.get("covariance_min_eigenvalue",-1)<-1e-9:raise ValueError("covariance")
 if any(run.get("forbidden_input_counts",{}).values()):raise ValueError("forbidden dependency")
 return {"status":"PASS","nodes":10,"orientation_role":"INITIALIZER_OR_DIAGNOSTIC_ONLY","double_counting_rule":"CANNOT_BECOME_INDEPENDENT_OBSERVATION_WHEN_PHASE2_CONSUMES_SAME_RAW_IMU","node_to_body_mapping":"UNKNOWN","phase2_started":False}
