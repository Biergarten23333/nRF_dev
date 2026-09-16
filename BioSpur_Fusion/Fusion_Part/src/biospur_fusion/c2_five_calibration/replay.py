"""Candidate-bound continuous C2 inference; each call owns a fresh recurrence."""
import hashlib
import json
import copy

import numpy as np

from biospur_fusion.c2_imucoco.backend import ChunkedPoseStream
from biospur_fusion.c2_sparse_nodes.inputs import NODES
from .frontend import FIT, prepare, initial_state
from .placement import C2_VERTICES
from .shared_orientation import with_heading_increment


def parameter_digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                     allow_nan=False).encode()).hexdigest()


def _array_digest(arrays):
    digest = hashlib.sha256()
    for name in sorted(arrays):
        array = np.ascontiguousarray(arrays[name])
        digest.update(json.dumps([name, array.dtype.str, array.shape]).encode())
        digest.update(memoryview(array).cast('B'))
    return digest.hexdigest()


def replay_prior(episodes, calibration, geometry, poser, *, probe=False,
                 wall_limit_s=600., progress=None, prefix=False):
    """Re-encode the entire C2 prefix before slicing any action for fitting.

    Recurrent states and predicted poses are deliberately not accepted as
    input. A new calibration must create its own initial pose and recurrence.
    The short standing probe is explicitly marked and cannot be a full replay.
    """
    if any(name.startswith('H') for name in episodes):
        raise ValueError('H-series data cannot enter calibration replay')
    actions = {name for name in episodes if name != '_continuous'}
    from .phase_contract import recorded_prefix
    ordered=recorded_prefix(actions) if prefix else sorted(actions)
    if not prefix and ({name[:2] for name in actions} != FIT or len(actions) != len(FIT)):
        raise ValueError('replay requires every recorded C2 action exactly once')
    if prefix and (calibration.get('prefix_last_action')!=ordered[-1]
                   or calibration.get('fit_actions')!=sorted(actions)):
        raise ValueError('prefix replay calibration/evidence cutoff mismatch')
    if '_continuous' not in episodes or '00_initial_still' not in episodes:
        raise ValueError('continuous C2 prefix and initial standing are required')
    if any(set(value) != set(NODES) for value in episodes.values()):
        raise ValueError('replay requires exactly the five retained nodes')
    if prefix:
        for node in NODES:
            current=episodes[ordered[-1]][node]['imu']
            continuous=episodes['_continuous'][node]['imu']
            if len(continuous)==0 or continuous[-1,0]>current[-1,0]:
                raise ValueError('continuous input extends beyond arrived prefix')
    frontend_hash, geometry_hash = parameter_digest(calibration), parameter_digest(geometry)
    standing = prepare(episodes['00_initial_still'], calibration, geometry)
    initial = initial_state(standing, geometry)
    prepared = ({k:v[:300] for k,v in standing.items()} if probe
                else prepare(episodes['_continuous'], calibration, geometry))
    binding = dict(frontend_parameter_sha256=frontend_hash,
        geometry_parameter_sha256=geometry_hash,
        prepared_stream_sha256=_array_digest(prepared),
        features_sha256=_array_digest({'features':prepared['features']}),
        initial_state_sha256=_array_digest({'initial':initial}),
        continuous_prefix=not probe, frames=len(prepared['time_s']),
        time_range_s=prepared['time_s'][[0, -1]].tolist(),
        retained_nodes=list(NODES), action_names=sorted(actions),
        prefix_last_action=ordered[-1], full_recorded_C2=len(actions)==len(FIT),
        recurrence_owner='one fresh ChunkedPoseStream per calibration candidate',
        prior_reused_from_other_calibration=False)
    stream = ChunkedPoseStream(poser, initial_global_rotation=initial, sensor_vertices=C2_VERTICES)
    predicted, audit = stream.run(prepared['features'], input_valid=prepared['input_valid'],
                                 wall_limit_s=wall_limit_s, progress=progress)
    if (parameter_digest(calibration) != frontend_hash
            or parameter_digest(geometry) != geometry_hash
            or _array_digest(prepared) != binding['prepared_stream_sha256']):
        raise ValueError('calibration or prepared observations changed during replay')
    result = dict(time_s=prepared['time_s'], prior=predicted['global_rotation'],
        observed=prepared['orientation'], acceleration=prepared['acceleration_mps2'],
        valid=prepared['input_valid'])
    return result, {**audit, 'replay_binding':binding}, initial


def slice_actions(data, contracts, *, continuous_grid=False, prefix=False):
    """Keep the established per-action 20 Hz sampling phase and support."""
    if prefix:
        from .phase_contract import recorded_prefix
        recorded_prefix(contracts)
    elif {name[:2] for name in contracts} != FIT or len(contracts) != len(FIT):
        raise ValueError('action windows must contain every recorded C2 action exactly once')
    result = {}
    if continuous_grid:
        data = {key:value[::3] for key,value in data.items()}
    for name, window in contracts.items():
        indexes = np.flatnonzero((data['time_s'] >= window['lo']) & (data['time_s'] <= window['hi']))
        if not continuous_grid:
            indexes = indexes[::3]
        if len(indexes) < 2:
            raise ValueError('insufficient C2 action replay support: '+name)
        result[name] = {key:value[indexes] for key,value in data.items()}
    return result


class C2ReplayEvaluator:
    """Full replay callback for proposals measured from one immutable baseline.

    The caller supplies sealed source/model/raw provenance. This owner binds
    those records, the exact window contract and derived observations; it
    never reuses another candidate's recurrent state or neural prediction.
    """
    def __init__(self, episodes, baseline, geometry, poser, contracts, provenance,
                 *, wall_limit_s=600., progress=None, continuous_grid=False, prefix=False):
        if 'shared_orientation_proposal' in baseline:
            raise ValueError('replay evaluator requires the original baseline frontend')
        required = {'input_sha256', 'source_sha256', 'model_sha256'}
        if set(provenance) != required or any(not value for value in provenance.values()):
            raise ValueError('replay requires sealed input, source and model provenance')
        if prefix:
            from .phase_contract import recorded_prefix
            names=recorded_prefix(contracts)
            if (set(episodes)-{'_continuous'}!=set(names)
                    or baseline.get('prefix_last_action')!=names[-1]):
                raise ValueError('prefix evaluator evidence/contract cutoff mismatch')
        self._prefix=prefix
        self._baseline, self._geometry = copy.deepcopy(baseline), copy.deepcopy(geometry)
        self._contracts, self._provenance = copy.deepcopy(contracts), copy.deepcopy(provenance)
        self._episodes, self._poser = episodes, poser
        self._wall_limit_s, self._progress = wall_limit_s, progress
        self._binding = dict(baseline_frontend_sha256=parameter_digest(self._baseline),
            action_contract_sha256=parameter_digest(self._contracts),
            provenance_sha256=parameter_digest(self._provenance))
        self._action_support = None
        self._continuous_grid = continuous_grid

    def __call__(self, total_delta_rad):
        calibration = with_heading_increment(self._baseline, total_delta_rad)
        data, audit, initial = replay_prior(self._episodes, calibration, self._geometry,
            self._poser, wall_limit_s=self._wall_limit_s, progress=self._progress, prefix=self._prefix)
        actions = slice_actions(data, self._contracts, continuous_grid=self._continuous_grid, prefix=self._prefix)
        support = {name:_array_digest({'time_s':q['time_s'], 'valid':q['valid']})
                   for name,q in actions.items()}
        if self._action_support is not None and support != self._action_support:
            raise ValueError('candidate changed action time or valid support')
        self._action_support = support
        binding = {**audit['replay_binding'], **self._binding,
                   'action_support_sha256':support, 'provenance':copy.deepcopy(self._provenance)}
        return dict(actions=actions, binding=binding, continuous=data, frontend=calibration,
                    initial=initial, neural_audit=audit)
