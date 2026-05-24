from __future__ import annotations

import ctypes
import math
import subprocess
from pathlib import Path

from .models import Frame, Layout, MethodName, SolveResult, SolverConfig

ROOT = Path(__file__).resolve().parents[1]
C_CORE = ROOT / "c_core"
BUILD_DIR = C_CORE / "build"
LIB_PATH = BUILD_DIR / "libbiospur_tagpos.so"

METHOD_TO_INT: dict[MethodName, int] = {
    "T1": 1,
    "T2": 2,
    "T3": 3,
    "T4": 4,
}


class CConfig(ctypes.Structure):
    _fields_ = [
        ("method", ctypes.c_int),
        ("max_iters", ctypes.c_int),
        ("huber_k", ctypes.c_double),
        ("min_sigma_mm", ctypes.c_double),
        ("convergence_mm", ctypes.c_double),
        ("max_step_mm", ctypes.c_double),
        ("quality_penalty_scale", ctypes.c_double),
        ("quality_penalty_cap", ctypes.c_double),
        ("residual_ema_start_mm", ctypes.c_double),
        ("residual_penalty_scale", ctypes.c_double),
        ("residual_penalty_cap", ctypes.c_double),
        ("reject_abs_threshold_mm", ctypes.c_double),
        ("reject_min_improvement_mm", ctypes.c_double),
        ("reject_improvement_ratio", ctypes.c_double),
    ]


class CResult(ctypes.Structure):
    _fields_ = [
        ("status", ctypes.c_int),
        ("method", ctypes.c_int),
        ("n_input", ctypes.c_int),
        ("n_used", ctypes.c_int),
        ("iterations", ctypes.c_int),
        ("rejected_index", ctypes.c_int),
        ("xyz_mm", ctypes.c_double * 3),
        ("residual_rms_mm", ctypes.c_double),
        ("residual_p95_abs_mm", ctypes.c_double),
        ("max_abs_residual_mm", ctypes.c_double),
    ]


def build_c_core(force: bool = False) -> Path:
    src = C_CORE / "src" / "tagpos_solver.c"
    header = C_CORE / "include" / "biospur_tagpos" / "tagpos_solver.h"
    if (
        not force
        and LIB_PATH.exists()
        and LIB_PATH.stat().st_mtime >= max(src.stat().st_mtime, header.stat().st_mtime)
    ):
        return LIB_PATH
    BUILD_DIR.mkdir(parents=True, exist_ok=True)
    cmd = [
        "gcc",
        "-O3",
        "-std=c99",
        "-fPIC",
        "-shared",
        "-I",
        str(C_CORE / "include"),
        str(src),
        "-lm",
        "-o",
        str(LIB_PATH),
    ]
    subprocess.run(cmd, check=True)
    return LIB_PATH


_LIB: ctypes.CDLL | None = None


def load_c_lib() -> ctypes.CDLL:
    global _LIB
    if _LIB is None:
        lib_path = build_c_core()
        lib = ctypes.CDLL(str(lib_path))
        lib.biospur_tagpos_default_config.argtypes = [ctypes.POINTER(CConfig), ctypes.c_int]
        lib.biospur_tagpos_default_config.restype = None
        ptr_d = ctypes.POINTER(ctypes.c_double)
        ptr_i = ctypes.POINTER(ctypes.c_int)
        lib.biospur_tagpos_solve_frame.argtypes = [
            ptr_d,
            ptr_d,
            ptr_d,
            ptr_d,
            ptr_d,
            ptr_d,
            ptr_d,
            ctypes.c_int,
            ptr_d,
            ctypes.c_double,
            ctypes.POINTER(CConfig),
            ptr_d,
            ptr_d,
            ptr_i,
            ctypes.POINTER(CResult),
        ]
        lib.biospur_tagpos_solve_frame.restype = ctypes.c_int
        _LIB = lib
    return _LIB


def _c_array(values: list[float]):
    return (ctypes.c_double * len(values))(*values)


def _i_array(values: list[int]):
    return (ctypes.c_int * len(values))(*values)


def make_c_config(config: SolverConfig) -> CConfig:
    lib = load_c_lib()
    cfg = CConfig()
    method = METHOD_TO_INT[config.method]
    lib.biospur_tagpos_default_config(ctypes.byref(cfg), method)
    cfg.method = method
    cfg.max_iters = int(config.max_iters)
    cfg.huber_k = float(config.huber_k)
    cfg.min_sigma_mm = float(config.min_sigma_mm)
    cfg.convergence_mm = float(config.convergence_mm)
    cfg.max_step_mm = float(config.max_step_mm)
    cfg.reject_abs_threshold_mm = float(config.reject_abs_threshold_mm)
    cfg.reject_min_improvement_mm = float(config.reject_min_improvement_mm)
    cfg.reject_improvement_ratio = float(config.reject_improvement_ratio)
    return cfg


class TagPositionSolver:
    def __init__(self, layout: Layout, config: SolverConfig):
        self.layout = layout
        self.config = config
        self.lib = load_c_lib()
        self.c_config = make_c_config(config)
        self.last_by_tag: dict[str, tuple[float, float, float]] = {}
        self.q_ema_by_tag_anchor: dict[tuple[str, int], float] = {}
        self.residual_ema_by_tag_anchor: dict[tuple[str, int], float] = {}

    def _update_quality_ema(self, tag: str, aid: int, q: float) -> float:
        key = (tag, aid)
        prev = self.q_ema_by_tag_anchor.get(key, q)
        alpha = self.config.q_ema_alpha
        val = alpha * q + (1.0 - alpha) * prev
        self.q_ema_by_tag_anchor[key] = val
        return val

    def _get_residual_ema(self, tag: str, aid: int) -> float:
        return self.residual_ema_by_tag_anchor.get((tag, aid), 0.0)

    def _update_residual_ema(self, tag: str, residuals: dict[int, float]) -> None:
        alpha = self.config.residual_ema_alpha
        for aid, residual in residuals.items():
            key = (tag, aid)
            value = abs(residual)
            prev = self.residual_ema_by_tag_anchor.get(key, value)
            self.residual_ema_by_tag_anchor[key] = alpha * value + (1.0 - alpha) * prev

    def solve_frame(self, frame: Frame) -> SolveResult | None:
        obs = [o for o in frame.observations if o.anchor_id in self.layout.anchors and o.range_mm > 0.0]
        if len(obs) < self.config.min_anchors:
            return None
        n = len(obs)
        anchor_xyz: list[float] = []
        ranges: list[float] = []
        delays: list[float] = []
        sigmas: list[float] = []
        qualities: list[float] = []
        q_ema: list[float] = []
        r_ema: list[float] = []
        anchor_ids: list[int] = []
        for item in obs:
            anchor = self.layout.anchors[item.anchor_id]
            anchor_ids.append(item.anchor_id)
            anchor_xyz.extend([anchor.x_mm, anchor.y_mm, anchor.z_mm])
            ranges.append(item.range_mm)
            delays.append(anchor.d_anchor_mm)
            sigmas.append(anchor.sigma_mm)
            q = item.quality_percent if math.isfinite(item.quality_percent) and item.quality_percent > 0.0 else 100.0
            qualities.append(q)
            q_ema.append(self._update_quality_ema(frame.tag, item.anchor_id, q))
            r_ema.append(self._get_residual_ema(frame.tag, item.anchor_id))
        x0_tuple = self.last_by_tag.get(frame.tag)
        if x0_tuple is None:
            x0_tuple = (
                sum(anchor_xyz[0::3]) / n,
                sum(anchor_xyz[1::3]) / n,
                sum(anchor_xyz[2::3]) / n,
            )
        out_xyz = _c_array([0.0, 0.0, 0.0])
        out_residuals = _c_array([0.0] * n)
        out_used = _i_array([0] * n)
        out_result = CResult()
        rc = self.lib.biospur_tagpos_solve_frame(
            _c_array(anchor_xyz),
            _c_array(ranges),
            _c_array(delays),
            _c_array(sigmas),
            _c_array(qualities),
            _c_array(q_ema),
            _c_array(r_ema),
            ctypes.c_int(n),
            _c_array(list(x0_tuple)),
            ctypes.c_double(self.layout.tag_delay_mm),
            ctypes.byref(self.c_config),
            out_xyz,
            out_residuals,
            out_used,
            ctypes.byref(out_result),
        )
        if rc != 0:
            return None
        xyz = (float(out_xyz[0]), float(out_xyz[1]), float(out_xyz[2]))
        self.last_by_tag[frame.tag] = xyz
        residuals_by_anchor = {aid: float(out_residuals[i]) for i, aid in enumerate(anchor_ids)}
        used_by_anchor = {aid: bool(out_used[i]) for i, aid in enumerate(anchor_ids)}
        self._update_residual_ema(frame.tag, residuals_by_anchor)
        rejected_anchor_id = None
        if out_result.rejected_index >= 0 and out_result.rejected_index < len(anchor_ids):
            rejected_anchor_id = anchor_ids[out_result.rejected_index]
        return SolveResult(
            status="ok",
            method=self.config.method,
            tag=frame.tag,
            sweep=frame.sweep,
            host_elapsed_s=frame.host_elapsed_s,
            host_epoch_s=frame.host_epoch_s,
            x_mm=xyz[0],
            y_mm=xyz[1],
            z_mm=xyz[2],
            anchors_used=int(out_result.n_used),
            anchors_input=int(out_result.n_input),
            rejected_anchor_id=rejected_anchor_id,
            residual_rms_mm=float(out_result.residual_rms_mm),
            residual_p95_abs_mm=float(out_result.residual_p95_abs_mm),
            max_abs_residual_mm=float(out_result.max_abs_residual_mm),
            residuals_by_anchor=residuals_by_anchor,
            used_by_anchor=used_by_anchor,
        )

