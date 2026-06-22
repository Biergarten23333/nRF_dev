#!/usr/bin/env python3
"""LOOCV static spatial-bias compensation test for the Erlangen V4-io table.

This script intentionally uses only the 24 static V4-io rows from
FULL/tables/tag_abs_errors_per_session.csv.  It compares:

  0. no compensation,
  1. global translation from the 23 training points,
  2. affine/linear regression,
  3. coordinate-only Gaussian Process Regression.

The target is the compensation vector truth - uwb.  The source table stores
err_x/err_y/err_z as aligned - truth, so those columns are negated after a
sign consistency check.
"""

from __future__ import annotations

import argparse
import math
import sys
import warnings
from dataclasses import dataclass
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

try:
    from sklearn.exceptions import ConvergenceWarning
    from sklearn.gaussian_process import GaussianProcessRegressor
    from sklearn.gaussian_process.kernels import ConstantKernel, Matern, WhiteKernel
    from sklearn.linear_model import LinearRegression
    from sklearn.model_selection import LeaveOneOut
    from sklearn.preprocessing import StandardScaler
except ImportError as exc:  # pragma: no cover - exercised only when deps are missing.
    raise SystemExit(
        "Missing required dependency: scikit-learn. Install it in the Python "
        "environment used to run this script, for example: python3 -m pip install scikit-learn"
    ) from exc


SCRIPT_PATH = Path(__file__).resolve()
FULL_DIR = SCRIPT_PATH.parents[1]
DEFAULT_INPUT = FULL_DIR / "tables" / "tag_abs_errors_per_session.csv"
DEFAULT_OUTPUT = FULL_DIR / "figs" / "loocv_static_compensation_cdf.png"

ALIGNED_COLS = ["aligned_x_mm", "aligned_y_vertical_mm", "aligned_z_mm"]
TRUTH_COLS = ["truth_x_mm", "truth_y_vertical_mm", "truth_z_mm"]
ERR_COLS = ["err_x_mm", "err_y_vertical_mm", "err_z_mm"]


@dataclass
class ModelResult:
    name: str
    errors_mm: np.ndarray
    deltas_mm: np.ndarray


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run strict 24-point static LOOCV compensation for V4-io."
    )
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT, help="Input CSV path.")
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help="Output CDF PNG path.",
    )
    parser.add_argument(
        "--version",
        default="v4-io",
        help="Version value to select from the input table.",
    )
    parser.add_argument(
        "--gpr-restarts",
        type=int,
        default=5,
        help="Optimizer restarts per scalar GPR. Use 0 for fastest deterministic fitting.",
    )
    parser.add_argument(
        "--length-scale-lower-mm",
        type=float,
        default=300.0,
        help="Physical lower bound for GPR Matern length scale before StandardScaler conversion.",
    )
    parser.add_argument(
        "--length-scale-upper-mm",
        type=float,
        default=5000.0,
        help="Physical upper bound for GPR Matern length scale before StandardScaler conversion.",
    )
    parser.add_argument("--random-state", type=int, default=42)
    return parser.parse_args()


def require_columns(df: pd.DataFrame, cols: list[str]) -> None:
    missing = [c for c in cols if c not in df.columns]
    if missing:
        raise ValueError(f"Input CSV is missing required columns: {missing}")


def load_static_v4io(input_path: Path, version: str) -> tuple[pd.DataFrame, np.ndarray, np.ndarray, np.ndarray]:
    if not input_path.exists():
        raise FileNotFoundError(f"Input CSV not found: {input_path}")

    df = pd.read_csv(input_path)
    require_columns(df, ["version", "ID", *ALIGNED_COLS, *TRUTH_COLS, *ERR_COLS])

    data = df[df["version"] == version].copy()
    if data.empty:
        raise ValueError(f"No rows found for version == {version!r}")
    data = data.sort_values("ID").reset_index(drop=True)
    if len(data) != 24:
        warnings.warn(
            f"Expected 24 static rows for {version!r}, found {len(data)}.",
            RuntimeWarning,
            stacklevel=2,
        )

    x_uwb = data[ALIGNED_COLS].astype(float).to_numpy()
    truth = data[TRUTH_COLS].astype(float).to_numpy()
    err_cols = data[ERR_COLS].astype(float).to_numpy()

    aligned_minus_truth = x_uwb - truth
    truth_minus_aligned = truth - x_uwb
    tol = 1e-6
    if np.nanmax(np.abs(err_cols - aligned_minus_truth)) < tol:
        y_comp = -err_cols
        sign_msg = "err_* matches aligned - truth; using y = truth - aligned = -err_*."
    elif np.nanmax(np.abs(err_cols - truth_minus_aligned)) < tol:
        y_comp = err_cols
        sign_msg = "err_* already matches truth - aligned; using y = err_*."
    else:
        max_a = float(np.nanmax(np.abs(err_cols - aligned_minus_truth)))
        max_t = float(np.nanmax(np.abs(err_cols - truth_minus_aligned)))
        raise ValueError(
            "Could not determine err_* sign convention. "
            f"max|err-(aligned-truth)|={max_a:.6g}, "
            f"max|err-(truth-aligned)|={max_t:.6g}"
        )

    print(f"Loaded {len(data)} rows from {input_path}")
    print(f"Selected version: {version}")
    print(f"Sign check: {sign_msg}")
    return data, x_uwb, truth, y_comp


def error_3d(corrected_xyz: np.ndarray, truth_xyz: np.ndarray) -> np.ndarray:
    return np.linalg.norm(corrected_xyz - truth_xyz, axis=1)


def stats(errors: np.ndarray) -> dict[str, float]:
    errors = np.asarray(errors, dtype=float)
    return {
        "median": float(np.nanmedian(errors)),
        "p95": float(np.nanpercentile(errors, 95.0)),
        "rmse": float(math.sqrt(np.nanmean(errors**2))),
    }


def make_gpr_kernel(length_scale_bounds_scaled: list[tuple[float, float]]):
    return (
        ConstantKernel(1.0, constant_value_bounds=(1e-3, 1e3))
        * Matern(
            length_scale=np.ones(3, dtype=float),
            length_scale_bounds=length_scale_bounds_scaled,
            nu=1.5,
        )
        + WhiteKernel(noise_level=1e-2, noise_level_bounds=(1e-6, 1e2))
    )


def predict_gpr_delta(
    x_train: np.ndarray,
    y_train: np.ndarray,
    x_test: np.ndarray,
    length_scale_lower_mm: float,
    length_scale_upper_mm: float,
    gpr_restarts: int,
    random_state: int,
) -> tuple[np.ndarray, list[str]]:
    """Fit three scalar GPRs and return one compensation vector prediction."""
    notes: list[str] = []
    x_scaler = StandardScaler()
    y_scaler = StandardScaler()
    x_train_s = x_scaler.fit_transform(x_train)
    x_test_s = x_scaler.transform(x_test)
    y_train_s = y_scaler.fit_transform(y_train)

    # Convert physical millimeter bounds to standardized-coordinate bounds.
    # This preserves the requested 300..5000 mm bound despite StandardScaler.
    x_scale = np.asarray(x_scaler.scale_, dtype=float)
    lower = np.maximum(length_scale_lower_mm / x_scale, 1e-6)
    upper = np.maximum(length_scale_upper_mm / x_scale, lower * 1.001)
    bounds_scaled = [(float(lo), float(hi)) for lo, hi in zip(lower, upper)]

    pred_s = np.zeros((1, 3), dtype=float)
    for dim in range(3):
        kernel = make_gpr_kernel(bounds_scaled)
        gpr = GaussianProcessRegressor(
            kernel=kernel,
            alpha=1e-8,
            normalize_y=False,
            n_restarts_optimizer=gpr_restarts,
            random_state=random_state + dim,
        )
        try:
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always", ConvergenceWarning)
                gpr.fit(x_train_s, y_train_s[:, dim])
            for warning in caught:
                if issubclass(warning.category, ConvergenceWarning):
                    notes.append(f"dim {dim}: {warning.message}")
            pred_s[0, dim] = float(gpr.predict(x_test_s)[0])
        except Exception as exc:
            notes.append(f"dim {dim}: optimized GPR failed ({exc!r}); retrying fixed kernel")
            try:
                fallback = GaussianProcessRegressor(
                    kernel=kernel,
                    alpha=1e-6,
                    optimizer=None,
                    normalize_y=False,
                )
                fallback.fit(x_train_s, y_train_s[:, dim])
                pred_s[0, dim] = float(fallback.predict(x_test_s)[0])
            except Exception as fallback_exc:
                notes.append(
                    f"dim {dim}: fixed-kernel GPR failed ({fallback_exc!r}); "
                    "using train mean for this component"
                )
                pred_s[0, dim] = 0.0  # y_train_s is standardized, so zero is train mean.

    return y_scaler.inverse_transform(pred_s)[0], notes


def run_loocv(
    x_uwb: np.ndarray,
    truth: np.ndarray,
    y_comp: np.ndarray,
    args: argparse.Namespace,
) -> tuple[list[ModelResult], list[str]]:
    n = len(x_uwb)
    loo = LeaveOneOut()

    deltas = {
        "Model 0 Baseline": np.zeros((n, 3), dtype=float),
        "Model 1 Global Translation": np.zeros((n, 3), dtype=float),
        "Model 2 Affine/Linear": np.zeros((n, 3), dtype=float),
        "Model 3 Coordinate-only GPR": np.zeros((n, 3), dtype=float),
    }
    gpr_notes: list[str] = []

    for fold_idx, (train_idx, test_idx) in enumerate(loo.split(x_uwb), start=1):
        x_train, x_test = x_uwb[train_idx], x_uwb[test_idx]
        y_train = y_comp[train_idx]
        test_i = int(test_idx[0])

        deltas["Model 1 Global Translation"][test_i] = np.mean(y_train, axis=0)

        linear = LinearRegression()
        linear.fit(x_train, y_train)
        deltas["Model 2 Affine/Linear"][test_i] = linear.predict(x_test)[0]

        gpr_delta, notes = predict_gpr_delta(
            x_train=x_train,
            y_train=y_train,
            x_test=x_test,
            length_scale_lower_mm=args.length_scale_lower_mm,
            length_scale_upper_mm=args.length_scale_upper_mm,
            gpr_restarts=args.gpr_restarts,
            random_state=args.random_state + 1000 * fold_idx,
        )
        deltas["Model 3 Coordinate-only GPR"][test_i] = gpr_delta
        for note in notes:
            gpr_notes.append(f"fold {fold_idx}: {note}")

    results: list[ModelResult] = []
    for name, delta in deltas.items():
        corrected = x_uwb + delta
        results.append(ModelResult(name=name, errors_mm=error_3d(corrected, truth), deltas_mm=delta))
    return results, gpr_notes


def print_summary(results: list[ModelResult]) -> None:
    print("\nStrict static LOOCV 3D error summary")
    print("-" * 78)
    print(f"{'Model':36s} {'Median (mm)':>12s} {'P95 (mm)':>12s} {'RMSE (mm)':>12s}")
    print("-" * 78)
    for result in results:
        s = stats(result.errors_mm)
        print(f"{result.name:36s} {s['median']:12.3f} {s['p95']:12.3f} {s['rmse']:12.3f}")
    print("-" * 78)


def plot_cdf(results: list[ModelResult], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    plt.figure(figsize=(7.2, 4.8))
    for result in results:
        values = np.sort(np.asarray(result.errors_mm, dtype=float))
        cdf = np.arange(1, len(values) + 1, dtype=float) / len(values)
        plt.step(values, cdf, where="post", linewidth=2.0, label=result.name.replace("Model ", "M"))
    plt.xlabel("3D Error (mm)")
    plt.ylabel("Cumulative Probability")
    plt.title("Static 24-position LOOCV Compensation CDF")
    plt.grid(True, alpha=0.3)
    plt.legend(loc="lower right", fontsize=8)
    plt.tight_layout()
    plt.savefig(output_path, dpi=180)
    plt.close()
    print(f"Saved CDF figure: {output_path}")


def main() -> int:
    args = parse_args()
    if args.length_scale_lower_mm <= 0 or args.length_scale_upper_mm <= 0:
        raise ValueError("GPR length-scale bounds must be positive.")
    if args.length_scale_lower_mm >= args.length_scale_upper_mm:
        raise ValueError("GPR lower length-scale bound must be smaller than the upper bound.")

    _, x_uwb, truth, y_comp = load_static_v4io(args.input, args.version)
    results, gpr_notes = run_loocv(x_uwb=x_uwb, truth=truth, y_comp=y_comp, args=args)
    print_summary(results)

    if gpr_notes:
        print("\nGPR fitting notes/warnings:")
        for note in gpr_notes[:20]:
            print(f"  - {note}")
        if len(gpr_notes) > 20:
            print(f"  ... {len(gpr_notes) - 20} additional notes suppressed")

    plot_cdf(results, args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
