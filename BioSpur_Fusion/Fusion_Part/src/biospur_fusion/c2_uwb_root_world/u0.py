"""Approved C2 U0 range model without IMU-payload consumption.

This module owns only the Fusion-side adapter around the frozen v47 UWB
decoder.  It deliberately never calls ``decode_measurements`` because that
entry point also decodes raw IMU records.
"""
from __future__ import annotations

import math
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Mapping, Sequence

import numpy as np
from scipy.optimize import least_squares

from biospur_fusion.ingest.v47 import _uwb_event, iter_cobs_records

import sys

_REPO = Path(__file__).resolve().parents[4]
_B306_TOOLS = _REPO / "B306_Part" / "tools"
if str(_B306_TOOLS) not in sys.path:
    sys.path.insert(0, str(_B306_TOOLS))
from fusion_host_binary import FrameError, decode_frame  # noqa: E402


@dataclass(frozen=True)
class ClockModel:
    boot_epoch: int
    a_ns_per_us: float
    b_ns: float
    sigma_ns: float

    def seconds(self, timer_us: float) -> float:
        return self.a_ns_per_us * 1e-9 * timer_us + self.b_ns * 1e-9


@dataclass(frozen=True)
class UwbRow:
    node: str
    boot: int
    sequence: int
    sweep: int
    strobe_us: int
    frame_us: int
    anchor_ids: tuple[int, ...]
    ranges_mm: tuple[int, ...]
    t_round_us: tuple[int, ...]
    quality: tuple[int, ...]
    valid_mask: int
    identity: int = 0
    node_ms: int = 0


@dataclass(frozen=True)
class DecodeSummary:
    complete_records: int
    decode_errors: int
    incomplete_tail_bytes: int
    uwb_rows: int
    skipped_non_uwb: int


@dataclass(frozen=True)
class U0Result:
    state: np.ndarray
    covariance: np.ndarray
    xyz_m: np.ndarray
    success: bool
    reason: str
    residuals_m: np.ndarray
    standardized_residuals: np.ndarray
    influences: np.ndarray
    anchors_used: tuple[int, ...]
    link_epochs_s: np.ndarray
    reference_epoch_s: float
    condition: float
    rank: int
    nfev: int
    cost: float
    optimality: float
    status: int
    message: str


def decode_uwb_only(path: Path) -> tuple[list[UwbRow], DecodeSummary]:
    """Decode only kind-1 v47 frames; kind-3 payload bytes are never parsed."""
    rows: list[UwbRow] = []
    last_timer: dict[str, int] = {}
    boot_by_node: dict[str, int] = {}
    complete = errors = tail = skipped = 0
    for index, start, end, encoded, is_complete in iter_cobs_records(path):
        if not is_complete:
            tail = len(encoded)
            continue
        complete += 1
        try:
            frame = decode_frame(encoded)
            if frame.kind != 1:
                skipped += 1
                continue
            timer = struct.unpack_from("<Q", frame.payload, 102)[0]
            boot = boot_by_node.setdefault(frame.node_name, 0)
            previous = last_timer.get(frame.node_name)
            if previous is not None and timer < previous:
                boot += 1
                boot_by_node[frame.node_name] = boot
            last_timer[frame.node_name] = int(timer)
            event = _uwb_event(frame, boot, (index, start, end, encoded))
            value = event.payload
            rows.append(UwbRow(
                node=event.node_id, boot=boot, sequence=frame.sequence,
                sweep=int(value["sweep"]), strobe_us=int(value["strobe_us"]),
                frame_us=int(value["frame_us"]),
                anchor_ids=tuple(int(x) for x in value["anchor_id"]),
                ranges_mm=tuple(int(x) for x in value["range_mm"]),
                t_round_us=tuple(int(x) for x in value["t_round_us"]),
                quality=tuple(int(x) for x in value["quality_percent"]),
                valid_mask=int(value["valid_mask"]),
                identity=int(value["identity"]), node_ms=int(value["node_ms"]),
            ))
        except (FrameError, struct.error, IndexError, ValueError):
            errors += 1
    return rows, DecodeSummary(complete, errors, tail, len(rows), skipped)


def predict_state(state: np.ndarray, covariance: np.ndarray, dt: float,
                  q_accel: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    if not math.isfinite(dt) or dt <= 0:
        raise ValueError("U0 time must be finite and strictly increasing")
    f = np.eye(6)
    f[:3, 3:] = np.eye(3) * dt
    g = np.vstack((np.eye(3) * (0.5 * dt * dt), np.eye(3) * dt))
    q = g @ np.diag(np.asarray(q_accel, float) ** 2) @ g.T
    return f @ state, f @ covariance @ f.T + q


def _repair_psd(matrix: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    symmetric = 0.5 * (matrix + matrix.T)
    values, vectors = np.linalg.eigh(symmetric)
    scale = max(1.0, float(np.linalg.norm(symmetric, 2)))
    if float(values.min()) < -100.0 * np.finfo(float).eps * scale:
        raise ValueError("sandwich covariance is not PSD within numerical tolerance")
    repaired_values = np.maximum(values, 0.0)
    repaired = (vectors * repaired_values) @ vectors.T
    floor = np.finfo(float).eps * max(1.0, float(repaired_values.max()))
    repaired += np.eye(matrix.shape[0]) * floor
    return repaired, values, repaired_values + floor


def solve_u0_row(
    row: UwbRow,
    *,
    anchors_m: np.ndarray,
    clock: ClockModel,
    predicted_state: np.ndarray,
    predicted_covariance: np.ndarray,
    bias_m: Mapping[int, float],
    sigma_history_m: Mapping[int, float],
    sigma_bias_m: Mapping[int, float],
    sigma_layout_seed_m: float = 0.0564866166214546,
    calibration_zero_uncertainty: bool = False,
) -> U0Result:
    if row.boot != clock.boot_epoch:
        return _failed(predicted_state, predicted_covariance, "CLOCK_BOOT_UNAVAILABLE")
    valid = [slot for slot in range(8) if row.valid_mask & (1 << slot)
             and 0 < row.ranges_mm[slot] < 0xFFFF]
    if len(valid) < 4:
        return _failed(predicted_state, predicted_covariance, "FEWER_THAN_FOUR_LINKS")
    ids = np.asarray([row.anchor_ids[s] for s in valid], int)
    if tuple(row.anchor_ids) != tuple(range(8)) or np.any(ids < 0) or np.any(ids >= 8):
        return _failed(predicted_state, predicted_covariance, "ANCHOR_IDENTITY_MISMATCH")
    ranges = np.asarray([row.ranges_mm[s] for s in valid], float) / 1000.0
    epochs = np.asarray([
        clock.seconds(row.strobe_us + 0.5 * row.t_round_us[s]) for s in valid
    ])
    t0 = float(np.median(epochs))
    offsets = epochs - t0
    velocity_scale = float(np.linalg.norm(predicted_state[3:]))
    sigma_clock = math.sqrt(2.0) * clock.sigma_ns * 1e-9
    sigma_round = abs(clock.a_ns_per_us * 1e-9) * 0.25 / math.sqrt(3.0)
    sigma = []
    for slot, aid in zip(valid, ids):
        if calibration_zero_uncertainty:
            history_value = bias_sigma_value = bias_value = 0.0
        else:
            if int(aid) not in bias_m or int(aid) not in sigma_history_m or int(aid) not in sigma_bias_m:
                return _failed(predicted_state, predicted_covariance, "CALIBRATION_TABLE_INCOMPLETE")
            bias_value = float(bias_m[int(aid)])
            history_value = float(sigma_history_m[int(aid)])
            bias_sigma_value = float(sigma_bias_m[int(aid)])
            if not all(math.isfinite(x) and x >= 0.0 for x in (bias_value, history_value, bias_sigma_value)):
                return _failed(predicted_state, predicted_covariance, "CALIBRATION_TABLE_INVALID")
        quality = max(float(row.quality[slot]), 1.0)
        variance = (
            sigma_layout_seed_m ** 2 * 100.0 / quality
            + (0.0005 / math.sqrt(3.0)) ** 2
            + velocity_scale ** 2 * (sigma_clock ** 2 + sigma_round ** 2)
            + history_value ** 2
            + bias_sigma_value ** 2
        )
        sigma.append(math.sqrt(variance))
    sigma_array = np.asarray(sigma)
    anchor_rows = anchors_m[ids]
    bias = np.asarray([0.0 if calibration_zero_uncertainty else float(bias_m[int(aid)]) for aid in ids])

    try:
        p, _, _ = _repair_psd(np.asarray(predicted_covariance, float))
        chol = np.linalg.cholesky(p)
    except (ValueError, np.linalg.LinAlgError, FloatingPointError):
        return _failed(predicted_state, predicted_covariance, "PREDICTED_COVARIANCE_REJECT")

    def parts(x: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        y = x[:3] + offsets[:, None] * x[3:]
        delta = anchor_rows - y
        distances = np.linalg.norm(delta, axis=1)
        if np.any(distances <= math.sqrt(np.finfo(float).eps) * np.linalg.norm(np.ptp(anchors_m, axis=0))):
            raise ValueError("range derivative singular at anchor")
        direction = delta / distances[:, None]
        residual = ranges - bias - distances
        jac_range = np.hstack((direction, offsets[:, None] * direction)) / sigma_array[:, None]
        return residual / sigma_array, jac_range, residual, distances

    def fun(x: np.ndarray) -> np.ndarray:
        standardized, _, _, _ = parts(x)
        return np.concatenate((standardized, np.linalg.solve(chol, x - predicted_state)))

    def jac(x: np.ndarray) -> np.ndarray:
        _, jr, _, _ = parts(x)
        return np.vstack((jr, np.linalg.solve(chol, np.eye(6))))

    try:
        result = least_squares(
            fun, x0=np.asarray(predicted_state, float), jac=jac,
            bounds=(-np.inf, np.inf), method="trf", ftol=1e-8, xtol=1e-8,
            gtol=1e-8, x_scale="jac", loss="huber", f_scale=1.0,
            diff_step=None, tr_solver="exact", tr_options={},
            jac_sparsity=None, max_nfev=100, verbose=0,
        )
        standardized, jr, residual_m, _ = parts(result.x)
        singular = np.linalg.svd(jr[:, :3], compute_uv=False)
        tolerance = max(jr[:, :3].shape) * np.finfo(float).eps * singular[0]
        rank = int(np.sum(singular > tolerance))
        psi_r = np.clip(standardized, -1.0, 1.0)
        weight_r = np.ones_like(standardized)
        nonzero = standardized != 0
        weight_r[nonzero] = psi_r[nonzero] / standardized[nonzero]
        geometry = jr[:, :3].T @ np.diag(weight_r) @ jr[:, :3]
        geig = np.linalg.eigvalsh(geometry)
        positive = geig[geig > max(geometry.shape) * np.finfo(float).eps * max(1.0, geig[-1])]
        condition = math.inf if positive.size < 3 else float(positive[-1] / positive[0])
        all_residual = fun(result.x)
        all_jac = jac(result.x)
        psi = np.clip(all_residual, -1.0, 1.0)
        weights = np.ones_like(all_residual)
        nz = all_residual != 0
        weights[nz] = psi[nz] / all_residual[nz]
        a = all_jac.T @ np.diag(weights) @ all_jac
        b = all_jac.T @ np.diag(psi ** 2) @ all_jac
        pinv = np.linalg.pinv(a, rcond=max(a.shape) * np.finfo(float).eps)
        covariance, _, _ = _repair_psd(pinv @ b @ pinv)
        finite = (all(np.all(np.isfinite(v)) for v in (
            result.x, result.fun, result.jac, covariance, jr, residual_m, epochs))
            and math.isfinite(float(result.cost)) and math.isfinite(float(result.optimality)))
        accepted = bool(result.status in (1, 2, 3, 4) and finite and rank == 3 and condition <= 1e8)
        reason = "ACCEPTED" if accepted else "SOLVER_OR_GEOMETRY_REJECT"
        if not accepted:
            return _failed(predicted_state, predicted_covariance, reason, rank=rank,
                           condition=condition, nfev=result.nfev, cost=result.cost,
                           optimality=result.optimality, status=result.status,
                           message=str(result.message))
        return U0Result(result.x, covariance, result.x[:3], True, reason,
                        residual_m, standardized, weight_r, tuple(int(x) for x in ids),
                        epochs, t0, condition, rank, result.nfev, float(result.cost),
                        float(result.optimality), int(result.status), str(result.message))
    except (ValueError, np.linalg.LinAlgError, FloatingPointError):
        return _failed(predicted_state, predicted_covariance, "NUMERICAL_REJECT")


def _failed(state: np.ndarray, covariance: np.ndarray, reason: str, *, rank: int = 0,
            condition: float = math.inf, nfev: int = 0, cost: float = math.nan,
            optimality: float = math.nan, status: int = 0, message: str = "") -> U0Result:
    return U0Result(np.asarray(state, float), np.asarray(covariance, float),
                    np.full(3, np.nan), False, reason, np.empty(0), np.empty(0),
                    np.empty(0), (), np.empty(0), math.nan, condition, rank, nfev, cost,
                    optimality, status, message)


def iter_node(rows: Sequence[UwbRow], node: str) -> Iterator[UwbRow]:
    yield from sorted((row for row in rows if row.node == node), key=lambda r: (r.boot, r.strobe_us, r.sequence))
