from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

MethodName = Literal["T1", "T2", "T3", "T4"]


@dataclass(frozen=True)
class Anchor:
    id: int
    label: str
    x_mm: float
    y_mm: float
    z_mm: float
    d_anchor_mm: float = 0.0
    sigma_mm: float = 50.0


@dataclass(frozen=True)
class Layout:
    path: str
    anchors: dict[int, Anchor]
    tag_delay_mm: float = 0.0
    metadata: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Observation:
    anchor_id: int
    range_mm: float
    quality_percent: float = 100.0
    status: str = "O"


@dataclass(frozen=True)
class Frame:
    tag: str
    sweep: int
    host_elapsed_s: float
    host_epoch_s: float
    observations: tuple[Observation, ...]


@dataclass(frozen=True)
class SolverConfig:
    method: MethodName = "T1"
    max_iters: int = 8
    huber_k: float = 2.0
    min_sigma_mm: float = 5.0
    convergence_mm: float = 0.02
    max_step_mm: float = 500.0
    q_ema_alpha: float = 0.3
    residual_ema_alpha: float = 0.3
    reject_abs_threshold_mm: float = 120.0
    reject_min_improvement_mm: float = 20.0
    reject_improvement_ratio: float = 0.75
    min_anchors: int = 4


@dataclass(frozen=True)
class SolveResult:
    status: str
    method: MethodName
    tag: str
    sweep: int
    host_elapsed_s: float
    host_epoch_s: float
    x_mm: float
    y_mm: float
    z_mm: float
    anchors_used: int
    anchors_input: int
    rejected_anchor_id: int | None
    residual_rms_mm: float
    residual_p95_abs_mm: float
    max_abs_residual_mm: float
    residuals_by_anchor: dict[int, float]
    used_by_anchor: dict[int, bool]


@dataclass(frozen=True)
class TrajectoryResult:
    layout_path: str
    method: MethodName
    frames_input: int
    frames_solved: int
    results: list[SolveResult]
    metadata: dict = field(default_factory=dict)

