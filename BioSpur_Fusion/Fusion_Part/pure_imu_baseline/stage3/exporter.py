"""Offline raw/corrected viewer export with immutable parallel buffers."""
from __future__ import annotations

import base64
import gzip
import hashlib
import json
from pathlib import Path

import numpy as np

from pure_imu_baseline.config import GEOMETRY
from pure_imu_baseline.stage2.config import BONES, SEGMENT_ORIGIN_JOINT

ARRAY_KEYS = (
    "time_s", "q_GB_wxyz", "corrected_q_GB_wxyz", "valid", "filter_reset",
    "joint_positions_m", "corrected_joint_positions_m", "joint_available",
    "correction_rad", "bias_rad_s", "correction_confidence", "correction_state",
    "inactive_reason", "observation_type", "correction_epoch", "nearest_gap_s",
)


def _sha(a: np.ndarray) -> str:
    return hashlib.sha256(np.ascontiguousarray(a).tobytes()).hexdigest()


def _pack(arrays: dict[str, np.ndarray]) -> tuple[bytes, dict]:
    payload = bytearray(); schema = {}
    for name in ARRAY_KEYS:
        a = np.ascontiguousarray(arrays[name])
        padding = (-len(payload)) % max(1, a.dtype.itemsize)
        payload.extend(b"\0" * padding); offset = len(payload)
        encoded = a.tobytes(); payload.extend(encoded)
        schema[name] = {"dtype": a.dtype.str, "shape": list(a.shape),
                        "offset": offset, "nbytes": len(encoded), "sha256": _sha(a)}
    return bytes(payload), schema


def _write_chunks(path: Path, compressed: bytes) -> None:
    encoded = base64.b64encode(compressed).decode("ascii")
    chunks = [encoded[i:i+1_048_576] for i in range(0, len(encoded), 1_048_576)]
    path.write_text("window.BioSpurPayloadChunks=[\n" +
                    "\n".join(json.dumps(x)+"," for x in chunks) + "\n];\n", encoding="ascii")


def _edges(joint_names: list[str]) -> list[dict]:
    index = {name: i for i, name in enumerate(joint_names)}
    return [{"name": name, "a": index[a], "b": index[b], "class": cls}
            for name, a, b, cls in BONES]


def _page(capture: str, meta: dict) -> str:
    status = ("CORRECTED_BRANCH_PROMOTED_RATE_ONLY" if meta["corrected_branch_promoted"] else
              "EXPERIMENTAL_CORRECTED_BRANCH_NOT_PROMOTED · RAW_BASELINE_REMAINS_DEFAULT")
    return f'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>BioSpur Capture {capture} raw/corrected 3D</title><link rel="stylesheet" href="C123_STAGE3_VIEWER.css"></head>
<body><main id="biospur-viewer" data-capture="{capture}">
<header><h1>BioSpur Stage 3 — Capture {capture}</h1><div class="branch-status">{status}</div><div id="load-status" role="status">Loading immutable raw/corrected buffers…</div></header>
<section class="toolbar" aria-label="Playback controls">
<label>Capture <select id="capture-select" data-testid="capture-select"><option value="1">Capture 1</option><option value="2">Capture 2</option><option value="3">Capture 3</option></select></label>
<label>Mode <select id="comparison-mode" data-testid="comparison-mode"><option>RAW</option><option>CORRECTED</option><option>RAW_AND_CORRECTED_OVERLAY</option><option>CORRECTION_DIFFERENCE</option></select></label>
<label>Node <select id="node-detail">{''.join(f'<option value="{i}">{node}</option>' for i,node in enumerate(meta['node_ids']))}</select></label>
<button id="play-pause" type="button" data-testid="play-pause">Play</button><button id="step-back" type="button">−1 frame</button><button id="step-forward" type="button">+1 frame</button>
<label>Speed <select id="speed-select"><option value="0.25">0.25×</option><option value="0.5">0.5×</option><option value="1" selected>1×</option><option value="2">2×</option><option value="4">4×</option></select></label>
<label>Camera <select id="camera-mode" data-testid="camera-mode"><option>FREE_ORBIT</option><option>WORLD_FIXED_FRONT</option><option>WORLD_FIXED_SIDE</option><option>WORLD_FIXED_TOP</option><option>PELVIS_FRONT_LOCKED</option><option>PELVIS_SIDE_LOCKED</option></select></label><button id="reset-camera" type="button">Reset camera</button></section>
<section class="timeline"><input id="timeline" type="range" min="0" max="1" step="1" value="0" data-testid="timeline"><output id="time-output">0.000 s</output><label>Timestamp (s) <input id="timestamp-input" type="number" min="0" step="0.001" value="0"></label><button id="jump-time" type="button">Jump</button><label>Event <select id="event-select"></select></label><button id="jump-event" type="button">Jump to event</button></section>
<section class="toggles"><label><input id="toggle-joints" type="checkbox"> Joint labels</label><label><input id="toggle-segments" type="checkbox"> Segment names</label><label><input id="toggle-sensors" type="checkbox"> Sensor IDs</label><label><input id="toggle-global-axes" type="checkbox" checked> Global axes</label><label><input id="toggle-body-axes" type="checkbox" checked> Pelvis/body axes</label><label><input id="toggle-segment-frames" type="checkbox"> Per-segment frames</label><label><input id="toggle-ground" type="checkbox" checked> Ground plane</label><label><input id="toggle-ghost" type="checkbox" checked> Initial-pose ghost</label></section>
<section class="viewport-wrap"><canvas id="viewport" data-testid="viewport" aria-label="Interactive three-dimensional raw and corrected skeleton"></canvas><div id="view-readout"></div></section>
<footer>Raw is authoritative · overlay uses raw ghost + corrected solid · drag orbit · shift/right drag pan · wheel zoom</footer>
</main><script>window.BioSpurViewerMeta={json.dumps(meta,separators=(',',':'),allow_nan=False)};</script><script src="CAPTURE{capture}_STAGE3_VIEWER_DATA.js"></script><script src="C123_STAGE3_VIEWER_CORE.js"></script></body></html>'''


def _patched_core(stage2_core: Path) -> str:
    core = stage2_core.read_text(encoding="utf-8")
    core = core.replace('"<f4": Float32Array, "|u1": Uint8Array',
                        '"<f4": Float32Array, "|u1": Uint8Array, "<u4": Uint32Array')
    core = core.replace(
        'function qAt(frame, segment) {\n    const offset = (frame * segmentCount + segment) * 4;\n    return [arrays.q_GB_wxyz[offset], arrays.q_GB_wxyz[offset+1], arrays.q_GB_wxyz[offset+2], arrays.q_GB_wxyz[offset+3]];\n  }',
        'function qAt(frame, segment) {\n    const offset = (frame * segmentCount + segment) * 4;\n    const mode = document.getElementById("comparison-mode").value;\n    const a = mode === "RAW" ? arrays.q_GB_wxyz : arrays.corrected_q_GB_wxyz;\n    return [a[offset], a[offset+1], a[offset+2], a[offset+3]];\n  }')
    core = core.replace(
        'function pointAt(frame, joint) {\n    const offset = (frame * jointCount + joint) * 3;\n    return [arrays.joint_positions_m[offset], arrays.joint_positions_m[offset+1], arrays.joint_positions_m[offset+2]];\n  }',
        'function pointAt(frame, joint) {\n    const offset = (frame * jointCount + joint) * 3;\n    const mode = document.getElementById("comparison-mode").value;\n    const a = mode === "RAW" ? arrays.joint_positions_m : arrays.corrected_joint_positions_m;\n    return [a[offset], a[offset+1], a[offset+2]];\n  }\n\n  function rawPointAt(frame, joint) {\n    const offset = (frame * jointCount + joint) * 3;\n    return [arrays.joint_positions_m[offset], arrays.joint_positions_m[offset+1], arrays.joint_positions_m[offset+2]];\n  }')
    marker = '    if (toggles["global-axes"].checked) axisFrame(project, [0, 0, 0], [1, 0, 0, 0], 0.20);\n\n'
    injected = marker + '''    const comparisonMode = document.getElementById("comparison-mode").value;
    if (comparisonMode === "RAW_AND_CORRECTED_OVERLAY" || comparisonMode === "CORRECTION_DIFFERENCE") {
      meta.edges.forEach(edge => {
        if (!jointValid(frame, edge.a) || !jointValid(frame, edge.b)) return;
        line(project, rawPointAt(frame, edge.a), rawPointAt(frame, edge.b), "#94a3b8", 3, 0.38);
        if (comparisonMode === "CORRECTION_DIFFERENCE") {
          line(project, rawPointAt(frame, edge.b), pointAt(frame, edge.b), "#f43f5e", 1, 0.85);
        }
      });
    }

'''
    if marker not in core: raise RuntimeError("Stage 2 core injection point missing")
    core = core.replace(marker, injected)
    old = 'readout.textContent = `frame ${frame.toLocaleString()} / ${(meta.frames-1).toLocaleString()} · ${time.toFixed(3)} s · valid ${validSegments}/10 · common yaw ${Number.isFinite(commonYaw) ? commonYaw.toFixed(1) : "n/a"}° · inter-segment spread ${Number.isFinite(spread) ? spread.toFixed(1) : "n/a"}°${resetNodes.length ? ` · RESET ${resetNodes.join(", ")}` : ""}`;'
    new = '''const selectedNode = Number(document.getElementById("node-detail").value);
    const so = frame*segmentCount+selectedNode;
    const reasonNames = ["PELVIS_GAUGE", "ACTIVE_STATIONARY_RATE", "INSUFFICIENT_SUPPORT", "RAW_INVALID", "GAP_RESTART", "LOW_CONFIDENCE", "KINEMATIC_WITHHELD"];
    const obsNames = ["NONE", "STATIONARY_DIFFERENTIAL_YAW_RATE"];
    readout.textContent = `frame ${frame.toLocaleString()} / ${(meta.frames-1).toLocaleString()} · ${time.toFixed(3)} s · mode ${comparisonMode} · valid ${validSegments}/10 · ${meta.node_ids[selectedNode]} c=${(arrays.correction_rad[so]*180/Math.PI).toFixed(3)}° b=${(arrays.bias_rad_s[so]*180/Math.PI).toFixed(4)}°/s confidence=${arrays.correction_confidence[so].toFixed(3)} · ${obsNames[arrays.observation_type[so]] || "NONE"}/${reasonNames[arrays.inactive_reason[so]] || "UNKNOWN"} · epoch ${arrays.correction_epoch[so]} · nearest gap ${Number.isFinite(arrays.nearest_gap_s[so]) ? arrays.nearest_gap_s[so].toFixed(3)+" s" : "n/a"}${resetNodes.length ? ` · RESET ${resetNodes.join(", ")}` : ""}`;'''
    if old not in core: raise RuntimeError("Stage 2 readout injection point missing")
    core = core.replace(old, new)
    core = core.replace('    const commonYaw = arrays.common_body_yaw_deg[frame];\n    const spread = arrays.inter_segment_heading_spread_deg[frame];\n', '')
    core = core.replace('window.location.href = `CAPTURE${captureSelect.value}_INTERACTIVE_3D.html`',
                        'window.location.href = `CAPTURE${captureSelect.value}_RAW_CORRECTED_INTERACTIVE_3D.html`')
    core = core.replace('Object.values(toggles).forEach(toggle => toggle.addEventListener("change", drawFrame));',
                        'Object.values(toggles).forEach(toggle => toggle.addEventListener("change", drawFrame));\n    document.getElementById("comparison-mode").addEventListener("change", drawFrame);\n    document.getElementById("node-detail").addEventListener("change", drawFrame);')
    core = core.replace('arrays.q_GB_wxyz.length === meta.frames*segmentCount*4',
                        'arrays.q_GB_wxyz.length === meta.frames*segmentCount*4 && arrays.corrected_q_GB_wxyz.length === arrays.q_GB_wxyz.length')
    core = core.replace('await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));',
                        'await new Promise(resolve => setTimeout(resolve, 0));')
    before_result = '    const result = {capture: meta.capture, pass: checks.every(check => check.pass), checks,'
    extra = '''    const rawCorrectionHash = meta.buffer_sha256.correction_rad;
    for (const mode of ["RAW", "CORRECTED", "RAW_AND_CORRECTED_OVERLAY", "CORRECTION_DIFFERENCE"]) {
      document.getElementById("comparison-mode").value = mode;
      document.getElementById("comparison-mode").dispatchEvent(new Event("change", {bubbles:true}));
      assertion(checks, `comparison mode ${mode}`, document.getElementById("comparison-mode").value === mode);
    }
    const cameraFirewall = rawCorrectionHash === meta.buffer_sha256.correction_rad;
    assertion(checks, "camera/mode correction firewall", cameraFirewall);
    document.getElementById("comparison-mode").value = "RAW"; drawFrame();

'''
    if before_result not in core: raise RuntimeError("Stage 2 selftest injection point missing")
    core = core.replace(before_result, extra + before_result)
    return core


def write_shared(output: Path, stage2_source: Path, promoted: bool) -> None:
    core = _patched_core(stage2_source / "viewer_core.js")
    (output / "C123_STAGE3_VIEWER_CORE.js").write_text(core, encoding="utf-8")
    css = (stage2_source / "viewer.css").read_text(encoding="utf-8")
    css += "\n.branch-status{font-weight:700;color:#fbbf24}.toolbar select#comparison-mode{min-width:16rem}\n"
    (output / "C123_STAGE3_VIEWER.css").write_text(css, encoding="utf-8")
    status = "promoted rate-only branch" if promoted else "experimental branch not promoted; raw remains default"
    cards = "".join(f'<a class="capture-card" href="CAPTURE{c}_RAW_CORRECTED_INTERACTIVE_3D.html"><strong>Capture {c}</strong><span>Open raw/corrected comparison</span></a>' for c in ("1","2","3"))
    index = f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>BioSpur Stage 3 raw/corrected review</title><link rel="stylesheet" href="C123_STAGE3_VIEWER.css"></head><body><main class="index"><h1>BioSpur Stage 3 raw/corrected review</h1><p>{status}. Raw is authoritative and the default mode.</p><section class="capture-grid">{cards}</section><p class="note">Open directly with file:// in Chromium, or serve this directory with python3 -m http.server.</p></main></body></html>'''
    (output / "C123_RAW_CORRECTED_INTERACTIVE_VIEWER_INDEX.html").write_text(index, encoding="utf-8")


def export_capture(capture: str, data: dict[str, np.ndarray], events: list[dict],
                   output: Path, promoted: bool) -> dict:
    arrays = {
        "time_s": np.ascontiguousarray(data["time_s"], dtype="<f8"),
        "q_GB_wxyz": np.ascontiguousarray(data["q_GB_wxyz"], dtype="<f4"),
        "corrected_q_GB_wxyz": np.ascontiguousarray(data["corrected_q_GB_wxyz"], dtype="<f4"),
        "valid": np.ascontiguousarray(data["valid"], dtype=np.uint8),
        "filter_reset": np.ascontiguousarray(data["filter_reset"], dtype=np.uint8),
        "joint_positions_m": np.ascontiguousarray(data["joint_positions_m"], dtype="<f4"),
        "corrected_joint_positions_m": np.ascontiguousarray(data["corrected_joint_positions_m"], dtype="<f4"),
        "joint_available": np.ascontiguousarray(data["joint_available"], dtype=np.uint8),
        "correction_rad": np.ascontiguousarray(data["correction_rad"], dtype="<f4"),
        "bias_rad_s": np.ascontiguousarray(data["bias_rad_s"], dtype="<f4"),
        "correction_confidence": np.ascontiguousarray(data["correction_confidence"], dtype="<f4"),
        "correction_state": np.ascontiguousarray(data["correction_state"], dtype=np.uint8),
        "inactive_reason": np.ascontiguousarray(data["inactive_reason"], dtype=np.uint8),
        "observation_type": np.ascontiguousarray(data["observation_type"], dtype=np.uint8),
        "correction_epoch": np.ascontiguousarray(data["correction_epoch"], dtype="<u4"),
        "nearest_gap_s": np.ascontiguousarray(data["nearest_gap_s"], dtype="<f4"),
    }
    raw, schema = _pack(arrays); compressed = gzip.compress(raw, compresslevel=6, mtime=0)
    _write_chunks(output / f"CAPTURE{capture}_STAGE3_VIEWER_DATA.js", compressed)
    joint_names = [str(x) for x in data["joint_names"]]
    segment_names = [str(x) for x in data["segment_names"]]
    meta = {"schema": "biospur.pure_imu.stage3.viewer.v1", "capture": capture,
            "frames": len(data["time_s"]), "duration_s": float(data["time_s"][-1]),
            "display_rate_hz": float(1/np.median(np.diff(data["time_s"]))), "arrays": schema,
            "node_ids": [str(x) for x in data["node_ids"]], "segment_names": segment_names,
            "joint_names": joint_names, "edges": _edges(joint_names),
            "segment_origins": [joint_names.index(SEGMENT_ORIGIN_JOINT[x]) for x in segment_names],
            "events": events, "ghost_frame": 0,
            "ground_z_m": -(GEOMETRY["thigh_left"]+GEOMETRY["shank_left"]),
            "colors": {"left":"#f59e0b","right":"#22d3ee","core":"#f8fafc","pelvis":"#22c55e"},
            "corrected_branch_promoted": promoted, "raw_default": True,
            "buffer_sha256": {name: spec["sha256"] for name,spec in schema.items()},
            "payload": {"raw_sha256": hashlib.sha256(raw).hexdigest(),
                        "gzip_sha256": hashlib.sha256(compressed).hexdigest(),
                        "raw_bytes": len(raw), "gzip_bytes": len(compressed)}}
    (output / f"CAPTURE{capture}_RAW_CORRECTED_INTERACTIVE_3D.html").write_text(_page(capture, meta), encoding="utf-8")
    return meta
