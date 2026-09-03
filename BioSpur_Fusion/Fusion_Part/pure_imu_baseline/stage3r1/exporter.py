"""Offline Stage 3-R1 viewer and fixed-view comparison export."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from pure_imu_baseline.config import PARENT_CHILD
from pure_imu_baseline.stage3.analysis import nearest_gap_distance
from pure_imu_baseline.stage3.exporter import export_capture as stage3_export_capture
from pure_imu_baseline.stage3.exporter import write_shared as stage3_write_shared


def _node_edge_arrays(raw: dict, formal: dict, promotion: np.ndarray) -> dict:
    n, m = raw["valid"].shape
    values = {name: np.zeros((n, m), dtype=dtype) for name, dtype in (
        ("correction_rad", np.float64), ("bias_rad_s", np.float64),
        ("correction_confidence", np.float64), ("correction_state", np.uint8),
        ("inactive_reason", np.uint8), ("observation_type", np.uint8),
        ("correction_epoch", np.uint32))}
    for edge, child_value in enumerate(formal["edge_children"]):
        child = int(child_value)
        values["correction_rad"][:, child] = formal["edge_eta_rad"][:, edge] if promotion[edge] else 0.0
        values["bias_rad_s"][:, child] = formal["edge_applied_rate_rad_s"][:, edge] if promotion[edge] else 0.0
        values["correction_confidence"][:, child] = formal["edge_confidence"][:, edge]
        values["correction_state"][:, child] = formal["edge_state"][:, edge]
        values["inactive_reason"][:, child] = formal["edge_reason"][:, edge]
        values["observation_type"][:, child] = formal["edge_stationary"][:, edge].astype(np.uint8)
        values["correction_epoch"][:, child] = formal["edge_epoch"][:, edge]
    return values


def export_capture(capture: str, raw: dict, formal: dict, corrected_q: np.ndarray,
                   corrected_positions: np.ndarray, promotion: np.ndarray,
                   output: Path, events: list[dict]) -> dict:
    mapped = {key: value for key, value in raw.items()}
    mapped.update(_node_edge_arrays(raw, formal, promotion))
    mapped["corrected_q_GB_wxyz"] = corrected_q
    mapped["corrected_joint_positions_m"] = corrected_positions
    mapped["nearest_gap_s"] = nearest_gap_distance(raw["time_s"], raw["valid"], raw["filter_reset"])
    original_ids = [str(value) for value in raw["node_ids"]]
    names = [f"{p}->{c}" for p, c in PARENT_CHILD]
    display_ids = original_ids.copy()
    for edge, child_value in enumerate(formal["edge_children"]):
        status = "PROMOTED" if promotion[edge] else "NOT_PROMOTED"
        display_ids[int(child_value)] = f"{original_ids[int(child_value)]} · {names[edge]} · {status}"
    mapped["node_ids"] = np.array(display_ids)
    meta = stage3_export_capture(capture, mapped, events, output, bool(np.any(promotion)))
    return meta


def write_shared(output: Path) -> None:
    stage3_source = Path(__file__).resolve().parents[1] / "stage2"
    stage3_write_shared(output, stage3_source, False)
    core_path = output / "C123_STAGE3_VIEWER_CORE.js"
    core = core_path.read_text(encoding="utf-8")
    replacements = (
        ("RAW_AND_CORRECTED_OVERLAY", "OVERLAY"),
        ("CORRECTION_DIFFERENCE", "DIFFERENCE"),
        ('"CORRECTED"', '"STAGE3R1_ELIGIBLE_EDGES_ONLY"'),
        (" c=${", " eta=${"),
        (" b=${", " dη/dt=${"),
        ('const obsNames = ["NONE", "STATIONARY_DIFFERENTIAL_YAW_RATE"];',
         'const obsNames = ["EDGE_NOT_STATIONARY", "EDGE_STATIONARY"];'),
    )
    for old, new in replacements:
        core = core.replace(old, new)
    core_path.write_text(core, encoding="utf-8")
    for capture in "123":
        page = output / f"CAPTURE{capture}_RAW_CORRECTED_INTERACTIVE_3D.html"
        text = page.read_text(encoding="utf-8")
        text = text.replace("BioSpur Stage 3", "BioSpur Stage 3-R1")
        text = text.replace("RAW_AND_CORRECTED_OVERLAY", "OVERLAY")
        text = text.replace("CORRECTION_DIFFERENCE", "DIFFERENCE")
        text = text.replace("<option>CORRECTED</option>", "<option>STAGE3R1_ELIGIBLE_EDGES_ONLY</option>")
        text = text.replace("biospur.pure_imu.stage3.viewer.v1", "biospur.pure_imu.stage3r1.viewer.v1")
        text = text.replace("EXPERIMENTAL_CORRECTED_BRANCH_NOT_PROMOTED · RAW_BASELINE_REMAINS_DEFAULT",
                            "STAGE3R1 SELECTIVE EDGE VERDICT · DISPLAY_ONLY_NOT_PROMOTION_EVIDENCE")
        page.write_text(text, encoding="utf-8")
    old_index = output / "C123_RAW_CORRECTED_INTERACTIVE_VIEWER_INDEX.html"
    text = old_index.read_text(encoding="utf-8")
    text = text.replace("BioSpur Stage 3", "BioSpur Stage 3-R1")
    text = text.replace("experimental branch not promoted; raw remains default",
                        "eligible edges only; raw remains authoritative")
    text = text.replace("raw/corrected", "raw/eligible-edge")
    text = text.replace("</main>", '<p class="note">Float32 viewer buffers are DISPLAY_ONLY_NOT_PROMOTION_EVIDENCE.</p></main>')
    target = output / "C123_STAGE3R1_INTERACTIVE_VIEWER_INDEX.html"
    target.write_text(text, encoding="utf-8")
    old_index.unlink()


def fixed_comparison_panel(capture: str, raw_positions: np.ndarray,
                           corrected_positions: np.ndarray, output: Path) -> str:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    frame = len(raw_positions) // 2
    views = (("front", (1, 2)), ("side", (0, 2)), ("top", (0, 1)), ("oblique", None))
    bones = ((0,1),(1,2),(2,3),(3,4),(1,5),(5,6),(6,7),(0,8),(8,9),(9,10),(0,11),(11,12),(12,13))
    fig = plt.figure(figsize=(12, 3.4), constrained_layout=True)
    for panel, (name, axes) in enumerate(views, 1):
        if axes is None:
            ax = fig.add_subplot(1, 4, panel, projection="3d")
            for a, b in bones:
                ax.plot(*raw_positions[frame, [a,b]].T, color="0.65", linestyle="--", linewidth=1)
                ax.plot(*corrected_positions[frame, [a,b]].T, color="#1261a0", linewidth=1.5)
            ax.view_init(elev=22, azim=-55)
            ax.set_axis_off()
        else:
            ax = fig.add_subplot(1, 4, panel)
            for a, b in bones:
                ax.plot(raw_positions[frame, [a,b], axes[0]], raw_positions[frame, [a,b], axes[1]], color="0.65", linestyle="--", linewidth=1)
                ax.plot(corrected_positions[frame, [a,b], axes[0]], corrected_positions[frame, [a,b], axes[1]], color="#1261a0", linewidth=1.5)
            ax.set_aspect("equal", adjustable="datalim"); ax.grid(alpha=.2)
        ax.set_title(name)
    fig.suptitle(f"Capture {capture} midpoint · raw dashed / eligible-edge solid")
    path = output / f"CAPTURE{capture}_STAGE3R1_FIXED_COMPARISON_PANELS.png"
    fig.savefig(path, dpi=140)
    plt.close(fig)
    return path.name


def viewer_roundtrip_audit(captures: dict, limit: float) -> dict:
    details = {}
    maximum = 0.0
    for capture, values in captures.items():
        capture_details = {}
        for name in ("corrected_q_GB_wxyz", "corrected_joint_positions_m", "edge_eta_rad", "edge_applied_rate_rad_s"):
            value = np.asarray(values[name], dtype=np.float64)
            error = float(np.nanmax(np.abs(value - value.astype(np.float32).astype(np.float64))))
            capture_details[name] = error
            maximum = max(maximum, error)
        details[capture] = capture_details
    return {"schema": "biospur.pure_imu.stage3r1.viewer_roundtrip.v1",
            "label": "DISPLAY_ONLY_NOT_PROMOTION_EVIDENCE", "captures": details,
            "maximum_abs_roundtrip_error": maximum, "limit": limit, "pass": maximum <= limit,
            "used_for_promotion": False}
