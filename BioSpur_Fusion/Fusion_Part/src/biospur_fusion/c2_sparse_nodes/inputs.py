"""Five-node payload firewall and uninterrupted sensor-local orientation state.

No ten-node pose, axis, mounting, geometry, or position posterior is loaded here.
The shared transport envelope is decoded before node selection; sensor payloads
are decoded only after selection. Holdout continuation uses the same VQF states.
"""
from __future__ import annotations

from collections import Counter
from pathlib import Path
import hashlib
import json
import struct

import numpy as np
from vqf import VQF

from biospur_fusion.c2_coupled_progressive.contracts import EPISODES
from biospur_fusion.c2_coupled_progressive.holdout_replay import _complete_cobs_frames
from biospur_fusion.c2_uwb_root_world.run_calibration import (
    DATASET, PHYSICAL_DIRECTORY, _clock_models,
    _beacon_boundary_bridges, labelled_bounds_global_ns,
)
from biospur_fusion.c2_uwb_root_world.u0 import decode_frame, FrameError

NODES = ('BSFC2CC', 'BSFEC35', 'BSFB165', 'BSF6C53', 'BSF8BC4')
SEGMENTS = ('pelvis', 'forearm_left', 'forearm_right', 'shank_left', 'shank_right')
ROOT = Path(__file__).resolve().parents[3]
CLOCK = ROOT / 'logs/c2_uwb_beacon_clock_20260903_141552/CLOCK_TABLE_CALIBRATION_ONLY.json'
RAW = DATASET / 'system/fusion_continuous/fusion_host_raw.cobs.bin'
SAMPLE = struct.Struct('<Hhhhhhh')


def sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as f:
        for b in iter(lambda: f.read(4 << 20), b''):
            h.update(b)
    return h.hexdigest()


def episode_contracts(holdout: bool = False) -> dict:
    bridges = _beacon_boundary_bridges(CLOCK)
    names = ('H01_boxing', 'H02_golf') if holdout else EPISODES
    output = {}
    for name in names:
        p = DATASET / ('holdout' if holdout else 'actions') / (
            name if holdout else PHYSICAL_DIRECTORY[name]) / 'rep_01'
        manifest = p / 'manifest/CONTINUOUS_RANGE.json'
        r = json.loads(manifest.read_text())
        events = p / 'events/ACTION_EVENTS.jsonl'
        lo, hi = labelled_bounds_global_ns(events, bridges)
        output[name] = dict(start=r['start_byte_inclusive'], stop=r['end_byte_exclusive'],
                            lo=lo * 1e-9, hi=hi * 1e-9, slice_sha256=r['slice_sha256'],
                            manifest_sha256=sha(manifest), events_sha256=sha(events))
    return output


def selected_frame(encoded: bytes):
    frame = decode_frame(encoded)
    return frame if frame.node_name in NODES and frame.kind == 3 else None


class FiveNodeFrontend:
    def __init__(self):
        all_clocks = _clock_models(CLOCK)
        self.clocks = {n: all_clocks[n] for n in NODES}
        self.vqf = {n: VQF(.005) for n in NODES}
        self.last = {}
        self.counts = Counter()
        self.cursor = None

    def read(self, contracts: dict, start: int | None = None, *, keep_continuous=False) -> tuple[dict, dict]:
        start = min(r['start'] for r in contracts.values()) if start is None else start
        stop = max(r['stop'] for r in contracts.values())
        if self.cursor is not None and start != self.cursor:
            raise ValueError('VQF continuation must start at the exact previous byte boundary')
        stored = {e: {n: {'imu': []} for n in NODES} for e in contracts}
        if keep_continuous:
            stored['_continuous'] = {n: {'imu': []} for n in NODES}
        hashes = {e: hashlib.sha256() for e in contracts}
        for a, b, encoded in _complete_cobs_frames(RAW, start, stop):
            ep = next((e for e, r in contracts.items() if a >= r['start'] and b <= r['stop']), None)
            if ep is not None:
                hashes[ep].update(encoded + b'\0')
            if not encoded:
                continue
            try:
                f = selected_frame(encoded)
            except (FrameError, ValueError):
                self.counts['envelope_errors'] += 1
                continue
            if f is None:
                self.counts['excluded_before_payload_decode'] += 1
                continue
            n = f.node_name
            clock = self.clocks[n]
            self.counts[f'payload/{n}/{f.kind}'] += 1
            if f.kind == 3:
                version, count, _, base = struct.unpack_from('<BBHQ', f.payload)
                if version != 7 or not 1 <= count <= 16 or len(f.payload) != 14 + count * SAMPLE.size:
                    raise ValueError('IMU payload contract')
                for k in range(count):
                    delta, *values = SAMPLE.unpack_from(f.payload, 14 + k * SAMPLE.size)
                    timer = base + delta
                    if n in self.last:
                        dt = timer - self.last[n]
                        if dt <= 0:
                            raise ValueError(f'non-monotonic IMU {n}: {dt}')
                        if dt > 7500:
                            self.counts[f'gap/{n}'] += 1
                    self.last[n] = timer
                    acc = np.asarray(values[:3], float) / 2048 * 9.80665
                    gyr = np.deg2rad(np.asarray(values[3:], float) / 16.384)
                    self.vqf[n].update(gyr, acc)
                    t = clock.seconds(timer)
                    row = np.r_[t, self.vqf[n].getQuat6D(), acc, gyr]
                    if keep_continuous:
                        stored['_continuous'][n]['imu'].append(row)
                    if ep is not None and contracts[ep]['lo'] <= t < contracts[ep]['hi']:
                        stored[ep][n]['imu'].append(row)
        self.cursor = stop
        for e in contracts:
            if hashes[e].hexdigest() != contracts[e]['slice_sha256']:
                raise ValueError(f'raw action slice hash mismatch: {e}')
        for e in stored:
            for n in NODES:
                for kind in ('imu',):
                    arr = np.asarray(stored[e][n][kind], float)
                    if arr.ndim != 2 or len(arr) < 4 or not np.all(np.isfinite(arr)):
                        raise ValueError(f'insufficient/invalid input: {e}/{n}/{kind}')
                    if np.any(np.diff(arr[:, 0]) <= 0):
                        raise ValueError(f'non-monotonic {kind}: {e}/{n}')
                    stored[e][n][kind] = arr
        audit = dict(counts=dict(self.counts), consumed_nodes=list(NODES),
                     continuous_states=5, reset_count=0, gap_policy='NO_SYNTHETIC_UPDATES',
                     holdout_used_for_fit=False, cursor=stop,
                     clock_sha256=sha(CLOCK),
                     clock_role='Existing cross-node TIMER2 mapping only; no ranges, anchor layout, UWB positions or payloads consumed',
                     imu_payload_only=True, uwb_payload_decodes=0,
                     inter_action_motion_retained=keep_continuous,
                     contracts=contracts)
        return stored, audit


def save_input(path: Path, episodes: dict):
    np.savez_compressed(path, **{f'{e}/{n}/{k}': a for e, ns in episodes.items()
                               for n, ks in ns.items() for k, a in ks.items()})
