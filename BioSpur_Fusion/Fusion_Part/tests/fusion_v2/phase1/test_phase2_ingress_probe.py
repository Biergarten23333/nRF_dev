import sys
from pathlib import Path
import pytest
ROOT=Path(__file__).resolve().parents[5];sys.path.insert(0,str(ROOT/"BioSpur_Fusion/Fusion_Part/src"))
from biospur_fusion.phase2.ingress_probe import NODES,probe_phase1_run
def run():return {"states":[{"hardware_node_id":n,"logical_role":None,"mapping_status":"UNASSIGNED","yaw_gauge_id":"YAW_GAUGE_"+n,"bg_radps":[0,0,0],"ba_mps2":[0,0,0],"covariance_min_eigenvalue":0} for n in NODES],"forbidden_input_counts":{"Q1":0,"T4":0,"UWB":0}}
def test_positive():assert probe_phase1_run(run())["phase2_started"] is False
def test_missing_node_rejected():
 r=run();r["states"].pop()
 with pytest.raises(ValueError):probe_phase1_run(r)
def test_mapping_rejected():
 r=run();r["states"][0]["logical_role"]="pelvis"
 with pytest.raises(ValueError):probe_phase1_run(r)
def test_forbidden_dependency_rejected():
 r=run();r["forbidden_input_counts"]["UWB"]=1
 with pytest.raises(ValueError):probe_phase1_run(r)
