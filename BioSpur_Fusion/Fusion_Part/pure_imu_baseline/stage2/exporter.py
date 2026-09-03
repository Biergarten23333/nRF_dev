"""Deterministic compact typed-array export for the offline viewer."""
from __future__ import annotations

import base64
import gzip
import hashlib
import json
import shutil
from pathlib import Path

import numpy as np

from pure_imu_baseline.config import GEOMETRY

from .config import (BONES, CALIBRATION_WINDOWS_S, SEGMENT_ORIGIN_JOINT,
                     VIEWER_PAYLOAD_CHUNK_CHARS)

VIEWER_ARRAY_KEYS = (
    "time_s", "q_GB_wxyz", "valid", "filter_reset",
    "joint_positions_m", "joint_available",
    "common_body_yaw_deg", "inter_segment_heading_spread_deg",
    "pelvis_tilt_deg", "torso_tilt_deg",
)


def _viewer_arrays(data: dict[str, np.ndarray], metrics: dict[str, np.ndarray]) -> dict[str, np.ndarray]:
    return {
        "time_s": np.ascontiguousarray(data["time_s"], dtype="<f8"),
        "q_GB_wxyz": np.ascontiguousarray(data["q_GB_wxyz"], dtype="<f4"),
        "valid": np.ascontiguousarray(data["valid"], dtype=np.uint8),
        "filter_reset": np.ascontiguousarray(data["filter_reset"], dtype=np.uint8),
        "joint_positions_m": np.ascontiguousarray(data["joint_positions_m"], dtype="<f4"),
        "joint_available": np.ascontiguousarray(data["joint_available"], dtype=np.uint8),
        **{key: np.ascontiguousarray(metrics[key], dtype="<f4") for key in metrics},
    }


def pack_arrays(arrays: dict[str, np.ndarray]) -> tuple[bytes, dict]:
    payload = bytearray()
    schema = {}
    for name in VIEWER_ARRAY_KEYS:
        array = np.ascontiguousarray(arrays[name])
        alignment = max(1, array.dtype.itemsize)
        padding = (-len(payload)) % alignment
        payload.extend(b"\0" * padding)
        offset = len(payload)
        encoded = array.tobytes(order="C")
        payload.extend(encoded)
        schema[name] = {
            "dtype": array.dtype.str,
            "shape": list(array.shape),
            "offset": offset,
            "nbytes": len(encoded),
        }
    return bytes(payload), schema


def unpack_arrays(payload: bytes, schema: dict) -> dict[str, np.ndarray]:
    result = {}
    for name, spec in schema.items():
        result[name] = np.frombuffer(payload, dtype=np.dtype(spec["dtype"]),
                                     count=int(np.prod(spec["shape"])),
                                     offset=spec["offset"]).reshape(spec["shape"])
    return result


def _write_data_js(path: Path, compressed: bytes) -> None:
    encoded = base64.b64encode(compressed).decode("ascii")
    pieces = [encoded[i:i+VIEWER_PAYLOAD_CHUNK_CHARS]
              for i in range(0, len(encoded), VIEWER_PAYLOAD_CHUNK_CHARS)]
    lines = ["window.BioSpurPayloadChunks = ["]
    lines.extend(json.dumps(piece) + "," for piece in pieces)
    lines.append("];\n")
    path.write_text("\n".join(lines), encoding="ascii")


def _capture_page(capture: str, metadata: dict) -> str:
    meta = json.dumps(metadata, separators=(",", ":"), allow_nan=False)
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>BioSpur Capture {capture} interactive 3D</title>
  <link rel="stylesheet" href="C123_VIEWER.css">
</head>
<body>
<main id="biospur-viewer" data-capture="{capture}">
  <header>
    <h1>BioSpur pure-IMU — Capture {capture}</h1>
    <div id="load-status" role="status">Loading frozen typed-array trajectory…</div>
  </header>
  <section class="toolbar" aria-label="Playback controls">
    <label>Capture <select id="capture-select" data-testid="capture-select">
      <option value="1">Capture 1</option><option value="2">Capture 2</option><option value="3">Capture 3</option>
    </select></label>
    <button id="play-pause" type="button" data-testid="play-pause">Play</button>
    <button id="step-back" type="button" aria-label="Step one display frame backward">−1 frame</button>
    <button id="step-forward" type="button" aria-label="Step one display frame forward">+1 frame</button>
    <label>Speed <select id="speed-select"><option value="0.25">0.25×</option><option value="0.5">0.5×</option><option value="1" selected>1×</option><option value="2">2×</option><option value="4">4×</option></select></label>
    <label>Camera <select id="camera-mode" data-testid="camera-mode">
      <option>FREE_ORBIT</option><option>WORLD_FIXED_FRONT</option><option>WORLD_FIXED_SIDE</option><option>WORLD_FIXED_TOP</option><option>PELVIS_FRONT_LOCKED</option><option>PELVIS_SIDE_LOCKED</option>
    </select></label>
    <button id="reset-camera" type="button">Reset camera</button>
  </section>
  <section class="timeline" aria-label="Timeline controls">
    <input id="timeline" type="range" min="0" max="1" step="1" value="0" aria-label="Complete capture timeline" data-testid="timeline">
    <output id="time-output">0.000 s</output>
    <label>Timestamp (s) <input id="timestamp-input" type="number" min="0" step="0.001" value="0"></label>
    <button id="jump-time" type="button">Jump</button>
    <label>Event <select id="event-select"></select></label>
    <button id="jump-event" type="button">Jump to event</button>
  </section>
  <section class="toggles" aria-label="Display toggles">
    <label><input id="toggle-joints" type="checkbox"> Joint labels</label>
    <label><input id="toggle-segments" type="checkbox"> Segment names</label>
    <label><input id="toggle-sensors" type="checkbox"> Sensor IDs</label>
    <label><input id="toggle-global-axes" type="checkbox" checked> Global axes</label>
    <label><input id="toggle-body-axes" type="checkbox" checked> Pelvis/body axes</label>
    <label><input id="toggle-segment-frames" type="checkbox"> Per-segment frames</label>
    <label><input id="toggle-ground" type="checkbox" checked> Ground plane</label>
    <label><input id="toggle-ghost" type="checkbox" checked> Initial-pose ghost</label>
  </section>
  <section class="viewport-wrap">
    <canvas id="viewport" data-testid="viewport" aria-label="Interactive three-dimensional pure-IMU skeleton. Drag to orbit, shift-drag to pan, and use the wheel to zoom."></canvas>
    <div id="view-readout" aria-live="polite"></div>
  </section>
  <footer>Drag: orbit · Shift-drag or right-drag: pan · Wheel: zoom · model coordinates remain immutable</footer>
</main>
<script>window.BioSpurViewerMeta={meta};</script>
<script src="CAPTURE{capture}_VIEWER_DATA.js"></script>
<script src="C123_VIEWER_CORE.js"></script>
</body>
</html>
"""


def _edge_metadata(joint_names: list[str]) -> list[dict]:
    index = {name: i for i, name in enumerate(joint_names)}
    return [{"name": name, "a": index[a], "b": index[b], "class": display_class}
            for name, a, b, display_class in BONES]


def export_capture(capture: str, data: dict[str, np.ndarray],
                   metrics: dict[str, np.ndarray], events: list[dict],
                   output: Path, static_root: Path) -> tuple[dict, dict]:
    arrays = _viewer_arrays(data, metrics)
    raw, array_schema = pack_arrays(arrays)
    compressed = gzip.compress(raw, compresslevel=6, mtime=0)
    decoded = unpack_arrays(gzip.decompress(compressed), array_schema)
    time_s = arrays["time_s"]
    joint_names = [str(x) for x in data["joint_names"]]
    segment_names = [str(x) for x in data["segment_names"]]
    node_ids = [str(x) for x in data["node_ids"]]
    cal_start, cal_stop = CALIBRATION_WINDOWS_S[capture]
    ghost_frame = int(np.argmin(np.abs(time_s-(cal_start+cal_stop)/2.0)))
    metadata = {
        "schema": "biospur.pure_imu.offline_viewer.v1",
        "capture": capture,
        "title": f"Capture {capture}",
        "frames": int(len(time_s)),
        "duration_s": float(time_s[-1]),
        "display_rate_hz": float(1.0/np.median(np.diff(time_s))),
        "arrays": array_schema,
        "node_ids": node_ids,
        "segment_names": segment_names,
        "joint_names": joint_names,
        "edges": _edge_metadata(joint_names),
        "segment_origins": [joint_names.index(SEGMENT_ORIGIN_JOINT[name]) for name in segment_names],
        "events": events,
        "calibration_window_s": [cal_start, cal_stop],
        "ghost_frame": ghost_frame,
        "ground_z_m": -(GEOMETRY["thigh_left"]+GEOMETRY["shank_left"]),
        "colors": {"left": "#f59e0b", "right": "#22d3ee", "core": "#f8fafc", "pelvis": "#22c55e"},
        "frame_contract": {"global_axes": "+X forward, +Y left, +Z up",
                           "quaternion": "wxyz Hamilton active local-to-global"},
        "payload": {"encoding": "base64(gzip(concatenated typed arrays))",
                    "raw_sha256": hashlib.sha256(raw).hexdigest(),
                    "gzip_sha256": hashlib.sha256(compressed).hexdigest(),
                    "raw_bytes": len(raw), "gzip_bytes": len(compressed)},
    }
    data_path = output / f"CAPTURE{capture}_VIEWER_DATA.js"
    page_path = output / f"CAPTURE{capture}_INTERACTIVE_3D.html"
    _write_data_js(data_path, compressed)
    page_path.write_text(_capture_page(capture, metadata), encoding="utf-8")

    parity = {}
    for key in VIEWER_ARRAY_KEYS:
        original = arrays[key]
        recovered = decoded[key]
        parity[key] = {
            "exact_equal": bool(np.array_equal(original, recovered, equal_nan=True)),
            "source_sha256": hashlib.sha256(original.tobytes()).hexdigest(),
            "roundtrip_sha256": hashlib.sha256(recovered.tobytes()).hexdigest(),
        }
    return metadata, parity


def write_shared_viewer_files(output: Path, static_root: Path) -> None:
    shutil.copyfile(static_root / "viewer_core.js", output / "C123_VIEWER_CORE.js")
    shutil.copyfile(static_root / "viewer.css", output / "C123_VIEWER.css")
    cards = "\n".join(
        f'<a class="capture-card" href="CAPTURE{capture}_INTERACTIVE_3D.html"><strong>Capture {capture}</strong><span>Open complete interactive trajectory</span></a>'
        for capture in ("1", "2", "3")
    )
    index = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>BioSpur pure-IMU interactive review</title><link rel="stylesheet" href="C123_VIEWER.css"></head>
<body><main class="index"><h1>BioSpur pure-IMU interactive 3D review</h1><p>Frozen Stage 1 trajectories · offline · fixed model coordinates</p><section class="capture-grid">{cards}</section><p class="note">Pages open directly with <code>file://</code>. If local script restrictions are enabled, run <code>./launch_viewer.sh</code> and open the printed localhost URL.</p></main></body></html>
"""
    (output / "C123_INTERACTIVE_3D_VIEWER_INDEX.html").write_text(index, encoding="utf-8")
    launcher = """#!/usr/bin/env bash
set -euo pipefail
viewer_dir=$(cd -- "$(dirname -- "$0")" && pwd)
viewer_port=${1:-8765}
echo "BioSpur Stage 2 viewer: http://127.0.0.1:${viewer_port}/C123_INTERACTIVE_3D_VIEWER_INDEX.html"
exec python3 -m http.server --bind 127.0.0.1 --directory "$viewer_dir" "$viewer_port"
"""
    launcher_path = output / "launch_viewer.sh"
    launcher_path.write_text(launcher, encoding="utf-8")
    launcher_path.chmod(0o755)
