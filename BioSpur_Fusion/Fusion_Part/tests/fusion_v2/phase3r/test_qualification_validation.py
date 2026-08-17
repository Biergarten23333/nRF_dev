import copy
import numpy as np
import pytest

from biospur_fusion.imu_pose_v1.observability import svd_scan
from biospur_fusion.imu_pose_v1.qualification import audit_real_master,validate_svd_report


def test_svd_validator_rejects_hardcoded_rank_mutation():
    matrix=np.diag([3.,2.,1.,0.]);report=svd_scan(matrix);validate_svd_report(matrix,report)
    bad=copy.deepcopy(report);bad['tolerance_scan']['0.0001']['rank']+=1
    with pytest.raises(ValueError):validate_svd_report(matrix,bad)


def test_real_action_auditor_recomputes_counts_factors_and_uwb():
    factor={'count':1,'state_delta_sq':.1,'information_trace':2.,'residual_sq':1.,'jacobian_nonzero_blocks':2}
    action=lambda c:{'classification':c,'whole_body_availability':.9,'bone_length_max_variation':1e-15,
      'maximum_production_step_deg':{'x':2.},'maximum_B0_aligned_50hz_step_deg':{'x':2.},
      'factor_activation':{'joint':factor}}
    master={'actions':[action('DEVELOPMENT') for _ in range(19)]+[action('CONTAMINATED_RETROSPECTIVE_DIAGNOSTIC') for _ in range(3)],'uwb_numeric_decode':0}
    out=audit_real_master(master);assert out['action_count']==22 and out['factors']['joint']['state_delta_sq']>0
    master['uwb_numeric_decode']=1
    with pytest.raises(ValueError):audit_real_master(master)
