#!/usr/bin/env python3
"""Apply a static V4-io spatial GPR compensation model to ROTO V5 samples.

The model is trained on all 24 static rows from
FULL/tables/tag_abs_errors_per_session.csv, using the same coordinate-only GPR
configuration as static_loocv_gpr_compensation.py.  It is then applied directly
to the dynamic ROTO supervision table.

The static source table stores err_x/err_y/err_z as aligned - truth, so the
training target is negated to represent the compensation vector truth - uwb.
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
    from sklearn.preprocessing import StandardScaler
except ImportError as exc:  # pragma: no cover
    raise SystemExit(
        "Missing required dependency: scikit-learn. Run with the prepared venv, "
        "for example: /tmp/biospur_static_gpr_venv/bin/python this_script.py"
    ) from exc


SCRIPT_PATH = Path(__file__).resolve()
FULL_DIR = SCRIPT_PATH.parents[1]
ANALYSIS_DIR = FULL_DIR.parent
DEFAULT_STATIC = FULL_DIR / "tables" / "tag_abs_errors_per_session.csv"
DEFAULT_DYNAMIC = (
    ANALYSIS_DIR
    / "FULL_V5_roto_deepdive"
    / "tables"
    / "roto_v5_dloo_samples.csv"
)
DEFAULT_OUTPUT = FULL_DIR / "figs" / "roto_dynamic_compensation_cdf.png"

STATIC_X_COLS = ["aligned_x_mm", "aligned_y_vertical_mm", "aligned_z_mm"]
STATIC_TRUTH_COLS = ["truth_x_mm", "truth_y_vertical_mm", "truth_z_mm"]
STATIC_ERR_COLS = ["err_x_mm", "err_y_vertical_mm", "err_z_mm"]
DYN_X_COLS = ["x", "y", "z"]
DYN_TRUTH_COLS = ["truth_x", "truth_y", "truth_z"]


@dataclass
class StaticGPR:
    x_scaler: StandardScaler
    y_scaler: StandardScaler
    models: list[GaussianProcessRegressor]
    notes: list[str]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Train static V4-io GPR and apply it to ROTO dynamic samples."
    )
    parser.add_argument("--static-csv", type=Path, default=DEFAULT_STATIC)
    parser.add_argument("--dynamic-csv", type=Path, default=DEFAULT_DYNAMIC)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--version", default="v4-io")
    parser.add_argument("--max-raw-error-mm", type=float, default=500.0)
    parser.add_argument("--gpr-restarts", type=int, default=5)
    parser.add_argument("--length-scale-lower-mm", type=float, default=300.0)
    parser.add_argument("--length-scale-upper-mm", type=float, default=5000.0)
    parser.add_argument("--random-state", type=int, default=42)
    return parser.parse_args()


def require_columns(df: pd.DataFrame, columns: list[str], label: str) -> None:
    missing = [c for c in columns if c not in df.columns]
    if missing:
        raise ValueError(f"{label} is missing required columns: {missing}")


def load_static(static_csv: Path, version: str) -> tuple[pd.DataFrame, np.ndarray, np.ndarray]:
    if not static_csv.exists():
        raise FileNotFoundError(f"Static CSV not found: {static_csv}")
    df = pd.read_csv(static_csv)
    require_columns(df, ["version", "ID", *STATIC_X_COLS, *STATIC_TRUTH_COLS, *STATIC_ERR_COLS], "static CSV")
    data = df[df["version"] == version].copy().sort_values("ID").reset_index(drop=True)
    if len(data) != 24:
        warnings.warn(
            f"Expected 24 rows for version={version!r}; found {len(data)}.",
            RuntimeWarning,
            stacklevel=2,
        )
    if data.empty:
        raise ValueError(f"No static rows found for version={version!r}")

    x_static = data[STATIC_X_COLS].astype(float).to_numpy()
    truth_static = data[STATIC_TRUTH_COLS].astype(float).to_numpy()
    err_static = data[STATIC_ERR_COLS].astype(float).to_numpy()

    aligned_minus_truth = x_static - truth_static
    truth_minus_aligned = truth_static - x_static
    tol = 1e-6
    if np.nanmax(np.abs(err_static - aligned_minus_truth)) < tol:
        y_comp = -err_static
        print("Static sign check: err_* = aligned - truth; training target is -err_*.")
    elif np.nanmax(np.abs(err_static - truth_minus_aligned)) < tol:
        y_comp = err_static
        print("Static sign check: err_* = truth - aligned; training target is err_*.")
    else:
        raise ValueError("Could not determine static err_* sign convention.")

    return data, x_static, y_comp


def load_dynamic(dynamic_csv: Path, max_raw_error_mm: float) -> tuple[pd.DataFrame, np.ndarray, np.ndarray, np.ndarray]:
    if not dynamic_csv.exists():
        raise FileNotFoundError(f"Dynamic CSV not found: {dynamic_csv}")
    df = pd.read_csv(dynamic_csv)
    require_columns(df, [*DYN_X_COLS, *DYN_TRUTH_COLS, "err3d_mm"], "dynamic CSV")

    mask = np.isfinite(df[DYN_X_COLS + DYN_TRUTH_COLS + ["err3d_mm"]].astype(float)).all(axis=1)
    mask &= df["err3d_mm"].astype(float) <= max_raw_error_mm
    data = df[mask].copy().reset_index(drop=True)
    if data.empty:
        raise ValueError("No dynamic rows remain after cleaning.")

    x_dyn = data[DYN_X_COLS].astype(float).to_numpy()
    truth_dyn = data[DYN_TRUTH_COLS].astype(float).to_numpy()
    raw_err = np.linalg.norm(x_dyn - truth_dyn, axis=1)
    max_table_delta = float(np.nanmax(np.abs(raw_err - data["err3d_mm"].astype(float).to_numpy())))
    print(
        f"Dynamic rows: raw={len(df)}, kept={len(data)}, "
        f"removed={len(df) - len(data)} with err3d_mm > {max_raw_error_mm:.1f} or nonfinite."
    )
    print(f"Dynamic error consistency: max |computed_err3d - table_err3d| = {max_table_delta:.6f} mm")
    return data, x_dyn, truth_dyn, raw_err


def make_kernel(length_scale_bounds_scaled: list[tuple[float, float]]):
    return (
        ConstantKernel(1.0, constant_value_bounds=(1e-3, 1e3))
        * Matern(
            length_scale=np.ones(3, dtype=float),
            length_scale_bounds=length_scale_bounds_scaled,
            nu=1.5,
        )
        + WhiteKernel(noise_level=1e-2, noise_level_bounds=(1e-6, 1e2))
    )


def train_static_gpr(
    x_static: np.ndarray,
    y_comp: np.ndarray,
    length_scale_lower_mm: float,
    length_scale_upper_mm: float,
    gpr_restarts: int,
    random_state: int,
) -> StaticGPR:
    x_scaler = StandardScaler()
    y_scaler = StandardScaler()
    x_s = x_scaler.fit_transform(x_static)
    y_s = y_scaler.fit_transform(y_comp)

    x_scale = np.asarray(x_scaler.scale_, dtype=float)
    lower = np.maximum(length_scale_lower_mm / x_scale, 1e-6)
    upper = np.maximum(length_scale_upper_mm / x_scale, lower * 1.001)
    bounds_scaled = [(float(lo), float(hi)) for lo, hi in zip(lower, upper)]

    models: list[GaussianProcessRegressor] = []
    notes: list[str] = []
    for dim in range(3):
        kernel = make_kernel(bounds_scaled)
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
                gpr.fit(x_s, y_s[:, dim])
            for warning in caught:
                if issubclass(warning.category, ConvergenceWarning):
                    notes.append(f"dim {dim}: {warning.message}")
        except Exception as exc:
            notes.append(f"dim {dim}: optimized GPR failed ({exc!r}); retrying fixed kernel")
            gpr = GaussianProcessRegressor(
                kernel=kernel,
                alpha=1e-6,
                optimizer=None,
                normalize_y=False,
            )
            gpr.fit(x_s, y_s[:, dim])
        models.append(gpr)
    return StaticGPR(x_scaler=x_scaler, y_scaler=y_scaler, models=models, notes=notes)


def predict_delta(model: StaticGPR, x: np.ndarray) -> np.ndarray:
    x_s = model.x_scaler.transform(x)
    pred_s = np.column_stack([gpr.predict(x_s) for gpr in model.models])
    return model.y_scaler.inverse_transform(pred_s)


def metric_summary(errors: np.ndarray) -> dict[str, float]:
    errors = np.asarray(errors, dtype=float)
    return {
        "median": float(np.nanmedian(errors)),
        "p95": float(np.nanpercentile(errors, 95.0)),
        "rmse": float(math.sqrt(np.nanmean(errors**2))),
    }


def print_summary(before: np.ndarray, after: np.ndarray) -> None:
    before_s = metric_summary(before)
    after_s = metric_summary(after)
    print("\nROTO dynamic transfer evaluation")
    print("-" * 72)
    print(f"{'Condition':28s} {'Median (mm)':>12s} {'P95 (mm)':>12s} {'RMSE (mm)':>12s}")
    print("-" * 72)
    print(f"{'Before compensation':28s} {before_s['median']:12.3f} {before_s['p95']:12.3f} {before_s['rmse']:12.3f}")
    print(f"{'After static GPR':28s} {after_s['median']:12.3f} {after_s['p95']:12.3f} {after_s['rmse']:12.3f}")
    print("-" * 72)
    print(
        "Delta after-before: "
        f"median={after_s['median'] - before_s['median']:+.3f} mm, "
        f"P95={after_s['p95'] - before_s['p95']:+.3f} mm, "
        f"RMSE={after_s['rmse'] - before_s['rmse']:+.3f} mm"
    )


def plot_cdf(before: np.ndarray, after: np.ndarray, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    plt.figure(figsize=(7.2, 4.8))
    for label, values in [
        ("Before compensation", before),
        ("After static GPR", after),
    ]:
        values = np.sort(np.asarray(values, dtype=float))
        cdf = np.arange(1, len(values) + 1, dtype=float) / len(values)
        plt.step(values, cdf, where="post", linewidth=2.0, label=label)
    plt.xlabel("3D Error (mm)")
    plt.ylabel("Cumulative Probability")
    plt.title("ROTO Dynamic Transfer: Static GPR Compensation")
    plt.grid(True, alpha=0.3)
    plt.legend(loc="lower right")
    plt.tight_layout()
    plt.savefig(output, dpi=180)
    plt.close()
    print(f"Saved CDF figure: {output}")


def main() -> int:
    args = parse_args()
    if args.length_scale_lower_mm <= 0 or args.length_scale_upper_mm <= 0:
        raise ValueError("GPR length-scale bounds must be positive.")
    if args.length_scale_lower_mm >= args.length_scale_upper_mm:
        raise ValueError("GPR lower length-scale bound must be less than upper bound.")

    static_df, x_static, y_static = load_static(args.static_csv, args.version)
    print(f"Loaded static training rows: {len(static_df)} from {args.static_csv}")
    print(f"Training final static GPR on all {len(static_df)} points...")
    gpr = train_static_gpr(
        x_static=x_static,
        y_comp=y_static,
        length_scale_lower_mm=args.length_scale_lower_mm,
        length_scale_upper_mm=args.length_scale_upper_mm,
        gpr_restarts=args.gpr_restarts,
        random_state=args.random_state,
    )
    if gpr.notes:
        print("\nGPR training notes/warnings:")
        for note in gpr.notes[:20]:
            print(f"  - {note}")
        if len(gpr.notes) > 20:
            print(f"  ... {len(gpr.notes) - 20} additional notes suppressed")

    _, x_dyn, truth_dyn, before_err = load_dynamic(args.dynamic_csv, args.max_raw_error_mm)
    pred_delta = predict_delta(gpr, x_dyn)
    corrected = x_dyn + pred_delta
    after_err = np.linalg.norm(corrected - truth_dyn, axis=1)

    print_summary(before_err, after_err)
    plot_cdf(before_err, after_err, args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
