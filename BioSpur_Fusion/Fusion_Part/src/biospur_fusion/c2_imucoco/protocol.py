"""Implemented calibration scope, distinct from the proposed 00--19 protocol."""
from biospur_fusion.c2_sparse_nodes.protocol import calibration_ledger


FIT_PRODUCTS = {
    '00_initial_still': ['initial_sensor_rotations', 'initial_time', 'acc_bias_sensor'],
    '02_t_pose': ['forearm heading', 'measured_tpose_segments (optional frontend only)'],
    '06_elbow_left': ['left forearm flexion/pronation axes and mounting'],
    '07_elbow_right': ['right forearm flexion/pronation axes and mounting'],
    '10_knee_left_seated': ['left shank functional frame and heading'],
    '11_knee_right_seated': ['right shank functional frame and heading'],
    '14_trunk_flex_extend': ['pelvis functional frame and heading'],
    '17_final_still': ['final_time', 'common pelvis heading closure'],
}

MISSING_COMPONENTS = [
    'shoulder/hip and sensor position identification from calibration motion',
    'quantitative cross-action validation of the fitted body/sensor model',
    'measured dimensions in pose estimation, beyond display FK',
    'retained IMU observation consistency in the pose estimator',
    'validated initial pose and elbow state supplied to pose inference',
]


def implemented_ledger(episodes, calibration):
    ledger = calibration_ledger(episodes, calibration)
    for row in ledger['actions']:
        row['proposed_role'] = row.pop('role')
        row['proposed_purpose'] = row.pop('purpose')
        products = FIT_PRODUCTS.get(row['action'], []) if row['acquired'] else []
        row['implemented_role'] = ('parameter_fit' if products else
                                   'input_statistics_only' if row['acquired'] else 'not_acquired')
        row['fitted_products'] = products
        row['calibration_validation_passed'] = False
    ledger.update(parameter_fit_actions=sum(bool(r['fitted_products']) for r in ledger['actions']),
                  calibration_status='INCOMPLETE',
                  missing_components=MISSING_COMPONENTS.copy(),
                  claim='action accounting and partial IMU frontend fit only; proposed roles are not implemented evidence')
    return ledger


def require_replay_scope(*, diagnostic_only):
    # There is currently no implemented acceptance path. A caller cannot turn
    # a saved finite-output probe or an edited status string into acceptance.
    if not diagnostic_only:
        raise ValueError('five-node calibration is incomplete; full replay is available only with --diagnostic-only')
