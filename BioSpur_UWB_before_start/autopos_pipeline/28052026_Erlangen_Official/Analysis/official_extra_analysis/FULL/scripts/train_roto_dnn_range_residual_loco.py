#!/usr/bin/env python3
from __future__ import annotations

import argparse
import importlib.util
import json
import math
import os
import random
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from typing import Any

os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")
os.environ.setdefault("NUMEXPR_NUM_THREADS", "1")

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import torch
from scipy.optimize import least_squares
from sklearn.preprocessing import StandardScaler
from torch import nn
from torch.utils.data import DataLoader, TensorDataset


ANCHOR_COUNT = 8
DEFAULT_REPRESENTATIVE_FOLDS = ["R01", "R05", "R10", "R14", "R17"]


def find_base() -> Path:
    return Path(__file__).resolve().parents[4]


BASE = find_base()
ANALYSIS = BASE / "Analysis/official_extra_analysis"
FULL = ANALYSIS / "FULL"
TENSOR_PATH = FULL / "tables/roto_dnn_feature_tensor.npz"
FRAME_INDEX_PATH = FULL / "tables/roto_dnn_frame_index.csv"
LAYOUT_PATH = BASE / "solver/outputs/v1_to_v4_io_field_check/v5-commonmode/layout.json"
ROTO_DEEP_SCRIPT = ANALYSIS / "FULL_V5_roto_deepdive/scripts/run_roto_deepdive.py"
FIG_PATH = FULL / "figs/roto_dnn_range_residual_loco_cdf.png"
RESULTS_PATH = FULL / "tables/roto_dnn_range_residual_loco_results.csv"


def load_module(path: Path, name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load module from {path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def load_aligned_anchors() -> np.ndarray:
    # The layout.json is intentionally touched here so failures are explicit if
    # the requested layout artifact is missing. The coordinate transform itself
    # is delegated to the existing ROTO deep-dive context.
    data = json.loads(LAYOUT_PATH.read_text(encoding="utf-8"))
    if len(data.get("anchors", [])) != ANCHOR_COUNT:
        raise ValueError(f"expected {ANCHOR_COUNT} anchors in {LAYOUT_PATH}")
    roto = load_module(ROTO_DEEP_SCRIPT, "roto_deep_for_dnn_loco")
    ctx = roto.build_context()
    anchors = np.asarray(ctx["anchors_vicon"], dtype=np.float64)
    if anchors.shape != (ANCHOR_COUNT, 3):
        raise ValueError(f"unexpected aligned anchor shape: {anchors.shape}")
    return anchors


class RangeResidualCNN(nn.Module):
    def __init__(self, in_channels: int) -> None:
        super().__init__()
        self.net = nn.Sequential(
            nn.Conv1d(in_channels=in_channels, out_channels=32, kernel_size=3, padding=1),
            nn.ReLU(),
            nn.Conv1d(in_channels=32, out_channels=64, kernel_size=3, padding=1),
            nn.ReLU(),
            nn.Flatten(),
            nn.Linear(64 * ANCHOR_COUNT, 256),
            nn.ReLU(),
            nn.Dropout(0.2),
            nn.Linear(256, ANCHOR_COUNT),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        # Input: (batch, anchors, features). Conv1d expects (batch, features, anchors).
        return self.net(x.transpose(1, 2))


def set_seed(seed: int) -> None:
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    torch.cuda.manual_seed_all(seed)


def stats(err: np.ndarray) -> dict[str, float]:
    arr = np.asarray(err, dtype=np.float64)
    arr = arr[np.isfinite(arr)]
    if arr.size == 0:
        return {"median_mm": float("nan"), "p95_mm": float("nan"), "rmse_mm": float("nan"), "n": 0}
    return {
        "median_mm": float(np.percentile(arr, 50)),
        "p95_mm": float(np.percentile(arr, 95)),
        "rmse_mm": float(math.sqrt(np.mean(arr * arr))),
        "n": int(arr.size),
    }


def masked_mse(pred: torch.Tensor, target: torch.Tensor, mask: torch.Tensor) -> torch.Tensor:
    diff2 = (pred - target).pow(2) * mask
    denom = mask.sum().clamp_min(1.0)
    return diff2.sum() / denom


def selected_feature_indices(feature_names: list[str], mode: str) -> list[int]:
    if mode == "all6":
        names = ["range_mm", "quality_percent", "geo_dist_mm", "uwb_x", "uwb_y", "uwb_z"]
    elif mode == "no_geo":
        names = ["range_mm", "quality_percent", "uwb_x", "uwb_y", "uwb_z"]
    else:
        raise ValueError(f"unknown feature mode: {mode}")
    return [feature_names.index(name) for name in names]


def fit_transform_features(
    X: np.ndarray,
    train_mask: np.ndarray,
    test_mask: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, StandardScaler]:
    n_features = X.shape[-1]
    scaler = StandardScaler()
    flat_train = X[train_mask].reshape(-1, n_features)
    finite_rows = np.isfinite(flat_train).all(axis=1)
    if finite_rows.sum() == 0:
        raise ValueError("no finite training feature rows available for StandardScaler")
    scaler.fit(flat_train[finite_rows])

    def transform(part: np.ndarray) -> np.ndarray:
        shape = part.shape
        flat = part.reshape(-1, n_features)
        out = scaler.transform(flat).reshape(shape).astype(np.float32)
        # Missing anchors become feature-wise mean after scaling.
        return np.nan_to_num(out, nan=0.0, posinf=0.0, neginf=0.0)

    return transform(X[train_mask]), transform(X[test_mask]), scaler


def train_fold(
    X: np.ndarray,
    Y: np.ndarray,
    train_mask: np.ndarray,
    test_mask: np.ndarray,
    device: torch.device,
    epochs: int,
    batch_size: int,
    lr: float,
    seed: int,
) -> tuple[np.ndarray, list[float]]:
    set_seed(seed)
    X_train, X_test, _scaler = fit_transform_features(X, train_mask, test_mask)
    Y_train = Y[train_mask].astype(np.float32)
    Y_test = Y[test_mask].astype(np.float32)
    train_valid = np.isfinite(Y_train).astype(np.float32)

    y_fill = np.nan_to_num(Y_train, nan=0.0, posinf=0.0, neginf=0.0).astype(np.float32)
    ds = TensorDataset(
        torch.from_numpy(X_train),
        torch.from_numpy(y_fill),
        torch.from_numpy(train_valid),
    )
    loader = DataLoader(ds, batch_size=batch_size, shuffle=True, num_workers=0, pin_memory=torch.cuda.is_available())

    model: nn.Module = RangeResidualCNN(in_channels=X.shape[-1])
    model = model.to(device)
    if torch.cuda.device_count() > 1 and device.type == "cuda":
        model = nn.DataParallel(model)
    opt = torch.optim.Adam(model.parameters(), lr=lr)

    losses: list[float] = []
    model.train()
    for _epoch in range(epochs):
        running = 0.0
        weight = 0
        for xb, yb, mb in loader:
            xb = xb.to(device, non_blocking=True)
            yb = yb.to(device, non_blocking=True)
            mb = mb.to(device, non_blocking=True)
            opt.zero_grad(set_to_none=True)
            pred = model(xb)
            loss = masked_mse(pred, yb, mb)
            loss.backward()
            opt.step()
            n = int(mb.sum().detach().cpu().item())
            running += float(loss.detach().cpu().item()) * max(n, 1)
            weight += max(n, 1)
        losses.append(running / max(weight, 1))

    model.eval()
    preds: list[np.ndarray] = []
    test_ds = TensorDataset(torch.from_numpy(X_test))
    test_loader = DataLoader(test_ds, batch_size=batch_size * 2, shuffle=False, num_workers=0, pin_memory=torch.cuda.is_available())
    with torch.no_grad():
        for (xb,) in test_loader:
            xb = xb.to(device, non_blocking=True)
            preds.append(model(xb).detach().cpu().numpy())
    pred_test = np.concatenate(preds, axis=0).astype(np.float32)
    pred_test[~np.isfinite(Y_test)] = np.nan
    return pred_test, losses


def solve_position(anchors: np.ndarray, ranges: np.ndarray, x0: np.ndarray) -> np.ndarray:
    valid = np.isfinite(ranges) & (ranges > 0.0)
    if int(valid.sum()) < 4:
        return np.array([np.nan, np.nan, np.nan], dtype=np.float64)
    a = anchors[valid]
    r = ranges[valid].astype(np.float64)
    if not np.isfinite(x0).all():
        x0 = a.mean(axis=0)

    def residual(p: np.ndarray) -> np.ndarray:
        return np.linalg.norm(p[None, :] - a, axis=1) - r

    try:
        res = least_squares(
            residual,
            x0.astype(np.float64),
            method="trf",
            loss="linear",
            max_nfev=80,
            ftol=1e-7,
            xtol=1e-7,
            gtol=1e-7,
        )
        if not res.success and not np.isfinite(res.x).all():
            return np.array([np.nan, np.nan, np.nan], dtype=np.float64)
        return res.x.astype(np.float64)
    except Exception:
        return np.array([np.nan, np.nan, np.nan], dtype=np.float64)


def solve_chunk(
    anchors: np.ndarray,
    ranges: np.ndarray,
    initial: np.ndarray,
    truth: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    solved = np.empty((len(ranges), 3), dtype=np.float64)
    err = np.empty(len(ranges), dtype=np.float64)
    for i in range(len(ranges)):
        p = solve_position(anchors, ranges[i], initial[i])
        solved[i] = p
        err[i] = np.linalg.norm(p - truth[i]) if np.isfinite(p).all() else np.nan
    return solved, err


def solve_many(
    anchors: np.ndarray,
    ranges: np.ndarray,
    initial: np.ndarray,
    truth: np.ndarray,
    workers: int,
    chunk_size: int,
) -> tuple[np.ndarray, np.ndarray]:
    n = len(ranges)
    solved = np.empty((n, 3), dtype=np.float64)
    err = np.empty(n, dtype=np.float64)
    if workers <= 1 or n <= chunk_size:
        return solve_chunk(anchors, ranges, initial, truth)
    futures = []
    with ProcessPoolExecutor(max_workers=workers) as ex:
        for start in range(0, n, chunk_size):
            end = min(start + chunk_size, n)
            fut = ex.submit(solve_chunk, anchors, ranges[start:end], initial[start:end], truth[start:end])
            futures.append((start, end, fut))
        for start, end, fut in futures:
            p, e = fut.result()
            solved[start:end] = p
            err[start:end] = e
    return solved, err


def make_cdf(err: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    arr = np.asarray(err, dtype=np.float64)
    arr = np.sort(arr[np.isfinite(arr)])
    if arr.size == 0:
        return arr, arr
    y = np.arange(1, arr.size + 1, dtype=np.float64) / arr.size
    return arr, y


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Train LOCO DNN range-residual compensator and re-solve ROTO positions.")
    parser.add_argument("--folds", default="representative", help="'representative', 'all', or comma list like R01,R05")
    parser.add_argument("--feature-mode", choices=["no_geo", "all6"], default="no_geo")
    parser.add_argument("--epochs", type=int, default=50)
    parser.add_argument("--batch-size", type=int, default=512)
    parser.add_argument("--lr", type=float, default=1e-3)
    parser.add_argument("--seed", type=int, default=20260622)
    parser.add_argument("--solve-workers", type=int, default=8)
    parser.add_argument("--solve-chunk-size", type=int, default=128)
    parser.add_argument("--max-test-frames", type=int, default=0, help="Debug cap per fold; 0 means full test capture.")
    parser.add_argument("--fig", type=Path, default=FIG_PATH)
    parser.add_argument("--results", type=Path, default=RESULTS_PATH)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    started = time.perf_counter()
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    print(f"python: {sys.executable}")
    print(f"torch: {torch.__version__}; cuda={torch.cuda.is_available()}; cuda_devices={torch.cuda.device_count()}")
    if torch.cuda.is_available():
        print("cuda names:", [torch.cuda.get_device_name(i) for i in range(torch.cuda.device_count())])
    print(f"device: {device}; DataParallel enabled={torch.cuda.is_available() and torch.cuda.device_count() > 1}")

    z = np.load(TENSOR_PATH, allow_pickle=True)
    X_full = z["X"].astype(np.float32)
    Y = z["Y"].astype(np.float32)
    feature_names = [str(x) for x in z["feature_names"].tolist()]
    feature_idx = selected_feature_indices(feature_names, args.feature_mode)
    selected_names = [feature_names[i] for i in feature_idx]
    X = X_full[:, :, feature_idx].astype(np.float32)
    frames = pd.read_csv(FRAME_INDEX_PATH)
    require_cols = ["capture_id", "x", "y", "z", "truth_x", "truth_y", "truth_z"]
    missing = [c for c in require_cols if c not in frames.columns]
    if missing:
        raise ValueError(f"frame index missing columns: {missing}")
    if len(frames) != len(X):
        raise ValueError(f"frame index length {len(frames)} != tensor length {len(X)}")

    captures = sorted(frames["capture_id"].astype(str).unique().tolist())
    if args.folds == "all":
        folds = captures
    elif args.folds == "representative":
        folds = [c for c in DEFAULT_REPRESENTATIVE_FOLDS if c in captures]
    else:
        folds = [c.strip() for c in args.folds.split(",") if c.strip()]
    if not folds:
        raise ValueError("no folds selected")

    if args.feature_mode == "all6":
        print("WARNING: feature-mode all6 includes geo_dist_mm, and Y = range_mm - geo_dist_mm. This is an oracle/leaky diagnostic.")
    else:
        print("feature-mode no_geo: dropping geo_dist_mm to avoid label leakage.")
    print("selected features:", selected_names)
    print("folds:", folds)
    print(f"tensor X selected shape={X.shape}; Y shape={Y.shape}")

    anchors = load_aligned_anchors()
    initial_all = frames[["x", "y", "z"]].to_numpy(dtype=np.float64)
    truth_all = frames[["truth_x", "truth_y", "truth_z"]].to_numpy(dtype=np.float64)
    raw_ranges_all = X_full[:, :, feature_names.index("range_mm")].astype(np.float64)

    fold_rows: list[dict[str, Any]] = []
    all_existing_err: list[np.ndarray] = []
    all_baseline_err: list[np.ndarray] = []
    all_corrected_err: list[np.ndarray] = []

    for fold_i, capture in enumerate(folds, start=1):
        fold_start = time.perf_counter()
        test_mask = frames["capture_id"].astype(str).to_numpy() == capture
        train_mask = ~test_mask
        if args.max_test_frames and int(test_mask.sum()) > args.max_test_frames:
            idx = np.flatnonzero(test_mask)[: args.max_test_frames]
            test_mask = np.zeros_like(test_mask, dtype=bool)
            test_mask[idx] = True
            train_mask = ~test_mask

        print()
        print(
            f"[fold {fold_i}/{len(folds)}] holdout={capture}; train={int(train_mask.sum()):,}; "
            f"test={int(test_mask.sum()):,}; epochs={args.epochs}"
        )
        pred_residual, losses = train_fold(
            X,
            Y,
            train_mask,
            test_mask,
            device,
            epochs=args.epochs,
            batch_size=args.batch_size,
            lr=args.lr,
            seed=args.seed + fold_i,
        )
        print(f"  train loss: first={losses[0]:.3f}, last={losses[-1]:.3f}")

        test_indices = np.flatnonzero(test_mask)
        raw_ranges = raw_ranges_all[test_indices].copy()
        corrected_ranges = raw_ranges - pred_residual.astype(np.float64)
        # Keep original NaN mask and reject non-physical corrected distances.
        corrected_ranges[~np.isfinite(raw_ranges)] = np.nan
        corrected_ranges[corrected_ranges <= 0.0] = np.nan

        initial = initial_all[test_indices]
        truth = truth_all[test_indices]
        existing_err = np.linalg.norm(initial - truth, axis=1)

        print("  solving baseline raw ranges...")
        _base_pos, baseline_err = solve_many(
            anchors,
            raw_ranges,
            initial,
            truth,
            workers=args.solve_workers,
            chunk_size=args.solve_chunk_size,
        )
        print("  solving corrected ranges...")
        _corr_pos, corrected_err = solve_many(
            anchors,
            corrected_ranges,
            initial,
            truth,
            workers=args.solve_workers,
            chunk_size=args.solve_chunk_size,
        )

        s_existing = stats(existing_err)
        s_base = stats(baseline_err)
        s_corr = stats(corrected_err)
        fold_rows.append(
            {
                "capture_id": capture,
                "n_test": int(test_mask.sum()),
                "existing_xyz_median": s_existing["median_mm"],
                "raw_resolve_median": s_base["median_mm"],
                "dnn_corrected_median": s_corr["median_mm"],
                "raw_resolve_p95": s_base["p95_mm"],
                "dnn_corrected_p95": s_corr["p95_mm"],
                "raw_resolve_rmse": s_base["rmse_mm"],
                "dnn_corrected_rmse": s_corr["rmse_mm"],
                "median_improvement": s_base["median_mm"] - s_corr["median_mm"],
                "elapsed_s": time.perf_counter() - fold_start,
            }
        )
        all_existing_err.append(existing_err)
        all_baseline_err.append(baseline_err)
        all_corrected_err.append(corrected_err)
        print(
            f"  median existing_xyz={s_existing['median_mm']:.3f} mm; "
            f"raw_resolve={s_base['median_mm']:.3f} mm; "
            f"DNN_corrected={s_corr['median_mm']:.3f} mm; "
            f"delta={s_base['median_mm'] - s_corr['median_mm']:.3f} mm"
        )

    existing_all = np.concatenate(all_existing_err)
    baseline_all = np.concatenate(all_baseline_err)
    corrected_all = np.concatenate(all_corrected_err)
    summary_rows = [
        {"condition": "Existing table x/y/z", **stats(existing_all)},
        {"condition": "Raw ranges re-solved", **stats(baseline_all)},
        {"condition": "DNN corrected ranges re-solved", **stats(corrected_all)},
    ]

    print()
    print("Per-fold median summary:")
    with pd.option_context("display.max_columns", None, "display.width", 220, "display.float_format", "{:.3f}".format):
        print(pd.DataFrame(fold_rows).to_string(index=False))
    print()
    print("Aggregate summary:")
    with pd.option_context("display.max_columns", None, "display.width", 160, "display.float_format", "{:.3f}".format):
        print(pd.DataFrame(summary_rows).to_string(index=False))

    args.results.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(fold_rows).to_csv(args.results, index=False)
    print(f"saved fold results: {args.results}")

    args.fig.parent.mkdir(parents=True, exist_ok=True)
    plt.figure(figsize=(10, 6))
    for label, err in [
        ("Existing table x/y/z", existing_all),
        ("Raw ranges re-solved", baseline_all),
        ("DNN corrected ranges re-solved", corrected_all),
    ]:
        xs, ys = make_cdf(err)
        plt.plot(xs, ys, label=label, linewidth=2)
    plt.xlabel("3D Error (mm)")
    plt.ylabel("Cumulative Probability")
    plt.title(f"ROTO DNN Range Residual LOCO ({args.feature_mode}, folds={len(folds)})")
    plt.grid(True, alpha=0.35)
    plt.legend()
    plt.tight_layout()
    plt.savefig(args.fig, dpi=160)
    plt.close()
    print(f"saved CDF: {args.fig}")
    print(f"total elapsed_s={time.perf_counter() - started:.1f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
