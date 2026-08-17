from __future__ import annotations

from dataclasses import dataclass
import numpy as np

from . import so3
from .types import FrontendOutput, ImuSample


@dataclass(frozen=True)
class FrontendConfig:
    gravity_m_s2: float = 9.80665
    gyro_noise_rad_s_sqrt_hz: float = 0.008
    gyro_bias_walk_rad_s2_sqrt_hz: float = 0.0003
    accel_bias_walk_m_s3_sqrt_hz: float = 0.003
    accel_noise_m_s2: float = 0.60
    rest_gyro_rad_s: float = 0.07
    rest_accel_tolerance_m_s2: float = 0.45
    motion_accel_tolerance_m_s2: float = 1.25
    initial_orientation_sigma_rad: float = np.deg2rad(8.0)
    initial_gyro_bias_sigma_rad_s: float = np.deg2rad(1.0)
    initial_accel_bias_sigma_m_s2: float = 0.35
    gap_reset_s: float = 0.15
    max_dt_s: float = 0.03
    rest_bias_measurement_sigma_rad_s: float = 0.008
    enable_gyro_bias_estimation: bool = True
    enable_accel_bias_estimation: bool = True
    enable_gravity_update: bool = True


class ErrorStateImuFrontend:
    """Variable-dt 9-state orientation/gyro-bias/accelerometer-bias ESKF.

    ``q_WI`` maps IMU coordinates into the initialization/world frame.  The
    accelerometer is used exactly once through one robust likelihood.  At rest
    that likelihood includes gravity and accelerometer bias; during motion its
    covariance is inflated according to the norm mismatch.
    """

    def __init__(self, node_id: str, config: FrontendConfig | None = None):
        self.node_id = node_id
        self.config = config or FrontendConfig()
        self.q_WI = np.array([1., 0., 0., 0.])
        self.bg = np.zeros(3)
        self.ba = np.zeros(3)
        sig = self.config
        self.P = np.diag(np.r_[np.full(3, sig.initial_orientation_sigma_rad**2),
                               np.full(3, sig.initial_gyro_bias_sigma_rad_s**2),
                               np.full(3, sig.initial_accel_bias_sigma_m_s2**2)])
        self.last_time: float | None = None
        self.last_boot: int | None = None
        self.reset_epoch = 0
        self.initialized = False
        self.factor_counts = {"gyro_propagation": 0, "gyro_bias_update": 0,
                              "accelerometer_likelihood": 0, "accel_bias_update": 0}
        self.bias_update_norm = {"gyro": 0.0, "accel": 0.0}

    def _initialize(self, sample: ImuSample) -> None:
        a = sample.accel_m_s2
        if np.linalg.norm(a) < 1e-6:
            raise ValueError("cannot initialize from zero acceleration")
        # q_WI must map measured positive specific force onto world +Z.
        self.q_WI = so3.from_two_vectors(a/np.linalg.norm(a), np.array([0., 0., 1.]))
        self.last_time = sample.time_s
        self.last_boot = sample.boot_id
        self.initialized = True

    def _reset_for_boot(self, sample: ImuSample) -> None:
        self.__init__(self.node_id, self.config)
        self.reset_epoch += 1
        self._initialize(sample)

    def update(self, sample: ImuSample) -> FrontendOutput:
        if sample.node_id != self.node_id:
            raise ValueError("wrong node")
        if not self.initialized:
            self._initialize(sample)
        if sample.boot_id != self.last_boot:
            self._reset_for_boot(sample)
        assert self.last_time is not None
        dt = sample.time_s-self.last_time
        if dt < -1e-9:
            raise ValueError("non-causal sample order")
        if dt > self.config.gap_reset_s:
            self.P[:3, :3] += np.eye(3)*(self.config.gyro_noise_rad_s_sqrt_hz*dt*3)**2
        dt_prop = min(max(dt, 0.0), self.config.max_dt_s)
        if dt_prop > 0:
            omega = sample.gyro_rad_s-self.bg
            self.q_WI = so3.apply_right(self.q_WI, omega*dt_prop)
            F = np.eye(9)
            F[:3, :3] -= so3.skew(omega)*dt_prop
            F[:3, 3:6] = -np.eye(3)*dt_prop
            Q = np.diag(np.r_[
                np.full(3, self.config.gyro_noise_rad_s_sqrt_hz**2*dt_prop),
                np.full(3, self.config.gyro_bias_walk_rad_s2_sqrt_hz**2*dt_prop),
                np.full(3, self.config.accel_bias_walk_m_s3_sqrt_hz**2*dt_prop),
            ])
            self.P = F@self.P@F.T+Q
            self.factor_counts["gyro_propagation"] += 1

        g = self.config.gravity_m_s2
        acc_norm = float(np.linalg.norm(sample.accel_m_s2-self.ba))
        low_dynamic = (np.linalg.norm(sample.gyro_rad_s-self.bg) < self.config.rest_gyro_rad_s
                       and abs(acc_norm-g) < self.config.rest_accel_tolerance_m_s2)
        # Bias pseudo-measurements require an independent pre-recorded still marker.
        rest = low_dynamic and sample.rest_evidence
        accel_used = self.config.enable_gravity_update and abs(acc_norm-g) < self.config.motion_accel_tolerance_m_s2
        innovation_norm = float("nan")
        if accel_used:
            pred_specific = so3.matrix(self.q_WI).T@np.array([0., 0., g]) + self.ba
            y = sample.accel_m_s2-pred_specific
            H = np.zeros((3, 9))
            # Right perturbation: R(q exp(d))^T g ~= R^T g + [R^T g]x d.
            H[:, :3] = so3.skew(pred_specific-self.ba)
            if self.config.enable_accel_bias_estimation and rest:
                # A single known-static attitude observes only the bias mode
                # parallel to predicted specific force.  Treating all three
                # components as independently observable aliases tilt into ba.
                direction = (pred_specific-self.ba)/g
                H[:, 6:9] = np.outer(direction, direction)
            dynamic_scale = 1.0+(abs(acc_norm-g)/max(self.config.accel_noise_m_s2, 1e-6))**2
            if not rest:
                dynamic_scale *= 20.0
            Rm = np.eye(3)*self.config.accel_noise_m_s2**2*dynamic_scale
            S = H@self.P@H.T+Rm
            K = self.P@H.T@np.linalg.inv(S)
            if not self.config.enable_gyro_bias_estimation:
                K[3:6] = 0
            if not self.config.enable_accel_bias_estimation:
                K[6:9] = 0
            dx = K@y
            old_ba = self.ba.copy()
            self.q_WI = so3.apply_right(self.q_WI, dx[:3])
            self.bg += dx[3:6]
            self.ba += dx[6:9]
            I_KH = np.eye(9)-K@H
            self.P = I_KH@self.P@I_KH.T+K@Rm@K.T
            self.factor_counts["accelerometer_likelihood"] += 1
            if np.linalg.norm(self.ba-old_ba) > 1e-12:
                self.factor_counts["accel_bias_update"] += 1
                self.bias_update_norm["accel"] += float(np.linalg.norm(self.ba-old_ba))
            innovation_norm = float(np.linalg.norm(y))

        if rest and self.config.enable_gyro_bias_estimation:
            H = np.zeros((3, 9)); H[:, 3:6] = np.eye(3)
            y = sample.gyro_rad_s-self.bg
            Rm = np.eye(3)*self.config.rest_bias_measurement_sigma_rad_s**2
            S = H@self.P@H.T+Rm
            K = self.P@H.T@np.linalg.inv(S)
            # A newly asserted rest interval may follow operator preparation.
            # Do not retroactively jump the displayed orientation through the
            # theta-bg cross covariance; the observed bias affects subsequent
            # propagation causally.
            K[:3] = 0
            K[6:9] = 0
            dx = K@y
            old_bg = self.bg.copy()
            self.q_WI = so3.apply_right(self.q_WI, dx[:3])
            self.bg += dx[3:6]; self.ba += dx[6:9]
            I_KH = np.eye(9)-K@H
            self.P = I_KH@self.P@I_KH.T+K@Rm@K.T
            self.factor_counts["gyro_bias_update"] += 1
            self.bias_update_norm["gyro"] += float(np.linalg.norm(self.bg-old_bg))

        self.P = 0.5*(self.P+self.P.T)
        eig = np.linalg.eigvalsh(self.P)
        if eig[0] < -1e-10:
            raise FloatingPointError("frontend covariance lost PSD")
        self.last_time = sample.time_s; self.last_boot = sample.boot_id
        return FrontendOutput(self.node_id, sample.time_s, sample.uid, self.q_WI.copy(), self.bg.copy(),
                              self.ba.copy(), self.P.copy(), rest, accel_used,
                              self.reset_epoch, innovation_norm)

    def run(self, samples: list[ImuSample]) -> list[FrontendOutput]:
        return [self.update(x) for x in samples]
