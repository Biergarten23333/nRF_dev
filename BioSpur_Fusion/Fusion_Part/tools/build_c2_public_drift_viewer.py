"""Allowlisted presentation export; never exports ranging geometry or provenance."""
import argparse
import json
from pathlib import Path

import numpy as np

from build_c2_full_continuous_ab_viewer import LINES, _pack


def build(source: Path, output: Path):
    with np.load(source) as data:
        t = data['time_s'].astype(float)
        names = data['joint_names'].tolist()
        arrays = [data[k] for k in ('roots_a', 'roots_b', 'joints_relative', 'joints_relative_b')]
    n = len(t)
    if n < 2 or not np.all(np.diff(t) > 0):
        raise ValueError('Invalid time axis')
    for a, shape in zip(arrays, [(n, 3)] * 2 + [(n, len(names), 3)] * 2):
        if a.shape != shape or not np.isfinite(a).all():
            raise ValueError('Invalid native output')
    step = max(1, int(np.ceil(.02 / np.median(np.diff(t)))))
    selected = np.unique(np.r_[np.arange(0, n, step), n - 1])
    payload = dict(time=_pack(t[selected] - t[0]), count=len(selected), joints=len(names),
                   lines=[[names.index(a), names.index(b), c] for a, b, c in LINES],
                   ground_z=0, duration=float(t[-1] - t[0]))
    payload.update({k: _pack(a[selected]) for k, a in zip(('a', 'b', 'pa', 'pb'), arrays)})
    payload['metrics'] = [dict(end=float(np.linalg.norm(a[-1] - a[0])),
                               maximum=float(np.linalg.norm(a - a[0], axis=1).max()))
                          for a in arrays[:2]]
    template = Path(__file__).with_name('templates').joinpath('public_drift_ab.html').read_text()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(template.replace('__PAYLOAD__', json.dumps(payload, separators=(',', ':'))))
    return payload['metrics']


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('source', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    print(json.dumps(build(args.source, args.output), ensure_ascii=False))
