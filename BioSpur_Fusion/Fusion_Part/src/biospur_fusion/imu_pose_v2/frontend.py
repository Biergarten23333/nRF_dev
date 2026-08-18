from __future__ import annotations

import base64
from dataclasses import asdict, dataclass
import json

import numpy as np

from . import so3
from .types import FrontendFrame, ImuObservation


@dataclass(frozen=True, slots=True)
class FrontendConfig:
    gravity_m_s2: float = 9.80665
    gyro_noise: float = 0.008
    gyro_bias_walk: float = 0.0003
    accel_bias_walk: float = 0.003
    accel_sigma: float = 0.60
    rest_gyro_rad_s: float = 0.07
    rest_accel_tolerance: float = 0.45
    gravity_update_tolerance: float = 1.25
    rest_bias_sigma: float = 0.008
    innovation_chi2_gate: float = 16.27
    gravity_attitude_correction_max_rad: float = np.deg2rad(0.5)
    rest_bias_attitude_correction_max_rad: float = np.deg2rad(0.05)
    initialization_target_s: float = 2.0
    initialization_hard_cap_s: float = 5.0
    degraded_gap_s: float = 0.25
    unavailable_gap_s: float = 2.0
    integration_substep_s: float = 0.02


class ContinuousNodeFrontend:
    """Continuous 9-state right-error ESKF; action labels never control lifecycle."""

    def __init__(self, node_id: str, config: FrontendConfig | None = None):
        self.node_id = node_id
        self.config = config or FrontendConfig()
        self.q_WI = np.array([1.0, 0.0, 0.0, 0.0])
        self.bg = np.zeros(3)
        self.ba = np.zeros(3)
        self.P = np.diag(np.r_[np.full(3, np.deg2rad(8)**2), np.full(3, np.deg2rad(1)**2), np.full(3, .35**2)])
        self.last_sample_time_ns: int | None = None
        self.boot_epoch: int | None = None
        self.initialization_start_ns: int | None = None
        self.reset_epoch = 0
        self.action_boundary_reset_count = 0
        self.factor_counts = {"gyro_propagation": 0, "gravity_likelihood": 0, "rest_gyro_bias": 0,
                              "gravity_robust_downweight": 0, "rest_bias_robust_downweight": 0}

    def notify_action_boundary(self, _: str) -> None:
        # Explicit no-op with a qualification counter that must remain zero.
        return None

    def _initialize(self, observation: ImuObservation, sample_time_ns: int) -> None:
        accel = np.asarray(observation.accel_m_s2, float)
        if np.linalg.norm(accel) < 1e-6:
            raise ValueError("cannot initialize from zero specific force")
        self.q_WI = so3.from_two_vectors(accel, np.array([0.0, 0.0, 1.0]))
        self.last_sample_time_ns = sample_time_ns
        self.initialization_start_ns = sample_time_ns
        self.boot_epoch = observation.boot_epoch

    def _new_boot(self, observation: ImuObservation, sample_time_ns: int) -> None:
        reset = self.reset_epoch + 1
        node = self.node_id; config = self.config
        self.__init__(node, config)
        self.reset_epoch = reset
        self._initialize(observation, sample_time_ns)

    def _propagate(self, gyro: np.ndarray, dt: float) -> None:
        remaining = max(0.0, dt)
        while remaining > 0:
            step = min(remaining, self.config.integration_substep_s)
            omega = gyro - self.bg
            self.q_WI = so3.apply_right(self.q_WI, omega*step)
            F = np.eye(9)
            F[:3, :3] -= so3.skew(omega)*step
            F[:3, 3:6] = -np.eye(3)*step
            Q = np.diag(np.r_[
                np.full(3, self.config.gyro_noise**2*step),
                np.full(3, self.config.gyro_bias_walk**2*step),
                np.full(3, self.config.accel_bias_walk**2*step),
            ])
            self.P = F@self.P@F.T+Q
            remaining -= step
            self.factor_counts["gyro_propagation"] += 1

    def _correct(self, innovation: np.ndarray, H: np.ndarray, noise: np.ndarray, *,
                 attitude_correction_max_rad: float = np.inf, counter: str | None = None,
                 innovation_chi2_gate: float = np.inf) -> None:
        # A rest transition can expose a large orientation/bias cross term.
        # Robustly inflate this observation's covariance until both its NIS
        # and direct attitude correction fit the pre-registered causal gate;
        # never reset or snap the orientation at an action boundary.
        effective_noise = np.asarray(noise, float).copy(); downweighted = False
        for _ in range(8):
            S = H@self.P@H.T+effective_noise
            gain = np.linalg.solve(S, H@self.P).T
            delta = gain@innovation
            nis = float(innovation@np.linalg.solve(S, innovation))
            attitude_step = float(np.linalg.norm(delta[:3]))
            scale = max(1.0, nis/max(innovation_chi2_gate, 1e-12),
                        attitude_step/max(attitude_correction_max_rad, 1e-12))
            if scale <= 1.0 + 1e-12:
                break
            effective_noise *= min(scale*scale, 1e6); downweighted = True
        if downweighted and counter is not None:
            self.factor_counts[counter] += 1
        self.q_WI = so3.apply_right(self.q_WI, delta[:3])
        self.bg += delta[3:6]
        self.ba += delta[6:9]
        identity = np.eye(9)
        joseph = ((identity-gain@H)@self.P@(identity-gain@H).T
                  + gain@effective_noise@gain.T)
        # Right-error injection changes tangent coordinates.  Omitting this
        # reset Jacobian is a mandatory mutation failure.
        reset = np.eye(9)
        reset[:3, :3] = np.eye(3)-0.5*so3.skew(delta[:3])
        self.P = reset@joseph@reset.T
        self.P = 0.5*(self.P+self.P.T)

    def update(self, observation: ImuObservation, *, sample_age_us: float = 2500.0,
               causal_rest_evidence: bool = True) -> FrontendFrame:
        if observation.node_id != self.node_id:
            raise ValueError("wrong node routed to frontend")
        lo, hi = observation.sample_age_support_us
        if not lo <= sample_age_us <= hi:
            raise ValueError("sample age outside frozen bounded support")
        sample_time_ns = observation.common_time_ns-int(round(sample_age_us*1000))
        if self.last_sample_time_ns is None:
            self._initialize(observation, sample_time_ns)
        elif observation.boot_epoch != self.boot_epoch:
            self._new_boot(observation, sample_time_ns)
        assert self.last_sample_time_ns is not None and self.initialization_start_ns is not None
        dt = (sample_time_ns-self.last_sample_time_ns)*1e-9
        if dt < -1e-12:
            raise ValueError("non-causal node time")
        self._propagate(np.asarray(observation.gyro_rad_s, float), dt)
        corrected_accel = np.asarray(observation.accel_m_s2, float)-self.ba
        g = self.config.gravity_m_s2
        gyro_norm = float(np.linalg.norm(np.asarray(observation.gyro_rad_s)-self.bg))
        accel_mismatch = abs(float(np.linalg.norm(corrected_accel))-g)
        rest = causal_rest_evidence and gyro_norm < self.config.rest_gyro_rad_s and accel_mismatch < self.config.rest_accel_tolerance
        if accel_mismatch < self.config.gravity_update_tolerance:
            predicted = so3.matrix(self.q_WI).T@np.array([0.0, 0.0, g])+self.ba
            innovation = np.asarray(observation.accel_m_s2)-predicted
            H = np.zeros((3, 9)); H[:, :3] = so3.skew(predicted-self.ba)
            if rest:
                direction = (predicted-self.ba)/g
                H[:, 6:9] = np.outer(direction, direction)
            dynamic = 1+(accel_mismatch/max(self.config.accel_sigma, 1e-9))**2
            if not rest: dynamic *= 20
            self._correct(innovation, H, np.eye(3)*self.config.accel_sigma**2*dynamic,
                          attitude_correction_max_rad=self.config.gravity_attitude_correction_max_rad,
                          counter="gravity_robust_downweight",innovation_chi2_gate=self.config.innovation_chi2_gate)
            self.factor_counts["gravity_likelihood"] += 1
        if rest:
            H = np.zeros((3, 9)); H[:, 3:6] = np.eye(3)
            self._correct(np.asarray(observation.gyro_rad_s)-self.bg, H,
                          np.eye(3)*self.config.rest_bias_sigma**2,
                          attitude_correction_max_rad=self.config.rest_bias_attitude_correction_max_rad,
                          counter="rest_bias_robust_downweight",innovation_chi2_gate=self.config.innovation_chi2_gate)
            self.factor_counts["rest_gyro_bias"] += 1
        # Bounded unknown sample age is a nuisance, not white Gaussian noise.
        age_half_width_s = (hi-lo)*0.5e-6
        # The common bounded envelope and the selected nuisance realization are
        # retained separately.  This makes the 0.5/1/2/5 ms sensitivity sweep
        # visible without pretending that age is independent white noise.
        selected_age_s = abs(sample_age_us)*1e-6
        direction_uncertainty = gyro_norm*np.hypot(age_half_width_s, selected_age_s)
        self.P[:3, :3] += np.eye(3)*direction_uncertainty**2
        elapsed = (sample_time_ns-self.initialization_start_ns)*1e-9
        if elapsed < self.config.initialization_target_s:
            status = "REINITIALIZING"
        elif dt > self.config.unavailable_gap_s:
            status = "UNAVAILABLE"
        elif dt > self.config.degraded_gap_s:
            status = "PREDICTED_DEGRADED"
        else:
            status = "FILTERED"
        self.last_sample_time_ns = sample_time_ns
        return FrontendFrame(
            self.node_id, observation.boot_epoch, observation.uid, sample_time_ns,
            self.q_WI.copy(), self.bg.copy(), self.ba.copy(), self.P.copy(),
            rest, status, observation.common_time_ns-sample_time_ns, self.reset_epoch,
        )

    @staticmethod
    def _array(value: np.ndarray) -> dict:
        value = np.ascontiguousarray(value, dtype="<f8")
        return {"shape": list(value.shape), "dtype": "<f8", "base64": base64.b64encode(value.tobytes()).decode()}

    @staticmethod
    def _restore(payload: dict) -> np.ndarray:
        return np.frombuffer(base64.b64decode(payload["base64"]), dtype=payload["dtype"]).reshape(payload["shape"]).copy()

    def serialize(self) -> bytes:
        payload = {
            "schema": "biospur-phase3r2-node-state-v1", "node_id": self.node_id,
            "config": asdict(self.config), "q_WI": self._array(self.q_WI),
            "bg": self._array(self.bg), "ba": self._array(self.ba), "P": self._array(self.P),
            "last_sample_time_ns": self.last_sample_time_ns, "boot_epoch": self.boot_epoch,
            "initialization_start_ns": self.initialization_start_ns, "reset_epoch": self.reset_epoch,
            "action_boundary_reset_count": self.action_boundary_reset_count,
            "factor_counts": self.factor_counts,
        }
        return (json.dumps(payload, sort_keys=True, separators=(",", ":"))+"\n").encode()

    @classmethod
    def deserialize(cls, payload: bytes) -> "ContinuousNodeFrontend":
        row = json.loads(payload)
        if row["schema"] != "biospur-phase3r2-node-state-v1": raise ValueError("state schema mismatch")
        result = cls(row["node_id"], FrontendConfig(**row["config"]))
        for name in ("q_WI", "bg", "ba", "P"): setattr(result, name, cls._restore(row[name]))
        for name in ("last_sample_time_ns", "boot_epoch", "initialization_start_ns", "reset_epoch", "action_boundary_reset_count", "factor_counts"):
            setattr(result, name, row[name])
        return result
