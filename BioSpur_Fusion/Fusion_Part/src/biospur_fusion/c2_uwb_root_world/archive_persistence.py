"""Durable replay artifacts; this owner never changes estimator arrays.

A core archive is independently useful. Auxiliary completion is a separate
manifest fact, never inferred from a directory containing some files.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import time

import numpy as np


def _sync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _atomic_write(path: Path, writer) -> None:
    path = Path(path)
    descriptor, temporary = tempfile.mkstemp(prefix='.' + path.name + '.', dir=path.parent)
    try:
        with os.fdopen(descriptor, 'wb') as stream:
            writer(stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        _sync_directory(path.parent)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def atomic_json(path: Path, value) -> None:
    payload = (json.dumps(value, indent=2) + '\n').encode('utf-8')
    _atomic_write(path, lambda stream: stream.write(payload))


def atomic_npz(path: Path, arrays: dict) -> float:
    """Store without compression; returns write+durability wall seconds."""
    started = time.perf_counter()
    _atomic_write(path, lambda stream: np.savez(stream, **arrays))
    return time.perf_counter() - started


def persist_ancillary(output: Path, save) -> float:
    """Stage all auxiliary generation before promoting any auxiliary file.

    Per-file rename is atomic; the caller sets ancillary_complete only after
    this returns. A termination during promotion can leave a partial set, but
    cannot turn the already durable core manifest into a completion claim.
    """
    started = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix='.ancillary-', dir=output) as temporary:
        staging = Path(temporary)
        save(staging)
        for source in sorted(staging.iterdir()):
            if not source.is_file():
                raise ValueError('ancillary writer must produce flat files')
            with source.open('rb') as stream:
                os.fsync(stream.fileno())
            os.replace(source, output / source.name)
        _sync_directory(output)
    return time.perf_counter() - started
