"""Edge-local nonhinge heading likelihood from full-R3 joint accelerations.

Official QMT remains the heading owner for qualified hinge edges.  QMT 0.2.4
provides no observation for an unconstrained 3-DoF edge with full Euler ranges,
so this module supplies the bounded missing capability: compare the two
independently reconstructed joint specific-force vectors on the exact existing
pair-clock rows.  The result is a full circular likelihood, not a point lock.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
from typing import Any, Mapping, Sequence

import numpy as np
from scipy.signal import savgol_coeffs
from scipy.spatial.transform import Rotation

from .functional_geometry import AlignedPair, HINGE_EDGES, _center_terms
from .quaternion_contract import qmt_wxyz_to_scipy_active
from .segment_frames import EdgeConnectionVectors


_SHARED_NUISANCE_PARAMETER_BASIS = (
    *(f"CENTER_R3_{index}" for index in range(6)),
    *(f"PARENT_CALIBRATION_{index}" for index in range(24)),
    *(f"CHILD_CALIBRATION_{index}" for index in range(24)),
)
_SHARED_NUISANCE_PARAMETER_BASIS_SHA256 = hashlib.sha256(
    "\x00".join(_SHARED_NUISANCE_PARAMETER_BASIS).encode("utf-8")
).hexdigest()


@dataclass(frozen=True)
class NonhingeHeadingLikelihoodResult:
    edge: str
    delta_grid_rad: np.ndarray
    action_log_likelihood: np.ndarray
    posterior_weights: np.ndarray
    report: Mapping[str, Any]


@dataclass(frozen=True)
class NonhingeSharedNuisanceSufficientStatistics:
    """Action terms for one joint linear-Gaussian nuisance marginal.

    ``heading_information`` is the scalar normal term ``h.T W h`` on each
    registered S1 coordinate. ``shared_score`` is ``h.T W J`` in the stable
    54-D edge basis ``[center6, parent_calibration24, child_calibration24]``;
    both remain diagnostic derivatives of the heading coordinate. The four
    following fields are the reusable sufficient statistics for the actual
    marginal: ``J.T W J``, ``J.T W r``, ``r.T W r``, and ``log(det(D))``.
    The independent covariance ``D`` retains the actual candidate-dependent
    generative accelerometer/gyro-alpha white covariance.  Gap-orientation and
    pair-clock uncertainty are epistemic addenda: one candidate-independent
    PSD envelope is added to every S1 cell, so those terms cannot buy weight by
    rotating a covariance direction onto the residual.  The exact determinant
    remains part of the normalized likelihood.
    """

    heading_information: np.ndarray
    shared_score: np.ndarray
    shared_covariance: np.ndarray
    nuisance_normal: np.ndarray
    nuisance_score: np.ndarray
    residual_quadratic: np.ndarray
    independent_covariance_log_determinant: np.ndarray
    residual_dimension: int
    shared_nuisance_reference_mean: np.ndarray
    parameter_basis_sha256: str = _SHARED_NUISANCE_PARAMETER_BASIS_SHA256


def _joint_linear_gaussian_nuisance_log_marginal(
    *,
    nuisance_normal: np.ndarray,
    nuisance_score: np.ndarray,
    residual_quadratic: np.ndarray,
    independent_covariance_log_determinant: np.ndarray,
    shared_covariance: np.ndarray,
) -> tuple[np.ndarray, Mapping[str, Any]]:
    """Integrate one shared Gaussian nuisance once for every S1 candidate."""

    normal = np.asarray(nuisance_normal, dtype=float)
    score = np.asarray(nuisance_score, dtype=float)
    quadratic = np.asarray(residual_quadratic, dtype=float)
    independent_logdet = np.asarray(
        independent_covariance_log_determinant, dtype=float,
    )
    covariance = np.asarray(shared_covariance, dtype=float)
    candidate_count = len(quadratic)
    if (
        normal.ndim != 3
        or normal.shape[0] != candidate_count
        or normal.shape[1] != normal.shape[2]
        or score.shape != (candidate_count, normal.shape[1])
        or independent_logdet.shape != (candidate_count,)
        or covariance.shape != (normal.shape[1], normal.shape[1])
        or not all(np.isfinite(value).all() for value in (
            normal, score, quadratic, independent_logdet, covariance,
        ))
        or np.any(quadratic < -1e-9)
        or not np.allclose(
            normal, np.swapaxes(normal, 1, 2), atol=1e-10, rtol=0.0,
        )
        or not np.allclose(covariance, covariance.T, atol=1e-12, rtol=0.0)
    ):
        raise ValueError("joint nuisance sufficient statistics are invalid")
    covariance_eigenvalues, covariance_eigenvectors = np.linalg.eigh(
        0.5 * (covariance + covariance.T)
    )
    covariance_scale = max(1.0, float(np.max(np.abs(covariance_eigenvalues))))
    if float(np.min(covariance_eigenvalues)) < -1e-10 * covariance_scale:
        raise ValueError("joint nuisance covariance is not PSD")
    retained = covariance_eigenvalues > 1e-12 * covariance_scale
    covariance_factor = (
        covariance_eigenvectors[:, retained]
        * np.sqrt(np.maximum(covariance_eigenvalues[retained], 0.0))[None, :]
    )
    log_marginal = np.empty(candidate_count, dtype=float)
    marginal_quadratic = np.empty(candidate_count, dtype=float)
    nuisance_logdet = np.empty(candidate_count, dtype=float)
    identity = np.eye(covariance_factor.shape[1], dtype=float)
    minimum_normal_eigenvalue = np.inf
    for index in range(candidate_count):
        candidate_normal = 0.5 * (normal[index] + normal[index].T)
        minimum_normal_eigenvalue = min(
            minimum_normal_eigenvalue,
            float(np.min(np.linalg.eigvalsh(candidate_normal))),
        )
        whitened_normal = (
            covariance_factor.T @ candidate_normal @ covariance_factor
        )
        precision = identity + 0.5 * (
            whitened_normal + whitened_normal.T
        )
        sign, logdet = np.linalg.slogdet(precision)
        if sign <= 0.0 or not np.isfinite(logdet):
            raise RuntimeError("joint nuisance marginal precision is not positive")
        whitened_score = covariance_factor.T @ score[index]
        explained = float(
            whitened_score @ np.linalg.solve(precision, whitened_score)
        )
        candidate_quadratic = float(quadratic[index] - explained)
        tolerance = 1e-9 * max(1.0, float(quadratic[index]))
        if candidate_quadratic < -tolerance:
            raise RuntimeError("joint nuisance marginal produced negative energy")
        candidate_quadratic = max(0.0, candidate_quadratic)
        marginal_quadratic[index] = candidate_quadratic
        nuisance_logdet[index] = float(logdet)
        log_marginal[index] = -0.5 * (
            candidate_quadratic
            + independent_logdet[index]
            + float(logdet)
        )
    return log_marginal, {
        "schema": "biospur-c2-joint-linear-gaussian-shared-nuisance-marginal-v1",
        "shared_nuisance_dimension": int(covariance.shape[0]),
        "shared_nuisance_effective_rank": int(covariance_factor.shape[1]),
        "singular_shared_covariance_handling": (
            "LOW_RANK_EIGEN_FACTOR_NO_PSEUDOINVERSE"
        ),
        "minimum_nuisance_normal_eigenvalue": float(minimum_normal_eigenvalue),
        "marginal_quadratic_sha256": _array_sha256(marginal_quadratic),
        "candidate_nuisance_log_determinant_sha256": _array_sha256(
            nuisance_logdet
        ),
        "independent_covariance_log_determinant_sha256": _array_sha256(
            independent_logdet
        ),
        "candidate_log_determinant_included": True,
        "joint_linear_gaussian_shared_nuisance_marginal_performed": True,
        "irls_or_student_t_marginal_claimed": False,
    }


def _array_sha256(value: np.ndarray) -> str:
    array = np.ascontiguousarray(np.asarray(value))
    return hashlib.sha256(array.view(np.uint8)).hexdigest()


def _skew(value: np.ndarray) -> np.ndarray:
    x, y, z = np.asarray(value, dtype=float)
    return np.array(((0.0, -z, y), (z, 0.0, -x), (-y, x, 0.0)))


def _validated_covariance(
    name: str, value: np.ndarray, *, dimension: int = 3,
) -> np.ndarray:
    covariance = np.asarray(value, dtype=float)
    if (
        covariance.shape != (dimension, dimension)
        or not np.isfinite(covariance).all()
        or not np.allclose(covariance, covariance.T, atol=1e-12, rtol=0.0)
        or float(np.min(np.linalg.eigvalsh(covariance))) < -1e-12
    ):
        raise ValueError(
            f"{name} must be one finite PSD {dimension}x{dimension} covariance"
        )
    return 0.5 * (covariance + covariance.T)


def _validated_covariance_rows(
    name: str, value: np.ndarray, *, count: int,
) -> np.ndarray:
    covariance = np.asarray(value, dtype=float)
    if (
        covariance.shape != (count, 3, 3)
        or not np.isfinite(covariance).all()
        or not np.allclose(
            covariance, np.swapaxes(covariance, 1, 2), atol=1e-12, rtol=0.0,
        )
        or any(
            float(np.min(np.linalg.eigvalsh(row))) < -1e-12
            for row in covariance
        )
    ):
        raise ValueError(f"{name} must be finite PSD per-row 3x3 covariance")
    return 0.5 * (covariance + np.swapaxes(covariance, 1, 2))


def _normalize_log_weights(log_weights: np.ndarray) -> np.ndarray:
    values = np.asarray(log_weights, dtype=float)
    if values.ndim != 1 or not len(values) or not np.isfinite(values).all():
        raise ValueError("circular log weights must be one finite vector")
    shifted = values - float(np.max(values))
    weights = np.exp(shifted)
    total = float(np.sum(weights))
    if not np.isfinite(total) or total <= 0.0:
        raise RuntimeError("circular likelihood normalization failed")
    return weights / total


def _circular_convolve(weights: np.ndarray, variance_rad2: float) -> np.ndarray:
    values = np.asarray(weights, dtype=float)
    if variance_rad2 <= 0.0:
        return values.copy()
    count = len(values)
    offsets = np.arange(count, dtype=float) * 2.0 * np.pi / float(count)
    offsets = np.arctan2(np.sin(offsets), np.cos(offsets))
    kernel = np.exp(-0.5 * offsets**2 / float(variance_rad2))
    kernel /= np.sum(kernel)
    convolved = np.fft.ifft(np.fft.fft(values) * np.fft.fft(kernel)).real
    convolved = np.maximum(convolved, 0.0)
    return convolved / np.sum(convolved)


def _endpoint_dynamic_covariance(
    *,
    omega: np.ndarray,
    lever_sensor_to_joint_m: np.ndarray,
    accelerometer_covariance_m2_s4: np.ndarray,
    gyroscope_covariance_rad2_s2: np.ndarray,
    alpha_noise_gain_s2_inv: float,
) -> np.ndarray:
    lever = np.asarray(lever_sensor_to_joint_m, dtype=float)
    omega = np.asarray(omega, dtype=float)
    omega_jacobian = (
        float(omega @ lever) * np.eye(3)
        + np.outer(omega, lever)
        - 2.0 * np.outer(lever, omega)
    )
    alpha_jacobian = -_skew(lever)
    covariance = (
        accelerometer_covariance_m2_s4
        + omega_jacobian @ gyroscope_covariance_rad2_s2 @ omega_jacobian.T
        + alpha_jacobian
        @ (float(alpha_noise_gain_s2_inv) * gyroscope_covariance_rad2_s2)
        @ alpha_jacobian.T
    )
    return 0.5 * (covariance + covariance.T)


def _calibration_joint_force_jacobian(
    *,
    corrected_accelerometer_sensor: np.ndarray,
    smoothed_gyro_sensor: np.ndarray,
    angular_acceleration_sensor: np.ndarray,
    lever_sensor_to_joint_m: np.ndarray,
) -> np.ndarray:
    """Signed Jacobian of corrected joint force to one 24-D sensor state.

    The parameter ordering is the capture-wide calibration owner's exact
    ``[acc_bias(3), gyro_bias(3), acc_matrix(9), gyro_matrix(9)]`` order.
    Bias and residual matrices are coherent sensor states, so this Jacobian is
    later stacked across epochs and marginalized once rather than injected as
    independent row noise.
    """

    acc = np.asarray(corrected_accelerometer_sensor, dtype=float)
    omega = np.asarray(smoothed_gyro_sensor, dtype=float)
    alpha = np.asarray(angular_acceleration_sensor, dtype=float)
    lever = np.asarray(lever_sensor_to_joint_m, dtype=float)
    jacobian = np.zeros((3, 24), dtype=float)
    jacobian[:, 0:3] = -np.eye(3)
    omega_jacobian = (
        float(omega @ lever) * np.eye(3)
        + np.outer(omega, lever)
        - 2.0 * np.outer(lever, omega)
    )
    alpha_jacobian = -_skew(lever)
    jacobian[:, 3:6] = -omega_jacobian
    for output_axis in range(3):
        for input_axis in range(3):
            column = 6 + 3 * output_axis + input_axis
            delta_acc = np.zeros(3, dtype=float)
            delta_acc[output_axis] = -acc[input_axis]
            jacobian[:, column] = delta_acc

            gyro_column = 15 + 3 * output_axis + input_axis
            delta_omega = np.zeros(3, dtype=float)
            delta_alpha = np.zeros(3, dtype=float)
            delta_omega[output_axis] = -omega[input_axis]
            delta_alpha[output_axis] = -alpha[input_axis]
            jacobian[:, gyro_column] = (
                omega_jacobian @ delta_omega
                + alpha_jacobian @ delta_alpha
            )
    return jacobian


def _block_diagonal(values: Sequence[np.ndarray]) -> np.ndarray:
    dimension = int(sum(value.shape[0] for value in values))
    result = np.zeros((dimension, dimension), dtype=float)
    offset = 0
    for value in values:
        width = int(value.shape[0])
        result[offset:offset + width, offset:offset + width] = value
        offset += width
    return result


def _candidate_independent_psd_upper_envelope(
    covariances: Sequence[np.ndarray],
) -> np.ndarray:
    """Return one result-independent isotropic envelope for an S1 grid.

    Every candidate covariance may rotate with the child yaw.  Using any one
    of those matrices in that candidate's score without its normalization can
    reward variance alignment.  The maximum eigenvalue across the complete,
    preregistered grid yields one shared PSD matrix that Loewner-dominates all
    candidates; its determinant is therefore constant and cancels from every
    normalized likelihood ratio.
    """

    if not covariances:
        raise ValueError("S1 covariance envelope requires candidate matrices")
    shape = np.asarray(covariances[0]).shape
    if len(shape) != 2 or shape[0] != shape[1]:
        raise ValueError("S1 candidate covariance must be square")
    maximum_eigenvalue = 0.0
    for covariance in covariances:
        matrix = np.asarray(covariance, dtype=float)
        if matrix.shape != shape or not np.isfinite(matrix).all():
            raise ValueError("S1 candidate covariances differ or are nonfinite")
        matrix = 0.5 * (matrix + matrix.T)
        minimum, maximum = (
            float(value) for value in (
                np.min(np.linalg.eigvalsh(matrix)),
                np.max(np.linalg.eigvalsh(matrix)),
            )
        )
        if minimum < -1e-10:
            raise ValueError("S1 candidate covariance is not PSD")
        maximum_eigenvalue = max(maximum_eigenvalue, maximum)
    maximum_eigenvalue = max(maximum_eigenvalue, 1e-12)
    return np.eye(shape[0], dtype=float) * maximum_eigenvalue


def _candidate_independent_loewner_upper_envelope(
    covariances: Sequence[np.ndarray],
) -> np.ndarray:
    """Construct one permutation-invariant shared PSD upper envelope.

    A sequential ``envelope += -lambda_min(envelope-C_i) I`` construction
    over-inflates the result once per S1 cell and therefore changes with grid
    enumeration.  Center once at the arithmetic mean, then apply the single
    largest eigenshift required to dominate every candidate.
    """

    values = tuple(np.asarray(value, dtype=float) for value in covariances)
    if (
        not values
        or any(value.shape != values[0].shape for value in values)
        or any(not np.isfinite(value).all() for value in values)
    ):
        raise ValueError("Loewner envelope requires like-shaped covariances")
    symmetric = tuple(0.5 * (value + value.T) for value in values)
    center = np.mean(np.stack(symmetric), axis=0)
    shift = max(
        0.0,
        *(float(np.max(np.linalg.eigvalsh(value - center))) for value in symmetric),
    )
    scale = max(1.0, float(np.max(np.abs(np.linalg.eigvalsh(center)))))
    envelope = center + np.eye(center.shape[0]) * (
        shift + 100.0 * np.finfo(float).eps * scale
    )
    for value in symmetric:
        if float(np.min(np.linalg.eigvalsh(envelope - value))) < -1e-10 * scale:
            raise RuntimeError("mean-centered Loewner envelope failed domination")
    return 0.5 * (envelope + envelope.T)


def edge_local_joint_acceleration_heading_log_likelihood(
    *,
    pair: AlignedPair,
    connection: EdgeConnectionVectors,
    parent_quaternion_world_sensor_wxyz: np.ndarray,
    child_quaternion_world_sensor_wxyz: np.ndarray,
    delta_grid_rad: np.ndarray,
    sample_period_s: float,
    savgol_window_samples: int,
    savgol_polynomial: int,
    estimation_rate_hz: float,
    effective_epoch_cap: int,
    parent_accelerometer_covariance_m2_s4: np.ndarray,
    child_accelerometer_covariance_m2_s4: np.ndarray,
    parent_gyroscope_covariance_rad2_s2: np.ndarray,
    child_gyroscope_covariance_rad2_s2: np.ndarray,
    parent_gap_orientation_covariance_rad2: np.ndarray,
    child_gap_orientation_covariance_rad2: np.ndarray,
    parent_calibration_parameter_covariance: np.ndarray,
    child_calibration_parameter_covariance: np.ndarray,
    parent_calibration_parameter_reference_mean: np.ndarray | None = None,
    child_calibration_parameter_reference_mean: np.ndarray | None = None,
    noise_sigma_multiplier: float,
) -> tuple[
    np.ndarray,
    Mapping[str, Any],
    NonhingeSharedNuisanceSufficientStatistics,
]:
    """Evaluate one robust full-S1 action likelihood without selecting a pose."""

    if pair.edge in HINGE_EDGES or connection.edge != pair.edge:
        raise ValueError("joint-acceleration heading likelihood owns nonhinge edges only")
    count = len(pair.parent_acc)
    parent_quaternion = np.asarray(parent_quaternion_world_sensor_wxyz, dtype=float)
    child_quaternion = np.asarray(child_quaternion_world_sensor_wxyz, dtype=float)
    grid = np.asarray(delta_grid_rad, dtype=float)
    if (
        count < 3
        or pair.parent_acc.shape != (count, 3)
        or pair.child_acc.shape != (count, 3)
        or pair.parent_gyro.shape != (count, 3)
        or pair.child_gyro.shape != (count, 3)
        or parent_quaternion.shape != (count, 4)
        or child_quaternion.shape != (count, 4)
        or grid.ndim != 1
        or len(grid) < 8
        or not np.isfinite(grid).all()
        or sample_period_s <= 0.0
        or estimation_rate_hz <= 0.0
        or effective_epoch_cap <= 0
        or noise_sigma_multiplier < 1.0
        or not all(np.isfinite(np.asarray(value, dtype=float)).all() for value in (
            pair.parent_acc, pair.child_acc, pair.parent_gyro, pair.child_gyro,
            parent_quaternion, child_quaternion,
        ))
    ):
        raise ValueError("nonhinge heading likelihood input shape/settings are invalid")
    if not np.allclose(
        np.diff(np.unwrap(grid)), 2.0 * np.pi / len(grid),
        atol=1e-12, rtol=1e-12,
    ):
        raise ValueError("nonhinge heading likelihood requires one uniform full-S1 grid")
    window = int(savgol_window_samples)
    polynomial = int(savgol_polynomial)
    if window < 3 or window % 2 == 0 or polynomial < 1 or polynomial >= window:
        raise ValueError("nonhinge heading Savitzky-Golay design is invalid")

    parent_acc_covariance = _validated_covariance(
        "parent accelerometer", parent_accelerometer_covariance_m2_s4,
    )
    child_acc_covariance = _validated_covariance(
        "child accelerometer", child_accelerometer_covariance_m2_s4,
    )
    parent_gyro_covariance = _validated_covariance(
        "parent gyroscope", parent_gyroscope_covariance_rad2_s2,
    )
    child_gyro_covariance = _validated_covariance(
        "child gyroscope", child_gyroscope_covariance_rad2_s2,
    )
    parent_gap_covariance = _validated_covariance_rows(
        "parent gap orientation", parent_gap_orientation_covariance_rad2,
        count=count,
    )
    child_gap_covariance = _validated_covariance_rows(
        "child gap orientation", child_gap_orientation_covariance_rad2,
        count=count,
    )
    parent_calibration_covariance = _validated_covariance(
        "parent capture-wide calibration",
        parent_calibration_parameter_covariance,
        dimension=24,
    )
    child_calibration_covariance = _validated_covariance(
        "child capture-wide calibration",
        child_calibration_parameter_covariance,
        dimension=24,
    )
    parent_calibration_reference = np.zeros(24, dtype=float) if (
        parent_calibration_parameter_reference_mean is None
    ) else np.asarray(parent_calibration_parameter_reference_mean, dtype=float)
    child_calibration_reference = np.zeros(24, dtype=float) if (
        child_calibration_parameter_reference_mean is None
    ) else np.asarray(child_calibration_parameter_reference_mean, dtype=float)
    if (
        parent_calibration_reference.shape != (24,)
        or child_calibration_reference.shape != (24,)
        or not np.isfinite(parent_calibration_reference).all()
        or not np.isfinite(child_calibration_reference).all()
    ):
        raise ValueError("nonhinge calibration reference mean is invalid")
    center_covariance = np.asarray(connection.covariance_m2, dtype=float)
    if (
        center_covariance.shape != (6, 6)
        or not np.isfinite(center_covariance).all()
        or not np.allclose(center_covariance, center_covariance.T, atol=1e-12, rtol=0.0)
        or float(np.min(np.linalg.eigvalsh(
            0.5 * (center_covariance + center_covariance.T)
        ))) < -1e-12
    ):
        raise ValueError("nonhinge heading requires finite PSD full-R3 center covariance")
    center_covariance = 0.5 * (center_covariance + center_covariance.T)
    parent_lever = np.asarray(connection.parent_sensor_to_joint_m, dtype=float)
    child_lever = np.asarray(connection.child_sensor_to_joint_m, dtype=float)
    if parent_lever.shape != (3,) or child_lever.shape != (3,):
        raise ValueError("nonhinge heading requires two full-R3 center levers")
    shared_nuisance_reference_mean = np.concatenate((
        parent_lever,
        child_lever,
        parent_calibration_reference,
        child_calibration_reference,
    ))

    parent_world_sensor = qmt_wxyz_to_scipy_active(parent_quaternion).as_matrix()
    child_world_sensor = qmt_wxyz_to_scipy_active(child_quaternion).as_matrix()
    derivative_gain = float(np.sum(
        savgol_coeffs(
            window, polynomial, deriv=1, delta=sample_period_s, use="dot",
        ) ** 2
    ))
    half = window // 2
    epoch_rows = max(1, int(np.rint(1.0 / (sample_period_s * estimation_rate_hz))))
    valid_rows: list[np.ndarray] = []
    parent_joint_world: list[np.ndarray] = []
    child_joint_world: list[np.ndarray] = []
    parent_white_world_covariance: list[np.ndarray] = []
    child_white_world_covariance: list[np.ndarray] = []
    parent_gap_world_covariance: list[np.ndarray] = []
    child_gap_world_covariance: list[np.ndarray] = []
    parent_center_jacobian: list[np.ndarray] = []
    child_center_jacobian: list[np.ndarray] = []
    parent_calibration_jacobian: list[np.ndarray] = []
    child_calibration_jacobian: list[np.ndarray] = []
    child_time_derivative: list[np.ndarray] = []
    epoch_blocks: list[np.ndarray] = []
    concatenated_row_offset = 0
    for span in pair.contiguous_spans:
        start, stop = int(span.start), int(span.stop)
        if stop - start < window + 2 * half:
            continue
        parent_terms, parent_alpha, parent_smoothed_gyro = _center_terms(
            np.asarray(pair.parent_gyro[start:stop], dtype=float),
            dt=sample_period_s, window=window, polynomial=polynomial,
        )
        child_terms, child_alpha, child_smoothed_gyro = _center_terms(
            np.asarray(pair.child_gyro[start:stop], dtype=float),
            dt=sample_period_s, window=window, polynomial=polynomial,
        )
        local = np.arange(half, stop - start - half, dtype=int)
        source = start + local
        parent_corrected_sensor = (
            np.asarray(pair.parent_acc[start:stop], dtype=float)
            + np.einsum("nij,j->ni", parent_terms, parent_lever)
        )[local]
        child_corrected_sensor = (
            np.asarray(pair.child_acc[start:stop], dtype=float)
            + np.einsum("nij,j->ni", child_terms, child_lever)
        )[local]
        parent_rotation = parent_world_sensor[source]
        child_rotation = child_world_sensor[source]
        parent_world = np.einsum(
            "nij,nj->ni", parent_rotation, parent_corrected_sensor,
        )
        child_world = np.einsum(
            "nij,nj->ni", child_rotation, child_corrected_sensor,
        )
        parent_covariance_sensor = np.asarray([
            _endpoint_dynamic_covariance(
                omega=parent_smoothed_gyro[index],
                lever_sensor_to_joint_m=parent_lever,
                accelerometer_covariance_m2_s4=parent_acc_covariance,
                gyroscope_covariance_rad2_s2=parent_gyro_covariance,
                alpha_noise_gain_s2_inv=derivative_gain,
            )
            for position, index in enumerate(local)
        ])
        child_covariance_sensor = np.asarray([
            _endpoint_dynamic_covariance(
                omega=child_smoothed_gyro[index],
                lever_sensor_to_joint_m=child_lever,
                accelerometer_covariance_m2_s4=child_acc_covariance,
                gyroscope_covariance_rad2_s2=child_gyro_covariance,
                alpha_noise_gain_s2_inv=derivative_gain,
            )
            for position, index in enumerate(local)
        ])
        parent_white_covariance_world = np.einsum(
            "nij,njk,nlk->nil",
            parent_rotation, parent_covariance_sensor, parent_rotation,
        ) * noise_sigma_multiplier**2
        child_white_covariance_world = np.einsum(
            "nij,njk,nlk->nil",
            child_rotation, child_covariance_sensor, child_rotation,
        ) * noise_sigma_multiplier**2
        parent_orientation_jacobian = -np.einsum(
            "nij,njk->nik",
            parent_rotation,
            np.asarray([_skew(value) for value in parent_corrected_sensor]),
        )
        child_orientation_jacobian = -np.einsum(
            "nij,njk->nik",
            child_rotation,
            np.asarray([_skew(value) for value in child_corrected_sensor]),
        )
        parent_gap_covariance_world = np.einsum(
            "nij,njk,nlk->nil",
            parent_orientation_jacobian,
            parent_gap_covariance[source],
            parent_orientation_jacobian,
        )
        child_gap_covariance_world = np.einsum(
            "nij,njk,nlk->nil",
            child_orientation_jacobian,
            child_gap_covariance[source],
            child_orientation_jacobian,
        )
        parent_jacobian = np.einsum(
            "nij,njk->nik", parent_rotation, parent_terms[local],
        )
        child_jacobian = np.einsum(
            "nij,njk->nik", child_rotation, child_terms[local],
        )
        parent_calibration_sensor = np.asarray([
            _calibration_joint_force_jacobian(
                corrected_accelerometer_sensor=pair.parent_acc[source[position]],
                smoothed_gyro_sensor=parent_smoothed_gyro[index],
                angular_acceleration_sensor=parent_alpha[index],
                lever_sensor_to_joint_m=parent_lever,
            )
            for position, index in enumerate(local)
        ])
        child_calibration_sensor = np.asarray([
            _calibration_joint_force_jacobian(
                corrected_accelerometer_sensor=pair.child_acc[source[position]],
                smoothed_gyro_sensor=child_smoothed_gyro[index],
                angular_acceleration_sensor=child_alpha[index],
                lever_sensor_to_joint_m=child_lever,
            )
            for position, index in enumerate(local)
        ])
        parent_calibration_world = np.einsum(
            "nij,njk->nik", parent_rotation, parent_calibration_sensor,
        )
        child_calibration_world = np.einsum(
            "nij,njk->nik", child_rotation, child_calibration_sensor,
        )
        derivative = np.gradient(child_world, sample_period_s, axis=0)
        valid_rows.append(source)
        parent_joint_world.append(parent_world)
        child_joint_world.append(child_world)
        parent_white_world_covariance.append(parent_white_covariance_world)
        child_white_world_covariance.append(child_white_covariance_world)
        parent_gap_world_covariance.append(parent_gap_covariance_world)
        child_gap_world_covariance.append(child_gap_covariance_world)
        parent_center_jacobian.append(parent_jacobian)
        child_center_jacobian.append(child_jacobian)
        parent_calibration_jacobian.append(parent_calibration_world)
        child_calibration_jacobian.append(child_calibration_world)
        child_time_derivative.append(derivative)
        for block_start in range(0, len(source), epoch_rows):
            block_stop = min(block_start + epoch_rows, len(source))
            if block_stop - block_start >= window:
                epoch_blocks.append(
                    concatenated_row_offset
                    + np.arange(block_start, block_stop, dtype=int)
                )
        concatenated_row_offset += len(source)
    if not valid_rows:
        return np.zeros(len(grid), dtype=float), {
            "status": "LOCAL_NO_UPDATE_NO_COMPLETE_GAP_SAFE_FILTER_SUPPORT",
            "effective_epoch_count": 0,
            "likelihood_is_uniform": True,
            "full_r3_center_levers_used": True,
        }

    rows = np.concatenate(valid_rows)
    parent_world = np.concatenate(parent_joint_world)
    child_world = np.concatenate(child_joint_world)
    parent_white_covariance = np.concatenate(parent_white_world_covariance)
    child_white_covariance = np.concatenate(child_white_world_covariance)
    parent_gap_covariance_world = np.concatenate(parent_gap_world_covariance)
    child_gap_covariance_world = np.concatenate(child_gap_world_covariance)
    parent_jacobian = np.concatenate(parent_center_jacobian)
    child_jacobian = np.concatenate(child_center_jacobian)
    parent_calibration_jacobian_rows = np.concatenate(
        parent_calibration_jacobian,
    )
    child_calibration_jacobian_rows = np.concatenate(
        child_calibration_jacobian,
    )
    child_derivative = np.concatenate(child_time_derivative)
    blocks = epoch_blocks
    if len(blocks) > effective_epoch_cap:
        keep = np.unique(np.rint(
            np.linspace(0, len(blocks) - 1, effective_epoch_cap)
        ).astype(int))
        blocks = [blocks[index] for index in keep]
    if not blocks:
        return np.zeros(len(grid), dtype=float), {
            "status": "LOCAL_NO_UPDATE_NO_COMPLETE_INDEPENDENT_EPOCH",
            "effective_epoch_count": 0,
            "likelihood_is_uniform": True,
            "full_r3_center_levers_used": True,
        }

    lag_sigma = float(pair.alignment.report.get("lag_uncertainty_s", sample_period_s))
    if not np.isfinite(lag_sigma) or lag_sigma < 0.0:
        raise ValueError("pair-clock lag uncertainty is invalid")
    log_likelihood = np.zeros(len(grid), dtype=float)
    candidate_residuals: list[np.ndarray] = []
    candidate_covariances: list[np.ndarray] = []
    candidate_raw_full_covariances: list[np.ndarray] = []
    candidate_white_covariances: list[np.ndarray] = []
    candidate_gap_covariances: list[np.ndarray] = []
    candidate_clock_covariances: list[np.ndarray] = []
    shared_nuisance_covariance = _block_diagonal((
        center_covariance,
        parent_calibration_covariance,
        child_calibration_covariance,
    ))
    candidate_shared_jacobians: list[np.ndarray] = []
    candidate_heading_jacobians: list[np.ndarray] = []
    for candidate_index, delta in enumerate(grid):
        yaw = Rotation.from_rotvec([0.0, 0.0, float(delta)]).as_matrix()
        rotated_child = child_world @ yaw.T
        residual = parent_world - rotated_child
        rotated_child_white_covariance = np.einsum(
            "ij,njk,lk->nil", yaw, child_white_covariance, yaw,
        )
        rotated_child_gap_covariance = np.einsum(
            "ij,njk,lk->nil", yaw, child_gap_covariance_world, yaw,
        )
        rotated_child_jacobian = np.einsum(
            "ij,njk->nik", yaw, child_jacobian,
        )
        center_jacobian = np.concatenate(
            (parent_jacobian, -rotated_child_jacobian), axis=2,
        )
        rotated_child_calibration_jacobian = np.einsum(
            "ij,njk->nik", yaw, child_calibration_jacobian_rows,
        )
        calibration_jacobian = np.concatenate((
            parent_calibration_jacobian_rows,
            -rotated_child_calibration_jacobian,
        ), axis=2)
        rotated_derivative = child_derivative @ yaw.T
        block_residuals = []
        block_white_covariances = []
        block_gap_covariances = []
        block_shared_jacobians = []
        block_clock_jacobians = []
        block_heading_jacobians = []
        for block in blocks:
            block_residuals.append(np.median(residual[block], axis=0))
            white_covariance = np.mean(
                parent_white_covariance[block]
                + rotated_child_white_covariance[block],
                axis=0,
            )
            white_covariance = 0.5 * (
                white_covariance + white_covariance.T
            )
            floor = max(
                1e-12,
                100.0 * np.finfo(float).eps
                * float(np.trace(white_covariance)),
            )
            block_white_covariances.append(
                white_covariance + np.eye(3) * floor
            )
            gap_covariance = np.mean(
                parent_gap_covariance_world[block]
                + rotated_child_gap_covariance[block],
                axis=0,
            )
            block_gap_covariances.append(
                0.5 * (gap_covariance + gap_covariance.T)
            )
            block_shared_jacobians.append(np.concatenate((
                np.mean(center_jacobian[block], axis=0),
                np.mean(calibration_jacobian[block], axis=0),
            ), axis=1))
            block_clock_jacobians.append(
                -np.mean(rotated_derivative[block], axis=0)[:, None]
            )
            block_heading_jacobians.append(
                -np.cross(
                    np.array((0.0, 0.0, 1.0)),
                    np.median(rotated_child[block], axis=0),
                )
            )
        stacked_residual = np.concatenate(block_residuals)
        white_covariance = _block_diagonal(block_white_covariances)
        gap_covariance = _block_diagonal(block_gap_covariances)
        clock_jacobian = np.concatenate(block_clock_jacobians, axis=0)
        clock_covariance = lag_sigma**2 * clock_jacobian @ clock_jacobian.T
        epistemic_covariance = gap_covariance + clock_covariance
        shared_jacobian = np.concatenate(block_shared_jacobians, axis=0)
        heading_jacobian = np.concatenate(block_heading_jacobians)
        candidate_shared_jacobians.append(shared_jacobian)
        candidate_heading_jacobians.append(heading_jacobian)
        candidate_residuals.append(stacked_residual)
        candidate_white_covariances.append(white_covariance)
        candidate_clock_covariances.append(clock_covariance)
        candidate_covariances.append(white_covariance)
        candidate_raw_full_covariances.append(
            white_covariance + epistemic_covariance
        )
        candidate_gap_covariances.append(gap_covariance)
    epistemic_envelope = _candidate_independent_psd_upper_envelope([
        gap + clock for gap, clock in zip(
            candidate_gap_covariances,
            candidate_clock_covariances,
            strict=True,
        )
    ])
    candidate_covariances = [
        value + epistemic_envelope for value in candidate_covariances
    ]
    isotropic_envelope = _candidate_independent_psd_upper_envelope(
        candidate_covariances,
    )
    loewner_envelope = _candidate_independent_loewner_upper_envelope(
        candidate_covariances,
    )
    heading_information = np.zeros(len(grid), dtype=float)
    shared_score = np.zeros(
        (len(grid), shared_nuisance_covariance.shape[0]), dtype=float,
    )
    nuisance_normal = np.zeros(
        (
            len(grid),
            shared_nuisance_covariance.shape[0],
            shared_nuisance_covariance.shape[0],
        ),
        dtype=float,
    )
    nuisance_score = np.zeros_like(shared_score)
    residual_quadratic = np.zeros(len(grid), dtype=float)
    independent_logdet = np.zeros(len(grid), dtype=float)
    one_step_irls_weight = np.zeros(len(grid), dtype=float)
    action_shared_variance = np.zeros(len(grid), dtype=float)
    isotropic_ablation_log = np.zeros(len(grid), dtype=float)
    loewner_ablation_log = np.zeros(len(grid), dtype=float)
    actual_covariance_ablation_log = np.zeros(len(grid), dtype=float)
    raw_heteroscedastic_ablation_log = np.zeros(len(grid), dtype=float)
    white_covariance_only_log = np.zeros(len(grid), dtype=float)
    raw_white_gap_covariance_only_log = np.zeros(len(grid), dtype=float)
    raw_white_clock_covariance_only_log = np.zeros(len(grid), dtype=float)
    active_covariance_only_log = np.zeros(len(grid), dtype=float)
    raw_covariance_only_log = np.zeros(len(grid), dtype=float)
    residual_only_ablation_log = np.zeros(len(grid), dtype=float)
    isotropic_inverse = np.linalg.inv(isotropic_envelope)
    loewner_inverse = np.linalg.inv(loewner_envelope)
    isotropic_logdet = float(np.linalg.slogdet(isotropic_envelope)[1])
    loewner_logdet = float(np.linalg.slogdet(loewner_envelope)[1])
    for candidate_index, stacked_residual in enumerate(candidate_residuals):
        actual_candidate_covariance = candidate_covariances[candidate_index]
        actual_sign, actual_logdet = np.linalg.slogdet(
            actual_candidate_covariance
        )
        if actual_sign <= 0.0 or not np.isfinite(actual_logdet):
            raise RuntimeError("nonhinge independent covariance is not positive")
        actual_inverse = np.linalg.inv(actual_candidate_covariance)
        white_sign, white_logdet = np.linalg.slogdet(
            candidate_white_covariances[candidate_index]
        )
        if white_sign <= 0.0 or not np.isfinite(white_logdet):
            raise RuntimeError("nonhinge white covariance is not positive")
        white_gap_sign, white_gap_logdet = np.linalg.slogdet(
            candidate_white_covariances[candidate_index]
            + candidate_gap_covariances[candidate_index]
        )
        white_clock_sign, white_clock_logdet = np.linalg.slogdet(
            candidate_white_covariances[candidate_index]
            + candidate_clock_covariances[candidate_index]
        )
        if (
            white_gap_sign <= 0.0
            or white_clock_sign <= 0.0
            or not np.isfinite(white_gap_logdet)
            or not np.isfinite(white_clock_logdet)
        ):
            raise RuntimeError("nonhinge component covariance is not positive")
        raw_covariance = candidate_raw_full_covariances[candidate_index]
        raw_sign, raw_logdet = np.linalg.slogdet(raw_covariance)
        if raw_sign <= 0.0 or not np.isfinite(raw_logdet):
            raise RuntimeError("raw heteroscedastic covariance is not positive")
        raw_inverse = np.linalg.inv(raw_covariance)
        candidate_inverse = actual_inverse
        covariance_logdet = float(actual_logdet)
        squared = float(
            stacked_residual @ candidate_inverse @ stacked_residual
        )
        robust_weight = 1.0 / (1.0 + max(squared, 0.0))
        one_step_irls_weight[candidate_index] = robust_weight
        log_likelihood[candidate_index] = -0.5 * float(
            np.log1p(max(squared, 0.0)) + covariance_logdet
        )
        actual_squared = float(
            stacked_residual @ actual_inverse @ stacked_residual
        )
        actual_covariance_ablation_log[candidate_index] = -0.5 * (
            np.log1p(max(actual_squared, 0.0)) + actual_logdet
        )
        raw_squared = float(stacked_residual @ raw_inverse @ stacked_residual)
        raw_heteroscedastic_ablation_log[candidate_index] = -0.5 * (
            np.log1p(max(raw_squared, 0.0)) + raw_logdet
        )
        white_covariance_only_log[candidate_index] = -0.5 * white_logdet
        raw_white_gap_covariance_only_log[candidate_index] = (
            -0.5 * white_gap_logdet
        )
        raw_white_clock_covariance_only_log[candidate_index] = (
            -0.5 * white_clock_logdet
        )
        active_covariance_only_log[candidate_index] = -0.5 * actual_logdet
        raw_covariance_only_log[candidate_index] = -0.5 * raw_logdet
        residual_only_ablation_log[candidate_index] = -0.5 * np.log1p(
            max(actual_squared, 0.0)
        )
        heading_jacobian = candidate_heading_jacobians[candidate_index]
        nuisance_jacobian = candidate_shared_jacobians[candidate_index]
        information = float(
            heading_jacobian @ candidate_inverse @ heading_jacobian
        )
        score = heading_jacobian @ candidate_inverse @ nuisance_jacobian
        heading_information[candidate_index] = max(0.0, information)
        shared_score[candidate_index] = score
        nuisance_normal[candidate_index] = (
            nuisance_jacobian.T @ candidate_inverse @ nuisance_jacobian
        )
        nuisance_score[candidate_index] = (
            nuisance_jacobian.T @ candidate_inverse @ stacked_residual
        )
        residual_quadratic[candidate_index] = max(
            0.0,
            float(stacked_residual @ candidate_inverse @ stacked_residual),
        )
        independent_logdet[candidate_index] = float(covariance_logdet)
        isotropic_squared = float(
            stacked_residual @ isotropic_inverse @ stacked_residual
        )
        loewner_squared = float(
            stacked_residual @ loewner_inverse @ stacked_residual
        )
        isotropic_ablation_log[candidate_index] = -0.5 * (
            np.log1p(max(isotropic_squared, 0.0)) + isotropic_logdet
        )
        loewner_ablation_log[candidate_index] = -0.5 * (
            np.log1p(max(loewner_squared, 0.0)) + loewner_logdet
        )
        if information > 1e-15:
            action_shared_variance[candidate_index] = max(
                0.0,
                float(
                    score
                    @ shared_nuisance_covariance
                    @ score.T
                    / information**2
                ),
            )
    normalized = _normalize_log_weights(log_likelihood)
    resultant = np.sum(normalized * np.exp(1j * grid))
    entropy = float(-np.sum(normalized * np.log(np.maximum(normalized, 1e-300))))
    uniform_entropy = float(np.log(len(grid)))
    shared_heading_variance_floor_rad2 = float(np.max(action_shared_variance))

    def covariance_range(values: Sequence[np.ndarray]) -> Mapping[str, float]:
        eigenvalues = np.concatenate([
            np.linalg.eigvalsh(0.5 * (value + value.T)) for value in values
        ])
        traces = np.asarray([np.trace(value) for value in values], dtype=float)
        return {
            "minimum_eigenvalue": float(np.min(eigenvalues)),
            "maximum_eigenvalue": float(np.max(eigenvalues)),
            "minimum_trace": float(np.min(traces)),
            "maximum_trace": float(np.max(traces)),
        }
    sufficient_statistics = NonhingeSharedNuisanceSufficientStatistics(
        heading_information=heading_information.copy(),
        shared_score=shared_score.copy(),
        shared_covariance=shared_nuisance_covariance.copy(),
        nuisance_normal=nuisance_normal.copy(),
        nuisance_score=nuisance_score.copy(),
        residual_quadratic=residual_quadratic.copy(),
        independent_covariance_log_determinant=independent_logdet.copy(),
        residual_dimension=int(candidate_covariances[0].shape[0]),
        shared_nuisance_reference_mean=shared_nuisance_reference_mean.copy(),
    )
    covariance_ablation = {}
    for name, values in (
        ("GLOBAL_ISOTROPIC_MAX_EIGENVALUE", isotropic_ablation_log),
        ("CANDIDATE_INDEPENDENT_LOEWNER_ENVELOPE", loewner_ablation_log),
        (
            "ACTUAL_CANDIDATE_COVARIANCE_WITH_LOGDET",
            actual_covariance_ablation_log,
        ),
        (
            "RAW_HETEROSCEDASTIC_GAP_CLOCK_WITH_LOGDET_DIAGNOSTIC_ONLY",
            raw_heteroscedastic_ablation_log,
        ),
    ):
        weights = _normalize_log_weights(values)
        covariance_ablation[name] = {
            "normalized_weights_sha256": _array_sha256(weights),
            "information_gain_from_uniform_nats": float(
                np.log(len(weights))
                + np.sum(weights * np.log(np.maximum(weights, 1e-300)))
            ),
        }
    covariance_only_ablation = {}
    for name, values in (
        ("GENERATIVE_WHITE_ONLY_ZERO_RESIDUAL", white_covariance_only_log),
        (
            "RAW_WHITE_PLUS_HETEROSCEDASTIC_GAP_ZERO_RESIDUAL_DIAGNOSTIC_ONLY",
            raw_white_gap_covariance_only_log,
        ),
        (
            "RAW_WHITE_PLUS_HETEROSCEDASTIC_CLOCK_ZERO_RESIDUAL_DIAGNOSTIC_ONLY",
            raw_white_clock_covariance_only_log,
        ),
        (
            "ACTIVE_WHITE_PLUS_COMMON_EPISTEMIC_ENVELOPE_ZERO_RESIDUAL",
            active_covariance_only_log,
        ),
        (
            "RAW_HETEROSCEDASTIC_GAP_CLOCK_ZERO_RESIDUAL_DIAGNOSTIC_ONLY",
            raw_covariance_only_log,
        ),
        ("ACTIVE_RESIDUAL_ONLY_DIAGNOSTIC", residual_only_ablation_log),
    ):
        weights = _normalize_log_weights(values)
        covariance_only_ablation[name] = {
            "normalized_weights_sha256": _array_sha256(weights),
            "information_gain_from_uniform_nats": float(
                np.log(len(weights))
                + np.sum(weights * np.log(np.maximum(weights, 1e-300)))
            ),
        }
    return log_likelihood, {
        "schema": "biospur-c2-edge-local-joint-acceleration-heading-likelihood-v1",
        "status": "FINITE_FULL_S1_LIKELIHOOD",
        "edge": pair.edge,
        "action": pair.action,
        "valid_gap_safe_row_count": int(len(rows)),
        "valid_source_rows_sha256": _array_sha256(rows.astype(np.int64)),
        "effective_epoch_count": len(blocks),
        "effective_epoch_cap": int(effective_epoch_cap),
        "two_hundred_hz_rows_treated_as_independent_votes": False,
        "robust_epoch_aggregation": (
            "BLOCK_MEDIAN_THEN_ACTION_LEVEL_LOG1P_MAHALANOBIS"
        ),
        "full_r3_center_levers_used": True,
        "center_covariance_propagated": True,
        "full_center_covariance_cross_blocks_preserved": True,
        "gap_orientation_covariance_jacobian": "-R_TIMES_SKEW_F",
        "gap_orientation_covariance_propagated_per_row": True,
        "accelerometer_gyro_and_alpha_white_marginals_propagated": True,
        "pair_clock_lag_covariance_propagated_as_one_action_local_coherent_state": True,
        "capture_wide_calibration_parameter_order": (
            "ACC_BIAS3_GYRO_BIAS3_ACC_MATRIX9_GYRO_MATRIX9"
        ),
        "calibration_common_mode_marginalization": (
            "SIGNED_JACOBIAN_FULL_24X24_PER_SENSOR_IN_RAO_CONDITIONAL_"
            "CAPTURE_WIDE_NUISANCE_STATE"
        ),
        "calibration_common_mode_repeated_as_independent_epoch_noise": False,
        "center_and_calibration_shared_across_actions": True,
        "shared_heading_variance_floor_rad2": (
            shared_heading_variance_floor_rad2
        ),
        "shared_heading_floor_policy": (
            "LEGACY_FIRST_ORDER_RATIO_RETAINED_AS_DIAGNOSTIC_ONLY_NOT_APPLIED"
        ),
        "shared_heading_floor_applied_by_persistent_owner_once_per_prefix": False,
        "heading_information_sha256": _array_sha256(heading_information),
        "shared_nuisance_score_sha256": _array_sha256(shared_score),
        "nuisance_normal_j_t_w_j_sha256": _array_sha256(nuisance_normal),
        "nuisance_score_j_t_w_r_sha256": _array_sha256(nuisance_score),
        "residual_quadratic_r_t_w_r_sha256": _array_sha256(
            residual_quadratic
        ),
        "candidate_independent_covariance_logdet_sha256": _array_sha256(
            independent_logdet
        ),
        "candidate_independent_covariance_logdet_is_constant_on_s1": False,
        "joint_marginal_robustness_owner": (
            "GAP_SAFE_BLOCK_MEDIAN_PLUS_GAUSSIAN_SHARED_NUISANCE_MARGINAL"
        ),
        "one_step_pre_nuisance_irls_used_in_joint_marginal": False,
        "one_step_pre_nuisance_irls_weight_ablation_sha256": _array_sha256(
            one_step_irls_weight
        ),
        "shared_nuisance_absolute_reference_mean_sha256": _array_sha256(
            shared_nuisance_reference_mean
        ),
        "shared_nuisance_parameter_basis_sha256": (
            _SHARED_NUISANCE_PARAMETER_BASIS_SHA256
        ),
        "shared_nuisance_covariance_sha256": _array_sha256(
            shared_nuisance_covariance
        ),
        "low_heading_sensitivity_divided_into_persistent_max_floor": False,
        "candidate_covariance_score_policy": (
            "ACTUAL_GENERATIVE_WHITE_CANDIDATE_COVARIANCE_PLUS_"
            "CANDIDATE_INDEPENDENT_EPISTEMIC_GAP_CLOCK_ENVELOPE_WITH_EXACT_LOGDET"
        ),
        "candidate_dependent_covariance_used_in_score": True,
        "candidate_covariance_logdet_included": True,
        "gap_and_clock_epistemic_covariance_can_create_heading_evidence": False,
        "gap_and_clock_covariance_determinant_or_alignment_counted_as_functional_motion_information": False,
        "zero_residual_covariance_only_information_used_for_physical_branch_selection_or_viewer_pose": False,
        "covariance_owner_chosen_by_resultant_or_visual_sharpness": False,
        "covariance_owner_three_way_ablation": covariance_ablation,
        "zero_residual_covariance_only_information_ablation": (
            covariance_only_ablation
        ),
        "covariance_owner_selected_by_sharpness": False,
        "covariance_owner_selection_basis": (
            "GENERATIVE_WHITE_D_DELTA_EQUALS_PARENT_WHITE_PLUS_ROTATED_CHILD_"
            "WHITE;EPISTEMIC_GAP_AND_CLOCK_ADDENDA_USE_ONE_ISOTROPIC_PSD_"
            "UPPER_ENVELOPE_SO_UNCERTAINTY_ALONE_CANNOT_BUY_S1_WEIGHT"
        ),
        "covariance_units": "(m/s^2)^2 = m^2/s^4",
        "residual_units": "m/s^2",
        "center_lever_units": "m",
        "center_jacobian_units": "1/s^2",
        "calibration_parameter_basis_units": (
            "ACC_BIAS_M_S2,GYRO_BIAS_RAD_S,ACC_MATRIX_FRACTION,"
            "GYRO_MATRIX_FRACTION_PER_SENSOR"
        ),
        "actual_candidate_covariance_eigen_trace_range": covariance_range(
            candidate_covariances
        ),
        "white_covariance_eigen_trace_range": covariance_range(
            candidate_white_covariances
        ),
        "gap_covariance_eigen_trace_range": covariance_range(
            candidate_gap_covariances
        ),
        "pair_clock_covariance_eigen_trace_range": covariance_range(
            candidate_clock_covariances
        ),
        "epistemic_gap_clock_envelope_eigen_trace_range": covariance_range(
            (epistemic_envelope,)
        ),
        "raw_full_heteroscedastic_covariance_eigen_trace_range": covariance_range(
            candidate_raw_full_covariances
        ),
        "loewner_envelope_eigen_trace_range": covariance_range(
            (loewner_envelope,)
        ),
        "isotropic_envelope_eigen_trace_range": covariance_range(
            (isotropic_envelope,)
        ),
        "shared_center_covariance_eigen_trace_range": covariance_range(
            (center_covariance,)
        ),
        "shared_parent_calibration_covariance_eigen_trace_range": covariance_range(
            (parent_calibration_covariance,)
        ),
        "shared_child_calibration_covariance_eigen_trace_range": covariance_range(
            (child_calibration_covariance,)
        ),
        "isotropic_envelope_sha256": _array_sha256(isotropic_envelope),
        "loewner_envelope_sha256": _array_sha256(loewner_envelope),
        "candidate_grid_count": int(len(grid)),
        "candidate_grid_sha256": _array_sha256(grid),
        "normalized_likelihood_sha256": _array_sha256(normalized),
        "circular_resultant_magnitude": float(abs(resultant)),
        "circular_mean_coordinate_rad": float(np.angle(resultant)),
        "entropy_nats": entropy,
        "uniform_entropy_nats": uniform_entropy,
        "information_gain_from_uniform_nats": uniform_entropy - entropy,
        "multimodal_full_grid_retained": True,
        "argmax_used_as_hard_heading_lock": False,
        "rom_pose_label_pixel_or_carried_zero_used": False,
    }, sufficient_statistics


def _sufficient_statistics_sha256(
    statistics: NonhingeSharedNuisanceSufficientStatistics,
) -> str:
    digest = hashlib.sha256()
    digest.update(statistics.parameter_basis_sha256.encode("ascii"))
    digest.update(str(int(statistics.residual_dimension)).encode("ascii"))
    for value in (
        statistics.heading_information,
        statistics.shared_score,
        statistics.shared_covariance,
        statistics.nuisance_normal,
        statistics.nuisance_score,
        statistics.residual_quadratic,
        statistics.independent_covariance_log_determinant,
        statistics.shared_nuisance_reference_mean,
    ):
        digest.update(np.ascontiguousarray(np.asarray(value)).view(np.uint8))
    return digest.hexdigest()


def _positive_definite_precision(
    covariance: np.ndarray,
) -> tuple[np.ndarray, float]:
    value = np.asarray(covariance, dtype=float)
    eigenvalues, eigenvectors = np.linalg.eigh(0.5 * (value + value.T))
    scale = max(1.0, float(np.max(np.abs(eigenvalues))))
    if float(np.min(eigenvalues)) <= 1e-12 * scale:
        raise ValueError(
            "Rao-Blackwellized nuisance prior must be finite positive definite"
        )
    precision = (eigenvectors / eigenvalues[None, :]) @ eigenvectors.T
    return 0.5 * (precision + precision.T), float(np.sum(np.log(eigenvalues)))


def _circular_convolve_columns(
    values: np.ndarray, variance_rad2: float,
) -> np.ndarray:
    array = np.asarray(values, dtype=float)
    if variance_rad2 <= 0.0:
        return array.copy()
    count = len(array)
    offsets = np.arange(count, dtype=float) * 2.0 * np.pi / float(count)
    offsets = np.arctan2(np.sin(offsets), np.cos(offsets))
    kernel = np.exp(-0.5 * offsets**2 / float(variance_rad2))
    kernel /= np.sum(kernel)
    shape = (count,) + (1,) * (array.ndim - 1)
    result = np.fft.ifft(
        np.fft.fft(array, axis=0)
        * np.fft.fft(kernel).reshape(shape),
        axis=0,
    ).real
    return result


def _circular_transition_conditional_gaussian(
    *,
    weights: np.ndarray,
    conditional_mean: np.ndarray,
    conditional_covariance: np.ndarray,
    variance_rad2: float,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Propagate one factorized S1/Gaussian state without new evidence."""

    values = np.asarray(weights, dtype=float)
    means = np.asarray(conditional_mean, dtype=float)
    covariances = np.asarray(conditional_covariance, dtype=float)
    predicted_weights = _circular_convolve_columns(values, variance_rad2)
    predicted_weights = np.maximum(predicted_weights, 1e-300)
    predicted_weights /= np.sum(predicted_weights)
    if variance_rad2 <= 0.0:
        return predicted_weights, means.copy(), covariances.copy()
    first_numerator = _circular_convolve_columns(
        values[:, None] * means, variance_rad2,
    )
    second_moment = covariances + np.einsum(
        "gi,gj->gij", means, means,
    )
    second_numerator = _circular_convolve_columns(
        values[:, None, None] * second_moment, variance_rad2,
    )
    predicted_mean = first_numerator / predicted_weights[:, None]
    predicted_second = second_numerator / predicted_weights[:, None, None]
    predicted_covariance = predicted_second - np.einsum(
        "gi,gj->gij", predicted_mean, predicted_mean,
    )
    predicted_covariance = 0.5 * (
        predicted_covariance + np.swapaxes(predicted_covariance, 1, 2)
    )
    return predicted_weights, predicted_mean, predicted_covariance


def _rao_blackwellized_joint_heading_step(
    *,
    prior_weights: np.ndarray,
    conditional_mean: np.ndarray,
    conditional_covariance: np.ndarray,
    previous_external_mean: np.ndarray,
    previous_external_covariance: np.ndarray,
    current_external_mean: np.ndarray,
    current_external_covariance: np.ndarray,
    statistics: NonhingeSharedNuisanceSufficientStatistics,
    transition_variance_rad2: float,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, Mapping[str, Any]]:
    """One chronological S1 step carrying a shared Gaussian conditionally."""

    weights = np.asarray(prior_weights, dtype=float)
    means = np.asarray(conditional_mean, dtype=float)
    covariances = np.asarray(conditional_covariance, dtype=float)
    grid_count, nuisance_dimension = means.shape
    if (
        weights.shape != (grid_count,)
        or covariances.shape != (grid_count, nuisance_dimension, nuisance_dimension)
        or np.asarray(statistics.nuisance_normal).shape
        != (grid_count, nuisance_dimension, nuisance_dimension)
        or statistics.parameter_basis_sha256
        != _SHARED_NUISANCE_PARAMETER_BASIS_SHA256
    ):
        raise ValueError("Rao-Blackwellized heading state/basis changed")
    previous_precision, previous_covariance_logdet = _positive_definite_precision(
        previous_external_covariance
    )
    current_precision, current_covariance_logdet = _positive_definite_precision(
        current_external_covariance
    )
    previous_mean = np.asarray(previous_external_mean, dtype=float)
    current_mean = np.asarray(current_external_mean, dtype=float)
    if previous_mean.shape != (nuisance_dimension,) or current_mean.shape != (
        nuisance_dimension,
    ):
        raise ValueError("Rao-Blackwellized external prior mean changed basis")
    precision_delta = current_precision - previous_precision
    natural_delta = (
        current_precision @ current_mean - previous_precision @ previous_mean
    )
    external_log_weight_ratio = np.empty(grid_count, dtype=float)
    for index in range(grid_count):
        state_precision, state_covariance_logdet = _positive_definite_precision(
            covariances[index]
        )
        updated_precision = state_precision + precision_delta
        updated_precision = 0.5 * (updated_precision + updated_precision.T)
        sign, updated_precision_logdet = np.linalg.slogdet(updated_precision)
        if sign <= 0.0:
            raise RuntimeError("external nuisance prior update made state indefinite")
        updated_covariance = np.linalg.inv(updated_precision)
        updated_natural = state_precision @ means[index] + natural_delta
        updated_mean = updated_covariance @ updated_natural
        external_log_weight_ratio[index] = 0.5 * (
            -state_covariance_logdet
            - current_covariance_logdet
            + previous_covariance_logdet
            - updated_precision_logdet
            - float(means[index] @ state_precision @ means[index])
            - float(current_mean @ current_precision @ current_mean)
            + float(previous_mean @ previous_precision @ previous_mean)
            + float(updated_natural @ updated_mean)
        )
        means[index] = updated_mean
        covariances[index] = 0.5 * (
            updated_covariance + updated_covariance.T
        )
    reconditioned_weights = _normalize_log_weights(
        np.log(np.maximum(weights, 1e-300)) + external_log_weight_ratio
    )
    predicted_weights, means, covariances = (
        _circular_transition_conditional_gaussian(
            weights=reconditioned_weights,
            conditional_mean=means,
            conditional_covariance=covariances,
            variance_rad2=transition_variance_rad2,
        )
    )
    normal = np.asarray(statistics.nuisance_normal, dtype=float)
    local_score = np.asarray(statistics.nuisance_score, dtype=float)
    local_quadratic = np.asarray(statistics.residual_quadratic, dtype=float)
    logdet_d = np.asarray(
        statistics.independent_covariance_log_determinant, dtype=float,
    )
    reference = np.asarray(statistics.shared_nuisance_reference_mean, dtype=float)
    absolute_score = local_score - np.einsum("gij,j->gi", normal, reference)
    absolute_quadratic = (
        local_quadratic
        - 2.0 * (local_score @ reference)
        + np.einsum("i,gij,j->g", reference, normal, reference)
    )
    log_evidence = np.empty(grid_count, dtype=float)
    posterior_means = np.empty_like(means)
    posterior_covariances = np.empty_like(covariances)
    for index in range(grid_count):
        prior_precision, prior_logdet_covariance = _positive_definite_precision(
            covariances[index]
        )
        prior_natural = prior_precision @ means[index]
        posterior_precision = prior_precision + normal[index]
        posterior_precision = 0.5 * (
            posterior_precision + posterior_precision.T
        )
        sign, posterior_logdet_precision = np.linalg.slogdet(
            posterior_precision
        )
        if sign <= 0.0:
            raise RuntimeError("joint nuisance posterior precision is not positive")
        posterior_covariance = np.linalg.inv(posterior_precision)
        posterior_natural = prior_natural - absolute_score[index]
        posterior_mean = posterior_covariance @ posterior_natural
        posterior_means[index] = posterior_mean
        posterior_covariances[index] = 0.5 * (
            posterior_covariance + posterior_covariance.T
        )
        log_evidence[index] = -0.5 * (
            absolute_quadratic[index]
            + float(means[index] @ prior_precision @ means[index])
            - float(posterior_natural @ posterior_mean)
            + posterior_logdet_precision
            + prior_logdet_covariance
            + logdet_d[index]
        )
    posterior_weights = _normalize_log_weights(
        np.log(predicted_weights) + log_evidence
    )
    return posterior_weights, posterior_means, posterior_covariances, {
        "schema": "biospur-c2-rao-blackwellized-dynamic-heading-step-v1",
        "shared_nuisance_conditioned_on_each_s1_state": True,
        "shared_nuisance_repeated_as_independent_action_prior": False,
        "external_prior_information_ratio_update_applied": True,
        "external_prior_information_ratio_weight_normalization_included": True,
        "cross_action_reference_rebased_in_absolute_parameter_basis": True,
        "heading_transition_variance_rad2": float(transition_variance_rad2),
        "heading_transition_zero_is_exact_no_mixture_case": (
            transition_variance_rad2 == 0.0
        ),
        "nonzero_transition_nuisance_mixture_treatment": (
            "CONDITIONAL_FIRST_AND_SECOND_MOMENT_MATCH"
        ),
        "candidate_log_determinant_included": True,
        "current_external_covariance_logdet": current_covariance_logdet,
        "posterior_weights_sha256": _array_sha256(posterior_weights),
        "conditional_nuisance_mean_sha256": _array_sha256(posterior_means),
        "conditional_nuisance_covariance_sha256": _array_sha256(
            posterior_covariances
        ),
        "hard_heading_lock_created": False,
    }


class PersistentNonhingeHeadingLikelihoodOwner:
    """Chronological dynamic-heading owner with one shared nuisance state.

    ``delta`` is the time-varying parent-child heading correction used by the
    rooted heading tree. It is not a sensor mounting parameter. Each edge owns
    one circular HMM across actions and one capture-wide center/calibration
    nuisance posterior in the immutable absolute parameter basis.
    """

    def __init__(
        self,
        *,
        branch_ids: Sequence[str],
        nonhinge_edges: Sequence[str],
        delta_grid_rad: np.ndarray,
        gap_diffusion_rad2_s: float,
        unknown_interval_variance_floor_rad2: float,
    ) -> None:
        self.branch_ids = tuple(str(value) for value in branch_ids)
        self.nonhinge_edges = tuple(str(value) for value in nonhinge_edges)
        self.grid = np.asarray(delta_grid_rad, dtype=float).copy()
        if (
            not self.branch_ids
            or not self.nonhinge_edges
            or any(edge in HINGE_EDGES for edge in self.nonhinge_edges)
            or gap_diffusion_rad2_s < 0.0
            or unknown_interval_variance_floor_rad2 <= 0.0
        ):
            raise ValueError("persistent nonhinge heading owner settings are invalid")
        uniform = np.full(len(self.grid), 1.0 / len(self.grid), dtype=float)
        self._weights = {
            (branch, edge): uniform.copy()
            for branch in self.branch_ids for edge in self.nonhinge_edges
        }
        self._last_action_index = {key: -1 for key in self._weights}
        self._history: dict[
            str, list[NonhingeSharedNuisanceSufficientStatistics | None]
        ] = {edge: [] for edge in self.nonhinge_edges}
        self._history_hashes: dict[str, list[str | None]] = {
            edge: [] for edge in self.nonhinge_edges
        }
        self._transition_variances: dict[str, list[float]] = {
            edge: [] for edge in self.nonhinge_edges
        }
        self._action_names: dict[str, list[str]] = {
            edge: [] for edge in self.nonhinge_edges
        }
        self._last_time_s: dict[str, float] = {}
        self._last_boot_epoch: dict[str, int] = {}
        self._unknown_interval_floor_pending_anchor: set[str] = set()
        self._prefix_cache: dict[
            tuple[str, int, str], tuple[np.ndarray, Mapping[str, Any]]
        ] = {}
        self._edge_filter_state: dict[
            str, tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]
        ] = {}
        self._no_update_cache: dict[tuple[str, int], np.ndarray] = {}
        self._records: list[dict[str, Any]] = []
        self.gap_diffusion_rad2_s = float(gap_diffusion_rad2_s)
        self.unknown_interval_variance_floor_rad2 = float(
            unknown_interval_variance_floor_rad2
        )

    def _transition_for_pair(
        self, edge: str, pair: AlignedPair,
    ) -> tuple[float, str]:
        if edge in self._unknown_interval_floor_pending_anchor:
            self._unknown_interval_floor_pending_anchor.remove(edge)
            return 0.0, "PREVIOUS_UNKNOWN_INTERVAL_FLOOR_ALREADY_APPLIED"
        if edge not in self._last_time_s:
            return 0.0, "FIRST_ACTION_UNIFORM_PRIOR"
        same_boot = int(pair.parent_boot_epoch[0]) == self._last_boot_epoch[edge]
        exact_gap = float(pair.parent_observed_time_s[0]) - self._last_time_s[edge]
        if same_boot and exact_gap >= 0.0:
            return (
                exact_gap * self.gap_diffusion_rad2_s,
                "EXACT_SAME_BOOT_GAP",
            )
        return (
            self.unknown_interval_variance_floor_rad2,
            "UNKNOWN_OR_RESET_INTERVAL_REGISTERED_FLOOR",
        )

    def process(
        self,
        *,
        branch_id: str,
        chronological_index: int,
        likelihood_log_weights: np.ndarray,
        pair: AlignedPair,
        likelihood_report: Mapping[str, Any],
        shared_nuisance_statistics: NonhingeSharedNuisanceSufficientStatistics,
    ) -> NonhingeHeadingLikelihoodResult:
        key = (branch_id, pair.edge)
        if key not in self._weights:
            raise ValueError("nonhinge likelihood targets an unowned branch/edge")
        if chronological_index != self._last_action_index[key] + 1:
            raise ValueError("nonhinge likelihood owner forbids action reset or stitching")
        action_log = np.asarray(likelihood_log_weights, dtype=float)
        if action_log.shape != self.grid.shape or not np.isfinite(action_log).all():
            raise ValueError("nonhinge action likelihood differs from owner S1 grid")
        statistics = shared_nuisance_statistics
        statistics_hash = _sufficient_statistics_sha256(statistics)
        edge_history = self._history[pair.edge]
        if chronological_index == len(edge_history):
            transition, transition_cause = self._transition_for_pair(pair.edge, pair)
            edge_history.append(statistics)
            self._history_hashes[pair.edge].append(statistics_hash)
            self._transition_variances[pair.edge].append(transition)
            self._action_names[pair.edge].append(pair.action)
            self._last_time_s[pair.edge] = float(pair.parent_observed_time_s[-1])
            self._last_boot_epoch[pair.edge] = int(pair.parent_boot_epoch[-1])
        elif chronological_index < len(edge_history):
            if (
                self._history_hashes[pair.edge][chronological_index]
                != statistics_hash
                or self._action_names[pair.edge][chronological_index] != pair.action
            ):
                raise ValueError(
                    "nonhinge cross-branch action statistics/basis substitution"
                )
            transition = self._transition_variances[pair.edge][chronological_index]
            transition_cause = "EXACT_CROSS_BRANCH_REUSE"
        else:
            raise ValueError("nonhinge edge history skipped a chronological action")
        covariance = np.asarray(statistics.shared_covariance, dtype=float)
        covariance_hash = _array_sha256(covariance)
        current_external_mean = np.asarray(
            statistics.shared_nuisance_reference_mean, dtype=float,
        )
        cache_key = (
            pair.edge,
            chronological_index,
            hashlib.sha256(
                (covariance_hash + _array_sha256(current_external_mean)).encode(
                    "ascii"
                )
            ).hexdigest(),
        )
        cached = self._prefix_cache.get(cache_key)
        if cached is None:
            state = self._edge_filter_state.get(pair.edge)
            if state is None:
                prior_weights = np.full(
                    len(self.grid), 1.0 / len(self.grid), dtype=float,
                )
                conditional_mean = np.tile(
                    current_external_mean, (len(self.grid), 1),
                )
                conditional_covariance = np.tile(
                    covariance, (len(self.grid), 1, 1),
                )
                previous_external_mean = current_external_mean.copy()
                previous_external_covariance = covariance.copy()
            else:
                (
                    prior_weights,
                    conditional_mean,
                    conditional_covariance,
                    previous_external_mean,
                    previous_external_covariance,
                ) = state
            (
                posterior,
                posterior_mean,
                posterior_covariance,
                joint_report,
            ) = _rao_blackwellized_joint_heading_step(
                prior_weights=prior_weights,
                conditional_mean=conditional_mean.copy(),
                conditional_covariance=conditional_covariance.copy(),
                previous_external_mean=previous_external_mean,
                previous_external_covariance=previous_external_covariance,
                current_external_mean=current_external_mean,
                current_external_covariance=covariance,
                statistics=statistics,
                transition_variance_rad2=float(transition),
            )
            self._edge_filter_state[pair.edge] = (
                posterior.copy(),
                posterior_mean.copy(),
                posterior_covariance.copy(),
                current_external_mean.copy(),
                covariance.copy(),
            )
            cached = (posterior.copy(), dict(joint_report))
            self._prefix_cache[cache_key] = cached
        posterior, joint_report = cached
        self._weights[key] = posterior.copy()
        self._last_action_index[key] = int(chronological_index)
        resultant = np.sum(posterior * np.exp(1j * self.grid))
        entropy = float(-np.sum(
            posterior * np.log(np.maximum(posterior, 1e-300))
        ))
        record = {
            "branch_id": branch_id,
            "edge": pair.edge,
            "chronological_index": int(chronological_index),
            "action": pair.action,
            "action_log_likelihood_sha256": _array_sha256(action_log),
            "action_sufficient_statistics_sha256": statistics_hash,
            "shared_nuisance_parameter_basis_sha256": (
                statistics.parameter_basis_sha256
            ),
            "shared_nuisance_absolute_reference_mean_sha256": _array_sha256(
                statistics.shared_nuisance_reference_mean
            ),
            "current_prequential_shared_covariance_sha256": covariance_hash,
            "evolving_covariance_semantics": (
                "CURRENT_PREQUENTIAL_ABSOLUTE_STATE_PRIOR_CONDITIONS_ALL_"
                "ACCUMULATED_ABSOLUTE_BASIS_HEADING_FACTORS"
            ),
            "past_local_error_covariance_overwritten": False,
            "posterior_sha256": _array_sha256(posterior),
            "circular_resultant_magnitude": float(abs(resultant)),
            "entropy_nats": entropy,
            "information_gain_from_uniform_nats": float(
                np.log(len(posterior)) - entropy
            ),
            "heading_state_semantics": (
                "TIME_VARYING_PARENT_CHILD_HEADING_CORRECTION_DELTA_T_RAD"
            ),
            "heading_state_is_fixed_mounting_extrinsic": False,
            "shared_nuisance_prefix_treatment": (
                "RAO_BLACKWELLIZED_CIRCULAR_HEADING_FILTER_WITH_ONE_"
                "CAPTURE_WIDE_CONDITIONAL_GAUSSIAN_NUISANCE_STATE"
            ),
            "joint_linear_gaussian_nuisance_marginal_performed": True,
            "joint_irls_nuisance_marginal_performed": False,
            "robust_pre_nuisance_one_step_irls_used_for_owner_update": False,
            "exact_cartesian_heading_path_marginal_claimed": False,
            "historical_max_of_low_sensitivity_action_ratio_used": False,
            "static_prefix_then_total_diffusion_used": False,
            "diffusion_variance_rad2": float(transition),
            "diffusion_cause": transition_cause,
            "joint_marginal_model": dict(joint_report),
            "likelihood_report": dict(likelihood_report),
            "multimodal_full_grid_retained": True,
            "hard_heading_lock_created": False,
            "owner_output_qualified_as_pose_posterior": False,
        }
        self._records.append(record)
        return NonhingeHeadingLikelihoodResult(
            edge=pair.edge,
            delta_grid_rad=self.grid.copy(),
            action_log_likelihood=action_log.copy(),
            posterior_weights=posterior.copy(),
            report=record,
        )

    def record_action_no_update(
        self,
        *,
        branch_id: str,
        edge: str,
        chronological_index: int,
        action: str,
        cause: str,
        timing_pair: AlignedPair | None = None,
    ) -> NonhingeHeadingLikelihoodResult:
        key = (branch_id, edge)
        if key not in self._weights:
            raise ValueError("nonhinge no-update targets an unowned branch/edge")
        if chronological_index != self._last_action_index[key] + 1:
            raise ValueError("nonhinge no-update forbids action reset or stitching")
        first_branch_for_edge_action = chronological_index == len(
            self._history[edge]
        )
        if first_branch_for_edge_action:
            if timing_pair is not None:
                if timing_pair.edge != edge or timing_pair.action != action:
                    raise ValueError(
                        "nonhinge no-update timing pair changed edge/action ownership"
                    )
                transition_variance, transition_cause = self._transition_for_pair(
                    edge, timing_pair,
                )
                self._last_time_s[edge] = float(
                    timing_pair.parent_observed_time_s[-1]
                )
                self._last_boot_epoch[edge] = int(timing_pair.parent_boot_epoch[-1])
                timing_provenance = {
                    "kind": "EXACT_ALIGNED_PAIR_BOUNDARY",
                    "parent_observed_time_s_sha256": _array_sha256(
                        timing_pair.parent_observed_time_s
                    ),
                    "parent_boot_epoch_sha256": _array_sha256(
                        timing_pair.parent_boot_epoch
                    ),
                }
            else:
                transition_variance = self.unknown_interval_variance_floor_rad2
                transition_cause = "UNKNOWN_INTERVAL_REGISTERED_ONE_SHOT_FLOOR"
                self._unknown_interval_floor_pending_anchor.add(edge)
                timing_provenance = {
                    "kind": "NO_EDGE_TIME_AVAILABLE_ONE_SHOT_FLOOR",
                }
            self._history[edge].append(None)
            self._history_hashes[edge].append(None)
            self._transition_variances[edge].append(transition_variance)
            self._action_names[edge].append(action)
        elif (
            chronological_index >= len(self._history[edge])
            or self._history[edge][chronological_index] is not None
            or self._action_names[edge][chronological_index] != action
        ):
            raise ValueError("nonhinge no-update cross-branch history mismatch")
        prior = self._weights[key]
        cache_key = (edge, chronological_index)
        if first_branch_for_edge_action:
            state = self._edge_filter_state.get(edge)
            if state is None:
                posterior = _circular_convolve(
                    prior, transition_variance,
                )
            else:
                (
                    state_weights,
                    state_mean,
                    state_covariance,
                    external_mean,
                    external_covariance,
                ) = state
                posterior, state_mean, state_covariance = (
                    _circular_transition_conditional_gaussian(
                        weights=state_weights,
                        conditional_mean=state_mean,
                        conditional_covariance=state_covariance,
                        variance_rad2=transition_variance,
                    )
                )
                self._edge_filter_state[edge] = (
                    posterior.copy(),
                    state_mean,
                    state_covariance,
                    external_mean,
                    external_covariance,
                )
            self._no_update_cache[cache_key] = posterior.copy()
        else:
            posterior = self._no_update_cache[cache_key].copy()
            transition_variance = self._transition_variances[edge][
                chronological_index
            ]
            transition_cause = "EXACT_CROSS_BRANCH_REUSE"
            timing_provenance = {"kind": "EXACT_CROSS_BRANCH_REUSE"}
        self._weights[key] = posterior.copy()
        self._last_action_index[key] = int(chronological_index)
        action_log = np.zeros_like(posterior)
        record = {
            "branch_id": branch_id,
            "edge": edge,
            "chronological_index": int(chronological_index),
            "action": action,
            "posterior_sha256": _array_sha256(posterior),
            "diffusion_variance_rad2": float(transition_variance),
            "diffusion_cause": transition_cause,
            "timing_provenance": timing_provenance,
            "rao_conditional_nuisance_state_propagated_through_transition": True,
            "missing_action_diffusion_lost_before_next_informative_factor": False,
            "same_elapsed_interval_can_be_diffused_again_by_next_factor": False,
            "likelihood_report": {
                "status": "LOCAL_NO_UPDATE",
                "cause": str(cause),
                "information_added": False,
            },
            "hard_heading_lock_created": False,
            "owner_output_qualified_as_pose_posterior": False,
        }
        self._records.append(record)
        return NonhingeHeadingLikelihoodResult(
            edge=edge,
            delta_grid_rad=self.grid.copy(),
            action_log_likelihood=action_log,
            posterior_weights=posterior.copy(),
            report=record,
        )

    def posterior(self, branch_id: str, edge: str) -> Mapping[str, Any]:
        weights = self._weights[(branch_id, edge)].copy()
        resultant = np.sum(weights * np.exp(1j * self.grid))
        return {
            "delta_grid_rad": self.grid.copy(),
            "weights": weights,
            "circular_mean_coordinate_rad": float(np.angle(resultant)),
            "circular_resultant_magnitude": float(abs(resultant)),
            "multimodal_full_grid_retained": True,
            "argmax_used_as_hard_heading_lock": False,
        }

    def audit(self) -> Mapping[str, Any]:
        return {
            "schema": (
                "biospur-c2-persistent-dynamic-nonhinge-heading-joint-"
                "shared-nuisance-owner-v2"
            ),
            "branch_ids": list(self.branch_ids),
            "nonhinge_edges": list(self.nonhinge_edges),
            "delta_grid_sha256": _array_sha256(self.grid),
            "records": list(self._records),
            "heading_state_semantics": (
                "TIME_VARYING_PARENT_CHILD_HEADING_CORRECTION_DELTA_T_RAD"
            ),
            "per_action_reset_or_profile_stitch": False,
            "factorized_edge_posteriors_combined_cartesianly": False,
            "shared_center_or_calibration_nuisance_repeated_per_action": False,
            "historical_max_of_low_sensitivity_action_ratio_used": False,
            "static_prefix_then_total_diffusion_used": False,
            "rao_blackwellized_linear_gaussian_nuisance_marginal_performed": True,
            "candidate_log_determinant_included": True,
            "owner_output_qualified_as_pose_posterior": False,
        }
