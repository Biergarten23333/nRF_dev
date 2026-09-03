"""Budgeted staged solver, global synchronization, and information audit."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
import itertools
import math
import time
from typing import Any, Callable, Mapping, Sequence

import numpy as np
from scipy.optimize import least_squares, minimize_scalar

from .contracts import StageBudget
from .model import (
    C2CalibrationObjective,
    CalibrationState,
    HEADING_SEGMENTS,
    STATE_DIMENSION,
    STANDING_EDGE_LIMIT_DEG,
    wrap,
)


class StageBudgetExceeded(RuntimeError):
    pass


@dataclass
class _ResidualRecorder:
    function: Callable[[np.ndarray], np.ndarray]
    started: float
    wall_limit_s: float
    trace: list[dict[str, float]]
    evaluations: int = 0
    best_cost: float = math.inf

    def __call__(self, value: np.ndarray) -> np.ndarray:
        elapsed = time.monotonic() - self.started
        if elapsed > self.wall_limit_s:
            raise StageBudgetExceeded(f"stage exceeded {self.wall_limit_s:.1f}s")
        residual = self.function(value)
        if not np.isfinite(residual).all():
            raise FloatingPointError("non-finite residual")
        cost = float(0.5 * residual @ residual)
        self.evaluations += 1
        if cost < self.best_cost - max(1e-9, 1e-6 * abs(self.best_cost)) or self.evaluations == 1:
            self.best_cost = cost
            self.trace.append({
                "evaluation": float(self.evaluations),
                "elapsed_s": elapsed,
                "cost": cost,
            })
        return residual


def _least_squares(
    function: Callable[[np.ndarray], np.ndarray],
    initial: np.ndarray,
    low: np.ndarray,
    high: np.ndarray,
    budget: StageBudget,
    *,
    jac_sparsity: Any | None = None,
    jacobian: Callable[[np.ndarray], Any] | None = None,
) -> dict[str, Any]:
    started = time.monotonic()
    recorder = _ResidualRecorder(function, started, budget.wall_limit_s, [])
    try:
        def bounded_jacobian(value: np.ndarray) -> Any:
            if time.monotonic() - started > budget.wall_limit_s:
                raise StageBudgetExceeded(f"stage exceeded {budget.wall_limit_s:.1f}s in Jacobian")
            if jacobian is None:
                raise RuntimeError("bounded Jacobian called without an implementation")
            return jacobian(value)

        result = least_squares(
            recorder,
            np.clip(initial, low + 1e-9, high - 1e-9),
            bounds=(low, high),
            jac_sparsity=jac_sparsity,
            jac=bounded_jacobian if jacobian is not None else "2-point",
            max_nfev=budget.max_iterations,
            x_scale="jac",
            ftol=1e-7,
            xtol=1e-7,
            gtol=1e-7,
        )
        return {
            "finite": bool(np.isfinite(result.x).all() and np.isfinite(result.fun).all()),
            "success": bool(result.success),
            "status": int(result.status),
            "message": str(result.message),
            "state": result.x,
            "cost": float(result.cost),
            "optimality": float(result.optimality),
            "nfev": int(result.nfev),
            "njev": int(result.njev or 0),
            "elapsed_s": time.monotonic() - started,
            "trace": recorder.trace,
            "budget_exceeded": False,
        }
    except StageBudgetExceeded as exc:
        return {
            "finite": False,
            "success": False,
            "status": -2,
            "message": str(exc),
            "state": np.asarray(initial, dtype=float),
            "cost": recorder.best_cost,
            "optimality": math.inf,
            "nfev": recorder.evaluations,
            "njev": 0,
            "elapsed_s": time.monotonic() - started,
            "trace": recorder.trace,
            "budget_exceeded": True,
        }


def qmt_heading_seed(objective: C2CalibrationObjective) -> np.ndarray:
    headings = {segment: 0.0 for segment in ("pelvis", *objective.functional.body_from_sensor_by_segment)}
    for edge_name in ("elbow_left", "elbow_right", "knee_left", "knee_right"):
        estimate = objective.functional.hinge_axes.get(edge_name)
        if estimate is None:
            continue
        value = estimate.qmt_heading_report.get("heading_rad")
        if value is None:
            continue
        edge = objective.bundle.edges[edge_name]
        headings[edge.child] = headings[edge.parent] + float(value)
    return np.asarray([headings.get(segment, 0.0) for segment in HEADING_SEGMENTS])


def _optimize_offsets(
    objective: C2CalibrationObjective, initial: CalibrationState, budget: StageBudget,
) -> dict[str, Any]:
    low, high = objective.bounds()
    fixed_heading = initial.headings_rad.copy()

    def residual(offsets: np.ndarray) -> np.ndarray:
        return objective.residual(np.r_[fixed_heading, offsets])

    sparsity = objective.residual_jacobian_sparsity()[:, 9:]
    result = _least_squares(
        residual, initial.axial_offsets_m, low[9:], high[9:], budget,
        jac_sparsity=sparsity,
    )
    result["state"] = CalibrationState(fixed_heading, np.asarray(result["state"], dtype=float)).vector()
    result["stage"] = "B_FUNCTIONAL_MOUNTS_CONNECTIONS"
    return result


def _heading_multistart(
    objective: C2CalibrationObjective,
    initial: CalibrationState,
    budget: StageBudget,
    *,
    seed: int,
    config: Mapping[str, Any],
) -> dict[str, Any]:
    del seed  # deterministic full-circle sectors are preferable to random coverage.
    started = time.monotonic()
    sectors = max(4, int(budget.multistarts))
    width = 2.0 * math.pi / sectors
    edge_runs: dict[str, list[dict[str, Any]]] = {}
    relative_heading: dict[str, float] = {}
    edge_weight: dict[str, float] = {}
    near_optimal_deltas: dict[str, list[float]] = {}
    legal_options: dict[str, list[dict[str, Any]]] = {}
    edge_timing_trace: list[dict[str, Any]] = []
    evaluations = 0
    for edge_name in objective.bundle.edges:
        elapsed = time.monotonic() - started
        if elapsed > budget.wall_limit_s:
            raise RuntimeError(
                f"heading graph synchronization exceeded {budget.wall_limit_s:.1f}s "
                f"before edge {edge_name} after {evaluations} evaluations"
            )
        edge_started = time.monotonic()
        prepared_cost = objective.prepare_edge_heading_cost(
            edge_name, initial.axial_offsets_m,
        )
        preparation_elapsed_s = time.monotonic() - edge_started

        def cost(unwrapped: float) -> float:
            nonlocal evaluations
            elapsed = time.monotonic() - started
            if elapsed > budget.wall_limit_s:
                raise StageBudgetExceeded(
                    f"heading graph synchronization exceeded {budget.wall_limit_s:.1f}s "
                    f"inside edge {edge_name} after {evaluations} evaluations"
                )
            evaluations += 1
            return prepared_cost.soft_l1_cost(float(wrap(unwrapped)))

        runs = []
        for sector in range(sectors):
            if time.monotonic() - started > budget.wall_limit_s:
                raise RuntimeError(
                    f"heading graph synchronization exceeded {budget.wall_limit_s:.1f}s "
                    f"at edge {edge_name} sector {sector} after {evaluations} evaluations"
                )
            left = -math.pi + sector * width
            right = left + width
            result = minimize_scalar(
                cost, bounds=(left, right), method="bounded",
                options={"maxiter": int(budget.max_iterations), "xatol": 1e-6},
            )
            runs.append({
                "sector": sector,
                "sector_bounds_deg": np.degrees([left, right]).tolist(),
                "delta_rad": float(wrap(result.x)),
                "delta_deg": float(np.degrees(wrap(result.x))),
                "cost": float(result.fun),
                "success": bool(result.success and np.isfinite(result.fun)),
                "nfev": int(result.nfev),
            })
        finite = [run for run in runs if run["success"]]
        if not finite:
            raise RuntimeError(f"{edge_name}: full-circle edge search has no finite candidate")
        for run in finite:
            standing_angle = objective.edge_standing_relative_angle_deg(
                edge_name, float(run["delta_rad"]),
            )
            run["standing_relative_angle_deg"] = standing_angle
            run["standing_manifold_pass"] = (
                standing_angle <= STANDING_EDGE_LIMIT_DEG[edge_name]
            )
        legal = sorted(
            (run for run in finite if run["standing_manifold_pass"]),
            key=lambda run: run["cost"],
        )
        distinct: list[dict[str, Any]] = []
        for run in legal:
            if all(abs(float(np.degrees(wrap(
                run["delta_rad"] - retained["delta_rad"]
            )))) >= 2.0 for retained in distinct):
                distinct.append(run)
        if not distinct:
            raise RuntimeError(f"{edge_name}: all heading branches fail the standing manifold")
        legal_options[edge_name] = distinct
        best = distinct[0]
        relative_heading[edge_name] = float(best["delta_rad"])
        epsilon = max(1e-6, 0.01 * max(1.0, abs(float(best["cost"]))))
        near = [run for run in distinct if run["cost"] <= best["cost"] + epsilon]
        near_optimal_deltas[edge_name] = [float(run["delta_rad"]) for run in near]
        step = math.radians(0.25)
        curvature = max(
            1e-9,
            (cost(best["delta_rad"] + step) - 2.0 * best["cost"]
             + cost(best["delta_rad"] - step)) / (step * step),
        )
        edge_weight[edge_name] = curvature
        edge_runs[edge_name] = runs
        edge_timing_trace.append({
            **prepared_cost.audit(),
            "preparation_elapsed_s": preparation_elapsed_s,
            "search_elapsed_s": time.monotonic() - edge_started,
            "cumulative_elapsed_s": time.monotonic() - started,
            "evaluation_count": int(sum(run["nfev"] for run in runs) + 2),
        })

    # Weighted synchronization of all nine relative headings with the pelvis
    # yaw fixed to zero. This graph step remains valid if redundant edges are
    # added later; it is not action-by-action stitching.
    matrix = []
    target = []
    weights = []
    edge_names = []
    for edge_name, edge in objective.bundle.edges.items():
        parent, child = edge.parent, edge.child
        row = np.zeros(9)
        if parent != "pelvis":
            row[HEADING_SEGMENTS.index(parent)] = -1.0
        if child != "pelvis":
            row[HEADING_SEGMENTS.index(child)] = 1.0
        matrix.append(row)
        target.append(relative_heading[edge_name])
        weights.append(math.sqrt(edge_weight[edge_name]))
        edge_names.append(edge_name)
    weighted_matrix = np.asarray(matrix) * np.asarray(weights)[:, None]
    _, _, rank, singular = np.linalg.lstsq(
        weighted_matrix, np.asarray(target) * np.asarray(weights), rcond=1e-10,
    )
    def synchronize(relative: Mapping[str, float]) -> np.ndarray:
        values = np.asarray([relative[name] for name in edge_names])
        result, _, local_rank, _ = np.linalg.lstsq(
            weighted_matrix, values * np.asarray(weights), rcond=1e-10,
        )
        if int(local_rank) != 9:
            raise RuntimeError(f"heading synchronization rank is {local_rank}, expected 9")
        return wrap(result)

    headings = synchronize(relative_heading)
    if int(rank) != 9 or not np.isfinite(headings).all():
        raise RuntimeError(f"heading synchronization rank is {rank}, expected 9")
    # Coupled lower-limb topology is also a hard candidate gate. Explore the
    # retained full-circle lower-edge branches; do not soften topology into
    # the sensor objective.
    lower_edges = ("hip_left", "knee_left", "hip_right", "knee_right")
    combination_count = int(np.prod([len(legal_options[name]) for name in lower_edges]))
    maximum_combinations = int(
        config["stages"]["C_NINE_RELATIVE_HEADINGS"]["maximum_physical_combinations"]
    )
    if combination_count > maximum_combinations:
        raise RuntimeError(
            f"physical heading candidate bank has {combination_count} combinations; "
            f"bounded maximum is {maximum_combinations}"
        )
    physical_candidates = []
    for combination_index, combination in enumerate(itertools.product(*(
        legal_options[name] for name in lower_edges
    ))):
        if combination_index % 128 == 0 and time.monotonic() - started > budget.wall_limit_s:
            raise RuntimeError(
                f"heading graph synchronization exceeded {budget.wall_limit_s:.1f}s "
                f"during hard-topology combination {combination_index}/{combination_count}"
            )
        proposal = dict(relative_heading)
        for name, option in zip(lower_edges, combination):
            proposal[name] = float(option["delta_rad"])
        proposal_headings = synchronize(proposal)
        proposal_state = CalibrationState(
            proposal_headings, initial.axial_offsets_m.copy(),
        ).vector()
        feasibility = objective.feasibility_report(proposal_state, config=config)
        if feasibility["pass"]:
            cost_sum = sum(
                next(row["cost"] for row in legal_options[name]
                     if row["delta_rad"] == proposal[name])
                for name in lower_edges
            )
            physical_candidates.append((float(cost_sum), proposal, proposal_headings))
    if not physical_candidates:
        raise RuntimeError("no heading combination survives the hard standing topology gate")
    physical_candidates.sort(key=lambda row: row[0])
    _, relative_heading, headings = physical_candidates[0]
    target = [relative_heading[name] for name in edge_names]
    sync_residual = wrap(np.asarray(matrix) @ headings - np.asarray(target))

    spreads = []
    for values in near_optimal_deltas.values():
        if len(values) > 1:
            anchor = values[0]
            spreads.append(float(np.max(np.degrees(np.abs(wrap(np.asarray(values) - anchor))))))
    state = CalibrationState(headings, initial.axial_offsets_m.copy()).vector()
    return {
        "stage": "C_NINE_RELATIVE_HEADINGS",
        "state": state,
        "cost": float(sum(min(rows, key=lambda row: row["cost"])["cost"] for rows in edge_runs.values())),
        "finite_runs": int(sum(sum(run["success"] for run in rows) for rows in edge_runs.values())),
        "multistart_max_spread_deg": max(spreads, default=0.0),
        "edge_relative_heading_deg": {
            name: float(np.degrees(value)) for name, value in relative_heading.items()
        },
        "edge_curvature_weight": edge_weight,
        "edge_runs": edge_runs,
        "physical_candidate_bank": {
            "edge_legal_candidate_count": {
                name: len(rows) for name, rows in legal_options.items()
            },
            "lower_limb_combination_count": combination_count,
            "surviving_full_topology_count": len(physical_candidates),
            "gate_is_optimizer_loss": False,
        },
        "global_graph_synchronization": {
            "root_yaw_gauge": "pelvis=0",
            "edge_count": 9,
            "rank": int(rank),
            "singular_values": singular.tolist(),
            "maximum_sync_residual_deg": float(np.max(np.degrees(np.abs(sync_residual)))),
        },
        "evaluations": evaluations,
        "elapsed_s": time.monotonic() - started,
        "edge_cost_evaluation": {
            "parameterization": "PRECOMPUTED_FIXED_KINEMATIC_ROWS_PLUS_RELATIVE_Z_YAW",
            "algebraically_identical_to_direct_objective": True,
            "edge_timing_trace": edge_timing_trace,
        },
    }


def _refine_all(
    objective: C2CalibrationObjective,
    initial: CalibrationState,
    budget: StageBudget,
    *,
    seed: int,
    config: Mapping[str, Any],
) -> dict[str, Any]:
    rng = np.random.default_rng(seed)
    starts = [initial.vector()]
    for _ in range(max(0, budget.multistarts - 1)):
        candidate = initial.vector().copy()
        candidate[:9] = wrap(candidate[:9] + rng.normal(0.0, math.radians(20.0), 9))
        candidate[9:] += rng.normal(0.0, 0.01, len(candidate) - 9)
        starts.append(candidate)
    low, high = objective.bounds()
    stage_started = time.monotonic()
    deadline = stage_started + budget.wall_limit_s

    def run_start(index: int, start: np.ndarray) -> dict[str, Any]:
        cache_value: np.ndarray | None = None
        cache_residual: np.ndarray | None = None
        cache_jacobian: Any | None = None
        analytic_evaluations = 0
        cache_hits = 0

        def evaluate(value: np.ndarray) -> tuple[np.ndarray, Any]:
            nonlocal cache_value, cache_residual, cache_jacobian
            nonlocal analytic_evaluations, cache_hits
            observed = np.asarray(value, dtype=float)
            if cache_value is not None and np.array_equal(observed, cache_value):
                cache_hits += 1
                assert cache_residual is not None and cache_jacobian is not None
                return cache_residual, cache_jacobian
            cache_residual, cache_jacobian = objective.residual_and_analytic_jacobian(observed)
            cache_value = observed.copy()
            analytic_evaluations += 1
            return cache_residual, cache_jacobian

        worker_started = time.monotonic()
        remaining_s = deadline - worker_started
        if remaining_s <= 0.25:
            return {
                "finite": False, "success": False, "status": -2,
                "message": "shared all-action stage deadline reached before worker start",
                "state": start, "cost": math.inf, "optimality": math.inf,
                "nfev": 0, "njev": 0, "elapsed_s": 0.0, "trace": [],
                "budget_exceeded": True, "start_index": index,
                "analytic_sparse_jacobian": True,
                "analytic_evaluations": 0, "analytic_cache_hits": 0,
                "shared_stage_remaining_s_at_start": max(0.0, remaining_s),
            }
        run_budget = StageBudget(
            budget.name,
            budget.max_iterations,
            remaining_s,
            1,
            1,
        )
        try:
            result = _least_squares(
                lambda value: evaluate(value)[0], start, low, high, run_budget,
                jacobian=lambda value: evaluate(value)[1],
            )
        except Exception as exc:
            result = {
                "finite": False, "success": False, "status": -3,
                "message": f"{type(exc).__name__}: {exc}",
                "state": start, "cost": math.inf, "optimality": math.inf,
                "nfev": 0, "njev": 0,
                "elapsed_s": time.monotonic() - worker_started,
                "trace": [], "budget_exceeded": False,
            }
        result["start_index"] = index
        result["state"] = CalibrationState.from_vector(result["state"]).vector()
        result["analytic_sparse_jacobian"] = True
        result["analytic_evaluations"] = analytic_evaluations
        result["analytic_cache_hits"] = cache_hits
        result["shared_stage_remaining_s_at_start"] = max(0.0, remaining_s)
        return result

    workers = max(1, min(int(budget.parallel_workers), len(starts)))
    with ThreadPoolExecutor(
        max_workers=workers, thread_name_prefix="c2-stage-d",
    ) as executor:
        futures = [
            executor.submit(run_start, index, start)
            for index, start in enumerate(starts)
        ]
        runs = [future.result() for future in futures]
    finite = [run for run in runs if run["finite"]]
    if not finite:
        diagnostic = [{
            "start_index": run["start_index"],
            "elapsed_s": run["elapsed_s"],
            "nfev": run["nfev"],
            "message": run["message"],
            "budget_exceeded": run["budget_exceeded"],
            "best_cost": run["cost"],
        } for run in runs]
        raise RuntimeError(
            f"all-action refinement produced no finite candidate: {diagnostic}"
        )
    for run in finite:
        feasibility = objective.feasibility_report(run["state"], config)
        run["feasibility"] = {
            key: value for key, value in feasibility.items() if key != "kinematics"
        }
        run["physical_candidate_pass"] = bool(feasibility["pass"])
    legal = [run for run in finite if run["physical_candidate_pass"]]
    if not legal:
        diagnostic = [{
            "start_index": run["start_index"],
            "cost": run["cost"],
            "failed_physical_gates": [
                name for name, passed in run["feasibility"]["gates"].items()
                if not passed
            ],
        } for run in finite]
        raise RuntimeError(
            "all finite all-action refinements failed the nontradeable "
            f"physical candidate gate: {diagnostic}"
        )
    best = min(legal, key=lambda run: run["cost"])
    return {
        "stage": "D_ALL_ACTION_REFINEMENT",
        "state": best["state"],
        "selected_start": int(best["start_index"]),
        "cost": float(best["cost"]),
        "runs": runs,
        "finite_runs": len(finite),
        "physically_legal_runs": len(legal),
        "lower_cost_invalid_run_cannot_displace_legal_candidate": True,
        "physical_gate_is_optimizer_loss": False,
        "parallel_execution": {
            "worker_count": workers,
            "shared_stage_wall_limit_s": budget.wall_limit_s,
            "shared_deadline_not_per_start_budget_relaxation": True,
            "elapsed_s": time.monotonic() - stage_started,
        },
    }


def solve_staged(
    objective: C2CalibrationObjective,
    budgets: Mapping[str, StageBudget],
    config: Mapping[str, Any],
    *,
    previous_state: np.ndarray | None = None,
    seed: int = 20260829,
) -> dict[str, Any]:
    if previous_state is None:
        initial = objective.initial_state(qmt_heading_seed(objective))
    else:
        initial = CalibrationState.from_vector(previous_state)
    stage_b = _optimize_offsets(objective, initial, budgets["B_FUNCTIONAL_MOUNTS_CONNECTIONS"])
    stage_c = _heading_multistart(
        objective,
        CalibrationState.from_vector(stage_b["state"]),
        budgets["C_NINE_RELATIVE_HEADINGS"],
        seed=seed,
        config=config,
    )
    stage_d = _refine_all(
        objective,
        CalibrationState.from_vector(stage_c["state"]),
        budgets["D_ALL_ACTION_REFINEMENT"],
        seed=seed + 1,
        config=config,
    )
    state = np.asarray(stage_d["state"], dtype=float)
    feasibility = objective.feasibility_report(state, config)
    return {
        "state": state,
        "stages": [stage_b, stage_c, stage_d],
        "costs": objective.costs(state),
        "feasibility": feasibility,
        "finite": bool(np.isfinite(state).all()),
        "candidate_pass": bool(feasibility["pass"]),
    }


def numerical_jacobian(
    function: Callable[[np.ndarray], np.ndarray], value: np.ndarray, step: float = 1e-5,
    bounds: tuple[np.ndarray, np.ndarray] | None = None,
) -> np.ndarray:
    value = np.asarray(value, dtype=float)
    base = function(value)
    jacobian = np.empty((len(base), len(value)), dtype=float)
    for index in range(len(value)):
        delta = step * max(1.0, abs(float(value[index])))
        plus = value.copy(); minus = value.copy()
        if bounds is None:
            plus[index] += delta; minus[index] -= delta
        else:
            low, high = bounds
            plus[index] = min(high[index] - 1e-9, value[index] + delta)
            minus[index] = max(low[index] + 1e-9, value[index] - delta)
        width = plus[index] - minus[index]
        if width <= 0.0:
            raise ValueError("finite-difference coordinate has no interior width")
        jacobian[:, index] = (function(plus) - function(minus)) / width
    return jacobian


def information_report(objective: C2CalibrationObjective, value: np.ndarray) -> dict[str, Any]:
    jacobian = numerical_jacobian(
        lambda x: objective.measurement_residual(x, "train"), value,
        bounds=objective.bounds(),
    )
    heading = jacobian[:, :9]
    nuisance = jacobian[:, 9:]
    if nuisance.size:
        nuisance_basis, singular, _ = np.linalg.svd(nuisance, full_matrices=False)
        keep = singular > max(1e-10, (singular[0] if len(singular) else 0.0) * 1e-8)
        projected = heading - nuisance_basis[:, keep] @ (nuisance_basis[:, keep].T @ heading)
    else:
        projected = heading
    information = projected.T @ projected
    singular = np.linalg.svd(projected, compute_uv=False)
    threshold = max(1e-8, singular[0] * 1e-6) if len(singular) else 1e-8
    rank = int(np.count_nonzero(singular > threshold))
    covariance = np.linalg.pinv(information, rcond=1e-8)
    sigma_deg = np.degrees(np.sqrt(np.maximum(0.0, np.diag(covariance))))
    return {
        "gauge_reduced_heading_rank": rank,
        "heading_dimension": 9,
        "singular_values": singular.tolist(),
        "relative_threshold": 1e-6,
        "posterior_heading_sigma_deg": sigma_deg.tolist(),
        "maximum_posterior_heading_sigma_deg": float(np.max(sigma_deg)),
        "nuisance_profiled": ["eight_bounded_sensor_axial_offsets"],
        "raw_row_count_not_used_as_progress": True,
    }


def compare_states(progressive: np.ndarray, batch: np.ndarray) -> dict[str, float]:
    left = CalibrationState.from_vector(progressive)
    right = CalibrationState.from_vector(batch)
    return {
        "maximum_heading_difference_deg": float(np.max(np.degrees(np.abs(wrap(left.headings_rad - right.headings_rad))))),
        "maximum_axial_offset_difference_m": float(np.max(np.abs(left.axial_offsets_m - right.axial_offsets_m))),
    }
