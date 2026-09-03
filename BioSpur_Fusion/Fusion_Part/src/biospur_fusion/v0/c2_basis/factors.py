"""Covariance-blocked train/held-out joint-center factors for C2."""
from __future__ import annotations

from dataclasses import dataclass, replace
import hashlib
from typing import Any, Mapping, Sequence

import numpy as np

from biospur_fusion.v0.raw6_heading import B5Block, EDGES, Raw6Episode, b5_blocks

from .staging import BoundedStage, pipeline_wall_limit


@dataclass(frozen=True)
class EdgeBlockSet:
    edge: str
    parent: str
    child: str
    kind: str
    train: tuple[B5Block, ...]
    held_out: tuple[B5Block, ...]


@dataclass(frozen=True)
class FactorBundle:
    edges: Mapping[str, EdgeBlockSet]
    episode_order: tuple[str, ...]
    block_duration_s: float
    held_out_modulus: int
    held_out_residue: int
    covariance_information_audit: tuple[Mapping[str, Any], ...]
    construction_audit: Mapping[str, Any]


def _slice(block: B5Block, keep: np.ndarray) -> B5Block:
    kwargs: dict[str, Any] = {}
    for name in (
        "phase", "parent_rotation", "child_rotation", "parent_force", "child_force",
        "parent_kinematic", "child_kinematic", "sample_weight", "sample_time_ns",
        "parent_gyro", "child_gyro",
    ):
        value = getattr(block, name)
        kwargs[name] = None if value is None else value[keep]
    return B5Block(
        action=block.action,
        partition=block.partition,
        information_sigma_mps2=block.information_sigma_mps2,
        effective_sample_size=block.effective_sample_size,
        covariance_block_id=block.covariance_block_id,
        parent_acc_noise_cov=block.parent_acc_noise_cov,
        child_acc_noise_cov=block.child_acc_noise_cov,
        parent_gyro_noise_cov=block.parent_gyro_noise_cov,
        child_gyro_noise_cov=block.child_gyro_noise_cov,
        parent_noise_cov_source=block.parent_noise_cov_source,
        child_noise_cov_source=block.child_noise_cov_source,
        **kwargs,
    )


def _partition_block(
    block: B5Block, *, block_ns: int, modulus: int, residue: int,
) -> tuple[tuple[tuple[int, B5Block], ...], tuple[tuple[int, B5Block], ...]]:
    if block.sample_time_ns is None:
        raise ValueError("C2 factors require synchronized sample time")
    bins = (block.sample_time_ns - block.sample_time_ns[0]) // block_ns
    train: list[tuple[int, B5Block]] = []
    held: list[tuple[int, B5Block]] = []
    for bin_index in np.unique(bins):
        keep = bins == bin_index
        selected = _slice(block, keep)
        if int(bin_index) % modulus == residue:
            if np.count_nonzero(keep) >= 3:
                held.append((int(bin_index), selected))
        elif np.count_nonzero(keep) >= 6:
            train.append((int(bin_index), selected))
    return tuple(train), tuple(held)


def _lag1_effective_sample_size(values: np.ndarray, maximum_rho: float) -> tuple[float, float]:
    values = np.asarray(values, dtype=float)
    n = len(values)
    correlations = []
    for column in values.reshape(n, -1).T:
        centered = column - np.mean(column)
        denominator = float(centered @ centered)
        if denominator > 1e-12:
            correlations.append(float(centered[:-1] @ centered[1:] / denominator))
    rho = float(np.median(correlations)) if correlations else maximum_rho
    rho = float(np.clip(rho, 0.0, maximum_rho))
    effective = float(np.clip(n * (1.0 - rho) / (1.0 + rho), 1.0, n))
    return effective, rho


def fixed_geometry_noise_lever_bounds(
    config: Mapping[str, Any],
) -> dict[str, tuple[float, float]]:
    """Derive maximum connection norms only from preregistered geometry."""

    geometry = config["geometry"]
    upper_offset = np.asarray(geometry["sensor_axial_offset_upper_m"], dtype=float)
    lower_offset = np.asarray(geometry["sensor_axial_offset_lower_m"], dtype=float)
    if upper_offset.shape != (8,) or lower_offset.shape != (8,):
        raise ValueError("sensor axial offset geometry must have eight coordinates")
    torso_shoulder = float(np.hypot(
        0.5 * float(geometry["biacromial_bounds_m"][1]),
        float(geometry["chest_to_acromion_bounds_m"][1]),
    ))
    pelvis_torso = 0.5 * float(geometry["pelvis_imu_to_chest_imu_bounds_m"][1])
    pelvis_hip = float(np.hypot(
        0.5 * max(geometry["internal_hip_center_spacing_sensitivity_m"]),
        float(geometry["pelvis_center_to_hip_center_vertical_offset_m"]),
    ))
    return {
        "pelvis_torso": (pelvis_torso, pelvis_torso),
        "shoulder_left": (
            torso_shoulder,
            float(geometry["upper_arm_bounds_m"][1]) - lower_offset[0],
        ),
        "elbow_left": (
            upper_offset[0],
            float(geometry["forearm_bounds_m"][1]) - lower_offset[1],
        ),
        "shoulder_right": (
            torso_shoulder,
            float(geometry["upper_arm_bounds_m"][1]) - lower_offset[2],
        ),
        "elbow_right": (
            upper_offset[2],
            float(geometry["forearm_bounds_m"][1]) - lower_offset[3],
        ),
        "hip_left": (
            pelvis_hip,
            float(geometry["thigh_bounds_m"][1]) - lower_offset[4],
        ),
        "knee_left": (
            upper_offset[4],
            float(geometry["shank_bounds_m"][1]) - lower_offset[5],
        ),
        "hip_right": (
            pelvis_hip,
            float(geometry["thigh_bounds_m"][1]) - lower_offset[6],
        ),
        "knee_right": (
            upper_offset[6],
            float(geometry["shank_bounds_m"][1]) - lower_offset[7],
        ),
    }


def propagated_joint_noise_sigma(
    *,
    parent_acc_cov: np.ndarray,
    child_acc_cov: np.ndarray,
    parent_gyro_cov: np.ndarray,
    child_gyro_cov: np.ndarray,
    parent_gyro: np.ndarray,
    child_gyro: np.ndarray,
    parent_lever_bound_m: float,
    child_lever_bound_m: float,
    sample_period_s: float,
) -> tuple[float, Mapping[str, float]]:
    """Propagate verified sensor noise; dynamic signal is never noise itself."""

    matrices = tuple(np.asarray(value, dtype=float) for value in (
        parent_acc_cov, child_acc_cov, parent_gyro_cov, child_gyro_cov,
    ))
    if any(value.shape != (3, 3) or not np.isfinite(value).all() for value in matrices):
        raise ValueError("sensor noise covariance must be four finite 3x3 matrices")
    pa, ca, pg, cg = matrices
    dt = float(sample_period_s)
    if dt <= 0.0:
        raise ValueError("sample period must be positive")
    acc_variance = float(np.trace(pa + ca) / 3.0)
    parent_alpha_max_variance = float(np.max(np.linalg.eigvalsh(pg))) / (2.0 * dt * dt)
    child_alpha_max_variance = float(np.max(np.linalg.eigvalsh(cg))) / (2.0 * dt * dt)
    alpha_variance = (
        float(parent_lever_bound_m) ** 2 * parent_alpha_max_variance
        + float(child_lever_bound_m) ** 2 * child_alpha_max_variance
    )
    parent_rate_q90 = float(np.quantile(np.linalg.norm(parent_gyro, axis=1), 0.90))
    child_rate_q90 = float(np.quantile(np.linalg.norm(child_gyro, axis=1), 0.90))
    # ||d(omega x (omega x r))/d omega|| <= 4 ||omega|| ||r||.
    centripetal_variance = (
        (4.0 * parent_rate_q90 * float(parent_lever_bound_m)) ** 2
        * float(np.max(np.linalg.eigvalsh(pg)))
        + (4.0 * child_rate_q90 * float(child_lever_bound_m)) ** 2
        * float(np.max(np.linalg.eigvalsh(cg)))
    )
    variance = max(0.0, acc_variance + alpha_variance + centripetal_variance)
    return float(np.sqrt(variance)), {
        "accelerometer_noise_variance_m2_s4": acc_variance,
        "angular_acceleration_noise_variance_m2_s4": alpha_variance,
        "centripetal_noise_variance_m2_s4": centripetal_variance,
        "parent_rate_q90_rad_s": parent_rate_q90,
        "child_rate_q90_rad_s": child_rate_q90,
        "uses_dynamic_residual_or_jerk_as_noise": False,
    }


def _covariance_weighted_block(
    block: B5Block, *, edge: str, bin_index: int, config: Mapping[str, Any],
) -> tuple[B5Block, Mapping[str, Any]]:
    sampling = config["sampling"]
    n = len(block.sample_weight)
    lever_bounds = fixed_geometry_noise_lever_bounds(config)
    parent_lever_bound, child_lever_bound = lever_bounds[edge]
    if parent_lever_bound <= 0.0 or child_lever_bound <= 0.0:
        raise ValueError(f"{edge}: nonphysical covariance lever bound")
    signal = np.column_stack((
        block.parent_force,
        block.child_force,
        block.parent_gyro,
        block.child_gyro,
    ))
    effective, rho = _lag1_effective_sample_size(
        signal, float(sampling["maximum_lag1_correlation"]),
    )
    propagated_sigma, propagation = propagated_joint_noise_sigma(
        parent_acc_cov=block.parent_acc_noise_cov,
        child_acc_cov=block.child_acc_noise_cov,
        parent_gyro_cov=block.parent_gyro_noise_cov,
        child_gyro_cov=block.child_gyro_noise_cov,
        parent_gyro=block.parent_gyro,
        child_gyro=block.child_gyro,
        parent_lever_bound_m=parent_lever_bound,
        child_lever_bound_m=child_lever_bound,
        sample_period_s=1.0 / float(config["sampling"]["working_rate_hz"]),
    )
    sigma = float(np.clip(
        propagated_sigma,
        float(sampling["minimum_joint_center_block_sigma_mps2"]),
        float(sampling["maximum_joint_center_block_sigma_mps2"]),
    ))
    information_weight = np.sqrt(effective / n) / sigma
    block_id = f"{edge}:{block.action}:covariance_bin_{bin_index:03d}"
    weighted = replace(
        block,
        sample_weight=np.full(n, information_weight, dtype=float),
        information_sigma_mps2=sigma,
        effective_sample_size=effective,
        covariance_block_id=block_id,
    )
    return weighted, {
        "block_id": block_id,
        "edge": edge,
        "action": block.action,
        "rows": n,
        "lag1_correlation": rho,
        "effective_sample_size": effective,
        "joint_center_sigma_mps2": sigma,
        "parent_kinematic_noise_lever_bound_m": parent_lever_bound,
        "child_kinematic_noise_lever_bound_m": child_lever_bound,
        "lever_bound_derivation": sampling["kinematic_noise_lever_bound_derivation"],
        "parent_noise_cov_source": block.parent_noise_cov_source,
        "child_noise_cov_source": block.child_noise_cov_source,
        "noise_propagation": propagation,
        "per_sample_information_weight": information_weight,
    }


def build_factor_bundle(
    episodes: Sequence[Raw6Episode], config: Mapping[str, Any],
    *,
    deadline: BoundedStage | None = None,
    budget_mode: str = "FULL",
) -> FactorBundle:
    stage = deadline or BoundedStage(
        "FACTOR_CONSTRUCTION",
        pipeline_wall_limit(config, budget_mode, "factor_construction"),
    )
    sampling = config["sampling"]
    block_s = float(sampling["correlation_block_s"])
    modulus = int(sampling["held_out_block_modulus"])
    residue = int(sampling["held_out_block_residue"])
    output: dict[str, EdgeBlockSet] = {}
    information_audit: list[Mapping[str, Any]] = []
    for edge, parent, child, kind in EDGES:
        train_blocks: list[B5Block] = []
        held_blocks: list[B5Block] = []
        for episode in episodes:
            blocks = stage.run(
                f"{edge}:{episode.action}:b5_blocks",
                lambda edge=edge, episode=episode: b5_blocks(
                    edge, [episode], include_transitions=True, maximum_rows=None,
                ),
            )
            for block in blocks:
                train_rows, held_rows = _partition_block(
                    block, block_ns=int(round(block_s * 1e9)), modulus=modulus, residue=residue,
                )
                for bin_index, selected in train_rows:
                    weighted, report = _covariance_weighted_block(
                        selected, edge=edge, bin_index=bin_index, config=config,
                    )
                    train_blocks.append(weighted)
                    information_audit.append({**report, "partition": "train"})
                for bin_index, selected in held_rows:
                    weighted, report = _covariance_weighted_block(
                        selected, edge=edge, bin_index=bin_index, config=config,
                    )
                    held_blocks.append(weighted)
                    information_audit.append({**report, "partition": "held_out"})
                stage.checkpoint(
                    f"{edge}:{episode.action}:covariance_blocks_complete",
                    train_block_count=len(train_rows),
                    held_out_block_count=len(held_rows),
                )
        output[edge] = EdgeBlockSet(edge, parent, child, kind, tuple(train_blocks), tuple(held_blocks))
    bundle = FactorBundle(
        edges=output,
        episode_order=tuple(episode.action for episode in episodes),
        block_duration_s=block_s,
        held_out_modulus=modulus,
        held_out_residue=residue,
        covariance_information_audit=tuple(information_audit),
        construction_audit={},
    )
    for edge in output.values():
        train_actions = {block.action for block in edge.train}
        held_actions = {block.action for block in edge.held_out}
        missing = set(bundle.episode_order) - train_actions
        if missing:
            raise ValueError(f"{edge.edge}: complete episodes missing from training factors: {sorted(missing)}")
        if set(bundle.episode_order) - held_actions:
            raise ValueError(f"{edge.edge}: held-out blocks absent for a complete episode")
    stage.checkpoint(
        "factor_bundle_complete", episode_count=len(episodes), edge_count=len(output),
    )
    return replace(bundle, construction_audit=stage.report())


def append_factor_bundle(left: FactorBundle | None, right: FactorBundle) -> FactorBundle:
    """Append one chronological episode's immutable factors without future access."""

    if left is None:
        return right
    if len(right.episode_order) != 1:
        raise ValueError("incremental factor append requires exactly one new episode")
    action = right.episode_order[0]
    if action in left.episode_order:
        raise ValueError(f"factor episode already retained: {action}")
    if (
        left.block_duration_s != right.block_duration_s
        or left.held_out_modulus != right.held_out_modulus
        or left.held_out_residue != right.held_out_residue
    ):
        raise ValueError("incremental factor partition contract changed")
    return FactorBundle(
        edges={
            name: EdgeBlockSet(
                edge.edge, edge.parent, edge.child, edge.kind,
                (*edge.train, *right.edges[name].train),
                (*edge.held_out, *right.edges[name].held_out),
            )
            for name, edge in left.edges.items()
        },
        episode_order=(*left.episode_order, action),
        block_duration_s=left.block_duration_s,
        held_out_modulus=left.held_out_modulus,
        held_out_residue=left.held_out_residue,
        covariance_information_audit=(
            *left.covariance_information_audit,
            *right.covariance_information_audit,
        ),
        construction_audit={
            "mode": "INCREMENTAL_CHRONOLOGICAL_APPEND",
            "future_episode_access": False,
            "new_episode": action,
            "new_episode_stage": right.construction_audit,
        },
    )


def select_prefix(bundle: FactorBundle, episode_count: int) -> FactorBundle:
    order = bundle.episode_order[: int(episode_count)]
    selected = set(order)
    return FactorBundle(
        edges={
            name: EdgeBlockSet(
                edge.edge, edge.parent, edge.child, edge.kind,
                tuple(block for block in edge.train if block.action in selected),
                tuple(block for block in edge.held_out if block.action in selected),
            )
            for name, edge in bundle.edges.items()
        },
        episode_order=order,
        block_duration_s=bundle.block_duration_s,
        held_out_modulus=bundle.held_out_modulus,
        held_out_residue=bundle.held_out_residue,
        covariance_information_audit=tuple(
            row for row in bundle.covariance_information_audit
            if row["action"] in selected
        ),
        construction_audit={
            "mode": "PREFIX_VIEW_OF_EXISTING_BUNDLE",
            "future_episode_access": False,
            "source": bundle.construction_audit,
        },
    )


def factor_identity(bundle: FactorBundle, partition: str) -> str:
    if partition not in {"train", "held_out"}:
        raise ValueError(partition)
    digest = hashlib.sha256()
    digest.update("|".join(bundle.episode_order).encode())
    for edge_name in sorted(bundle.edges):
        digest.update(edge_name.encode())
        for block in getattr(bundle.edges[edge_name], partition):
            digest.update(block.action.encode())
            for name in (
                "phase", "parent_rotation", "child_rotation", "parent_force", "child_force",
                "parent_kinematic", "child_kinematic", "sample_weight", "sample_time_ns",
                "parent_gyro", "child_gyro", "parent_acc_noise_cov",
                "child_acc_noise_cov", "parent_gyro_noise_cov", "child_gyro_noise_cov",
            ):
                value = getattr(block, name)
                if value is not None:
                    digest.update(np.ascontiguousarray(value).tobytes())
            digest.update(str(block.information_sigma_mps2).encode())
            digest.update(str(block.effective_sample_size).encode())
            digest.update(str(block.covariance_block_id).encode())
            digest.update(str(block.parent_noise_cov_source).encode())
            digest.update(str(block.child_noise_cov_source).encode())
    return digest.hexdigest()
