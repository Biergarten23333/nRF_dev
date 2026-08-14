#!/usr/bin/env python3
"""Q1_T4_ESKF: quaternion/bias 15-error-state inertial foundation.

Conventions
-----------
``q_NB`` is a scalar-first Hamilton quaternion.  Its active rotation matrix
maps physical sensor/board-frame vectors B into the gravity-aligned local
navigation frame N: ``v_N = R_NB @ v_B``.  Gyro increments are expressed in B
and therefore multiply on the right: ``q_NB <- q_NB * Exp(omega_B dt)``.
Attitude error is right-multiplicative.

Position/velocity are expressed in N.  A T4 observation in V4 is applied only
when a validated frame binding supplies R_V4_N and the coincident origins.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
from scipy.linalg import expm

G_MPS2 = 9.80665
ERROR_STATE_SIZE = 15
_GAUSS5_NODES, _GAUSS5_WEIGHTS = np.polynomial.legendre.leggauss(5)


def skew(vector: np.ndarray) -> np.ndarray:
    x, y, z = np.asarray(vector, dtype=float)
    return np.array([[0.0, -z, y], [z, 0.0, -x], [-y, x, 0.0]])


def quaternion_normalize(q: np.ndarray, reference: np.ndarray | None = None) -> np.ndarray:
    q = np.asarray(q, dtype=float)
    norm = float(np.linalg.norm(q))
    if not math.isfinite(norm) or norm < 1e-15:
        raise ValueError("invalid quaternion")
    q = q / norm
    if reference is not None:
        return -q if float(q @ np.asarray(reference, dtype=float)) < 0.0 else q
    return -q if q[0] < 0.0 else q


def quaternion_multiply(left: np.ndarray, right: np.ndarray) -> np.ndarray:
    w1, x1, y1, z1 = np.asarray(left, dtype=float)
    w2, x2, y2, z2 = np.asarray(right, dtype=float)
    return np.array([
        w1*w2 - x1*x2 - y1*y2 - z1*z2,
        w1*x2 + x1*w2 + y1*z2 - z1*y2,
        w1*y2 - x1*z2 + y1*w2 + z1*x2,
        w1*z2 + x1*y2 - y1*x2 + z1*w2,
    ])


def quaternion_exp(rotation_vector_rad: np.ndarray) -> np.ndarray:
    vector = np.asarray(rotation_vector_rad, dtype=float)
    angle = float(np.linalg.norm(vector))
    if angle < 1e-10:
        # Second order keeps the norm error small before explicit normalization.
        return quaternion_normalize(np.r_[1.0 - angle*angle/8.0, 0.5*vector])
    return np.r_[math.cos(0.5*angle), vector * (math.sin(0.5*angle)/angle)]


def quaternion_to_matrix(q: np.ndarray) -> np.ndarray:
    w, x, y, z = quaternion_normalize(q)
    return np.array([
        [1-2*(y*y+z*z), 2*(x*y-w*z), 2*(x*z+w*y)],
        [2*(x*y+w*z), 1-2*(x*x+z*z), 2*(y*z-w*x)],
        [2*(x*z-w*y), 2*(y*z+w*x), 1-2*(x*x+y*y)],
    ])


def quaternion_from_two_vectors(source: np.ndarray, target: np.ndarray) -> np.ndarray:
    source = np.asarray(source, dtype=float); target = np.asarray(target, dtype=float)
    source /= np.linalg.norm(source); target /= np.linalg.norm(target)
    dot = float(np.clip(source @ target, -1.0, 1.0))
    if dot < -1.0 + 1e-10:
        axis = np.cross(source, [1.0, 0.0, 0.0])
        if np.linalg.norm(axis) < 1e-8:
            axis = np.cross(source, [0.0, 1.0, 0.0])
        return quaternion_normalize(np.r_[0.0, axis / np.linalg.norm(axis)])
    return quaternion_normalize(np.r_[1.0 + dot, np.cross(source, target)])


@dataclass(frozen=True)
class FrameBinding:
    """Auditable N/V4 binding. Unbound objects fail closed for T4 updates."""
    gravity_N_mps2: np.ndarray = field(default_factory=lambda: np.array([0., 0., -G_MPS2]))
    R_V4_N: np.ndarray | None = None
    origin_V4_m: np.ndarray | None = None
    signed_sensor_axis_map: np.ndarray | None = None
    initial_q_NB: np.ndarray | None = None
    yaw_gauge: str = "ARBITRARY_ZERO"
    yaw_sigma_rad: float = math.pi
    lever_arm_B_m: np.ndarray | None = None
    provenance: str = "UNBOUND"
    gravity_valid: bool = True
    v4_navigation_rotation_valid: bool = False
    signed_axes_valid: bool = False
    lever_arm_valid: bool = False
    # None preserves API compatibility: a capture-bound V4/N transform opts
    # into full p/v inertial dynamics, while an unbound real capture is
    # attitude-only.  Callers may set this explicitly for auditability.
    spatial_dynamics_enabled: bool | None = None

    @property
    def spatial_active(self) -> bool:
        return self.v4_navigation_rotation_valid if self.spatial_dynamics_enabled is None else self.spatial_dynamics_enabled

    def validate(self) -> None:
        gravity = np.asarray(self.gravity_N_mps2, dtype=float)
        if gravity.shape != (3,) or not np.isfinite(gravity).all() or np.linalg.norm(gravity) < 9.0:
            raise ValueError("invalid gravity convention")
        if self.v4_navigation_rotation_valid:
            if self.R_V4_N is None or self.origin_V4_m is None:
                raise ValueError("claimed V4 binding is incomplete")
            rotation = np.asarray(self.R_V4_N, dtype=float)
            if rotation.shape != (3, 3) or not np.allclose(rotation.T @ rotation, np.eye(3), atol=1e-8):
                raise ValueError("R_V4_N is not orthonormal")
            if np.linalg.det(rotation) < 0.999999:
                raise ValueError("R_V4_N is not a proper rotation")
            if self.provenance in ("", "UNBOUND"):
                raise ValueError("bound frame lacks provenance")
        if self.spatial_active and not self.v4_navigation_rotation_valid:
            raise ValueError("spatial dynamics require a validated V4/N binding")

    def v4_position_to_navigation(self, position_V4_m: np.ndarray) -> np.ndarray:
        self.validate()
        if not self.v4_navigation_rotation_valid:
            raise ValueError("SPATIAL_ACCELERATION_COUPLING_BLOCKED_FRAME_BINDING")
        return np.asarray(self.R_V4_N).T @ (np.asarray(position_V4_m) - self.origin_V4_m)


@dataclass(frozen=True)
class Q1Parameters:
    accel_noise_sigma_mps2_sqrt_hz: float = 0.12
    gyro_noise_sigma_rad_s_sqrt_hz: float = math.radians(0.12)
    accel_bias_rw_sigma_mps3_sqrt_hz: float = 0.002
    gyro_bias_rw_sigma_rad_s2_sqrt_hz: float = math.radians(0.002)
    gravity_sigma_mps2: float = 0.04
    zupt_sigma_mps: float = 0.02
    t4_position_sigma_m: np.ndarray = field(default_factory=lambda: np.array([.05, .05, .08]))
    max_dt_s: float = 0.1
    covariance_period_samples: int = 10
    gravity_nis_limit: float = 16.26623619623813  # chi-square(3), 99.9%
    gravity_norm_residual_limit_g: float = 0.060


@dataclass(frozen=True)
class GravityUpdateDecision:
    accepted: bool
    reason: str
    nis: float
    norm_residual_g: float


@dataclass(frozen=True)
class PositionUpdatePrediction:
    """Immutable pre-update position innovation evidence."""
    measurement_N_m: np.ndarray
    innovation_m: np.ndarray
    innovation_covariance_m2: np.ndarray
    nis: float


@dataclass(frozen=True)
class PositionUpdateDecision:
    accepted: bool
    reason: str
    prediction: PositionUpdatePrediction


class Q1T4ESKF:
    """Nominal [p,v,q,b_a,b_g] with 15-dimensional error covariance."""

    def __init__(self, parameters: Q1Parameters = Q1Parameters(),
                 binding: FrameBinding = FrameBinding()):
        binding.validate()
        self.parameters = parameters; self.binding = binding
        self.p = np.zeros(3); self.v = np.zeros(3); self.q = np.array([1., 0., 0., 0.])
        self.b_a = np.zeros(3); self.b_g = np.zeros(3)
        self.P = np.diag(np.r_[np.full(3, .1**2), np.full(3, .1**2),
                               np.full(2, math.radians(5.)**2), binding.yaw_sigma_rad**2,
                               np.full(3, .1**2), np.full(3, math.radians(.2)**2)])
        self.last_timestamp_s = None
        self.propagations = self.gravity_updates = self.zupt_updates = self.t4_updates = 0
        self.gravity_update_attempts = self.gravity_update_rejections = 0
        self.gravity_motion_ineligible = 0
        self.blocked_t4_updates = self.reinitializations = 0
        self.max_quaternion_norm_error = self.max_quaternion_sign_jump = 0.0
        self.max_covariance_asymmetry = 0.0; self.min_covariance_eigenvalue = math.inf
        self.max_covariance_eigenvalue = 0.0
        self.min_relative_covariance_eigenvalue = math.inf
        self.max_covariance_condition = 0.0
        self.cholesky_failures = 0
        self._covariance_elapsed_s = 0.0; self._covariance_samples = 0
        self._check()

    def initialize_from_stationary(self, accel_B_mps2: np.ndarray, gyro_B_rad_s: np.ndarray,
                                   accel_bias_B_mps2: np.ndarray | None = None) -> None:
        up_N = -np.asarray(self.binding.gravity_N_mps2) / np.linalg.norm(self.binding.gravity_N_mps2)
        force_B = np.asarray(accel_B_mps2, dtype=float)
        self.b_a = np.zeros(3) if accel_bias_B_mps2 is None else np.asarray(accel_bias_B_mps2, dtype=float).copy()
        self.b_g = np.asarray(gyro_B_rad_s, dtype=float).copy()
        if self.binding.initial_q_NB is None:
            self.q = quaternion_from_two_vectors(force_B - self.b_a, up_N)
        else:
            self.q = quaternion_normalize(self.binding.initial_q_NB)
        self._check()

    def propagate(self, timestamp_s: float, accel_B_mps2: np.ndarray,
                  gyro_B_rad_s: np.ndarray) -> None:
        timestamp_s = float(timestamp_s)
        if self.last_timestamp_s is None:
            self.last_timestamp_s = timestamp_s
            return
        dt = timestamp_s - self.last_timestamp_s
        if not math.isfinite(dt) or dt <= 0.0 or dt > self.parameters.max_dt_s:
            raise ValueError(f"invalid IMU dt {dt}")
        self.last_timestamp_s = timestamp_s
        accel = np.asarray(accel_B_mps2, dtype=float) - self.b_a
        omega = np.asarray(gyro_B_rad_s, dtype=float) - self.b_g
        previous_q = self.q.copy()
        rotation = quaternion_to_matrix(self.q)
        if self.binding.spatial_active:
            acceleration_N = rotation @ accel + np.asarray(self.binding.gravity_N_mps2)
            self.p += self.v*dt + .5*acceleration_N*dt*dt
            self.v += acceleration_N*dt
        self.q = quaternion_normalize(quaternion_multiply(self.q, quaternion_exp(omega*dt)), previous_q)

        F = np.zeros((ERROR_STATE_SIZE, ERROR_STATE_SIZE))
        if self.binding.spatial_active:
            F[0:3, 3:6] = np.eye(3)
            F[3:6, 6:9] = -rotation @ skew(accel)
            F[3:6, 9:12] = -rotation
        F[6:9, 6:9] = -skew(omega)
        F[6:9, 12:15] = -np.eye(3)
        self._covariance_elapsed_s += dt; self._covariance_samples += 1
        if self._covariance_samples >= self.parameters.covariance_period_samples:
            dc = self._covariance_elapsed_s
            G = np.zeros((ERROR_STATE_SIZE, 12))
            if self.binding.spatial_active:
                G[3:6, 0:3] = -rotation
            G[6:9, 3:6] = -np.eye(3)
            G[9:12, 6:9] = np.eye(3)
            G[12:15, 9:12] = np.eye(3)
            spectral = np.diag(np.r_[
                np.full(3, self.parameters.accel_noise_sigma_mps2_sqrt_hz**2),
                np.full(3, self.parameters.gyro_noise_sigma_rad_s_sqrt_hz**2),
                np.full(3, self.parameters.accel_bias_rw_sigma_mps3_sqrt_hz**2),
                np.full(3, self.parameters.gyro_bias_rw_sigma_rad_s2_sqrt_hz**2),
            ])
            continuous = G @ spectral @ G.T
            if self.binding.spatial_active:
                Phi, Qd = discretize_van_loan(F, continuous, dc)
            else:
                Phi, Qd = discretize_attitude_only(F, continuous, dc)
            self.P = Phi @ self.P @ Phi.T + Qd
            self.P = .5*(self.P + self.P.T)
            self._covariance_elapsed_s = 0.0; self._covariance_samples = 0
        self.propagations += 1
        self.max_quaternion_norm_error = max(self.max_quaternion_norm_error,
                                              abs(float(np.linalg.norm(self.q))-1.0))
        self.max_quaternion_sign_jump = max(self.max_quaternion_sign_jump,
                                             float(np.linalg.norm(self.q-previous_q)))
        if self.propagations % 200 == 0:
            self._check()

    def _correct(self, innovation: np.ndarray, H: np.ndarray, R: np.ndarray) -> float:
        innovation = np.atleast_1d(np.asarray(innovation, dtype=float))
        S = H @ self.P @ H.T + R
        K = np.linalg.solve(S, H @ self.P).T
        delta = K @ innovation
        self.p += delta[0:3]; self.v += delta[3:6]
        old_q = self.q.copy()
        self.q = quaternion_normalize(quaternion_multiply(self.q, quaternion_exp(delta[6:9])), old_q)
        self.b_a += delta[9:12]; self.b_g += delta[12:15]
        identity = np.eye(ERROR_STATE_SIZE); KH = K @ H
        self.P = (identity-KH) @ self.P @ (identity-KH).T + K @ R @ K.T
        # Right-error injection reset Jacobian, first order.
        reset = np.eye(ERROR_STATE_SIZE)
        reset[6:9, 6:9] -= .5*skew(delta[6:9])
        self.P = reset @ self.P @ reset.T
        self.P = .5*(self.P + self.P.T)
        self._check()
        return float(innovation @ np.linalg.solve(S, innovation))

    def gravity_update(self, accel_B_mps2: np.ndarray) -> float:
        gravity_up_N = -np.asarray(self.binding.gravity_N_mps2)
        predicted = self.b_a + quaternion_to_matrix(self.q).T @ gravity_up_N
        innovation = np.asarray(accel_B_mps2, dtype=float) - predicted
        H = np.zeros((3, ERROR_STATE_SIZE))
        H[:, 6:9] = skew(predicted - self.b_a)
        H[:, 9:12] = np.eye(3)
        nis = self._correct(innovation, H, np.eye(3)*self.parameters.gravity_sigma_mps2**2)
        self.gravity_updates += 1
        return nis

    def gravity_update_causal(self, accel_B_mps2: np.ndarray, *,
                              motion_state: str = "STATIONARY") -> GravityUpdateDecision:
        """Apply a causal gravity pseudo-measurement integrity gate.

        Propagation is deliberately outside this method and is never undone.
        A caller's already-causal motion state distinguishes legitimate motion
        ineligibility from a stationary measurement-integrity rejection.  No
        future samples, replacement, interpolation, or filter reset are used.
        """
        accel = np.asarray(accel_B_mps2, dtype=float)
        gravity_up_N = -np.asarray(self.binding.gravity_N_mps2)
        predicted = self.b_a + quaternion_to_matrix(self.q).T @ gravity_up_N
        innovation = accel - predicted
        H = np.zeros((3, ERROR_STATE_SIZE))
        H[:, 6:9] = skew(predicted - self.b_a)
        H[:, 9:12] = np.eye(3)
        R = np.eye(3)*self.parameters.gravity_sigma_mps2**2
        S = H @ self.P @ H.T + R
        nis = float(innovation @ np.linalg.solve(S, innovation))
        residual_g = abs(float(np.linalg.norm(accel))/G_MPS2 - 1.0)
        self.gravity_update_attempts += 1
        if motion_state != "STATIONARY":
            self.gravity_motion_ineligible += 1
            return GravityUpdateDecision(False, "MOTION_GRAVITY_INELIGIBLE", nis, residual_g)
        if not math.isfinite(nis) or nis > self.parameters.gravity_nis_limit:
            self.gravity_update_rejections += 1
            return GravityUpdateDecision(False, "INNOVATION_NIS_REJECTED", nis, residual_g)
        if residual_g > self.parameters.gravity_norm_residual_limit_g:
            self.gravity_update_rejections += 1
            return GravityUpdateDecision(False, "GRAVITY_NORM_REJECTED", nis, residual_g)
        self._correct(innovation, H, R)
        self.gravity_updates += 1
        return GravityUpdateDecision(True, "ACCEPTED", nis, residual_g)

    def zupt_update(self) -> float:
        H = np.zeros((3, ERROR_STATE_SIZE)); H[:, 3:6] = np.eye(3)
        nis = self._correct(-self.v, H, np.eye(3)*self.parameters.zupt_sigma_mps**2)
        self.zupt_updates += 1
        return nis

    def t4_position_update(self, position_V4_m: np.ndarray) -> float | None:
        try:
            measurement_N = self.binding.v4_position_to_navigation(position_V4_m)
        except ValueError as error:
            if str(error) == "SPATIAL_ACCELERATION_COUPLING_BLOCKED_FRAME_BINDING":
                self.blocked_t4_updates += 1
                return None
            raise
        H = np.zeros((3, ERROR_STATE_SIZE)); H[:, 0:3] = np.eye(3)
        sigma = np.asarray(self.parameters.t4_position_sigma_m, dtype=float)
        nis = self._correct(measurement_N-self.p, H, np.diag(sigma*sigma))
        self.t4_updates += 1
        return nis

    def predict_t4_position_update(self, position_V4_m: np.ndarray) -> PositionUpdatePrediction | None:
        """Compute T4 innovation/S/NIS without mutating any filter byte.

        This is the mandatory first half of guarded measurement handling.  It
        deliberately does not increment counters, normalize state, or touch P.
        """
        try:
            measurement_N = self.binding.v4_position_to_navigation(position_V4_m)
        except ValueError as error:
            if str(error) == "SPATIAL_ACCELERATION_COUPLING_BLOCKED_FRAME_BINDING":
                return None
            raise
        H = np.zeros((3, ERROR_STATE_SIZE)); H[:, 0:3] = np.eye(3)
        sigma = np.asarray(self.parameters.t4_position_sigma_m, dtype=float)
        innovation = measurement_N-self.p
        S = H @ self.P @ H.T + np.diag(sigma*sigma)
        nis = float(innovation @ np.linalg.solve(S, innovation))
        return PositionUpdatePrediction(measurement_N.copy(), innovation.copy(), S.copy(), nis)

    def t4_position_update_gated(self, position_V4_m: np.ndarray, *, accept: bool,
                                 rejection_reason: str = "REJECTED_NIS") -> PositionUpdateDecision | None:
        """Mutate state only after a caller has evaluated every required gate."""
        prediction = self.predict_t4_position_update(position_V4_m)
        if prediction is None:
            self.blocked_t4_updates += 1
            return None
        if not accept:
            return PositionUpdateDecision(False, rejection_reason, prediction)
        H = np.zeros((3, ERROR_STATE_SIZE)); H[:, 0:3] = np.eye(3)
        sigma = np.asarray(self.parameters.t4_position_sigma_m, dtype=float)
        self._correct(prediction.innovation_m, H, np.diag(sigma*sigma))
        self.t4_updates += 1
        return PositionUpdateDecision(True, "ACCEPTED", prediction)

    def _check(self) -> None:
        values = (self.p, self.v, self.q, self.b_a, self.b_g, self.P)
        if not all(np.isfinite(value).all() for value in values):
            raise FloatingPointError("non-finite Q1 state")
        asymmetry = float(np.max(np.abs(self.P-self.P.T)))
        eigenvalues = np.linalg.eigvalsh(.5*(self.P+self.P.T))
        scale = max(float(eigenvalues[-1]), 1.0)
        relative_min = float(eigenvalues[0]) / scale
        # Backward-error bound, not a dataset-tuned absolute tolerance.
        psd_roundoff = 64.0 * ERROR_STATE_SIZE * np.finfo(float).eps * scale
        if eigenvalues[0] < -psd_roundoff:
            raise FloatingPointError("materially non-PSD Q1 covariance")
        try:
            np.linalg.cholesky(.5*(self.P+self.P.T))
        except np.linalg.LinAlgError:
            self.cholesky_failures += 1
            raise FloatingPointError("Q1 covariance not positive definite at float64 resolution")
        self.max_covariance_asymmetry = max(self.max_covariance_asymmetry, asymmetry)
        self.min_covariance_eigenvalue = min(self.min_covariance_eigenvalue, float(eigenvalues[0]))
        self.max_covariance_eigenvalue = max(self.max_covariance_eigenvalue, float(eigenvalues[-1]))
        self.min_relative_covariance_eigenvalue = min(self.min_relative_covariance_eigenvalue, relative_min)
        if eigenvalues[0] > 0.0:
            self.max_covariance_condition = max(self.max_covariance_condition, float(eigenvalues[-1]/eigenvalues[0]))
        if abs(float(np.linalg.norm(self.q))-1.0) > 1e-10:
            raise FloatingPointError("Q1 quaternion lost normalization")


def discretize_van_loan(F: np.ndarray, continuous_process_covariance: np.ndarray,
                        dt_s: float) -> tuple[np.ndarray, np.ndarray]:
    """Exact zero-order-hold covariance discretization in float64.

    For ``Pdot = F P + P F.T + L``, the Van Loan exponential returns the
    exact transition and integrated process covariance for F/L held constant
    over the covariance interval.  No diagonal loading or PSD projection is
    performed.
    """
    F = np.asarray(F, dtype=float)
    L = np.asarray(continuous_process_covariance, dtype=float)
    if F.shape != (ERROR_STATE_SIZE, ERROR_STATE_SIZE) or L.shape != F.shape:
        raise ValueError("invalid covariance dynamics shape")
    dt_s = float(dt_s)
    if not math.isfinite(dt_s) or dt_s <= 0.0:
        raise ValueError("invalid covariance interval")
    block = np.zeros((2*ERROR_STATE_SIZE, 2*ERROR_STATE_SIZE), dtype=float)
    block[:ERROR_STATE_SIZE, :ERROR_STATE_SIZE] = F
    block[:ERROR_STATE_SIZE, ERROR_STATE_SIZE:] = L
    block[ERROR_STATE_SIZE:, ERROR_STATE_SIZE:] = -F.T
    exponential = expm(block*dt_s)
    phi = exponential[:ERROR_STATE_SIZE, :ERROR_STATE_SIZE]
    qd = exponential[:ERROR_STATE_SIZE, ERROR_STATE_SIZE:] @ phi.T
    qd = .5*(qd+qd.T)
    return phi, qd


def _rotation_and_integral(omega_skew: np.ndarray, duration_s: float) -> tuple[np.ndarray, np.ndarray]:
    """Return exp(-Omega*t) and integral_0^t exp(-Omega*s) ds."""
    omega = math.sqrt(max(0.0, -.5*float(np.trace(omega_skew@omega_skew))))
    t = float(duration_s); ident = np.eye(3); omega2 = omega_skew@omega_skew
    if omega < 1e-9:
        rotation = ident-omega_skew*t+.5*omega2*t*t
        integral = ident*t-.5*omega_skew*t*t+(1/6)*omega2*t**3
    else:
        angle = omega*t
        rotation = ident-(math.sin(angle)/omega)*omega_skew+((1-math.cos(angle))/omega**2)*omega2
        integral = ident*t-((1-math.cos(angle))/omega**2)*omega_skew+((angle-math.sin(angle))/omega**3)*omega2
    return rotation, integral


def discretize_attitude_only(F: np.ndarray, continuous_process_covariance: np.ndarray,
                             dt_s: float) -> tuple[np.ndarray, np.ndarray]:
    """Exact Phi and positive quadrature Qd for the unbound real-data mode.

    In this mode p/v and their unavailable frame couplings are dormant.
    Attitude and gyro bias retain their exact constant-rate transition;
    accelerometer and gyro bias random walks remain honestly represented.
    Five-point Gauss-Legendre integrates Qd as sums of positive semidefinite
    terms, without eigenvalue projection or diagonal loading.
    """
    F=np.asarray(F,float);L=np.asarray(continuous_process_covariance,float);dt_s=float(dt_s)
    phi=np.eye(ERROR_STATE_SIZE);omega_skew=-F[6:9,6:9]
    rotation,integral=_rotation_and_integral(omega_skew,dt_s)
    phi[6:9,6:9]=rotation;phi[6:9,12:15]=-integral
    qd=np.zeros_like(F)
    for node,weight in zip(_GAUSS5_NODES,_GAUSS5_WEIGHTS):
        s=.5*dt_s*(node+1);r,j=_rotation_and_integral(omega_skew,s)
        transition=np.eye(ERROR_STATE_SIZE);transition[6:9,6:9]=r;transition[6:9,12:15]=-j
        qd += (.5*dt_s*weight)*(transition@L@transition.T)
    qd=.5*(qd+qd.T)
    return phi,qd


@dataclass(frozen=True)
class MotionVetoParameters:
    gyro_on_dps: float = .5
    gyro_angle_on_deg: float = .5
    accel_deviation_on_g: float = .02
    confirm_dwell_s: float = .20
    quiet_dwell_s: float = .75
    settling_dwell_s: float = 1.50


class MotionVetoGate:
    """Minimal successor lesson: motion veto cannot be overruled by stable UWB."""
    def __init__(self, parameters: MotionVetoParameters = MotionVetoParameters()):
        self.p = parameters; self.state = "STATIONARY"; self.last_time_s = None
        self.confirm_elapsed = self.quiet_elapsed = self.settling_elapsed = 0.0
        self.transitions: list[dict] = []; self.locked_position = None

    def set_lock(self, position: np.ndarray) -> None:
        if self.state != "STATIONARY" or self.locked_position is not None:
            raise ValueError("stationary lock is immutable")
        self.locked_position = np.asarray(position, dtype=float).copy()

    def update(self, time_s: float, *, gyro_rms_dps: float, gyro_angle_deg: float,
               accel_deviation_g: float, candidate_stable: bool,
               velocity_mps: float = 0.0, fleet_common_mode: bool = False) -> str:
        dt = 0.0 if self.last_time_s is None else float(time_s)-self.last_time_s
        if dt < 0.0: raise ValueError("motion gate timestamp reversal")
        self.last_time_s = float(time_s)
        veto = (gyro_rms_dps > self.p.gyro_on_dps or
                gyro_angle_deg > self.p.gyro_angle_on_deg or
                accel_deviation_g > self.p.accel_deviation_on_g)
        # A simultaneously observed >=5-node table impulse is environmental,
        # not independent node motion. Standalone behavior remains available;
        # fleet context suppresses only the shared interval, never a private
        # node event before or after it.
        if fleet_common_mode:
            veto = False
        old = self.state
        if self.state == "STATIONARY":
            if veto: self.state = "MOTION_SUSPECTED"; self.confirm_elapsed = 0.0
        elif self.state == "MOTION_SUSPECTED":
            self.confirm_elapsed = self.confirm_elapsed+dt if veto else 0.0
            if self.confirm_elapsed >= self.p.confirm_dwell_s: self.state = "MOVING"
            elif not veto: self.state = "STATIONARY"
        elif self.state == "MOVING":
            self.quiet_elapsed = 0.0 if veto else (self.quiet_elapsed+dt if candidate_stable else 0.0)
            if self.quiet_elapsed >= self.p.quiet_dwell_s:
                self.state = "SETTLING"; self.settling_elapsed = 0.0
        elif self.state == "SETTLING":
            if veto:
                self.state = "MOVING"; self.settling_elapsed = 0.0; self.quiet_elapsed = 0.0
            else:
                good = candidate_stable and velocity_mps <= .05
                self.settling_elapsed = self.settling_elapsed+dt if good else 0.0
                if self.settling_elapsed >= self.p.settling_dwell_s: self.state = "STATIONARY"
        if self.state != old:
            self.transitions.append({"time_s": float(time_s), "from_state": old,
                                     "to_state": self.state, "hard_motion_veto": veto})
        return self.state
