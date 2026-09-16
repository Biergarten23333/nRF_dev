"""Common-clock pose calibration pairs with independently retained native gaps."""
from __future__ import annotations

from collections.abc import Mapping

import numpy as np

from biospur_fusion.c2_coupled_progressive.contracts import EDGES, SEGMENT_TO_NODE
from biospur_fusion.c2_coupled_progressive.continuous_calibration_inputs import (
    _associate, _readonly, _span_indices,
)
from biospur_fusion.c2_coupled_progressive.estimator import AlignedSpan
from biospur_fusion.c2_coupled_progressive.frontend import EpisodeFrontend


class ContinuousPoseAlignedSpans:
    """Callable replacement for the isolated native-200 module's span provider.

    Episode NodeSeries clocks are common-clock microseconds for pose evaluation.
    Register the exact selected native rows separately; never infer native gaps
    from clock-scaled timestamps or consume historical correlation lag reports.
    """

    def __init__(self, *, max_pair_offset_ns: int = 2_500_000):
        if not isinstance(max_pair_offset_ns, int) or not 0 <= max_pair_offset_ns <= 2_500_000:
            raise ValueError("pair tolerance must be integer ns within half cadence")
        self.max_pair_offset_ns = max_pair_offset_ns
        self._episodes = {}

    def register(self, episode: EpisodeFrontend, native_by_node: Mapping):
        if episode.chronological_index in self._episodes:
            raise ValueError("episode already registered")
        if set(native_by_node) != set(episode.nodes):
            raise ValueError("native sidecar must cover the exact episode nodes")
        native = {}
        for node, series in episode.nodes.items():
            row = {key: _readonly(native_by_node[node][key]) for key in
                   ("time_us", "common_global_ns", "boot_epoch", "contiguous_span_id")}
            n = len(series.time_us)
            if n < 1 or any(v.shape != (n,) or v.dtype.kind not in "iu" for v in row.values()):
                raise ValueError("native sidecar requires same-length integer arrays")
            if any(np.any(np.diff(row[key]) <= 0) for key in ("time_us", "common_global_ns")):
                raise ValueError("native/common sidecar clocks must be monotonic")
            if not np.array_equal(row["boot_epoch"], series.derived_boot_epoch):
                raise ValueError("native sidecar boot mismatch")
            if not np.array_equal(row["contiguous_span_id"], series.contiguous_span_id):
                raise ValueError("native sidecar span mismatch")
            if not np.allclose(row["common_global_ns"].astype(float) / 1000,
                               series.time_us, rtol=0, atol=1e-4):
                raise ValueError("episode clock does not match common-clock sidecar")
            native[node] = row
        self._episodes[episode.chronological_index] = (episode, native)

    def __call__(self, episode: EpisodeFrontend) -> tuple[AlignedSpan, ...]:
        registered, native = self._episodes[episode.chronological_index]
        if registered is not episode:
            raise ValueError("episode identity changed after native registration")
        result = []
        for edge in EDGES:
            pn, cn = SEGMENT_TO_NODE[edge.parent], SEGMENT_TO_NODE[edge.child]
            parent, child = native[pn], native[cn]
            ps, cs = episode.nodes[pn], episode.nodes[cn]
            pi, ci = _associate(parent, child, self.max_pair_offset_ns)
            for index, (p, c) in enumerate(_span_indices(parent, child, pi, ci, 80)):
                maximum = int(np.max(np.abs(parent["common_global_ns"][p] - child["common_global_ns"][c])))
                result.append(AlignedSpan(
                    episode_index=episode.chronological_index, qa_label=episode.qa_label,
                    edge=edge.name, span_index=index,
                    parent_segment=edge.parent, child_segment=edge.child,
                    parent_indices=_readonly(p), child_indices=_readonly(c),
                    time_root_s=_readonly(parent["common_global_ns"][p].astype(float) * 1e-9),
                    parent_time_s=_readonly(parent["time_us"][p].astype(float) * 1e-6),
                    child_time_s=_readonly(child["time_us"][c].astype(float) * 1e-6),
                    parent_acc_mps2=_readonly(ps.acc_mps2[p]),
                    child_acc_mps2=_readonly(cs.acc_mps2[c]),
                    parent_gyro_rads=_readonly(ps.gyro_rads[p]),
                    child_gyro_rads=_readonly(cs.gyro_rads[c]),
                    parent_quat_wxyz=_readonly(ps.quat_world_sensor_wxyz[p]),
                    child_quat_wxyz=_readonly(cs.quat_world_sensor_wxyz[c]),
                    lag_uncertainty_s=maximum * 1e-9,
                    alignment_status="COMMON_CLOCK_NATIVE_PAIRING_NOT_CORRELATION_PEAK",
                    timing_audit={"source": "CURRENT_COMMON_CLOCK_ORIGINAL_NATIVE_ROWS",
                                  "rows_cross_gap": False, "native_timer_step_us": 5000,
                                  "max_absolute_pair_offset_ns": maximum,
                                  "historical_lag_metadata_used": False},
                ))
        return tuple(result)
