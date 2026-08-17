from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping
import numpy as np

from . import so3
from .calibration import CalibrationBundle
from .fk import normalized_fk
from .joints import JOINTS
from .mapping import FrozenOperatorMapping
from .types import FactorActivation, FrontendOutput, PoseFrame, SEGMENTS


@dataclass(frozen=True)
class EstimatorConfig:
    measurement_floor_sigma_rad: float = np.deg2rad(0.7)
    measurement_huber_rad: float = np.deg2rad(15.0)
    temporal_relative_sigma_rad: float = np.deg2rad(5.0)
    hinge_orthogonal_sigma_rad: float = np.deg2rad(15.0)
    hinge_huber_rad: float = np.deg2rad(15.0)
    multi_rom_sigma: float = 0.20
    heading_sigma_rad: float = np.deg2rad(30.0)
    heading_huber_rad: float = np.deg2rad(20.0)
    iterations: int = 2
    # This is a whole-frame budget divided across the configured iterations;
    # it is not a fresh allowance for every Gauss-Newton iteration.
    max_frame_step_rad: float = np.deg2rad(12.0)
    line_search_min_scale: float = 1.0/64.0
    posterior_covariance_scale: float = 0.15
    segment_tilt_usable_sigma_rad: float = np.deg2rad(15.0)
    joint_relative_usable_sigma_rad: float = np.deg2rad(20.0)
    enable_sensor_measurement: bool = True
    enable_sensor_to_segment: bool = True
    enable_joint_closure: bool = True
    enable_hinge_axis: bool = True
    enable_rom: bool = True
    enable_relative_heading: bool = True
    enable_calibration_covariance: bool = True


@dataclass
class _LinearFactor:
    name: str
    r: np.ndarray
    H: np.ndarray
    W: np.ndarray


class CoupledPoseEstimator:
    """Causal joint orientation optimizer with a single 30-D articulated state.

    All ten segment orientations are solved in one normal system. Parent-child
    factors have simultaneous non-zero blocks in both segment columns, so the
    posterior covariance contains actual cross-segment terms.
    """

    def __init__(self, mapping: FrozenOperatorMapping, calibration: CalibrationBundle,
                 config: EstimatorConfig | None = None,
                 hinge_axes_child: Mapping[str, np.ndarray] | None = None,
                 heading_targets: Mapping[str, np.ndarray] | None = None,
                 heading_confidence: Mapping[str, float] | None = None):
        self.mapping = mapping
        self.calibration = calibration
        self.config = config or EstimatorConfig()
        self.index = {s: i for i, s in enumerate(SEGMENTS)}
        self.q: dict[str, np.ndarray] | None = None
        self.P = np.eye(30)*np.deg2rad(12.0)**2
        self.previous_relative: dict[str, np.ndarray] = {}
        self.previous_relative_delta: dict[str, np.ndarray] = {}
        self.hinge_axes = {k: np.asarray(v, float)/np.linalg.norm(v) for k,v in (hinge_axes_child or {}).items()}
        self.heading_targets = {k: so3.normalize(v) for k,v in (heading_targets or {}).items()}
        self.heading_confidence = dict(heading_confidence or {})
        self.activations: dict[str, FactorActivation] = {
            name: FactorActivation() for name in (
                "sensor_to_segment_measurement", "parent_child_articulation",
                "elbow_knee_dominant_axis", "multi_dof_soft_rom",
                "relative_heading_correction", "calibration_covariance",
            )
        }
        self.frames = 0
        self.last_time: float | None = None

    def _block(self, segment: str) -> slice:
        i = 3*self.index[segment]
        return slice(i, i+3)

    def _relative(self, q: Mapping[str, np.ndarray], parent: str, child: str) -> np.ndarray:
        return so3.between(q[parent], q[child])

    def _joint_H(self, parent: str, child: str, q: Mapping[str, np.ndarray] | None = None) -> np.ndarray:
        q = self.q if q is None else q
        if q is None:
            raise ValueError("joint Jacobian requires current orientations")
        rel = self._relative(q, parent, child)
        H = np.zeros((3, 30))
        # q_pc' = exp(-dp) q_pc exp(dc): right-local residual Jacobian.
        H[:, self._block(parent)] = -so3.matrix(rel).T
        H[:, self._block(child)] = np.eye(3)
        return H

    def _factors(self, q: Mapping[str, np.ndarray], measurements: Mapping[str, np.ndarray],
                 measurement_cov: Mapping[str, np.ndarray]) -> list[_LinearFactor]:
        factors: list[_LinearFactor] = []
        if self.config.enable_sensor_measurement:
            for segment in SEGMENTS:
                r = so3.log(so3.between(measurements[segment], q[segment]))
                H = np.zeros((3, 30)); H[:, self._block(segment)] = np.eye(3)
                cov = measurement_cov[segment]+np.eye(3)*self.config.measurement_floor_sigma_rad**2
                huber=min(1.0,self.config.measurement_huber_rad/max(float(np.linalg.norm(r)),1e-12))
                factors.append(_LinearFactor("sensor_to_segment_measurement", r, H, huber*np.linalg.inv(cov)))

        for spec in JOINTS:
            rel = self._relative(q, spec.parent, spec.child)
            Hrel = self._joint_H(spec.parent, spec.child, q)
            previous = self.previous_relative.get(spec.name, rel)
            previous_delta = self.previous_relative_delta.get(spec.name, np.zeros(3))
            delta_rel = so3.log(so3.between(previous, rel))
            Hdelta = so3.right_jacobian_inverse(delta_rel)@Hrel
            if self.config.enable_joint_closure:
                r = delta_rel-previous_delta
                W = np.eye(3)/self.config.temporal_relative_sigma_rad**2
                factors.append(_LinearFactor("parent_child_articulation", r, Hdelta, W))
            if spec.kind == "hinge" and self.config.enable_hinge_axis:
                axis = self.hinge_axes.get(spec.name, np.array([1., 0., 0.]))
                Pperp = np.eye(3)-np.outer(axis, axis)
                r = Pperp@delta_rel
                H = Pperp@Hdelta
                confidence=float(np.clip(self.heading_confidence.get(spec.name,1.0),0,1))
                robust=min(1.0,self.config.hinge_huber_rad/max(float(np.linalg.norm(r)),1e-12))
                factors.append(_LinearFactor("elbow_knee_dominant_axis", r, H,
                                             np.eye(3)*(confidence*robust/self.config.hinge_orthogonal_sigma_rad**2)))
            if spec.kind == "multi" and self.config.enable_rom:
                rv = so3.log(rel)
                scaled = rv/np.maximum(spec.rom_rad, 1e-6)
                r = self.config.multi_rom_sigma*scaled**3
                J = np.diag(3*self.config.multi_rom_sigma*scaled**2/np.maximum(spec.rom_rad, 1e-6))
                factors.append(_LinearFactor("multi_dof_soft_rom", r, J@so3.right_jacobian_inverse(rv)@Hrel, np.eye(3)))
            if self.config.enable_relative_heading and spec.name in self.heading_targets:
                confidence = float(np.clip(self.heading_confidence.get(spec.name, 0.0), 0, 1))
                if confidence > 0:
                    target = self.heading_targets[spec.name]
                    raw = so3.log(so3.between(target, rel))
                    r = np.array([raw[2]])
                    H = (so3.right_jacobian_inverse(raw)@Hrel)[2:3]
                    robust=min(1.0,self.config.heading_huber_rad/max(abs(float(raw[2])),1e-12))
                    factors.append(_LinearFactor("relative_heading_correction", r, H,
                                                 np.array([[confidence*robust/self.config.heading_sigma_rad**2]])))
        return factors

    @staticmethod
    def _factor_cost(factors: list[_LinearFactor]) -> float:
        return float(sum(f.r@f.W@f.r for f in factors))

    def update(self, time_s: float, inputs_by_node: Mapping[str, FrontendOutput]) -> PoseFrame:
        if set(inputs_by_node) != set(self.mapping.node_to_segment):
            raise ValueError("joint update requires one fresh input for each mapped node")
        if self.last_time is not None and time_s < self.last_time:
            raise ValueError("non-causal update")
        measurements: dict[str, np.ndarray] = {}
        measurement_cov: dict[str, np.ndarray] = {}
        for node, out in inputs_by_node.items():
            segment = self.mapping.segment_for(node)
            measurements[segment] = (self.calibration.apply(node, out.q_WI)
                                     if self.config.enable_sensor_to_segment else out.q_WI.copy())
            cov = out.covariance[:3, :3].copy()
            if self.config.enable_calibration_covariance and self.config.enable_sensor_to_segment:
                cov += self.calibration.by_node[node].covariance_rad2
            measurement_cov[segment] = cov
        if self.q is None:
            self.q = {s: measurements[s].copy() for s in SEGMENTS}

        final_factors: list[_LinearFactor] = []
        A = np.eye(30)*1e-10
        for _ in range(self.config.iterations):
            factors = self._factors(self.q, measurements, measurement_cov)
            A = np.eye(30)*1e-10
            b = np.zeros(30)
            for factor in factors:
                A += factor.H.T@factor.W@factor.H
                b += factor.H.T@factor.W@factor.r
            delta = -np.linalg.solve(A, b)
            max_norm = max(np.linalg.norm(delta[self._block(s)]) for s in SEGMENTS)
            iteration_budget=self.config.max_frame_step_rad/max(self.config.iterations,1)
            if max_norm > iteration_budget:
                delta *= iteration_budget/max_norm
            old_cost=self._factor_cost(factors);scale=1.0;accepted=False
            while scale>=self.config.line_search_min_scale:
                candidate={segment:so3.apply_right(self.q[segment],scale*delta[self._block(segment)])
                           for segment in SEGMENTS}
                candidate_factors=self._factors(candidate,measurements,measurement_cov)
                if self._factor_cost(candidate_factors)<=old_cost+1e-12:
                    self.q=candidate;accepted=True;break
                scale*=0.5
            if not accepted:
                break
        final_factors=self._factors(self.q,measurements,measurement_cov)
        A=np.eye(30)*1e-10
        for factor in final_factors:
            A+=factor.H.T@factor.W@factor.H
        self.P = self.config.posterior_covariance_scale*np.linalg.inv(A)
        self.P = 0.5*(self.P+self.P.T)
        if np.linalg.eigvalsh(self.P)[0] < -1e-8:
            raise FloatingPointError("coupled covariance lost PSD")

        for factor in final_factors:
            contribution = -self.P@factor.H.T@factor.W@factor.r
            info = factor.H.T@factor.W@factor.H
            self.activations[factor.name].add(factor.r, factor.H, contribution, info)
        if self.config.enable_calibration_covariance and self.config.enable_sensor_to_segment:
            r = np.concatenate([so3.log(so3.between(measurements[s], self.q[s])) for s in SEGMENTS])
            H = np.eye(30)
            info = np.zeros((30, 30))
            for node, cal in self.calibration.by_node.items():
                sl = self._block(cal.segment)
                info[sl, sl] = np.linalg.inv(cal.covariance_rad2+np.eye(3)*1e-12)
            contribution = -self.P@info@r
            self.activations["calibration_covariance"].add(r, H, contribution, info)

        joint_q = {}
        joint_sigma = {}
        for spec in JOINTS:
            rel = self._relative(self.q, spec.parent, spec.child)
            joint_q[spec.name] = rel
            H = self._joint_H(spec.parent, spec.child)
            C = H@self.P@H.T
            joint_sigma[spec.name] = float(np.sqrt(max(np.linalg.eigvalsh(C).max(), 0)))
            previous = self.previous_relative.get(spec.name, rel)
            self.previous_relative_delta[spec.name] = so3.log(so3.between(previous, rel))
            self.previous_relative[spec.name] = rel.copy()

        tilt_sigma = {}
        segment_quality = {}
        for segment in SEGMENTS:
            C = self.P[self._block(segment), self._block(segment)]
            vertical_local = so3.matrix(self.q[segment]).T@np.array([0., 0., 1.])
            Ptilt = np.eye(3)-np.outer(vertical_local, vertical_local)
            value = float(np.sqrt(max(np.linalg.eigvalsh(Ptilt@C@Ptilt).max(), 0)))
            tilt_sigma[segment] = value
            segment_quality[segment] = "USABLE_BODY_RELATIVE_TILT" if value <= self.config.segment_tilt_usable_sigma_rad else "DEGRADED_TILT_UNCERTAINTY"
        joint_quality = {j: ("USABLE_RELATIVE_ROTATION" if s <= self.config.joint_relative_usable_sigma_rad else "DEGRADED_RELATIVE_HEADING") for j,s in joint_sigma.items()}
        whole = all(v.startswith("USABLE") for v in segment_quality.values()) and all(v.startswith("USABLE") for v in joint_quality.values())
        reasons = [] if whole else sorted(set(v for v in (*segment_quality.values(), *joint_quality.values()) if v.startswith("DEGRADED")))
        self.frames += 1; self.last_time = time_s
        cutoff = max(x.time_s for x in inputs_by_node.values())
        return PoseFrame(time_s, cutoff, {k:v.copy() for k,v in self.q.items()}, joint_q,
                         normalized_fk(self.q), tilt_sigma, joint_sigma, segment_quality,
                         joint_quality, whole, tuple(reasons))

    def information_matrix(self) -> np.ndarray:
        return np.linalg.inv(self.P)

    def cross_state_norm(self) -> float:
        blocks = self.P.copy()
        for s in SEGMENTS:
            sl = self._block(s); blocks[sl, sl] = 0
        return float(np.linalg.norm(blocks))

    def activation_report(self) -> dict:
        return {k: vars(v).copy() for k,v in self.activations.items()}
