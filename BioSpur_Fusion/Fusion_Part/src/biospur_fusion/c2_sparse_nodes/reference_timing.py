"""Recover reference source-node sample times for post-freeze comparison only.

Legacy C2's root grid was populated using per-episode pair offsets. It is not
the hardware time of every node. Hxx instead stores elapsed, quantized shared
grid time. Neither convention can be replaced by the action-label start time.
"""
from functools import lru_cache
import json
from types import SimpleNamespace

import numpy as np

from biospur_fusion.c2_coupled_progressive.contracts import SEGMENT_TO_NODE
from biospur_fusion.c2_coupled_progressive.frontend import VerifiedFrontendArchive
from biospur_fusion.c2_coupled_progressive.pose_reset_avatar import _clock_offsets_to_pelvis
from .inputs import CLOCK, ROOT

H_REPORT = ROOT / 'logs/c2_hxx_frozen_replay_20260831_220900/HXX_FROZEN_C2_REPLAY_REPORT.json'


def map_reference_times(stored_s, target_clock, *, offset_s=None,
                        grid_start_ns=None, source_clock=None):
    """Invert the original resampling clock, then map the same TIMER2 instant."""
    stored_s = np.asarray(stored_s, float)
    if offset_s is not None:
        timer_us = (stored_s - offset_s) * 1e6
    elif grid_start_ns is not None and source_clock is not None:
        timer_us = (stored_s * 1e9 + grid_start_ns - source_clock['b_ns']) / source_clock['a_ns_per_us']
    else:
        raise ValueError('explicit original reference time convention required')
    return (timer_us * target_clock['a_ns_per_us'] + target_clock['b_ns']) * 1e-9


@lru_cache(maxsize=1)
def _metadata():
    frontend = VerifiedFrontendArchive()
    frontend.verify_seal_and_semantics()
    return frontend, json.loads(CLOCK.read_text())['models'], json.loads(H_REPORT.read_text())


@lru_cache(maxsize=19)
def _offsets(key):
    frontend, _, _ = _metadata()
    return _clock_offsets_to_pelvis(SimpleNamespace(
        pair_alignment_reports=frontend.alignment_reports_for_episode(int(key))))


def reference_sample_times(stored_s, key, segment, holdout):
    _, clocks, h_report = _metadata()
    node = SEGMENT_TO_NODE[segment]
    if holdout:
        return map_reference_times(stored_s, clocks[node],
            grid_start_ns=h_report['synchronization'][key]['actual_common_interval_ns'][0],
            source_clock=h_report['time_alignment'][key]['models'][node])
    return map_reference_times(stored_s, clocks[node], offset_s=_offsets(key)[segment])
