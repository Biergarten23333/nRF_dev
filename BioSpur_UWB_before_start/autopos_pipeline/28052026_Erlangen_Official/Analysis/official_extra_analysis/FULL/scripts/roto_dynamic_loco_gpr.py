#!/usr/bin/env python3
"""Leave-one-capture-out dynamic spatial GPR compensation for ROTO samples.

This test asks whether the dynamic trajectory contains a reusable coordinate-only
spatial error field.  It avoids temporal leakage by holding out one complete ROTO
capture at a time.

Exact sklearn GPR is cubic in the number of training samples, so each training
split is first aggregated into spatial voxels.  The model still uses the same
coordinate-only configuration as the static GPR tests:

  StandardScaler(X), StandardScaler(y),
  ConstantKernel * Matern(nu=1.5, length_scale bounds 300..5000 mm) + WhiteKernel.
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
        "Missing required dependency: scikit-learn. Run with /tmp/biospur_static_gpr_venv/bin/python."
    ) from exc


SCRIPT_PATH = Path(__file__).resolve()
FULL_DIR = SCRIPT_PATH.parents[1]
ANALYSIS_DIR = FULL_DIR.parent
DEFAULT_DYNAMIC = (
    ANALYSIS_DIR
    / "FULL_V5_roto_deepdive"
    / "tables"
    / "roto_v5_dloo_samples.csv"
)
DEFAULT_OUTPUT = FULL_DIR / "figs" / "roto_dynamic_loco_gpr_cdf.png"

X_COLS = ["x", "y", "z"]
TRUTH_COLS = ["truth_x", "truth_y", "truth_z"]


@dataclass
class FoldResult:
    capture_id: str
    train_frames: int
    train_voxels: int
    test_frames: int
    before_median: float
    before_p95: float
    before_rmse: float
    after_median: float
    after_p95: float
    after_rmse: float


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run ROTO dynamic leave-one-capture-out coordinate-only GPR."
    )
    parser.add_argument("--dynamic-csv", type=Path, default=DEFAULT_DYNAMIC)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--max-raw-error-mm", type=float, default=500.0)
    parser.add_argument(
        "--voxel-size-mm",
        type=float,
        default=100.0,
        help="Spatial voxel size used to aggregate training frames before exact GPR.",
    )
    parser.add_argument(
        "--gpr-restarts",
        type=int,
        default=0,
        help="Optimizer restarts per scalar GPR. 0 keeps the 17-fold test tractable.",
    )
    parser.add_argument("--length-scale-lower-mm", type=float, default=300.0)
    parser.add_argument("--length-scale-upper-mm", type=float, default=5000.0)
    parser.add_argument("--random-state", type=int, default=42)
    return parser.parse_args()


def require_columns(df: pd.DataFrame, cols: list[str]) -> None:
    missing = [c for c in cols if c not in df.columns]
    if missing:
        raise ValueError(f"Dynamic CSV is missing required columns: {missing}")


def load_dynamic(dynamic_csv: Path, max_raw_error_mm: float) -> pd.DataFrame:
    if not dynamic_csv.exists():
        raise FileNotFoundError(f"Dynamic CSV not found: {dynamic_csv}")
    df = pd.read_csv(dynamic_csv)
    require_columns(df, ["capture_id", *X_COLS, *TRUTH_COLS, "err3d_mm"])
    numeric = X_COLS + TRUTH_COLS + ["err3d_mm"]
    for col in numeric:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    mask = np.isfinite(df[numeric]).all(axis=1)
    mask &= df["err3d_mm"] <= max_raw_error_mm
    cleaned = df[mask].copy().reset_index(drop=True)
    if cleaned.empty:
        raise ValueError("No rows remain after dynamic cleaning.")
    print(
        f"Loaded dynamic rows: raw={len(df)}, kept={len(cleaned)}, "
        f"removed={len(df) - len(cleaned)} using err3d_mm <= {max_raw_error_mm:.1f} mm."
    )
    print(f"Captures: {', '.join(map(str, sorted(cleaned['capture_id'].unique())))}")
    return cleaned


def aggregate_training_voxels(df: pd.DataFrame, voxel_size_mm: float) -> tuple[np.ndarray, np.ndarray]:
    if voxel_size_mm <= 0:
        raise ValueError("voxel_size_mm must be positive.")
    x = df[X_COLS].to_numpy(dtype=float)
    truth = df[TRUTH_COLS].to_numpy(dtype=float)
    y = truth - x
    keys = np.floor(x / voxel_size_mm).astype(np.int64)
    tmp = pd.DataFrame(
        {
            "kx": keys[:, 0],
            "ky": keys[:, 1],
            "kz": keys[:, 2],
            "x": x[:, 0],
            "y": x[:, 1],
            "z": x[:, 2],
            "dx": y[:, 0],
            "dy": y[:, 1],
            "dz": y[:, 2],
        }
    )
    agg = tmp.groupby(["kx", "ky", "kz"], sort=False).mean(numeric_only=True).reset_index()
    x_agg = agg[["x", "y", "z"]].to_numpy(dtype=float)
    y_agg = agg[["dx", "dy", "dz"]].to_numpy(dtype=float)
    return x_agg, y_agg


def make_kernel(bounds_scaled: list[tuple[float, float]]):
    return (
        ConstantKernel(1.0, constant_value_bounds=(1e-3, 1e3))
        * Matern(
            length_scale=np.ones(3, dtype=float),
            length_scale_bounds=bounds_scaled,
            nu=1.5,
        )
        + WhiteKernel(noise_level=1e-2, noise_level_bounds=(1e-6, 1e2))
    )


def fit_predict_gpr(
    x_train: np.ndarray,
    y_train: np.ndarray,
    x_test: np.ndarray,
    args: argparse.Namespace,
    fold_seed: int,
) -> tuple[np.ndarray, list[str]]:
    x_scaler = StandardScaler()
    y_scaler = StandardScaler()
    x_train_s = x_scaler.fit_transform(x_train)
    x_test_s = x_scaler.transform(x_test)
    y_train_s = y_scaler.fit_transform(y_train)

    x_scale = np.asarray(x_scaler.scale_, dtype=float)
    lower = np.maximum(args.length_scale_lower_mm / x_scale, 1e-6)
    upper = np.maximum(args.length_scale_upper_mm / x_scale, lower * 1.001)
    bounds_scaled = [(float(lo), float(hi)) for lo, hi in zip(lower, upper)]

    pred_s = np.zeros((len(x_test), 3), dtype=float)
    notes: list[str] = []
    for dim in range(3):
        kernel = make_kernel(bounds_scaled)
        gpr = GaussianProcessRegressor(
            kernel=kernel,
            alpha=1e-8,
            normalize_y=False,
            n_restarts_optimizer=args.gpr_restarts,
            random_state=fold_seed + dim,
        )
        try:
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always", ConvergenceWarning)
                gpr.fit(x_train_s, y_train_s[:, dim])
            for warning in caught:
                if issubclass(warning.category, ConvergenceWarning):
                    notes.append(f"dim {dim}: {warning.message}")
            pred_s[:, dim] = gpr.predict(x_test_s)
        except Exception as exc:
            notes.append(f"dim {dim}: optimized GPR failed ({exc!r}); retrying fixed kernel")
            fallback = GaussianProcessRegressor(
                kernel=kernel,
                alpha=1e-6,
                optimizer=None,
                normalize_y=False,
            )
            fallback.fit(x_train_s, y_train_s[:, dim])
            pred_s[:, dim] = fallback.predict(x_test_s)
    return y_scaler.inverse_transform(pred_s), notes


def errors_mm(x: np.ndarray, truth: np.ndarray) -> np.ndarray:
    return np.linalg.norm(x - truth, axis=1)


def summarize(values: np.ndarray) -> dict[str, float]:
    values = np.asarray(values, dtype=float)
    return {
        "median": float(np.nanmedian(values)),
        "p95": float(np.nanpercentile(values, 95.0)),
        "rmse": float(math.sqrt(np.nanmean(values**2))),
    }


def plot_cdf(before: np.ndarray, after: np.ndarray, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    plt.figure(figsize=(7.2, 4.8))
    for label, values in [("Before compensation", before), ("After dynamic LOCO GPR", after)]:
        vals = np.sort(np.asarray(values, dtype=float))
        cdf = np.arange(1, len(vals) + 1, dtype=float) / len(vals)
        plt.step(vals, cdf, where="post", linewidth=2.0, label=label)
    plt.xlabel("3D Error (mm)")
    plt.ylabel("Cumulative Probability")
    plt.title("ROTO Dynamic Leave-One-Capture-Out GPR")
    plt.grid(True, alpha=0.3)
    plt.legend(loc="lower right")
    plt.tight_layout()
    plt.savefig(output, dpi=180)
    plt.close()
    print(f"Saved CDF figure: {output}")


def main() -> int:
    args = parse_args()
    if args.length_scale_lower_mm <= 0 or args.length_scale_upper_mm <= 0:
        raise ValueError("Length-scale bounds must be positive.")
    if args.length_scale_lower_mm >= args.length_scale_upper_mm:
        raise ValueError("Lower length-scale bound must be less than upper bound.")

    df = load_dynamic(args.dynamic_csv, args.max_raw_error_mm)
    captures = sorted(df["capture_id"].unique())
    all_before: list[np.ndarray] = []
    all_after: list[np.ndarray] = []
    fold_rows: list[FoldResult] = []
    all_notes: list[str] = []

    print(
        f"Running leave-one-capture-out GPR with voxel_size={args.voxel_size_mm:.1f} mm, "
        f"gpr_restarts={args.gpr_restarts}."
    )

    for fold_idx, cap in enumerate(captures, start=1):
        train_df = df[df["capture_id"] != cap]
        test_df = df[df["capture_id"] == cap]
        x_train, y_train = aggregate_training_voxels(train_df, args.voxel_size_mm)
        x_test = test_df[X_COLS].to_numpy(dtype=float)
        truth_test = test_df[TRUTH_COLS].to_numpy(dtype=float)

        print(
            f"[{fold_idx:02d}/{len(captures)}] holdout={cap}: "
            f"train_frames={len(train_df)}, train_voxels={len(x_train)}, test_frames={len(test_df)}"
        )
        pred_delta, notes = fit_predict_gpr(
            x_train=x_train,
            y_train=y_train,
            x_test=x_test,
            args=args,
            fold_seed=args.random_state + 1000 * fold_idx,
        )
        for note in notes:
            all_notes.append(f"{cap}: {note}")

        before = errors_mm(x_test, truth_test)
        after = errors_mm(x_test + pred_delta, truth_test)
        all_before.append(before)
        all_after.append(after)
        bs = summarize(before)
        as_ = summarize(after)
        fold_rows.append(
            FoldResult(
                capture_id=str(cap),
                train_frames=len(train_df),
                train_voxels=len(x_train),
                test_frames=len(test_df),
                before_median=bs["median"],
                before_p95=bs["p95"],
                before_rmse=bs["rmse"],
                after_median=as_["median"],
                after_p95=as_["p95"],
                after_rmse=as_["rmse"],
            )
        )

    before_all = np.concatenate(all_before)
    after_all = np.concatenate(all_after)
    bs = summarize(before_all)
    as_ = summarize(after_all)

    print("\nROTO dynamic leave-one-capture-out summary")
    print("-" * 72)
    print(f"{'Condition':28s} {'Median (mm)':>12s} {'P95 (mm)':>12s} {'RMSE (mm)':>12s}")
    print("-" * 72)
    print(f"{'Before compensation':28s} {bs['median']:12.3f} {bs['p95']:12.3f} {bs['rmse']:12.3f}")
    print(f"{'After dynamic LOCO GPR':28s} {as_['median']:12.3f} {as_['p95']:12.3f} {as_['rmse']:12.3f}")
    print("-" * 72)
    print(
        "Delta after-before: "
        f"median={as_['median'] - bs['median']:+.3f} mm, "
        f"P95={as_['p95'] - bs['p95']:+.3f} mm, "
        f"RMSE={as_['rmse'] - bs['rmse']:+.3f} mm"
    )

    print("\nPer-capture median deltas (after-before, mm):")
    for row in fold_rows:
        print(
            f"  {row.capture_id}: {row.after_median - row.before_median:+.3f} "
            f"(before {row.before_median:.3f}, after {row.after_median:.3f})"
        )

    if all_notes:
        print("\nGPR fitting notes/warnings:")
        for note in all_notes[:30]:
            print(f"  - {note}")
        if len(all_notes) > 30:
            print(f"  ... {len(all_notes) - 30} additional notes suppressed")

    plot_cdf(before_all, after_all, args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
