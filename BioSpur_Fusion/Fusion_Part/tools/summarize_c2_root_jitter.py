#!/usr/bin/env python3
"""Compare saved estimator outputs; never smooth or relabel motion as truth."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np


FIELDS = ('time_s', 'roots_b', 'root_state_b', 'uwb_time_s', 'uwb_node',
          'uwb_state_delta', 'uwb_accepted')


def load(path):
    with np.load(path, allow_pickle=False) as archive:
        return {key: archive[key] for key in FIELDS}


def summarize(data, start, stop):
    frame = (data['time_s'] >= start) & (data['time_s'] < stop)
    time, root = data['time_s'][frame], data['roots_b'][frame]
    if len(time) < 2:
        return {'frames': len(time)}
    dt = np.diff(time)
    steps = np.diff(root, axis=0)
    # A real missing-input interval is not a native 5 ms teleport.
    native = dt <= .0075
    velocity = steps / dt[:, None]
    events = ((data['uwb_time_s'] >= start) & (data['uwb_time_s'] < stop)
              & data['uwb_accepted'])
    delta = data['uwb_state_delta'][events]
    nodes = data['uwb_node'][events]
    result = {
        'frames': len(time), 'accepted_updates': int(events.sum()),
        'z_range_m': float(np.ptp(root[:, 2])),
        'z_total_variation_m': float(np.abs(steps[:, 2]).sum()),
        'max_displacement_from_window_start_m': float(np.linalg.norm(root-root[0], axis=1).max()),
        'final_displacement_from_window_start_m': float(np.linalg.norm(root[-1]-root[0])),
        'native_z_step_max_m': float(np.abs(steps[native, 2]).max()) if native.any() else None,
        'native_z_speed_p95_mps': float(np.quantile(np.abs(velocity[native, 2]), .95)) if native.any() else None,
        'native_z_speed_max_mps': float(np.abs(velocity[native, 2]).max()) if native.any() else None,
        'velocity_state_max_mps': float(np.linalg.norm(data['root_state_b'][frame, 3:6], axis=1).max()),
        'gap_count': int((~native).sum()),
        'gap_step_max_m': float(np.linalg.norm(steps[~native], axis=1).max()) if (~native).any() else 0.,
        'nodes': {},
    }
    for column, name in ((2, 'position_z'), (5, 'velocity_z')):
        v = delta[:, column]
        result[name] = dict(mean_absolute=float(np.mean(abs(v))) if len(v) else 0.,
                           p95_absolute=float(np.quantile(abs(v), .95)) if len(v) else 0.,
                           maximum_absolute=float(np.max(abs(v))) if len(v) else 0.)
    for node in np.unique(nodes):
        d = delta[nodes == node]
        result['nodes'][str(node)] = dict(count=len(d), mean_z_delta_m=float(d[:, 2].mean()),
                                         mean_vz_delta_mps=float(d[:, 5].mean()))
    return result


def compare(old_path, new_path, output):
    old, new = load(old_path), load(new_path)
    for key in ('time_s', 'uwb_time_s', 'uwb_node'):
        np.testing.assert_array_equal(old[key], new[key], err_msg=key)
    np.testing.assert_array_equal(old['roots_b'][0], new['roots_b'][0])
    metadata = json.loads(new_path.with_name('RESULT.json').read_text())
    regions = metadata['tag_geometry_metadata']['regions']
    begin, end = float(new['time_s'][0]), float(np.nextafter(new['time_s'][-1], np.inf))
    result = dict(scope='RAW_ESTIMATOR_MOTION_NOT_POSITION_ACCURACY',
                  baseline=str(old_path.resolve()), candidate=str(new_path.resolve()),
                  full=dict(baseline=summarize(old, begin, end), candidate=summarize(new, begin, end)),
                  regions=[])
    for row in regions:
        start, stop = row['start_ns'] * 1e-9, row['stop_ns'] * 1e-9
        result['regions'].append(dict(region=row['region_id'], kind=row['kind'],
                                      baseline=summarize(old, start, stop),
                                      candidate=summarize(new, start, stop)))
    output.write_text(json.dumps(result, indent=2) + '\n')
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline', type=Path, required=True)
    parser.add_argument('--candidate', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    report = compare(args.baseline, args.candidate, args.output)
    print(json.dumps(report['full'], indent=2))
