"""Authorized C1 support and frozen Root-R4 data adapter."""
from __future__ import annotations

from dataclasses import dataclass, replace

import numpy as np

from biospur_fusion.root_r4.data import C1Data, load_c1, m1_relative_points

from .constants import RAW_MATCHED_EPOCH_STRIDE


@dataclass(frozen=True)
class SupportDefinition:
    block_edges_s: np.ndarray
    block_id: np.ndarray
    matched_event_mask: np.ndarray
    matched_epochs: np.ndarray
    unavailable_m1_events: int

    def record(self, data: C1Data) -> dict:
        rows = []
        for block in range(5):
            event = self.matched_event_mask & (self.block_id == block)
            rows.append({"block": block, "start_s": float(self.block_edges_s[block]),
                         "stop_s": float(self.block_edges_s[block + 1]),
                         "events": int(np.sum(event)), "epochs": int(len(np.unique(data.epoch[event]))),
                         "raw_valid_links": int(np.sum(data.raw_valid[event])),
                         "t4_constituent_links": int(sum(int(int(mask).bit_count()) for mask in data.t4_used_mask[event]))})
        return {
            "schema": "biospur.root_r5a.matched_support.v1", "capture": 1,
            "original_root_r4_blocks_reused": True, "block_edges_s": self.block_edges_s.tolist(),
            "selection": f"every {RAW_MATCHED_EPOCH_STRIDE}th global epoch, then every available tag in that epoch",
            "raw_and_t4_are_separate_solves": True, "same_selected_physical_event_support": True,
            "rows": rows, "unavailable_m1_events_excluded_from_profiles": self.unavailable_m1_events,
        }


def load_authorized_c1() -> tuple[C1Data, dict, SupportDefinition]:
    data, lineage = load_c1()
    edges = np.quantile(data.measurement_s, np.linspace(0.0, 1.0, 6))
    block = np.digitize(data.measurement_s, edges[1:-1], right=False).astype(np.int8)
    geometry = np.all(np.isfinite(data.root_relative_n_m), axis=1)
    available_epochs = np.unique(data.epoch[geometry])
    selected_epochs = available_epochs[::RAW_MATCHED_EPOCH_STRIDE]
    matched = geometry & np.isin(data.epoch, selected_epochs)
    support = SupportDefinition(edges, block, matched, selected_epochs, int(np.sum(~geometry)))
    return data, lineage, support


def data_at_offset(data: C1Data, delta_t_s: float) -> C1Data:
    """Re-interpolate frozen M1 geometry; never mutate frozen M1 arrays."""

    event_relative, _, _, _, _ = m1_relative_points(
        data.m1, data.node_index, data.measurement_s + float(delta_t_s))
    raw_relative_flat, _, _, _, _ = m1_relative_points(
        data.m1, np.repeat(data.node_index, 8),
        (data.raw_measurement_s + float(delta_t_s)).reshape(-1))
    return replace(data, root_relative_n_m=event_relative,
                   raw_root_relative_n_m=raw_relative_flat.reshape(data.event_count, 8, 3))


def block_masks(support: SupportDefinition) -> list[np.ndarray]:
    return [support.matched_event_mask & (support.block_id == block) for block in range(5)]
