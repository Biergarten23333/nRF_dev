#!/usr/bin/env python3
"""Rerender P1 diagnostics from derived JSON after the pixel-audit failure."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from biospur_fusion.v0.c2_progressive.health import (
    render_bias,
    render_frame_graph,
    render_health,
    render_time_gap,
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    root = Path.cwd().resolve()
    run_dir = root / "logs/c2_basis_progressive_20260829T102836Z"
    output = run_dir / "P1_FRONTEND"
    health_path = output / "P1_INPUT_HEALTH_AND_GAPS.json"
    initial_path = output / "P1_INITIAL_STILL_STOCHASTIC_STATE.json"
    health = json.loads(health_path.read_text(encoding="utf-8"))
    initial = json.loads(initial_path.read_text(encoding="utf-8"))
    startup = json.loads((
        root / "config/biospur_fusion_v0_c2_main_contract_20260829/STARTUP_PARAMETERS.json"
    ).read_text(encoding="utf-8"))
    node_to_segment = {row["hardware_id"]: row["segment"] for row in startup["node_mapping"]}
    images = [
        output / "P1_INPUT_HEALTH_R3.png",
        output / "P1_TIME_AND_GAP_R3.png",
        output / "P1_BIAS_AND_DRIFT_R3.png",
        output / "P1_SENSOR_SEGMENT_FRAME_DIAGNOSTIC_R3.png",
    ]
    if any(path.exists() for path in images):
        raise FileExistsError("P1 R3 visual already exists; refusing overwrite")
    render_health(health, images[0])
    render_time_gap(health, images[1])
    render_bias(initial, health, images[2])
    render_frame_graph(
        initial,
        node_to_segment=node_to_segment,
        edges=startup["global_graph"]["directed_edges"],
        output=images[3],
    )
    for path in images:
        path.chmod(0o444)
    result = {
        "schema": "biospur-c2-p1-visual-revision-v3",
        "source_pixel_audit": {
            "path": str(run_dir / "P1_VISUAL_PIXEL_AUDIT_001.json"),
            "sha256": _sha256(run_dir / "P1_VISUAL_PIXEL_AUDIT_001.json"),
        },
        "payload_reopened": False,
        "source_derived_json": [
            {"path": str(health_path), "sha256": _sha256(health_path)},
            {"path": str(initial_path), "sha256": _sha256(initial_path)},
        ],
        "status": "PENDING_WORKER_AND_MONITOR_PIXEL_REINSPECTION",
        "images": [{
            "path": str(path),
            "sha256": _sha256(path),
            "bytes": path.stat().st_size,
        } for path in images],
        "labels": "NON-ANATOMICAL / NOT PASS",
        "gap_panel_binding": {
            "quantity": "sqrt(trace(cumulative orientation covariance))",
            "display_units": "degrees",
            "covariance_units": "radian squared",
            "model": "Sigma_increment = Sigma_bias * gap_s^2 + Sigma_gyro_observation * nominal_dt_s * gap_s",
            "nominal_dt_s": 0.005,
            "provenance": "initial-still median bias covariance and gyro observation covariance; each sealed inter-episode interval is a planned no-update, never an inferred missing-sample run",
            "measured_packet_dropout": False,
            "readiness_or_pass_gate": False
        },
        "saturation_panel_binding": {
            "quantity": "rows with any |int16 raw axis| >= 32760",
            "observed_total": 0,
            "rendering": "single discrete zero category with exact annotation"
        },
        "ik_rebase_repair_used": False,
    }
    result_path = output / "P1_VISUAL_REVISION_003.json"
    with result_path.open("x", encoding="utf-8") as handle:
        json.dump(result, handle, indent=2, sort_keys=True)
        handle.write("\n")
    result_path.chmod(0o444)
    print(json.dumps({**result, "result_sha256": _sha256(result_path)}, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
