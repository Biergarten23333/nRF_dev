"""Export posterior or legacy root-release diagnostic snapshots.

The legacy release is not contact preserving: it delays root corrections while
using current FK. Its existing default is retained because the posterior-only
candidate failed full-session sliding acceptance; neither mode is a slip cure.
"""
import argparse
import hashlib
import json
from pathlib import Path

import numpy as np

from biospur_fusion.c2_uwb_root_world.finite_horizon_release import FiniteHorizonRootRelease


def publish(source: Path, output: Path, period: float | None = None, *, mode: str = 'legacy-root-release'):
    if mode not in ('posterior', 'legacy-root-release'):
        raise ValueError('unknown publication mode')
    if mode == 'posterior' and period is not None:
        raise ValueError('release period requires explicit legacy-root-release mode')
    if output.exists() or output.with_suffix('.json').exists():
        raise FileExistsError(output)
    with np.load(source, allow_pickle=False) as data:
        arrays = {key: data[key] for key in data.files}
    time = arrays['time_s']
    posterior = arrays['roots_b_posterior']
    state = arrays['root_state_b']
    if (time.ndim != 1 or len(time) < 2 or posterior.shape != (len(time), 3)
            or state.shape != (len(time), 9)
            or not all(np.isfinite(x).all() for x in (time, posterior, state))):
        raise ValueError('finite native timestamps and root states are required')
    if not np.array_equal(posterior, state[:, :3]):
        raise ValueError('posterior root and saved state are different snapshots')
    if np.any(np.diff(time) <= 0):
        raise ValueError('source clocks must be ordered')
    if mode == 'posterior':
        # A publication snapshot cannot independently alter root, velocity or
        # relative joints. Estimator corrections remain visible and auditable.
        arrays['roots_b'] = posterior.copy()
        arrays['published_root_state_b'] = state.copy()
        arrays['publication_withheld_m'] = np.zeros_like(posterior)
        report = {
            'role': 'COHERENT_POSTERIOR_PUBLICATION_NOT_ESTIMATOR_CHANGE',
            'source': str(source.resolve()),
            'source_sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
            'publication_mode': mode, 'release_period_s': 0.,
            'maximum_release_delay_s': 0., 'max_withheld_m': 0.,
            'estimator_changed': False, 'root_pose_snapshot_preserved': True,
            'sample_count': len(time), 'scientific_pass': False,
            'chronology': 'INHERITS_SOURCE_ORDER_NOT_A_LIVE_TRANSPORT_VALIDATION',
            'limitation': 'Posterior corrections are estimation revisions, not physical velocity; no smoothness or contact-accuracy claim.',
        }
        output.parent.mkdir(parents=True, exist_ok=True)
        np.savez_compressed(output, **arrays)
        output.with_suffix('.json').write_text(json.dumps(report, indent=2) + '\n')
        return report
    events = arrays['uwb_time_s']
    deltas = arrays['absolute_position_delta_m']
    if np.any(np.diff(time) <= 0) or np.any(np.diff(events) < 0):
        raise ValueError('source clocks must be ordered')
    period = .12 if period is None else period
    release = FiniteHorizonRootRelease(period)
    published = np.empty_like(arrays['roots_b_posterior'])
    withheld = np.empty_like(published)
    published_state = state.copy()
    cursor = 0
    for i, epoch in enumerate(time):
        # In the saved runner, an equal-time IMU publication precedes UWB.
        while cursor < len(events) and events[cursor] < epoch:
            release.install(float(events[cursor]), deltas[cursor])
            cursor += 1
        sample = release.sample(float(epoch), arrays['roots_b_posterior'][i], arrays['root_state_b'][i, 3:6])
        published[i], withheld[i] = sample.position_m, sample.withheld_correction_m
        published_state[i, :3] = sample.position_m
        published_state[i, 3:6] = sample.velocity_mps
    arrays['roots_b'] = published
    arrays['published_root_state_b'] = published_state
    arrays['publication_withheld_m'] = withheld
    output.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(output, **arrays)
    speed = lambda p: np.linalg.norm(np.diff(p, axis=0), axis=1) / np.diff(time)
    report = {
        'role': 'FINITE_HORIZON_PUBLICATION_ONLY_NOT_ESTIMATOR_CHANGE',
        'publication_mode': mode, 'contact_consistent': False,
        'limitation': 'Explicit legacy diagnostic: root-only release may reintroduce foot sliding; not a coherent articulated snapshot.',
        'source': str(source.resolve()), 'source_sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
        'release_period_s': period, 'maximum_release_delay_s': period,
        'global_speed_cap': False, 'estimator_changed': False,
        'chronology': 'INHERITS_OFFLINE_MEASUREMENT_ORDER_NOT_ARRIVAL_CAUSAL',
        'sample_count': len(time), 'max_withheld_m': float(np.linalg.norm(withheld, axis=1).max()),
        'posterior_speed_max_mps': float(speed(arrays['roots_b_posterior']).max()),
        'published_speed_max_mps': float(speed(published).max()),
        'published_speed_p95_mps': float(np.percentile(speed(published), 95)),
        'b_final_displacement_m': float(np.linalg.norm(published[-1] - published[0])),
        'b_max_displacement_m': float(np.linalg.norm(published - published[0], axis=1).max()),
        'scientific_pass': False}
    output.with_suffix('.json').write_text(json.dumps(report, indent=2) + '\n')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--mode', choices=('posterior', 'legacy-root-release'), default='legacy-root-release')
    args = parser.parse_args()
    print(json.dumps(publish(args.input, args.output, mode=args.mode)))
