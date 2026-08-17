import json
from pathlib import Path
import numpy as np

from biospur_fusion.articulated_v2.binding import EXPECTED_NODES, FrozenMappingBinding, ROLES
from biospur_fusion.articulated_v2.estimator import ImuObservation
from biospur_fusion.articulated_v2.evaluation import evaluate_cold_holdout

ROOT=Path(__file__).resolve().parents[5]/'BioSpur_Fusion/Fusion_Part'

def test_cold_holdout_routes_metrics_without_action_input_to_estimator():
    b=FrozenMappingBinding('x','v1','c','s','d',dict(zip(EXPECTED_NODES,ROLES)),'OPERATOR_RECORDED','0'*64,'x',True,'FAILED')
    cfg=json.loads((ROOT/'config/fusion_v2/phase3/PHASE3_SOLVER_CONFIG.json').read_text()); obs=[]
    seq={n:0 for n in EXPECTED_NODES}
    for step in range(160):
        for node in EXPECTED_NODES:
            seq[node]+=1; obs.append(ImuObservation(node,step*.005,seq[node],np.array([.1,0,0]),np.array([0,0,9.80665])))
    obs.sort(key=lambda x:(x.time_s,x.node_id)); route={'action_id':'synthetic','classification':'IN_SCOPE_GATE','preparation_s':.2,'formal_s':.4,'recovery_s':.2}
    cfg={**cfg,'initialization_target_s':.1}
    result=evaluate_cold_holdout(obs,b,cfg,route)
    assert result['formal_scheduled_record_coverage']==1.0
    assert result['universal_safety_pass'] and result['uwb_numeric']==result['uwb_factors']==0
