"""Frozen corrected ten-node reference, for post-computation evaluation only.

The articulated trajectory is a solved body on one pelvis-owned timeline.
Applying the legacy per-sensor time recovery separately would tear that body
apart again. H uses its producer's common-grid epoch and pelvis clock.
"""
import json
from pathlib import Path

import numpy as np
from scipy.spatial.transform import Rotation, Slerp

from biospur_fusion.c2_coupled_progressive.contracts import EPISODES
from biospur_fusion.c2_coupled_progressive.output_coordinates import _validated_reflection
from .inputs import ROOT, CLOCK, sha
from .reference_timing import map_reference_times

REFERENCE_RUN = ROOT / 'logs/c2_native200_orientation_constrained_biomechanics_v4_20260904'
CAL_REFERENCE = REFERENCE_RUN / 'ARTICULATED_CALIBRATION_TRAJECTORY.npz'
H_REFERENCE = REFERENCE_RUN / 'ARTICULATED_HXX_TRAJECTORY.npz'
H_CLOCK_REPORT = ROOT / 'logs/c2_hxx_native200_calibration_v3_20260904/HXX_FROZEN_C2_REPLAY_REPORT.json'
EXPECTED_CAL_SHA = '94f9afb088c7f05a7dbcae0c7d6d2c18be76a6ca32e1d9b96861a8deb7962937'
EXPECTED_H_SHA = 'b0670430f76e58d268e276c4f65d8e446490446d74d953526dc335432c6c7765'
EXPECTED_H_CLOCK_SHA = 'ee105f230f7eb6c434ef447ae27d940158e4f4d47c06c4a5d3723722ac1e00ad'
SEGMENTS = ('pelvis', 'torso', 'upper_arm_left', 'forearm_left',
            'upper_arm_right', 'forearm_right', 'thigh_left', 'shank_left',
            'thigh_right', 'shank_right')


def verify_reference():
    for path, expected in ((CAL_REFERENCE, EXPECTED_CAL_SHA), (H_REFERENCE, EXPECTED_H_SHA),
                           (H_CLOCK_REPORT, EXPECTED_H_CLOCK_SHA)):
        if sha(path) != expected:
            raise ValueError('corrected ten-node reference changed: '+str(path))
    return dict(version=REFERENCE_RUN.name, role='POST_FREEZE_ENGINEERING_REFERENCE',
        source_sha256={str(p.relative_to(ROOT)):sha(p) for p in
            (CAL_REFERENCE, H_REFERENCE, H_CLOCK_REPORT, CLOCK)},
        adapter_sha256=sha(Path(__file__)),
        time_owner='one solved body, pelvis timeline; no independent segment retiming',
        coordinate_owner='frozen capture-wide post-FK output transform, applied exactly once',
        pose_repair_in_viewer=False, reference_used_in_calibration=False)


def output_matrix(archive):
    return _validated_reflection(archive['output_coordinates/matrix_world_output_from_internal'])


def corrected_sample_times(stored, target_clock, *, holdout=False, sync=None, source_clock=None):
    if holdout:
        return map_reference_times(stored, target_clock,
            grid_start_ns=sync['actual_common_interval_ns'][0], source_clock=source_clock)
    return map_reference_times(stored, target_clock, offset_s=0.)


def sample_body(archive, key, times, source_times):
    """Resample an already solved body without refitting or changing its branch."""
    stored=archive[f'trajectory/{key}/pelvis/time_root_s']
    t=np.asarray(source_times, float)
    if len(t)<2 or not np.all(np.diff(t)>0):
        raise ValueError('strictly increasing reference timeline required')
    clipped=np.clip(times,t[0],t[-1])
    right=np.clip(np.searchsorted(t,clipped),0,len(t)-1)
    exact=t[right]==clipped
    left=np.where(exact,right,np.maximum(0,right-1))
    valid=(times>=t[0]) & (times<=t[-1]) & ((t[right]-t[left])<=.025)
    output={}
    for segment in SEGMENTS:
        base=f'trajectory/{key}/{segment}'
        if not np.array_equal(archive[base+'/time_root_s'],stored):
            raise ValueError('articulated segments do not share a timeline')
        q=archive[base+'/quat_world_segment_wxyz']
        mask=archive[base+'/mask']
        valid &= mask[left] & mask[right]
        output[segment]=Slerp(t-t[0],Rotation.from_quat(q[:,[1,2,3,0]]))(clipped-t[0]).as_matrix()
    return output,valid


def baseline_on_grid(archive, key, times, contract, holdout):
    del contract  # Action labels cannot define or shift measurement time.
    target=json.loads(CLOCK.read_text())['models']['BSFC2CC']
    stored=archive[f'trajectory/{key}/pelvis/time_root_s']
    if holdout:
        report=json.loads(H_CLOCK_REPORT.read_text())
        t=corrected_sample_times(stored,target,holdout=True,
            sync=report['synchronization'][key],
            source_clock=report['time_alignment'][key]['models']['BSFC2CC'])
    else:
        t=corrected_sample_times(stored,target)
    return sample_body(archive,key,times,t)


def action_key(name):
    return name if name.startswith('H') else f'{EPISODES.index(name):02d}'
