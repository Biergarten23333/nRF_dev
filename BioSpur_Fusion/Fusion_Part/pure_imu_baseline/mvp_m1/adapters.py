"""Replay and future-live input adapters for the shared pose engine."""
from __future__ import annotations

from pathlib import Path

import numpy as np

from .engine import InputPacket


def load_replay(path: Path) -> dict[str, np.ndarray]:
    with np.load(path, allow_pickle=False) as archive:
        return {name: archive[name].copy() for name in archive.files}


def replay_packets(raw: dict[str, np.ndarray]):
    for frame in range(len(raw["time_s"])):
        yield InputPacket(
            timestamp_us=int(round(float(raw["time_s"][frame])*1e6)),
            frame_index=frame,
            raw_q_GB_wxyz=raw["q_GB_wxyz"][frame],
            validity_mask=raw["valid"][frame],
            filter_reset=raw["filter_reset"][frame],
            raw_joint_positions_m=raw["joint_positions_m"][frame],
            joint_available=raw["joint_available"][frame],
        )


class FutureLiveInputAdapter:
    """Interface-only adapter; live acquisition is deliberately not implemented."""

    def feed(self, packet: InputPacket) -> InputPacket:
        if packet.raw_q_GB_wxyz.shape != (10, 4):
            raise ValueError("expected ten wxyz quaternions")
        if packet.validity_mask.shape != (10,) or packet.filter_reset.shape != (10,):
            raise ValueError("expected ten-node validity/reset vectors")
        return packet
