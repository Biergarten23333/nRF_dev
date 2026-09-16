"""Standalone synchronized 3-D orbit viewer; consumes frozen joint trajectories."""
from __future__ import annotations

import json
import math
from pathlib import Path


def _finite_json(value):
    if isinstance(value, dict):
        return {key: _finite_json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_finite_json(item) for item in value]
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def write_viewer(path: Path, payload: dict):
    if payload.get('camera_convention') not in (None, 'C2_FROZEN'):
        raise ValueError('unknown replay camera convention')
    template = Path(__file__).with_name('viewer_template.html').read_text()
    data = json.dumps(_finite_json(payload), allow_nan=False, separators=(',', ':'))
    path.write_text(template.replace('__TRAJECTORY_DATA__', data.replace('</', '<\\/')))
