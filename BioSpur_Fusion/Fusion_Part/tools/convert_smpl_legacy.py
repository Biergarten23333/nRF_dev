#!/usr/bin/env python3
"""Convert an official legacy SMPL pickle's Chumpy arrays to NumPy arrays.

Run in a separate process. The compatibility aliases exist only during this
conversion; neither upstream sources nor the original model are modified.
Only use on the trusted model downloaded from the official SMPL website.
"""
import argparse
import hashlib
import inspect
import json
from pathlib import Path
import pickle

import numpy as np


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('destination', type=Path)
    args = parser.parse_args()
    if args.destination.exists():
        parser.error('destination exists; original assets are never overwritten')
    # Chumpy 0.70 predates Python 3.11 and NumPy 1.24. It only uses the args
    # field of getargspec in its import-time dependency declarations.
    inspect.getargspec = inspect.getfullargspec
    for name, value in dict(bool=np.bool_, int=int, float=float, complex=complex,
                            object=object, str=str, unicode=str).items():
        if name not in np.__dict__:
            setattr(np, name, value)
    import chumpy
    with args.source.open('rb') as f:
        original = pickle.load(f, encoding='latin1')
    converted = {k: np.asarray(v.r) if isinstance(v, chumpy.Ch) else v for k, v in original.items()}
    required = {'J_regressor', 'weights', 'posedirs', 'shapedirs', 'v_template', 'J', 'f', 'kintree_table'}
    if not required.issubset(converted):
        raise ValueError('not a complete official SMPL model')
    changed = []
    for key, value in original.items():
        if isinstance(value, chumpy.Ch):
            np.testing.assert_array_equal(value.r, converted[key])
            changed.append(key)
    with args.destination.open('xb') as f:
        pickle.dump(converted, f, protocol=4)
    audit = dict(source=str(args.source.resolve()),
        source_sha256=hashlib.sha256(args.source.read_bytes()).hexdigest(),
        output_sha256=hashlib.sha256(args.destination.read_bytes()).hexdigest(),
        converted_chumpy_arrays=changed, numerical_arrays_changed=False,
        shape_components=int(converted['shapedirs'].shape[-1]), chumpy_version='0.70')
    args.destination.with_suffix('.conversion.json').write_text(json.dumps(audit, indent=2)+'\n')
    print(json.dumps(audit, indent=2))


if __name__ == '__main__':
    main()
