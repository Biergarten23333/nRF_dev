"""P1 sensor-health estimates and explicitly non-anatomical diagnostics."""
from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Any, Mapping, Sequence

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.colors import ListedColormap
import numpy as np
from scipy.spatial.transform import Rotation

from .range_reader import DecodedAction


G = 9.80665
ACC_SCALE = G / 2048.0
GYRO_SCALE = np.deg2rad(1.0 / 16.384)
EXPECTED_STEP_US = 5_000
AXIS_COLORS = ("#d62728", "#2ca02c", "#1f77b4")


def _effective_rows(values: np.ndarray, maximum_lag: int = 200) -> float:
    values = np.asarray(values, dtype=float)
    if len(values) < 4:
        return 1.0
    centered = values - np.mean(values, axis=0, keepdims=True)
    denominator = np.sum(centered * centered, axis=0)
    positive = []
    for lag in range(1, min(maximum_lag, len(values) - 1) + 1):
        numerator = np.sum(centered[:-lag] * centered[lag:], axis=0)
        rho = np.divide(
            numerator,
            denominator,
            out=np.zeros_like(numerator),
            where=denominator > 0,
        )
        mean_rho = float(np.mean(rho))
        if mean_rho <= 0:
            break
        positive.append(mean_rho)
    tau = 1.0 + 2.0 * float(np.sum(positive))
    return float(max(1.0, min(len(values), len(values) / max(tau, 1.0))))


def _covariance(values: np.ndarray, quantization_variance: float) -> np.ndarray:
    if len(values) < 2:
        return np.eye(3) * quantization_variance
    return np.asarray(np.cov(values.T, ddof=1), dtype=float) + np.eye(3) * quantization_variance


def estimate_initial_still(
    action: DecodedAction,
    *,
    node_to_segment: Mapping[str, str],
) -> dict[str, Any]:
    if action.action != "00_initial_still":
        raise ValueError("capture-wide bias owner must be the sealed initial still")
    nodes = {}
    gyro_q = GYRO_SCALE**2 / 12.0
    acc_q = ACC_SCALE**2 / 12.0
    for node, rows in action.rows_by_node.items():
        acc = rows["acc_raw"].astype(float) * ACC_SCALE
        gyro = rows["gyro_raw"].astype(float) * GYRO_SCALE
        if len(rows) < 20:
            nodes[node] = {
                "segment": node_to_segment[node],
                "status": "LOW_INFORMATION_FEWER_THAN_20_ROWS",
                "rows": int(len(rows)),
            }
            continue
        bias = np.median(gyro, axis=0)
        centered = gyro - bias
        acc_mean = np.mean(acc, axis=0)
        acc_norm = float(np.linalg.norm(acc_mean))
        effective = _effective_rows(np.c_[centered, acc - acc_mean])
        gyro_cov = _covariance(centered, gyro_q)
        acc_cov = _covariance(acc, acc_q)
        nodes[node] = {
            "segment": node_to_segment[node],
            "status": "STOCHASTIC_ESTIMATE_NOT_HEALTH_PASS",
            "rows": int(len(rows)),
            "effective_rows": effective,
            "gyro_bias_rad_s": bias.tolist(),
            "gyro_observation_covariance_rad2_s2": gyro_cov.tolist(),
            "gyro_bias_covariance_rad2_s2": ((np.pi / 2.0) * gyro_cov / effective).tolist(),
            "gyro_quantization_variance_rad2_s2": gyro_q,
            "accelerometer_mean_mps2": acc_mean.tolist(),
            "accelerometer_norm_mps2": acc_norm,
            "accelerometer_observation_covariance_m2_s4": acc_cov.tolist(),
            "accelerometer_quantization_variance_m2_s4": acc_q,
            "gravity_sensor_unit": (acc_mean / max(acc_norm, np.finfo(float).eps)).tolist(),
            "unique_accelerometer_code_rows": int(len(np.unique(rows["acc_raw"], axis=0))),
            "unique_gyroscope_code_rows": int(len(np.unique(rows["gyro_raw"], axis=0))),
        }
    return {
        "schema": "biospur-c2-p1-initial-still-stochastic-state-v1",
        "source_action": action.action,
        "source_interval": list(action.interval),
        "bias_estimator": "PER_AXIS_MEDIAN",
        "covariance": "SAMPLE_COVARIANCE_PLUS_LSB_SQUARED_OVER_12",
        "bias_covariance": "PI_OVER_2_TIMES_OBSERVATION_COVARIANCE_OVER_AUTOCORRELATION_REDUCED_EFFECTIVE_ROWS",
        "gravity_retained": True,
        "claims_not_created": [
            "yaw", "joint_axis", "joint_center", "bone_length",
            "segment_frame", "calibration_pass",
        ],
        "nodes": nodes,
    }


def build_health_report(
    actions: Sequence[DecodedAction],
    *,
    plan_ranges: Sequence[Mapping[str, Any]],
    node_to_segment: Mapping[str, str],
    continuity_audit: Mapping[str, Any],
    initial: Mapping[str, Any],
) -> dict[str, Any]:
    action_rows = []
    bias_proxy_by_node: dict[str, list[dict[str, Any]]] = defaultdict(list)
    totals = CounterLike()
    for action, planned in zip(actions, plan_ranges, strict=True):
        action_start = int(planned["action_interval_for_diagnostics"][0])
        per_node = {}
        for node, rows in action.rows_by_node.items():
            times = rows["node_timer_us"].astype(np.int64)
            epochs = rows["derived_boot_epoch"]
            diffs = np.concatenate([
                np.diff(times[epochs == epoch])
                for epoch in np.unique(epochs)
                if np.count_nonzero(epochs == epoch) > 1
            ]) if len(rows) else np.empty(0, dtype=np.int64)
            positive = diffs[diffs > 0]
            missing = np.maximum(0, np.rint(positive / EXPECTED_STEP_US).astype(int) - 1)
            pre = rows[rows["raw_start_offset"] < action_start]
            if len(pre) >= 20:
                proxy = np.median(pre["gyro_raw"].astype(float) * GYRO_SCALE, axis=0)
                bias_proxy_by_node[node].append({
                    "action": action.action,
                    "chronological_index": action.chronological_index,
                    "rows": int(len(pre)),
                    "gyro_preaction_median_rad_s": proxy.tolist(),
                    "interpretation": "SLOW_BIAS_PROXY_WITH_MOTION_CONTAMINATION_UNCERTAINTY",
                })
            acc_saturated = int(np.count_nonzero(np.any(np.abs(rows["acc_raw"].astype(np.int32)) >= 32760, axis=1)))
            gyro_saturated = int(np.count_nonzero(np.any(np.abs(rows["gyro_raw"].astype(np.int32)) >= 32760, axis=1)))
            per_node[node] = {
                "segment": node_to_segment[node],
                "rows": int(len(rows)),
                "median_step_us": float(np.median(positive)) if len(positive) else None,
                "p99_step_us": float(np.quantile(positive, 0.99)) if len(positive) else None,
                "maximum_step_us": int(np.max(positive)) if len(positive) else None,
                "missing_sample_slots_within_range": int(np.sum(missing)),
                "nonpositive_steps": int(np.count_nonzero(diffs <= 0)),
                "accelerometer_saturation_rows": acc_saturated,
                "gyroscope_saturation_rows": gyro_saturated,
                "derived_boot_epochs": [int(value) for value in np.unique(epochs)],
            }
            totals.add("rows", len(rows))
            totals.add("missing_sample_slots_within_ranges", int(np.sum(missing)))
            totals.add("nonpositive_steps", int(np.count_nonzero(diffs <= 0)))
            totals.add("accelerometer_saturation_rows", acc_saturated)
            totals.add("gyroscope_saturation_rows", gyro_saturated)
        action_rows.append({
            "action": action.action,
            "chronological_index": action.chronological_index,
            "prefit_training_interval": list(action.interval),
            "nodes": per_node,
            "decode_errors": dict(action.decode_audit["decode_errors"]),
            "opaque_record_counts": {
                key: value for key, value in action.decode_audit["counts"].items()
                if key.startswith("opaque_kind_")
            },
        })
    gap_covariance = {}
    for node, continuity in continuity_audit["nodes"].items():
        still = initial["nodes"][node]
        if "gyro_bias_covariance_rad2_s2" not in still:
            gap_covariance[node] = {"status": "UNAVAILABLE_LOW_INFORMATION_INITIAL_STILL"}
            continue
        bias_cov = np.asarray(still["gyro_bias_covariance_rad2_s2"], dtype=float)
        obs_cov = np.asarray(still["gyro_observation_covariance_rad2_s2"], dtype=float)
        cumulative = np.zeros((3, 3), dtype=float)
        events = []
        for row in continuity["inter_episode_gaps"]:
            gap_s = float(row["gap_us"]) * 1e-6
            increment = bias_cov * gap_s**2 + obs_cov * (EXPECTED_STEP_US * 1e-6) * gap_s
            cumulative += increment
            events.append({
                **row,
                "orientation_covariance_increment_rad2": increment.tolist(),
                "cumulative_orientation_covariance_rad2": cumulative.tolist(),
            })
        gap_covariance[node] = {
            "model": "BIAS_COMMON_MODE_T_SQUARED_PLUS_WHITE_GYRO_NOISE_DT_TIMES_T",
            "events": events,
            "final_cumulative_orientation_covariance_rad2": cumulative.tolist(),
        }
    return {
        "schema": "biospur-c2-p1-input-health-v1",
        "status": "DIAGNOSTIC_NON_ANATOMICAL_NOT_PASS",
        "time_domain": "NODE_LOCAL_B306_TIMER_US;NO_CROSS_NODE_TIME_ALIGNMENT_CLAIM",
        "exact_equality_used_as_physical_health_gate": False,
        "ordinary_nonideality_policy": "REPORT_AND_WIDEN_UNCERTAINTY;DO_NOT_TERMINATE",
        "totals": totals.values,
        "actions": action_rows,
        "preaction_bias_proxies": dict(bias_proxy_by_node),
        "continuity": continuity_audit,
        "gap_orientation_covariance": gap_covariance,
        "unresolved": [
            "accelerometer scale-factor error",
            "gyroscope scale-factor error",
            "cross-axis nonorthogonality",
            "cross-node time alignment",
            "temperature sensitivity because temperature is outside allowed fields",
            "segment frames and joint geometry",
        ],
    }


class CounterLike:
    def __init__(self) -> None:
        self.values: dict[str, int] = defaultdict(int)

    def add(self, key: str, value: int) -> None:
        self.values[key] += int(value)


def _label(fig: plt.Figure, subtitle: str) -> None:
    fig.suptitle(
        "NON-ANATOMICAL / NOT PASS", y=0.995,
        color="#b00020", fontsize=16, fontweight="bold",
    )
    fig.text(0.5, 0.958, subtitle, ha="center", va="top", fontsize=10)


def render_health(report: Mapping[str, Any], output: Path) -> None:
    actions = report["actions"]
    nodes = list(actions[0]["nodes"])
    row_counts = np.array([[row["nodes"][node]["rows"] for node in nodes] for row in actions])
    missing = np.array([[row["nodes"][node]["missing_sample_slots_within_range"] for node in nodes] for row in actions])
    p99 = np.array([[row["nodes"][node]["p99_step_us"] or np.nan for node in nodes] for row in actions])
    saturations = np.array([[row["nodes"][node]["accelerometer_saturation_rows"] + row["nodes"][node]["gyroscope_saturation_rows"] for node in nodes] for row in actions])
    fig, axes = plt.subplots(2, 2, figsize=(15, 9))
    _label(fig, "Sealed pre-fit ranges; native B306 timer health only")
    for ax, values, title, cmap in (
        (axes[0, 0], row_counts, "decoded IMU rows", "viridis"),
        (axes[0, 1], p99, "99th percentile native sample step (µs)", "magma"),
        (axes[1, 0], missing, "in-range inferred missing 5 ms slots", "Reds"),
        (axes[1, 1], saturations, "raw-code saturation rows (acc + gyro)", "Oranges"),
    ):
        if title.startswith("raw-code saturation") and not np.any(values):
            image = ax.imshow(
                values, aspect="auto",
                cmap=ListedColormap(["#f7f7f7"]), vmin=-0.5, vmax=0.5,
            )
            colorbar = fig.colorbar(image, ax=ax, shrink=0.8, ticks=[0])
            colorbar.set_label("exact saturation rows")
            ax.text(
                0.5, 0.5, "EXACTLY ZERO\nall 19 actions × 10 nodes",
                transform=ax.transAxes, ha="center", va="center",
                fontsize=12, fontweight="bold", color="#444444",
                bbox={"facecolor": "white", "edgecolor": "#444444", "alpha": 0.92},
            )
        else:
            image = ax.imshow(values, aspect="auto", cmap=cmap)
            fig.colorbar(image, ax=ax, shrink=0.8)
        ax.set_title(title)
        ax.set_yticks(range(len(actions)), [row["action"] for row in actions], fontsize=7)
        ax.set_xticks(range(len(nodes)), nodes, rotation=55, ha="right", fontsize=7)
    fig.tight_layout(rect=(0.0, 0.0, 1.0, 0.91), h_pad=2.0, w_pad=1.4)
    fig.savefig(output, dpi=170)
    plt.close(fig)


def render_time_gap(report: Mapping[str, Any], output: Path) -> None:
    actions = report["actions"]
    nodes = list(actions[0]["nodes"])
    p50 = np.array([[row["nodes"][node]["median_step_us"] or np.nan for node in nodes] for row in actions])
    gap_std_deg = np.zeros((len(actions), len(nodes)), dtype=float)
    for node_index, node in enumerate(nodes):
        gap = report["gap_orientation_covariance"][node]
        for event in gap.get("events", []):
            target = int(event["to_action_index"])
            covariance = np.asarray(event["cumulative_orientation_covariance_rad2"])
            gap_std_deg[target:, node_index] = np.rad2deg(np.sqrt(max(0.0, np.trace(covariance))))
    fig, axes = plt.subplots(2, 1, figsize=(15, 9))
    _label(fig, "Gaps are no-update intervals; they are never concatenated")
    # Keep a second global-status rendering inside the visible axes band.  The
    # R2 figure backend clipped the figure-level suptitle for this one layout.
    axes[0].text(
        0.5, 1.24, "NON-ANATOMICAL / NOT PASS",
        transform=axes[0].transAxes, ha="center", va="bottom",
        color="#b00020", fontsize=16, fontweight="bold", clip_on=False,
        bbox={"facecolor": "white", "edgecolor": "#b00020", "alpha": 0.96},
    )
    image = axes[0].imshow(p50, aspect="auto", cmap="cividis", vmin=4_900, vmax=5_100)
    axes[0].set_title("median native B306 timer step (µs)")
    axes[0].set_yticks(range(len(actions)), [row["action"] for row in actions], fontsize=7)
    axes[0].set_xticks(range(len(nodes)), nodes, rotation=55, ha="right", fontsize=7)
    fig.colorbar(image, ax=axes[0], shrink=0.8)
    for index, node in enumerate(nodes):
        axes[1].plot(range(len(actions)), gap_std_deg[:, index], marker=".", linewidth=1, label=node)
    axes[1].set_title("cumulative inter-episode orientation uncertainty from no-update gap model")
    axes[1].set_ylabel("sqrt(trace covariance), degrees")
    axes[1].set_xticks(range(len(actions)), [row["action"] for row in actions], rotation=60, ha="right", fontsize=7)
    axes[1].legend(ncol=5, fontsize=7)
    axes[1].grid(alpha=0.25)
    axes[1].text(
        0.5, 0.96,
        "PLANNED SEALED INTER-EPISODE NO-UPDATE COVARIANCE GROWTH — NOT PACKET DROPOUT; NOT A READINESS/PASS GATE",
        transform=axes[1].transAxes, ha="center", va="top",
        color="#b00020", fontsize=9, fontweight="bold",
        bbox={"facecolor": "white", "edgecolor": "#b00020", "alpha": 0.94},
    )
    fig.tight_layout(rect=(0.0, 0.0, 1.0, 0.91), h_pad=2.0)
    fig.savefig(output, dpi=170)
    plt.close(fig)


def render_bias(initial: Mapping[str, Any], health: Mapping[str, Any], output: Path) -> None:
    nodes = list(initial["nodes"])
    bias = np.array([initial["nodes"][node].get("gyro_bias_rad_s", [np.nan] * 3) for node in nodes])
    fig, axes = plt.subplots(2, 1, figsize=(15, 9))
    _label(fig, "Initial-still stochastic bias and pre-action drift proxies; no fit/readiness claim")
    positions = np.arange(len(nodes))
    width = 0.25
    for axis in range(3):
        axes[0].bar(positions + (axis - 1) * width, np.rad2deg(bias[:, axis]), width, color=AXIS_COLORS[axis], label="xyz"[axis])
    axes[0].set_xticks(positions, nodes, rotation=45, ha="right")
    axes[0].set_ylabel("initial median gyro bias (deg/s)")
    axes[0].legend()
    axes[0].grid(axis="y", alpha=0.25)
    for node_index, node in enumerate(nodes):
        proxies = health["preaction_bias_proxies"].get(node, [])
        if not proxies:
            continue
        delta = np.array([row["gyro_preaction_median_rad_s"] for row in proxies]) - bias[node_index]
        axes[1].plot(
            [row["chronological_index"] for row in proxies],
            np.rad2deg(np.linalg.norm(delta, axis=1)),
            marker=".", linewidth=1, label=node,
        )
    axes[1].set_ylabel("pre-action median change norm (deg/s)")
    axes[1].set_xlabel("sealed chronological action index")
    axes[1].set_title("slow-drift proxy (can contain imperfect-rest motion)")
    axes[1].legend(ncol=5, fontsize=7)
    axes[1].grid(alpha=0.25)
    fig.tight_layout(rect=(0.0, 0.0, 1.0, 0.91), h_pad=2.0)
    fig.savefig(output, dpi=170)
    plt.close(fig)


def _tilt_rotation(gravity_sensor: np.ndarray) -> np.ndarray:
    gravity_sensor = np.asarray(gravity_sensor, dtype=float)
    gravity_sensor /= max(np.linalg.norm(gravity_sensor), np.finfo(float).eps)
    rotation, _ = Rotation.align_vectors(
        np.array([[0.0, 0.0, 1.0]]), gravity_sensor[None, :],
    )
    return rotation.as_matrix()


def render_frame_graph(
    initial: Mapping[str, Any],
    *,
    node_to_segment: Mapping[str, str],
    edges: Sequence[Sequence[str]],
    output: Path,
) -> None:
    positions = {
        "pelvis": (0.0, 0.0, 0.0),
        "torso": (0.0, 0.0, 1.25),
        "upper_arm_left": (-1.1, 0.0, 1.6),
        "forearm_left": (-2.0, 0.0, 1.35),
        "upper_arm_right": (1.1, 0.0, 1.6),
        "forearm_right": (2.0, 0.0, 1.35),
        "thigh_left": (-0.55, 0.0, -1.0),
        "shank_left": (-0.65, 0.0, -2.0),
        "thigh_right": (0.55, 0.0, -1.0),
        "shank_right": (0.65, 0.0, -2.0),
    }
    segment_to_node = {segment: node for node, segment in node_to_segment.items()}
    fig = plt.figure(figsize=(14, 10))
    _label(fig, "Schematic rooted-tree connectivity + gravity-only sensor tilt; no joint centers or anatomical geometry")
    ax = fig.add_subplot(111, projection="3d")
    for parent, child in edges:
        p = np.array(positions[parent]); c = np.array(positions[child])
        ax.plot(*np.vstack([p, c]).T, color="#777777", linewidth=2, linestyle="--")
    for segment, position_tuple in positions.items():
        position = np.array(position_tuple, dtype=float)
        node = segment_to_node[segment]
        still = initial["nodes"][node]
        rotation = _tilt_rotation(np.asarray(still.get("gravity_sensor_unit", [0.0, 0.0, 1.0])))
        ax.scatter(*position, color="black", s=30)
        for axis in range(3):
            direction = rotation[:, axis] * 0.20
            ax.quiver(*position, *direction, color=AXIS_COLORS[axis], linewidth=2, arrow_length_ratio=0.18)
        ax.text(*(position + np.array([0.05, 0.02, 0.06])), f"{segment}\n{node}", fontsize=8)
    ax.text2D(
        0.02, 0.97,
        "DIAGRAM LAYOUT ONLY — NO JOINT CENTERS OR ANATOMICAL GEOMETRY\n"
        "RGB = gravity-tilted raw sensor x/y/z; yaw and every segment frame remain unresolved.",
        transform=ax.transAxes, fontsize=8.5, va="top",
        bbox={"facecolor": "white", "alpha": 0.9, "edgecolor": "#b00020"},
    )
    ax.set_xlabel("schematic graph x")
    ax.set_ylabel("schematic depth (not anatomy)")
    ax.set_zlabel("schematic graph level")
    ax.set_xlim(-2.6, 2.6)
    ax.set_ylim(-0.9, 0.9)
    ax.set_zlim(-2.35, 2.05)
    ax.set_box_aspect((5.2, 1.8, 4.4))
    ax.set_proj_type("ortho")
    ax.view_init(elev=18, azim=-70)
    fig.subplots_adjust(left=0.02, right=0.98, bottom=0.14, top=0.90)
    fig.savefig(output, dpi=180)
    plt.close(fig)
