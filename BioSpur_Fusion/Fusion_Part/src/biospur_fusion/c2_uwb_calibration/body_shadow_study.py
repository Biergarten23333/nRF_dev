"""Pre-registered statistical owner for the C2 O2 body-shadow study.

The causal geometry and held-range labels are owned by
``causal_body_shadow_validation``.  This module starts only after those rows
are frozen.  It owns the train-only nuisance gauge, the three strictly nested
interpretable location models, and paired validation statistics.  It never
reads captures, poses, H01/H02, or tracker state.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from types import MappingProxyType
from typing import Mapping, Sequence

import numpy as np
from scipy.optimize import minimize
from scipy.special import erf, ndtri

from biospur_fusion.c2_coupled_progressive.contracts import NODE_TO_SEGMENT


PAIR_REFERENCE = "BSFEC35/0"
POSITIVE_TAIL_SCALE_RATIO = 2.0
SHADOW_COEFFICIENT_MAX_M = 1.0
NUISANCE_COEFFICIENT_ABS_MAX_M = 5.0
MINIMUM_SCALE_M = 1e-4
MAXIMUM_SCALE_M = 2.0
BOOTSTRAP_SEED = 20260905
BOOTSTRAP_REPLICATES = 1000
BOOTSTRAP_BLOCK_EPOCHS = 10
DESIGN_CONDITION_MAXIMUM = 1e4
ELIGIBILITY_FRACTION_MINIMUM = 0.95
EXPOSURE_CLUSTER_MINIMUM = 30
VALIDATION_ACTION_COVERAGE_MINIMUM = 6
VALIDATION_ACTION_COUNT = 8
MINIMUM_EVALUABLE_FIXED_STRATA = 30
PAIR_MINIMUM_TRAIN_ACTIONS = 8
PAIR_MINIMUM_TRAIN_BLOCKS = 100
PAIR_MINIMUM_TRAIN_ROWS = 1000
PAIR_ORDER = tuple(
    f"{node}/{anchor}"
    for node in sorted(NODE_TO_SEGMENT)
    for anchor in range(8)
)
if PAIR_REFERENCE not in PAIR_ORDER:
    raise RuntimeError("O2 pair reference is absent from the C2 node inventory")

COMMON_CONTINUOUS_COLUMNS = (
    "causal_predicted_range_m",
    "causal_tag_origin_x_m",
    "causal_tag_origin_y_m",
    "causal_tag_origin_z_m",
    "base_sigma_m",
)
SHADOW_COLUMNS = (
    "own_inward_probability",
    "torso_exposure",
    "other_limb_exposure",
)

FULL_ROW_DTYPE = np.dtype([
    ("action_index", "u1"),
    ("source_epoch_index", "<i4"),
    ("node_index", "u1"),
    ("anchor", "u1"),
    ("pose_frame", "<i4"),
    ("pose_age_ms", "<f8"),
    ("link_time_ns", "<f8"),
    ("loo_prediction_m", "<f8"),
    ("signed_innovation_m", "<f8"),
    ("eligibility_rank", "u1"),
    ("eligibility_condition", "<f8"),
    ("solver_rank", "u1"),
    ("solver_condition", "<f8"),
    ("omitted_identity_count", "u1"),
    ("own_inward_probability", "<f8"),
    ("torso_exposure", "<f8"),
    ("other_limb_exposure", "<f8"),
    ("causal_predicted_range_m", "<f8"),
    ("causal_tag_origin_x_m", "<f8"),
    ("causal_tag_origin_y_m", "<f8"),
    ("causal_tag_origin_z_m", "<f8"),
    ("base_sigma_m", "<f8"),
])


MODEL_CONTRACT = MappingProxyType({
    "schema": "biospur.c2.o2_full.model_contract.v1",
    "response": "measured-held minus causal same-node LOO predicted range, metres",
    "likelihood": (
        "f(y|mu,s)=sqrt(2/pi)/(3s)*exp(-(y-mu)^2/(2s_side^2)); "
        "s_side=s for y<mu and 2s for y>=mu; mu is the mode/location, "
        "not the mean; E[Y]=mu+sqrt(2/pi)*s"
    ),
    "B0": "frozen common nuisance + beta_own*own_inward_probability",
    "B1": "B0 + beta_torso*torso_exposure",
    "B2": "B1 + beta_limb*other_limb_exposure",
    "shadow_bounds_m": [0.0, SHADOW_COEFFICIENT_MAX_M],
    "shadow_coefficient_count": 3,
    "pair_gauge": (
        f"global intercept plus 79 reference contrasts; {PAIR_REFERENCE}=0; "
        "reported pair effects also sum-centred without changing predictions"
    ),
    "common_continuous_columns": COMMON_CONTINUOUS_COLUMNS,
    "continuous_scaling": "train-only centre and RMS; validation uses frozen transform",
    "nuisance_bounds": (
        "raw/reference-coded contribution bounds are +/-5m; after train RMS "
        "column scaling optimizer bounds are +/-5m*design_column_rms"
    ),
    "nuisance_stability": "B0 nuisance coefficients and likelihood scale byte-frozen in B1/B2",
    "fit_order": "B0 joint nuisance+own; then torso-only increment; then limb-only increment",
    "nesting_fit_owner": (
        "B1 freezes every B0 coefficient and scale and fits only beta_torso; "
        "B2 freezes every B1 coefficient and fits only beta_limb"
    ),
    "quantile": (
        "left mass 1/3: mu+s*Phi^-1(q/(2/3)); right mass 2/3: "
        "mu+2s*Phi^-1((1+(q-1/3)/(2/3))/2)"
    ),
    "crps": (
        "exact E|X-y|-0.5E|X-X'| using the 1/3 left and 2/3 right "
        "half-normal mixture; no sampling or quadrature"
    ),
    "fit_seed": 20260905,
    "optimizer": (
        "B0 location minimizes 0.5*sum((y-Xb)^2/k_side^2), k_side=1 left "
        "and 2 right, using deterministic L-BFGS-B analytic gradient, zero "
        "initial coefficients, maxiter=1000, maxls=32, ftol=1e-12, gtol=1e-8; "
        "B0 profile scale MLE=sqrt(mean(((y-Xb)/k_side)^2)); non-success, "
        "nonfinite parameters, or scale outside [1e-4,2]m hard-fails; B1/B2 "
        "use the same objective while freezing the previous fit and scale"
    ),
    "forbidden": (
        "action/time/posture intercept; pair/node/anchor by shadow interaction; "
        "validation normalization/refit/selection; variance-only increment"
    ),
})

EVALUATION_CONTRACT = MappingProxyType({
    "schema": "biospur.c2.o2_full.evaluation_contract.v1",
    "split": "first 11 canonical actions train; last 8 untouched validation",
    "bootstrap": {
        "seed": BOOTSTRAP_SEED,
        "replicates": BOOTSTRAP_REPLICATES,
        "block_epochs": BOOTSTRAP_BLOCK_EPOCHS,
        "pairing": "same eligible validation rows for all models",
        "sampling": (
            "hierarchical paired bootstrap: sample 8 validation actions with "
            "replacement; for every selected action occurrence sample its frozen "
            "floor(source_epoch_index/10) blocks with replacement; use identical "
            "selected rows for every model delta and preserve row multiplicity"
        ),
        "percentile": "NumPy linear 5th/95th percentiles over 1000 draws",
    },
    "required_directions": {
        "nll_B1_minus_B0_upper95": "<0",
        "nll_B2_minus_B1_upper95": "<0",
        "q90_pinball_B1_minus_B0_upper95": "<0",
        "q90_pinball_B2_minus_B1_upper95": "<0",
        "crps_increment_upper95": "<0 (tie at zero fails)",
        "positive_tail_conditional_mean_absolute_error_increment_upper95": (
            "<0 (tail is signed_innovation_m >= train q90, NumPy linear; tie fails)"
        ),
        "residualized_exposure_top_minus_bottom_lower95": ">0",
        "validation_action_positive_count": f">={VALIDATION_ACTION_COVERAGE_MINIMUM}/8",
        "significant_fixed_stratum_negative_reversals": "0",
    },
    "fixed_strata": (
        "node x anchor x own-facing train-tertile x causal-range train-quartile"
    ),
})


@dataclass(frozen=True)
class FrozenDesign:
    names: tuple[str, ...]
    pair_order: tuple[str, ...]
    continuous_centres: np.ndarray
    continuous_rms: np.ndarray
    design_column_rms: np.ndarray
    common_matrix: np.ndarray
    singular_values: np.ndarray
    rank: int
    condition: float

    def __post_init__(self) -> None:
        for name in (
            "continuous_centres", "continuous_rms", "design_column_rms",
            "common_matrix", "singular_values"
        ):
            value = np.array(getattr(self, name), dtype=float, copy=True)
            value.setflags(write=False)
            object.__setattr__(self, name, value)


@dataclass(frozen=True)
class FittedNestedModels:
    nuisance_names: tuple[str, ...]
    nuisance_coefficients_m: np.ndarray
    beta_own_m: float
    beta_torso_m: float
    beta_limb_m: float
    scale_m: float
    positive_tail_scale_ratio: float
    train_objective: Mapping[str, float]

    def __post_init__(self) -> None:
        value = np.array(self.nuisance_coefficients_m, dtype=float, copy=True)
        value.setflags(write=False)
        object.__setattr__(self, "nuisance_coefficients_m", value)
        object.__setattr__(self, "train_objective", MappingProxyType(dict(self.train_objective)))


@dataclass(frozen=True)
class FrozenContrastOwner:
    """Train-only residualization and exposure-bin owner for one increment."""

    feature_name: str
    parent_model: str
    residualizer_names: tuple[str, ...]
    residualizer_coefficients: np.ndarray
    residualized_exposure: np.ndarray
    residualized_rms: float
    low_edge: float
    high_edge: float

    def __post_init__(self) -> None:
        for name in ("residualizer_coefficients", "residualized_exposure"):
            value = np.array(getattr(self, name), dtype=float, copy=True)
            value.setflags(write=False)
            object.__setattr__(self, name, value)


@dataclass(frozen=True)
class FrozenBootstrapPlan:
    """Response-free hierarchical action/time-block resampling weights."""

    action_values: tuple[int, ...]
    unit_actions: np.ndarray
    unit_blocks: np.ndarray
    weights: np.ndarray
    seed: int
    replicates: int

    def __post_init__(self) -> None:
        for name, dtype in (
            ("unit_actions", np.int64),
            ("unit_blocks", np.int64),
            ("weights", np.int16),
        ):
            value = np.array(getattr(self, name), dtype=dtype, copy=True)
            value.setflags(write=False)
            object.__setattr__(self, name, value)


def freeze_bootstrap_plan(
    action_index: np.ndarray,
    epoch_index: np.ndarray,
    *,
    seed: int = BOOTSTRAP_SEED,
    replicates: int = BOOTSTRAP_REPLICATES,
) -> FrozenBootstrapPlan:
    """Freeze one response-independent plan shared by every validation metric."""

    action = np.asarray(action_index, dtype=int)
    epoch = np.asarray(epoch_index, dtype=int)
    if action.ndim != 1 or epoch.shape != action.shape or not len(action):
        raise ValueError("bootstrap plan identities must be aligned nonempty vectors")
    action_values = tuple(sorted(set(action.tolist())))
    if len(action_values) != VALIDATION_ACTION_COUNT:
        raise ValueError("bootstrap plan must contain exactly eight validation actions")
    block = epoch // BOOTSTRAP_BLOCK_EPOCHS
    units = tuple(sorted({(int(a), int(b)) for a, b in zip(action, block, strict=True)}))
    unit_actions = np.asarray([row[0] for row in units], dtype=np.int64)
    unit_blocks = np.asarray([row[1] for row in units], dtype=np.int64)
    by_action = {
        value: np.flatnonzero(unit_actions == value) for value in action_values
    }
    if any(len(indices) == 0 for indices in by_action.values()):
        raise ValueError("bootstrap action has no frozen time block")
    rng = np.random.default_rng(int(seed))
    weights = np.zeros((int(replicates), len(units)), dtype=np.int16)
    for replicate in range(int(replicates)):
        for action_value in rng.choice(
            np.asarray(action_values), size=len(action_values), replace=True
        ):
            indices = by_action[int(action_value)]
            chosen = rng.choice(indices, size=len(indices), replace=True)
            np.add.at(weights[replicate], chosen, 1)
    return FrozenBootstrapPlan(
        action_values=action_values,
        unit_actions=unit_actions,
        unit_blocks=unit_blocks,
        weights=weights,
        seed=int(seed),
        replicates=int(replicates),
    )


def _bootstrap_plan_metric(
    delta: np.ndarray,
    action_index: np.ndarray,
    epoch_index: np.ndarray,
    plan: FrozenBootstrapPlan,
    inclusion_mask: np.ndarray,
) -> np.ndarray:
    """Apply a frozen plan to block sums/counts without changing resampling."""

    values = np.asarray(delta, dtype=float)
    action = np.asarray(action_index, dtype=int)
    epoch = np.asarray(epoch_index, dtype=int)
    included = np.asarray(inclusion_mask, dtype=bool)
    block = epoch // BOOTSTRAP_BLOCK_EPOCHS
    sums = np.empty(len(plan.unit_actions), dtype=float)
    counts = np.empty(len(plan.unit_actions), dtype=float)
    for index, (action_value, block_value) in enumerate(zip(
        plan.unit_actions, plan.unit_blocks, strict=True
    )):
        mask = included & (action == action_value) & (block == block_value)
        sums[index] = float(np.sum(values[mask]))
        counts[index] = float(np.count_nonzero(mask))
    observed_units = {
        (int(a), int(b)) for a, b in zip(action, block, strict=True)
    }
    planned_units = set(zip(
        plan.unit_actions.astype(int).tolist(),
        plan.unit_blocks.astype(int).tolist(),
        strict=True,
    ))
    if observed_units != planned_units:
        raise ValueError("bootstrap plan and metric action/block identities differ")
    denominator = plan.weights @ counts
    if np.any(denominator <= 0.0):
        raise ValueError("bootstrap draw contains no included metric rows")
    return (plan.weights @ sums) / denominator


def _require_rows(rows: np.ndarray) -> np.ndarray:
    values = np.asarray(rows)
    if values.dtype != FULL_ROW_DTYPE or values.ndim != 1 or not len(values):
        raise ValueError("O2 full rows must use the nonempty frozen structured dtype")
    for name in FULL_ROW_DTYPE.names or ():
        if values.dtype[name].kind == "f" and not np.all(np.isfinite(values[name])):
            raise ValueError(f"nonfinite full-study row column: {name}")
    return values


def freeze_common_design(rows: np.ndarray, train_mask: np.ndarray) -> FrozenDesign:
    values = _require_rows(rows)
    train = np.asarray(train_mask, dtype=bool)
    if train.shape != (len(values),) or not np.any(train) or np.all(train):
        raise ValueError("train/validation masks must be nonempty and row-aligned")
    pair_index = values["node_index"].astype(int) * 8 + values["anchor"].astype(int)
    if np.any(pair_index < 0) or np.any(pair_index >= len(PAIR_ORDER)):
        raise ValueError("row pair identity is outside the frozen inventory")
    reference_index = PAIR_ORDER.index(PAIR_REFERENCE)
    pair_columns = tuple(index for index in range(len(PAIR_ORDER)) if index != reference_index)
    pair = np.column_stack([(pair_index == index).astype(float) for index in pair_columns])
    continuous = np.column_stack([values[name] for name in COMMON_CONTINUOUS_COLUMNS])
    centres = np.mean(continuous[train], axis=0)
    centred_train = continuous[train] - centres
    rms = np.sqrt(np.mean(centred_train * centred_train, axis=0))
    if np.any(~np.isfinite(rms)) or np.any(rms <= np.finfo(float).eps):
        raise ValueError("common nuisance continuous column is unscaled or constant")
    scaled = (continuous - centres) / rms
    raw_matrix = np.column_stack((np.ones(len(values)), pair, scaled))
    names = (
        "global_intercept",
        *(f"pair[{PAIR_ORDER[index]}]" for index in pair_columns),
        *COMMON_CONTINUOUS_COLUMNS,
    )
    design_rms = np.sqrt(np.mean(raw_matrix[train] ** 2, axis=0))
    if np.any(~np.isfinite(design_rms)) or np.any(design_rms <= np.finfo(float).eps):
        raise ValueError("common nuisance design column is absent from training")
    matrix = raw_matrix / design_rms
    train_matrix = np.asarray(matrix[train], dtype=float)
    singular = np.linalg.svd(train_matrix, compute_uv=False)
    tolerance = max(train_matrix.shape) * np.finfo(float).eps * singular[0]
    rank = int(np.sum(singular > tolerance))
    condition = float(singular[0] / singular[-1]) if rank == train_matrix.shape[1] else math.inf
    if rank != train_matrix.shape[1]:
        raise ValueError("common nuisance train design is rank deficient")
    if condition > DESIGN_CONDITION_MAXIMUM:
        raise ValueError("common nuisance train design exceeds the frozen condition gate")
    return FrozenDesign(
        names, PAIR_ORDER, centres, rms, design_rms,
        matrix, singular, rank, condition,
    )


def residualized_shadow_audit(
    rows: np.ndarray, train_mask: np.ndarray, design: FrozenDesign
) -> Mapping[str, object]:
    values = _require_rows(rows)
    train = np.asarray(train_mask, dtype=bool)
    base = np.column_stack((
        design.common_matrix[train], values["own_inward_probability"][train]
    ))
    shadow = np.column_stack((
        values["torso_exposure"][train], values["other_limb_exposure"][train]
    ))
    coefficients, *_ = np.linalg.lstsq(base, shadow, rcond=None)
    residual = shadow - base @ coefficients
    rms = np.sqrt(np.mean(residual * residual, axis=0))
    if np.any(rms <= np.finfo(float).eps):
        rank, condition = 0, math.inf
    else:
        scaled = residual / rms
        singular = np.linalg.svd(scaled, compute_uv=False)
        tolerance = max(scaled.shape) * np.finfo(float).eps * singular[0]
        rank = int(np.sum(singular > tolerance))
        condition = float(singular[0] / singular[-1]) if rank == 2 else math.inf
    direct: dict[str, object] = {}
    matrices = {
        "B0": base,
        "B1": np.column_stack((base, shadow[:, 0])),
        "B2": np.column_stack((base, shadow)),
    }
    direct_pass = True
    previous_rank = None
    for model, matrix in matrices.items():
        column_rms = np.sqrt(np.mean(matrix * matrix, axis=0))
        if np.any(column_rms <= np.finfo(float).eps):
            model_rank, model_condition, singular = 0, math.inf, np.array([], dtype=float)
        else:
            scaled_matrix = matrix / column_rms
            singular = np.linalg.svd(scaled_matrix, compute_uv=False)
            tolerance = max(scaled_matrix.shape) * np.finfo(float).eps * singular[0]
            model_rank = int(np.sum(singular > tolerance))
            model_condition = (
                float(singular[0] / singular[-1])
                if model_rank == scaled_matrix.shape[1] else math.inf
            )
        expected_rank = matrix.shape[1]
        increment_ok = previous_rank is None or model_rank == previous_rank + 1
        model_pass = bool(
            model_rank == expected_rank
            and model_condition <= DESIGN_CONDITION_MAXIMUM
            and increment_ok
        )
        direct_pass = direct_pass and model_pass
        direct[model] = {
            "columns": expected_rank,
            "rank": model_rank,
            "condition": model_condition,
            "singular_values": tuple(float(value) for value in singular),
            "column_rms": tuple(float(value) for value in column_rms),
            "adds_exactly_one_rank": increment_ok,
            "pass": model_pass,
        }
        previous_rank = model_rank
    return MappingProxyType({
        "rank": rank,
        "condition": condition,
        "residual_rms": tuple(float(value) for value in rms),
        "B2_limb_adds_rank_beyond_B1": bool(rank == 2),
        "direct_scaled_designs": MappingProxyType(direct),
        "pass": bool(
            rank == 2 and condition <= DESIGN_CONDITION_MAXIMUM and direct_pass
        ),
    })


def _weighted_square_objective(
    coefficients: np.ndarray, matrix: np.ndarray, response: np.ndarray
) -> tuple[float, np.ndarray]:
    residual = response - matrix @ coefficients
    side = np.where(residual >= 0.0, POSITIVE_TAIL_SCALE_RATIO, 1.0)
    weight = 1.0 / (side * side)
    objective = 0.5 * float(np.dot(residual * weight, residual))
    gradient = -(matrix.T @ (weight * residual))
    return objective, gradient


def _fit_location(
    matrix: np.ndarray,
    response: np.ndarray,
    *,
    lower: np.ndarray,
    upper: np.ndarray,
) -> tuple[np.ndarray, float]:
    initial = np.zeros(matrix.shape[1], dtype=float)
    result = minimize(
        lambda value: _weighted_square_objective(value, matrix, response),
        initial,
        jac=True,
        method="L-BFGS-B",
        bounds=tuple(zip(lower, upper, strict=True)),
        options={"maxiter": 1000, "ftol": 1e-12, "gtol": 1e-8, "maxls": 32},
    )
    if not result.success or not np.all(np.isfinite(result.x)):
        raise RuntimeError(f"O2 location fit failed: {result.message}")
    return np.asarray(result.x, dtype=float), float(result.fun)


def fit_nested_models(
    rows: np.ndarray, train_mask: np.ndarray, design: FrozenDesign
) -> FittedNestedModels:
    values = _require_rows(rows)
    train = np.asarray(train_mask, dtype=bool)
    response = np.asarray(values["signed_innovation_m"][train], dtype=float)
    own = values["own_inward_probability"][train, None]
    b0_matrix = np.column_stack((design.common_matrix[train], own))
    nuisance_lower, nuisance_upper = nuisance_scaled_bounds(design)
    lower = np.concatenate((nuisance_lower, np.array([0.0])))
    upper = np.concatenate((nuisance_upper, np.array([SHADOW_COEFFICIENT_MAX_M])))
    b0, objective0 = _fit_location(b0_matrix, response, lower=lower, upper=upper)
    nuisance = b0[:-1]
    beta_own = float(b0[-1])
    prediction0 = design.common_matrix[train] @ nuisance + beta_own * own[:, 0]
    residual0 = response - prediction0
    side = np.where(residual0 >= 0.0, POSITIVE_TAIL_SCALE_RATIO, 1.0)
    scale = float(np.sqrt(np.mean((residual0 / side) ** 2)))
    if not MINIMUM_SCALE_M <= scale <= MAXIMUM_SCALE_M:
        raise RuntimeError("train likelihood scale is outside frozen bounds")

    torso = values["torso_exposure"][train, None]
    beta_torso, objective1 = _fit_location(
        torso,
        response - prediction0,
        lower=np.array([0.0]),
        upper=np.array([SHADOW_COEFFICIENT_MAX_M]),
    )
    prediction1 = prediction0 + float(beta_torso[0]) * torso[:, 0]
    limb = values["other_limb_exposure"][train, None]
    beta_limb, objective2 = _fit_location(
        limb,
        response - prediction1,
        lower=np.array([0.0]),
        upper=np.array([SHADOW_COEFFICIENT_MAX_M]),
    )
    return FittedNestedModels(
        nuisance_names=design.names,
        nuisance_coefficients_m=nuisance,
        beta_own_m=beta_own,
        beta_torso_m=float(beta_torso[0]),
        beta_limb_m=float(beta_limb[0]),
        scale_m=scale,
        positive_tail_scale_ratio=POSITIVE_TAIL_SCALE_RATIO,
        train_objective={"B0": objective0, "B1_increment": objective1, "B2_increment": objective2},
    )


def nuisance_scaled_bounds(design: FrozenDesign) -> tuple[np.ndarray, np.ndarray]:
    """Bounds for a scaled fit that preserve the raw ±5 m contribution bound."""

    magnitude = NUISANCE_COEFFICIENT_ABS_MAX_M * design.design_column_rms
    lower = np.asarray(-magnitude, dtype=float)
    upper = np.asarray(magnitude, dtype=float)
    lower.setflags(write=False)
    upper.setflags(write=False)
    return lower, upper


def model_predictions(
    rows: np.ndarray, design: FrozenDesign, fit: FittedNestedModels
) -> Mapping[str, np.ndarray]:
    values = _require_rows(rows)
    base = design.common_matrix @ fit.nuisance_coefficients_m
    b0 = base + fit.beta_own_m * values["own_inward_probability"]
    b1 = b0 + fit.beta_torso_m * values["torso_exposure"]
    b2 = b1 + fit.beta_limb_m * values["other_limb_exposure"]
    return MappingProxyType({"B0": b0, "B1": b1, "B2": b2})


def sum_zero_pair_effects(
    design: FrozenDesign, fit: FittedNestedModels
) -> Mapping[str, float]:
    """Report reference-coded pair effects in an equivalent sum-zero gauge."""

    reference_index = design.pair_order.index(PAIR_REFERENCE)
    effects = np.zeros(len(design.pair_order), dtype=float)
    coefficient_cursor = 1
    for pair_index in range(len(design.pair_order)):
        if pair_index == reference_index:
            continue
        effects[pair_index] = (
            fit.nuisance_coefficients_m[coefficient_cursor]
            / design.design_column_rms[coefficient_cursor]
        )
        coefficient_cursor += 1
    effects -= np.mean(effects)
    if abs(float(np.sum(effects))) > 1e-12:
        raise RuntimeError("sum-zero pair gauge reconstruction failed")
    return MappingProxyType({
        name: float(value) for name, value in zip(design.pair_order, effects, strict=True)
    })


def two_piece_normal_nll(response: np.ndarray, location: np.ndarray, scale: float) -> np.ndarray:
    residual = np.asarray(response, dtype=float) - np.asarray(location, dtype=float)
    if not MINIMUM_SCALE_M <= float(scale) <= MAXIMUM_SCALE_M:
        raise ValueError("two-piece scale is outside frozen bounds")
    side = np.where(residual >= 0.0, POSITIVE_TAIL_SCALE_RATIO, 1.0)
    normalizer = math.log(scale * (1.0 + POSITIVE_TAIL_SCALE_RATIO) * math.sqrt(math.pi / 2.0))
    return normalizer + 0.5 * (residual / (scale * side)) ** 2


def two_piece_normal_quantile(location: np.ndarray, scale: float, quantile: float) -> np.ndarray:
    if not 0.0 < quantile < 1.0:
        raise ValueError("quantile must be strictly inside (0,1)")
    left_weight = 1.0 / (1.0 + POSITIVE_TAIL_SCALE_RATIO)
    if quantile < left_weight:
        z = ndtri(quantile / (2.0 * left_weight))
        delta = scale * z
    else:
        right_weight = 1.0 - left_weight
        z = ndtri(0.5 * (1.0 + (quantile - left_weight) / right_weight))
        delta = scale * POSITIVE_TAIL_SCALE_RATIO * z
    return np.asarray(location, dtype=float) + float(delta)


def two_piece_normal_tail_moments(
    location: np.ndarray, scale: float, threshold: float
) -> tuple[np.ndarray, np.ndarray]:
    """Return exact survival and first-tail moment at a fixed threshold."""

    if not MINIMUM_SCALE_M <= float(scale) <= MAXIMUM_SCALE_M:
        raise ValueError("two-piece scale is outside frozen bounds")
    mu = np.asarray(location, dtype=float)
    cutoff = float(threshold) - mu
    s_left = float(scale)
    s_right = float(scale) * POSITIVE_TAIL_SCALE_RATIO
    w_left = s_left / (s_left + s_right)
    w_right = 1.0 - w_left
    mean_left = s_left * math.sqrt(2.0 / math.pi)
    mean_right = s_right * math.sqrt(2.0 / math.pi)
    survival = np.empty_like(mu)
    first_moment = np.empty_like(mu)

    right_only = cutoff >= 0.0
    if np.any(right_only):
        z = cutoff[right_only] / s_right
        half_tail = 1.0 - erf(z / math.sqrt(2.0))
        half_first = mean_right * np.exp(-0.5 * z * z)
        survival[right_only] = w_right * half_tail
        first_moment[right_only] = w_right * (
            mu[right_only] * half_tail + half_first
        )

    includes_mode = ~right_only
    if np.any(includes_mode):
        z = -cutoff[includes_mode] / s_left
        left_mass = erf(z / math.sqrt(2.0))
        left_first = mean_left * (1.0 - np.exp(-0.5 * z * z))
        survival[includes_mode] = w_right + w_left * left_mass
        first_moment[includes_mode] = (
            w_right * (mu[includes_mode] + mean_right)
            + w_left * (mu[includes_mode] * left_mass - left_first)
        )
    if np.any(survival <= 0.0) or not np.all(np.isfinite(first_moment)):
        raise ValueError("positive-tail moment is numerically unobservable")
    return survival, first_moment


def _half_normal_absolute(scale: float, threshold: np.ndarray) -> np.ndarray:
    value = np.asarray(threshold, dtype=float)
    mean = scale * math.sqrt(2.0 / math.pi)
    output = mean - value
    positive = value > 0.0
    if np.any(positive):
        z = value[positive] / scale
        cdf = erf(z / math.sqrt(2.0))
        truncated = mean * (1.0 - np.exp(-0.5 * z * z))
        output[positive] += 2.0 * (value[positive] * cdf - truncated)
    return output


def two_piece_normal_crps(response: np.ndarray, location: np.ndarray, scale: float) -> np.ndarray:
    delta = np.asarray(response, dtype=float) - np.asarray(location, dtype=float)
    left_scale = float(scale)
    right_scale = float(scale) * POSITIVE_TAIL_SCALE_RATIO
    left_weight = left_scale / (left_scale + right_scale)
    right_weight = 1.0 - left_weight
    expected_abs = (
        left_weight * _half_normal_absolute(left_scale, -delta)
        + right_weight * _half_normal_absolute(right_scale, delta)
    )
    half_gini_constant = 2.0 * (2.0 - math.sqrt(2.0)) / math.sqrt(math.pi)
    pair_abs = (
        left_weight**2 * half_gini_constant * left_scale
        + right_weight**2 * half_gini_constant * right_scale
        + 2.0 * left_weight * right_weight * math.sqrt(2.0 / math.pi) * (left_scale + right_scale)
    )
    return expected_abs - 0.5 * pair_abs


def q90_pinball(response: np.ndarray, location: np.ndarray, scale: float) -> np.ndarray:
    predicted = two_piece_normal_quantile(location, scale, 0.90)
    residual = np.asarray(response, dtype=float) - predicted
    return np.maximum(0.90 * residual, -0.10 * residual)


def paired_action_epoch_bootstrap(
    row_delta: np.ndarray,
    action_index: np.ndarray,
    epoch_index: np.ndarray,
    *,
    seed: int = BOOTSTRAP_SEED,
    replicates: int = BOOTSTRAP_REPLICATES,
    inclusion_mask: np.ndarray | None = None,
    plan: FrozenBootstrapPlan | None = None,
) -> Mapping[str, float]:
    delta = np.asarray(row_delta, dtype=float)
    action = np.asarray(action_index, dtype=int)
    epoch = np.asarray(epoch_index, dtype=int)
    if delta.ndim != 1 or action.shape != delta.shape or epoch.shape != delta.shape:
        raise ValueError("bootstrap rows must be one-dimensional and aligned")
    if not len(delta) or not np.all(np.isfinite(delta)):
        raise ValueError("bootstrap metric must be nonempty and finite")
    included = (
        np.ones(len(delta), dtype=bool)
        if inclusion_mask is None else np.asarray(inclusion_mask, dtype=bool)
    )
    if included.shape != delta.shape or not np.any(included):
        raise ValueError("bootstrap inclusion mask must retain aligned rows")
    frozen_plan = plan or freeze_bootstrap_plan(
        action, epoch, seed=seed, replicates=replicates
    )
    if frozen_plan.seed != int(seed) or frozen_plan.replicates != int(replicates):
        raise ValueError("bootstrap plan seed/replicate contract differs")
    draws = _bootstrap_plan_metric(delta, action, epoch, frozen_plan, included)
    return MappingProxyType({
        "estimate": float(np.mean(delta[included])),
        "lower95": float(np.quantile(draws, 0.05)),
        "upper95": float(np.quantile(draws, 0.95)),
        "replicates": int(replicates),
        "seed": int(seed),
        "block_epochs": BOOTSTRAP_BLOCK_EPOCHS,
    })


def freeze_residualized_contrast(
    rows: np.ndarray,
    train_mask: np.ndarray,
    design: FrozenDesign,
    *,
    feature_name: str,
) -> FrozenContrastOwner:
    """Freeze a train-only Frisch-Waugh exposure residualizer."""

    values = _require_rows(rows)
    train = np.asarray(train_mask, dtype=bool)
    if train.shape != (len(values),):
        raise ValueError("contrast train mask must be row-aligned")
    matrix = np.column_stack((
        design.common_matrix,
        values["own_inward_probability"],
    ))
    names = (*design.names, "own_inward_probability")
    if feature_name == "torso_exposure":
        parent = "B0"
    elif feature_name == "other_limb_exposure":
        parent = "B1"
        matrix = np.column_stack((matrix, values["torso_exposure"]))
        names = (*names, "torso_exposure")
    else:
        raise ValueError("contrast feature is not a registered model increment")
    coefficients, *_ = np.linalg.lstsq(
        matrix[train], values[feature_name][train], rcond=None
    )
    residual = values[feature_name] - matrix @ coefficients
    rms = float(np.sqrt(np.mean(residual[train] ** 2)))
    if not math.isfinite(rms) or rms <= np.finfo(float).eps:
        raise ValueError("residualized exposure is degenerate")
    normalized = residual / rms
    low, high = np.quantile(normalized[train], (0.25, 0.75))
    if not math.isfinite(float(low)) or not float(high) > float(low):
        raise ValueError("residualized exposure quartiles are degenerate")
    return FrozenContrastOwner(
        feature_name=feature_name,
        parent_model=parent,
        residualizer_names=names,
        residualizer_coefficients=coefficients,
        residualized_exposure=normalized,
        residualized_rms=rms,
        low_edge=float(low),
        high_edge=float(high),
    )


def _hierarchical_contrast_bootstrap(
    residual: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    action_index: np.ndarray,
    epoch_index: np.ndarray,
    *,
    seed: int,
    replicates: int,
    plan: FrozenBootstrapPlan | None = None,
) -> Mapping[str, float]:
    """Paired action/time-block bootstrap of a high-minus-low mean."""

    response = np.asarray(residual, dtype=float)
    high_mask = np.asarray(high, dtype=bool)
    low_mask = np.asarray(low, dtype=bool)
    action = np.asarray(action_index, dtype=int)
    epoch = np.asarray(epoch_index, dtype=int)
    if not (
        response.shape == high_mask.shape == low_mask.shape == action.shape == epoch.shape
        and response.ndim == 1
    ):
        raise ValueError("contrast bootstrap inputs must be aligned vectors")
    if np.any(high_mask & low_mask) or not np.any(high_mask) or not np.any(low_mask):
        raise ValueError("contrast bootstrap requires disjoint high and low rows")
    frozen_plan = plan or freeze_bootstrap_plan(
        action, epoch, seed=seed, replicates=replicates
    )
    high_draws = _bootstrap_plan_metric(
        response, action, epoch, frozen_plan, high_mask
    )
    low_draws = _bootstrap_plan_metric(
        response, action, epoch, frozen_plan, low_mask
    )
    draws = high_draws - low_draws
    return MappingProxyType({
        "estimate": float(np.mean(response[high_mask]) - np.mean(response[low_mask])),
        "lower95": float(np.quantile(draws, 0.05)),
        "upper95": float(np.quantile(draws, 0.95)),
        "replicates": int(replicates),
        "seed": int(seed),
        "block_epochs": BOOTSTRAP_BLOCK_EPOCHS,
    })


def residualized_exposure_contrast_audit(
    rows: np.ndarray,
    validation_mask: np.ndarray,
    predictions: Mapping[str, np.ndarray],
    owner: FrozenContrastOwner,
    *,
    replicates: int = BOOTSTRAP_REPLICATES,
    seed: int = BOOTSTRAP_SEED,
    plan: FrozenBootstrapPlan | None = None,
) -> Mapping[str, object]:
    """Evaluate one held-out residualized exposure contrast."""

    values = _require_rows(rows)
    validation = np.asarray(validation_mask, dtype=bool)
    if validation.shape != (len(values),):
        raise ValueError("contrast validation mask must be row-aligned")
    parent = np.asarray(predictions[owner.parent_model], dtype=float)
    if parent.shape != (len(values),):
        raise ValueError("contrast parent prediction is not row-aligned")
    residual = values["signed_innovation_m"] - parent
    high = validation & (owner.residualized_exposure >= owner.high_edge)
    low = validation & (owner.residualized_exposure <= owner.low_edge)
    audit = _hierarchical_contrast_bootstrap(
        residual[validation], high[validation], low[validation],
        values["action_index"][validation], values["source_epoch_index"][validation],
        seed=seed, replicates=replicates, plan=plan,
    )
    action_rows: dict[str, object] = {}
    positive_actions = 0
    for action_value in sorted(set(values["action_index"][validation].astype(int).tolist())):
        action_mask = validation & (values["action_index"] == action_value)
        action_high = action_mask & high
        action_low = action_mask & low
        high_epochs = len(np.unique(values["source_epoch_index"][action_high]))
        low_epochs = len(np.unique(values["source_epoch_index"][action_low]))
        if high_epochs < EXPOSURE_CLUSTER_MINIMUM or low_epochs < EXPOSURE_CLUSTER_MINIMUM:
            estimate = None
            passed = False
        else:
            estimate = float(np.mean(residual[action_high]) - np.mean(residual[action_low]))
            passed = estimate > 0.0
        positive_actions += int(passed)
        action_rows[str(action_value)] = {
            "high_epoch_clusters": high_epochs,
            "low_epoch_clusters": low_epochs,
            "estimate_m": estimate,
            "positive": passed,
        }
    return MappingProxyType({
        "feature": owner.feature_name,
        "parent_model": owner.parent_model,
        "low_edge": owner.low_edge,
        "high_edge": owner.high_edge,
        "bootstrap": dict(audit),
        "positive_validation_actions": positive_actions,
        "minimum_positive_validation_actions": VALIDATION_ACTION_COVERAGE_MINIMUM,
        "actions": MappingProxyType(action_rows),
        "pass": bool(
            audit["lower95"] > 0.0
            and positive_actions >= VALIDATION_ACTION_COVERAGE_MINIMUM
        ),
    })


def pair_coverage_gate(
    rows: np.ndarray, train_mask: np.ndarray, validation_mask: np.ndarray
) -> Mapping[str, object]:
    values = _require_rows(rows)
    train = np.asarray(train_mask, dtype=bool)
    validation = np.asarray(validation_mask, dtype=bool)
    output: dict[str, object] = {}
    passed = True
    for pair_index, pair_name in enumerate(PAIR_ORDER):
        node_index, anchor = divmod(pair_index, 8)
        identity = (values["node_index"] == node_index) & (values["anchor"] == anchor)
        train_identity = train & identity
        validation_identity = validation & identity
        train_actions = len(np.unique(values["action_index"][train_identity]))
        train_blocks = len(np.unique(np.column_stack((
            values["action_index"][train_identity],
            values["source_epoch_index"][train_identity] // BOOTSTRAP_BLOCK_EPOCHS,
        )), axis=0)) if np.any(train_identity) else 0
        train_rows = int(np.count_nonzero(train_identity))
        validation_rows = int(np.count_nonzero(validation_identity))
        row_pass = bool(
            train_actions >= PAIR_MINIMUM_TRAIN_ACTIONS
            and train_blocks >= PAIR_MINIMUM_TRAIN_BLOCKS
            and train_rows >= PAIR_MINIMUM_TRAIN_ROWS
            and validation_rows > 0
        )
        passed = passed and row_pass
        output[pair_name] = {
            "train_actions": train_actions,
            "train_blocks": train_blocks,
            "train_rows": train_rows,
            "validation_rows": validation_rows,
            "pass": row_pass,
        }
    return MappingProxyType({"pass": passed, "pairs": MappingProxyType(output)})


def eligibility_gate(
    attempts: Mapping[tuple[int, int], int],
    eligible: Mapping[tuple[int, int], int],
    optimizer_failures: int,
) -> Mapping[str, object]:
    keys = tuple(sorted(attempts))
    expected_keys = {
        (action, node) for action in range(19) for node in range(len(NODE_TO_SEGMENT))
    }
    fractions = {
        f"{action}/{node}": (
            float(eligible.get((action, node), 0) / attempts[(action, node)])
            if attempts[(action, node)] > 0 else 0.0
        )
        for action, node in keys
    }
    passed = bool(
        set(keys) == expected_keys
        and optimizer_failures == 0
        and all(value >= ELIGIBILITY_FRACTION_MINIMUM for value in fractions.values())
    )
    return MappingProxyType({
        "pass": passed,
        "minimum_fraction": min(fractions.values()) if fractions else 0.0,
        "action_node_denominators_complete": set(keys) == expected_keys,
        "fractions": MappingProxyType(fractions),
        "optimizer_failures": int(optimizer_failures),
        "every_eligible_link_scored": int(sum(eligible.values())) if eligible else 0,
    })


def exposure_cluster_gate(
    rows: np.ndarray, train_mask: np.ndarray, validation_mask: np.ndarray
) -> Mapping[str, object]:
    """Freeze train quartiles and require validation epoch-cluster support."""

    values = _require_rows(rows)
    train = np.asarray(train_mask, dtype=bool)
    validation = np.asarray(validation_mask, dtype=bool)
    if train.shape != validation.shape or train.shape != (len(values),):
        raise ValueError("exposure coverage masks must be row-aligned")
    output: dict[str, object] = {}
    feature_passes = []
    for name in ("torso_exposure", "other_limb_exposure"):
        low, high = np.quantile(values[name][train], (0.25, 0.75))
        train_low = train & (values[name] <= low)
        train_high = train & (values[name] >= high)
        train_blocks = np.unique(np.column_stack((
            values["action_index"][train],
            values["source_epoch_index"][train] // BOOTSTRAP_BLOCK_EPOCHS,
        )), axis=0)
        action_rows: dict[str, object] = {}
        passing_actions = 0
        for action in sorted(set(values["action_index"][validation].astype(int).tolist())):
            mask = validation & (values["action_index"] == action)
            low_epochs = np.unique(values["source_epoch_index"][mask & (values[name] <= low)])
            high_epochs = np.unique(values["source_epoch_index"][mask & (values[name] >= high)])
            passed = bool(
                len(low_epochs) >= EXPOSURE_CLUSTER_MINIMUM
                and len(high_epochs) >= EXPOSURE_CLUSTER_MINIMUM
            )
            passing_actions += int(passed)
            action_rows[str(action)] = {
                "low_epoch_clusters": int(len(low_epochs)),
                "high_epoch_clusters": int(len(high_epochs)),
                "pass": passed,
            }
        feature_pass = passing_actions >= VALIDATION_ACTION_COVERAGE_MINIMUM
        feature_passes.append(feature_pass)
        output[name] = {
            "train_low_quartile": float(low),
            "train_high_quartile": float(high),
            "train_support": {
                "actions": int(len(np.unique(values["action_index"][train]))),
                "epoch_blocks": int(len(train_blocks)),
                "rows": int(np.count_nonzero(train)),
                "low_rows": int(np.count_nonzero(train_low)),
                "high_rows": int(np.count_nonzero(train_high)),
                "low_distinct_epochs": int(len(np.unique(
                    np.column_stack((
                        values["action_index"][train_low],
                        values["source_epoch_index"][train_low],
                    )), axis=0
                ))),
                "high_distinct_epochs": int(len(np.unique(
                    np.column_stack((
                        values["action_index"][train_high],
                        values["source_epoch_index"][train_high],
                    )), axis=0
                ))),
            },
            "passing_validation_actions": passing_actions,
            "actions": action_rows,
            "pass": feature_pass,
        }
    output["pass"] = bool(all(feature_passes))
    return MappingProxyType(output)


def significant_negative_stratum_reversals(
    row_association: np.ndarray,
    rows: np.ndarray,
    validation_mask: np.ndarray,
    *,
    facing_edges: Sequence[float],
    range_edges: Sequence[float],
    minimum_epoch_clusters: int = EXPOSURE_CLUSTER_MINIMUM,
    replicates: int = BOOTSTRAP_REPLICATES,
    plan: FrozenBootstrapPlan | None = None,
) -> Mapping[str, object]:
    """Detect preregistered significantly negative fixed-stratum effects."""

    values = _require_rows(rows)
    association = np.asarray(row_association, dtype=float)
    validation = np.asarray(validation_mask, dtype=bool)
    if association.shape != (len(values),) or validation.shape != association.shape:
        raise ValueError("stratum inputs must be row-aligned")
    facing = values["own_inward_probability"]
    causal_range = values["causal_predicted_range_m"]
    facing_bin = np.digitize(facing, np.asarray(facing_edges, dtype=float)[1:-1])
    range_bin = np.digitize(causal_range, np.asarray(range_edges, dtype=float)[1:-1])
    reversals = []
    unevaluable = []
    evaluated = 0
    keys = zip(
        values["node_index"].astype(int),
        values["anchor"].astype(int),
        facing_bin.astype(int),
        range_bin.astype(int),
        strict=True,
    )
    key_array = np.asarray(list(keys), dtype=int)
    frozen_plan = plan or freeze_bootstrap_plan(
        values["action_index"][validation],
        values["source_epoch_index"][validation],
        seed=BOOTSTRAP_SEED,
        replicates=replicates,
    )
    for key in sorted({tuple(row) for row in key_array[validation]}):
        mask = validation & np.all(key_array == np.asarray(key), axis=1)
        cluster_count = len(np.unique(np.column_stack((
            values["action_index"][mask], values["source_epoch_index"][mask]
        )), axis=0))
        supporting_actions = len(np.unique(values["action_index"][mask]))
        if (
            cluster_count < minimum_epoch_clusters
            or supporting_actions < VALIDATION_ACTION_COVERAGE_MINIMUM
        ):
            unevaluable.append({
                "key": key,
                "clusters": cluster_count,
                "supporting_actions": supporting_actions,
            })
            continue
        try:
            audit = paired_action_epoch_bootstrap(
                association[validation],
                values["action_index"][validation],
                values["source_epoch_index"][validation],
                seed=frozen_plan.seed,
                replicates=replicates,
                inclusion_mask=mask[validation],
                plan=frozen_plan,
            )
        except ValueError as exc:
            unevaluable.append({
                "key": key,
                "clusters": cluster_count,
                "supporting_actions": supporting_actions,
                "reason": str(exc),
            })
            continue
        evaluated += 1
        if float(audit["upper95"]) < 0.0:
            reversals.append({"key": key, "clusters": cluster_count, "bootstrap": dict(audit)})
    return MappingProxyType({
        "evaluated_strata": evaluated,
        "minimum_evaluable_strata": MINIMUM_EVALUABLE_FIXED_STRATA,
        "unevaluable_strata": tuple(unevaluable),
        "significant_negative_reversals": tuple(reversals),
        "coverage_pass": evaluated >= MINIMUM_EVALUABLE_FIXED_STRATA,
        "pass": bool(
            evaluated >= MINIMUM_EVALUABLE_FIXED_STRATA and not reversals
        ),
    })


def evaluate_predictive_scores(
    rows: np.ndarray,
    train_mask: np.ndarray,
    validation_mask: np.ndarray,
    predictions: Mapping[str, np.ndarray],
    fit: FittedNestedModels,
    *,
    replicates: int = BOOTSTRAP_REPLICATES,
    plan: FrozenBootstrapPlan | None = None,
) -> Mapping[str, object]:
    """Evaluate paired held-out proper scores without refitting validation."""

    values = _require_rows(rows)
    train = np.asarray(train_mask, dtype=bool)
    validation = np.asarray(validation_mask, dtype=bool)
    response = values["signed_innovation_m"]
    if train.shape != validation.shape or train.shape != response.shape:
        raise ValueError("evaluation masks must be row-aligned")
    for model in ("B0", "B1", "B2"):
        if np.asarray(predictions[model]).shape != response.shape:
            raise ValueError("models must use identical paired rows")
    frozen_plan = plan or freeze_bootstrap_plan(
        values["action_index"][validation],
        values["source_epoch_index"][validation],
        seed=BOOTSTRAP_SEED,
        replicates=replicates,
    )
    nll = {
        model: two_piece_normal_nll(response, predictions[model], fit.scale_m)
        for model in ("B0", "B1", "B2")
    }
    pinball = {
        model: q90_pinball(response, predictions[model], fit.scale_m)
        for model in ("B0", "B1", "B2")
    }
    crps = {
        model: two_piece_normal_crps(response, predictions[model], fit.scale_m)
        for model in ("B0", "B1", "B2")
    }
    increments = (("B1", "B0"), ("B2", "B1"))
    result: dict[str, object] = {}
    gates = []
    tail_threshold = float(np.quantile(response[train], 0.90))
    tail = response >= tail_threshold
    if not np.any(validation & tail):
        raise ValueError("validation positive tail is empty")
    tail_conditional_means: dict[str, np.ndarray] = {}
    for model in ("B0", "B1", "B2"):
        survival, first = two_piece_normal_tail_moments(
            predictions[model], fit.scale_m, tail_threshold
        )
        tail_conditional_means[model] = first / survival
    for child, parent in increments:
        key = f"{child}_minus_{parent}"
        nll_audit = paired_action_epoch_bootstrap(
            (nll[child] - nll[parent])[validation],
            values["action_index"][validation],
            values["source_epoch_index"][validation],
            replicates=replicates,
            plan=frozen_plan,
        )
        pinball_audit = paired_action_epoch_bootstrap(
            (pinball[child] - pinball[parent])[validation],
            values["action_index"][validation],
            values["source_epoch_index"][validation],
            replicates=replicates,
            plan=frozen_plan,
        )
        crps_audit = paired_action_epoch_bootstrap(
            (crps[child] - crps[parent])[validation],
            values["action_index"][validation],
            values["source_epoch_index"][validation],
            replicates=replicates,
            plan=frozen_plan,
        )
        tail_loss_parent = np.abs(response - tail_conditional_means[parent])
        tail_loss_child = np.abs(response - tail_conditional_means[child])
        tail_audit = paired_action_epoch_bootstrap(
            (tail_loss_child - tail_loss_parent)[validation],
            values["action_index"][validation],
            values["source_epoch_index"][validation],
            replicates=replicates,
            inclusion_mask=tail[validation],
            plan=frozen_plan,
        )
        mean_changed = variance_only_increment_cannot_pass(
            predictions[parent][validation], predictions[child][validation]
        )
        passed = bool(
            nll_audit["upper95"] < 0.0
            and pinball_audit["upper95"] < 0.0
            and crps_audit["upper95"] < 0.0
            and tail_audit["upper95"] < 0.0
            and mean_changed
        )
        gates.append(passed)
        result[key] = {
            "nll": dict(nll_audit),
            "q90_positive_tail_pinball": dict(pinball_audit),
            "crps": dict(crps_audit),
            "positive_tail_train_threshold_m": tail_threshold,
            "positive_tail_inclusion": "signed_innovation_m >= train q90 (NumPy linear)",
            "positive_tail_conditional_mean_absolute_error": dict(tail_audit),
            "mean_prediction_changed": mean_changed,
            "pass": passed,
        }
    result["pass_without_contrast_or_reversal_gates"] = bool(all(gates))
    return MappingProxyType(result)


def variance_only_increment_cannot_pass(
    location_before: np.ndarray, location_after: np.ndarray
) -> bool:
    """Mechanism gate: every accepted increment must change held-out means."""

    before = np.asarray(location_before, dtype=float)
    after = np.asarray(location_after, dtype=float)
    return bool(np.any(after != before))


def evaluate_full_study(
    rows: np.ndarray,
    train_mask: np.ndarray,
    validation_mask: np.ndarray,
    design: FrozenDesign,
    fit: FittedNestedModels,
    *,
    replicates: int = BOOTSTRAP_REPLICATES,
) -> Mapping[str, object]:
    """Apply every independent validation gate; no composite can rescue one."""

    values = _require_rows(rows)
    train = np.asarray(train_mask, dtype=bool)
    validation = np.asarray(validation_mask, dtype=bool)
    plan = freeze_bootstrap_plan(
        values["action_index"][validation],
        values["source_epoch_index"][validation],
        seed=BOOTSTRAP_SEED,
        replicates=replicates,
    )
    predictions = model_predictions(values, design, fit)
    scores = evaluate_predictive_scores(
        values, train, validation, predictions, fit,
        replicates=replicates, plan=plan,
    )
    facing_quantiles = np.quantile(
        values["own_inward_probability"][train], (1.0 / 3.0, 2.0 / 3.0)
    )
    range_quantiles = np.quantile(
        values["causal_predicted_range_m"][train], (0.25, 0.50, 0.75)
    )
    facing_edges = (-math.inf, *map(float, facing_quantiles), math.inf)
    range_edges = (-math.inf, *map(float, range_quantiles), math.inf)
    if np.any(np.diff(facing_edges) <= 0.0) or np.any(np.diff(range_edges) <= 0.0):
        raise ValueError("frozen fixed-stratum edges are degenerate")
    contrast_rows: dict[str, object] = {}
    reversal_rows: dict[str, object] = {}
    contrast_passes = []
    reversal_passes = []
    for feature_name in ("torso_exposure", "other_limb_exposure"):
        owner = freeze_residualized_contrast(
            values, train, design, feature_name=feature_name
        )
        contrast = residualized_exposure_contrast_audit(
            values,
            validation,
            predictions,
            owner,
            replicates=replicates,
            seed=BOOTSTRAP_SEED,
            plan=plan,
        )
        parent_residual = (
            values["signed_innovation_m"] - predictions[owner.parent_model]
        )
        row_association = owner.residualized_exposure * parent_residual
        reversal = significant_negative_stratum_reversals(
            row_association,
            values,
            validation,
            facing_edges=facing_edges,
            range_edges=range_edges,
            replicates=replicates,
            plan=plan,
        )
        contrast_rows[feature_name] = {
            **dict(contrast),
            "residualizer_names": owner.residualizer_names,
            "residualizer_coefficients": tuple(
                float(value) for value in owner.residualizer_coefficients
            ),
            "residualized_rms": owner.residualized_rms,
        }
        reversal_rows[feature_name] = dict(reversal)
        contrast_passes.append(bool(contrast["pass"]))
        reversal_passes.append(bool(reversal["pass"]))
    coefficient_gate = bool(
        np.all(np.isfinite(fit.nuisance_coefficients_m))
        and 0.0 <= fit.beta_own_m <= SHADOW_COEFFICIENT_MAX_M
        and 0.0 <= fit.beta_torso_m <= SHADOW_COEFFICIENT_MAX_M
        and 0.0 <= fit.beta_limb_m <= SHADOW_COEFFICIENT_MAX_M
        and MINIMUM_SCALE_M <= fit.scale_m <= MAXIMUM_SCALE_M
    )
    gates = {
        "paired_predictive_scores": bool(scores["pass_without_contrast_or_reversal_gates"]),
        "residualized_exposure_contrasts": bool(all(contrast_passes)),
        "fixed_stratum_no_negative_reversal": bool(all(reversal_passes)),
        "coefficients_finite_monotone_bounded": coefficient_gate,
        "no_occlusion_based_link_deletion": True,
        "anchor_geometry_unchanged": True,
    }
    return MappingProxyType({
        "scores": dict(scores),
        "contrasts": MappingProxyType(contrast_rows),
        "fixed_stratum_reversals": MappingProxyType(reversal_rows),
        "fixed_stratum_edges": {
            "own_facing_train_tertiles": facing_edges,
            "causal_range_train_quartiles_m": range_edges,
        },
        "sum_zero_pair_effects_m": dict(sum_zero_pair_effects(design, fit)),
        "coefficients": {
            "beta_own_m": fit.beta_own_m,
            "beta_torso_m": fit.beta_torso_m,
            "beta_limb_m": fit.beta_limb_m,
            "scale_m": fit.scale_m,
        },
        "downstream_policy": {
            "estimability_denominator": (
                "eligible held-link labels only; attempted/eligible/ineligible "
                "counts and reasons are owned by extraction evidence"
            ),
            "occlusion_based_link_deletion": False,
            "covariance_inflation_applied": False,
            "covariance_mapping": "DEFERRED_NO_SOLVER_INTEGRATION",
            "anchor_rank_and_condition_change": 0.0,
        },
        "gates": MappingProxyType(gates),
        "pass": bool(all(gates.values())),
    })
