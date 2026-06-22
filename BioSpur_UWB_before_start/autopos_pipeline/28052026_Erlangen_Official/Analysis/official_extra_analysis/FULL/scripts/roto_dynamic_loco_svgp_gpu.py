#!/usr/bin/env python3
"""GPU sparse variational GP for ROTO leave-one-capture-out compensation.

This is the high-resolution counterpart to roto_dynamic_loco_gpr.py.  It keeps
the same leakage-safe split (hold out one complete capture), but trains on the
full dynamic frame set for each fold instead of voxel-aggregating the training
data for exact CPU GPR.

Implementation details:
  * GPyTorch sparse variational GP, mini-batch ELBO.
  * Batched independent outputs for dx, dy, dz in one model.
  * One worker per CUDA device; captures are split round-robin across GPUs.
  * Inputs and targets are standardized per fold.
  * Matern nu=1.5 kernel with physical length-scale bounds converted to the
    standardized coordinate system.
"""

from __future__ import annotations

import argparse
import math
import os
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
import multiprocessing as mp

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

try:
    import torch
    import gpytorch
    from sklearn.preprocessing import StandardScaler
except ImportError as exc:  # pragma: no cover
    raise SystemExit(
        "Missing required dependency. Run with /tmp/biospur_static_gpr_venv/bin/python "
        "after installing gpytorch."
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
DEFAULT_OUTPUT = FULL_DIR / "figs" / "roto_dynamic_loco_svgp_gpu_cdf.png"

X_COLS = ["x", "y", "z"]
TRUTH_COLS = ["truth_x", "truth_y", "truth_z"]
OUTPUT_DIMS = 3


class BatchIndependentSVGP(gpytorch.models.ApproximateGP):
    def __init__(self, inducing_points: torch.Tensor, lengthscale_bounds: tuple[float, float]):
        batch_shape = torch.Size([OUTPUT_DIMS])
        m = inducing_points.shape[-2]
        variational_distribution = gpytorch.variational.CholeskyVariationalDistribution(
            m, batch_shape=batch_shape
        )
        variational_strategy = gpytorch.variational.VariationalStrategy(
            self,
            inducing_points,
            variational_distribution,
            learn_inducing_locations=True,
        )
        super().__init__(variational_strategy)
        self.mean_module = gpytorch.means.ConstantMean(batch_shape=batch_shape)
        self.covar_module = gpytorch.kernels.ScaleKernel(
            gpytorch.kernels.MaternKernel(
                nu=1.5,
                ard_num_dims=3,
                batch_shape=batch_shape,
                lengthscale_constraint=gpytorch.constraints.Interval(
                    lengthscale_bounds[0], lengthscale_bounds[1]
                ),
            ),
            batch_shape=batch_shape,
        )

    def forward(self, x: torch.Tensor) -> gpytorch.distributions.MultivariateNormal:
        mean_x = self.mean_module(x)
        covar_x = self.covar_module(x)
        return gpytorch.distributions.MultivariateNormal(mean_x, covar_x)


@dataclass
class ArgsSnapshot:
    dynamic_csv: str
    max_raw_error_mm: float
    inducing_points: int
    epochs: int
    batch_size: int
    eval_batch_size: int
    lr: float
    length_scale_lower_mm: float
    length_scale_upper_mm: float
    seed: int


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run GPU sparse variational GP on ROTO leave-one-capture-out splits."
    )
    parser.add_argument("--dynamic-csv", type=Path, default=DEFAULT_DYNAMIC)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--max-raw-error-mm", type=float, default=500.0)
    parser.add_argument("--devices", default="0,1", help="Comma-separated CUDA device ids.")
    parser.add_argument("--inducing-points", type=int, default=1024)
    parser.add_argument("--epochs", type=int, default=60)
    parser.add_argument("--batch-size", type=int, default=4096)
    parser.add_argument("--eval-batch-size", type=int, default=8192)
    parser.add_argument("--lr", type=float, default=0.03)
    parser.add_argument("--length-scale-lower-mm", type=float, default=300.0)
    parser.add_argument("--length-scale-upper-mm", type=float, default=5000.0)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument(
        "--captures",
        default="",
        help="Optional comma-separated capture subset for smoke tests.",
    )
    return parser.parse_args()


def load_dynamic(path: Path, max_raw_error_mm: float) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(f"Dynamic CSV not found: {path}")
    df = pd.read_csv(path)
    required = ["capture_id", *X_COLS, *TRUTH_COLS, "err3d_mm"]
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError(f"Dynamic CSV missing required columns: {missing}")
    for col in [*X_COLS, *TRUTH_COLS, "err3d_mm"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    mask = np.isfinite(df[[*X_COLS, *TRUTH_COLS, "err3d_mm"]]).all(axis=1)
    mask &= df["err3d_mm"] <= max_raw_error_mm
    cleaned = df.loc[mask, ["capture_id", *X_COLS, *TRUTH_COLS, "err3d_mm"]].copy()
    cleaned.reset_index(drop=True, inplace=True)
    if cleaned.empty:
        raise ValueError("No dynamic rows remain after cleaning.")
    return cleaned


def summarize(values: np.ndarray) -> dict[str, float]:
    values = np.asarray(values, dtype=float)
    return {
        "median": float(np.nanmedian(values)),
        "p95": float(np.nanpercentile(values, 95.0)),
        "rmse": float(math.sqrt(np.nanmean(values**2))),
    }


def choose_inducing(x_train_s: np.ndarray, m: int, seed: int, device: torch.device) -> torch.Tensor:
    rng = np.random.default_rng(seed)
    n = len(x_train_s)
    replace = n < m
    idx = rng.choice(n, size=m, replace=replace)
    base = torch.as_tensor(x_train_s[idx], dtype=torch.float32, device=device)
    return base.unsqueeze(0).repeat(OUTPUT_DIMS, 1, 1).contiguous()


def train_predict_fold(
    df: pd.DataFrame,
    holdout_capture: str,
    device_id: int,
    cfg: ArgsSnapshot,
    fold_index: int,
) -> dict:
    torch.set_num_threads(2)
    device = torch.device(f"cuda:{device_id}")
    torch.cuda.set_device(device)
    torch.manual_seed(cfg.seed + 1000 * fold_index)
    np.random.seed(cfg.seed + 1000 * fold_index)

    train_df = df[df["capture_id"] != holdout_capture]
    test_df = df[df["capture_id"] == holdout_capture]
    x_train = train_df[X_COLS].to_numpy(dtype=np.float32)
    truth_train = train_df[TRUTH_COLS].to_numpy(dtype=np.float32)
    y_train = truth_train - x_train
    x_test = test_df[X_COLS].to_numpy(dtype=np.float32)
    truth_test = test_df[TRUTH_COLS].to_numpy(dtype=np.float32)

    x_scaler = StandardScaler()
    y_scaler = StandardScaler()
    x_train_s = x_scaler.fit_transform(x_train).astype(np.float32)
    y_train_s = y_scaler.fit_transform(y_train).astype(np.float32)
    x_test_s = x_scaler.transform(x_test).astype(np.float32)

    lower_scaled = cfg.length_scale_lower_mm / np.asarray(x_scaler.scale_, dtype=np.float32)
    upper_scaled = cfg.length_scale_upper_mm / np.asarray(x_scaler.scale_, dtype=np.float32)
    # GPyTorch Interval is scalar here; use conservative bounds that still enforce
    # the requested physical range order after per-fold standardization.
    lower_bound = float(np.min(lower_scaled))
    upper_bound = float(np.max(upper_scaled))

    inducing = choose_inducing(
        x_train_s=x_train_s,
        m=min(cfg.inducing_points, len(x_train_s)),
        seed=cfg.seed + 1000 * fold_index,
        device=device,
    )
    model = BatchIndependentSVGP(inducing, (lower_bound, upper_bound)).to(device)
    likelihood = gpytorch.likelihoods.GaussianLikelihood(batch_shape=torch.Size([OUTPUT_DIMS])).to(device)

    x_tensor = torch.as_tensor(x_train_s, dtype=torch.float32)
    y_tensor = torch.as_tensor(y_train_s, dtype=torch.float32)
    dataset = torch.utils.data.TensorDataset(x_tensor, y_tensor)
    loader = torch.utils.data.DataLoader(
        dataset,
        batch_size=cfg.batch_size,
        shuffle=True,
        num_workers=0,
        pin_memory=True,
        drop_last=False,
    )

    model.train()
    likelihood.train()
    optimizer = torch.optim.Adam(
        [{"params": model.parameters()}, {"params": likelihood.parameters()}],
        lr=cfg.lr,
    )
    mll = gpytorch.mlls.VariationalELBO(likelihood, model, num_data=len(x_train_s))

    t0 = time.time()
    last_loss = float("nan")
    for _ in range(cfg.epochs):
        loss_accum = 0.0
        n_batches = 0
        for xb, yb in loader:
            xb = xb.to(device, non_blocking=True)
            yb = yb.to(device, non_blocking=True)
            optimizer.zero_grad(set_to_none=True)
            output = model(xb)
            loss = -mll(output, yb.transpose(0, 1)).sum()
            loss.backward()
            optimizer.step()
            loss_accum += float(loss.detach().cpu())
            n_batches += 1
        last_loss = loss_accum / max(n_batches, 1)

    model.eval()
    likelihood.eval()
    preds = []
    x_test_tensor = torch.as_tensor(x_test_s, dtype=torch.float32)
    with torch.no_grad(), gpytorch.settings.fast_pred_var():
        for start in range(0, len(x_test_tensor), cfg.eval_batch_size):
            xb = x_test_tensor[start : start + cfg.eval_batch_size].to(device)
            pred = likelihood(model(xb)).mean.transpose(0, 1).detach().cpu().numpy()
            preds.append(pred)
    pred_s = np.vstack(preds)
    pred_delta = y_scaler.inverse_transform(pred_s)

    before = np.linalg.norm(x_test - truth_test, axis=1)
    after = np.linalg.norm((x_test + pred_delta) - truth_test, axis=1)
    torch.cuda.synchronize(device)
    elapsed = time.time() - t0

    return {
        "capture_id": holdout_capture,
        "device_id": device_id,
        "train_frames": int(len(x_train)),
        "test_frames": int(len(x_test)),
        "inducing_points": int(inducing.shape[-2]),
        "epochs": int(cfg.epochs),
        "last_loss": float(last_loss),
        "elapsed_s": float(elapsed),
        "before": before.astype(float).tolist(),
        "after": after.astype(float).tolist(),
        "before_summary": summarize(before),
        "after_summary": summarize(after),
    }


def worker_run(device_id: int, captures: list[str], df: pd.DataFrame, cfg: ArgsSnapshot) -> list[dict]:
    results = []
    for local_idx, capture in enumerate(captures):
        fold_index = int(capture[1:]) if capture.startswith("R") and capture[1:].isdigit() else local_idx
        result = train_predict_fold(df, capture, device_id, cfg, fold_index)
        print(
            f"GPU{device_id} done {capture}: median "
            f"{result['before_summary']['median']:.3f}->{result['after_summary']['median']:.3f} mm, "
            f"elapsed {result['elapsed_s']:.1f}s",
            flush=True,
        )
        results.append(result)
    return results


def plot_cdf(before: np.ndarray, after: np.ndarray, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    plt.figure(figsize=(7.2, 4.8))
    for label, values in [("Before compensation", before), ("After GPU SVGP LOCO", after)]:
        vals = np.sort(np.asarray(values, dtype=float))
        cdf = np.arange(1, len(vals) + 1, dtype=float) / len(vals)
        plt.step(vals, cdf, where="post", linewidth=2.0, label=label)
    plt.xlabel("3D Error (mm)")
    plt.ylabel("Cumulative Probability")
    plt.title("ROTO Dynamic LOCO GPU Sparse Variational GP")
    plt.grid(True, alpha=0.3)
    plt.legend(loc="lower right")
    plt.tight_layout()
    plt.savefig(output, dpi=180)
    plt.close()
    print(f"Saved CDF figure: {output}")


def parse_devices(text: str) -> list[int]:
    devices = [int(x.strip()) for x in text.split(",") if x.strip()]
    if not devices:
        raise ValueError("At least one CUDA device id is required.")
    return devices


def main() -> int:
    args = parse_args()
    devices = parse_devices(args.devices)
    if not torch.cuda.is_available():
        raise RuntimeError("CUDA is not available to PyTorch.")
    if max(devices) >= torch.cuda.device_count():
        raise RuntimeError(f"Requested devices {devices}, but only {torch.cuda.device_count()} CUDA devices exist.")
    if args.length_scale_lower_mm <= 0 or args.length_scale_upper_mm <= 0:
        raise ValueError("Length-scale bounds must be positive.")
    if args.length_scale_lower_mm >= args.length_scale_upper_mm:
        raise ValueError("Lower length-scale bound must be smaller than upper bound.")

    df = load_dynamic(args.dynamic_csv, args.max_raw_error_mm)
    captures = sorted(df["capture_id"].unique())
    if args.captures.strip():
        wanted = {x.strip() for x in args.captures.split(",") if x.strip()}
        captures = [c for c in captures if c in wanted]
    if not captures:
        raise ValueError("No captures selected.")

    cfg = ArgsSnapshot(
        dynamic_csv=str(args.dynamic_csv),
        max_raw_error_mm=float(args.max_raw_error_mm),
        inducing_points=int(args.inducing_points),
        epochs=int(args.epochs),
        batch_size=int(args.batch_size),
        eval_batch_size=int(args.eval_batch_size),
        lr=float(args.lr),
        length_scale_lower_mm=float(args.length_scale_lower_mm),
        length_scale_upper_mm=float(args.length_scale_upper_mm),
        seed=int(args.seed),
    )

    print(
        f"Loaded dynamic rows: {len(df)}; captures={len(captures)}; "
        f"devices={devices}; inducing={args.inducing_points}; epochs={args.epochs}; "
        f"batch_size={args.batch_size}; torch={torch.__version__}; gpytorch={gpytorch.__version__}"
    )
    for dev in devices:
        props = torch.cuda.get_device_properties(dev)
        print(f"GPU{dev}: {props.name}, memory={props.total_memory / (1024**3):.1f} GiB")

    capture_groups = [[] for _ in devices]
    for i, capture in enumerate(captures):
        capture_groups[i % len(devices)].append(capture)
    for dev, group in zip(devices, capture_groups):
        print(f"GPU{dev} assigned captures: {', '.join(group)}")

    ctx = mp.get_context("spawn")
    all_results: list[dict] = []
    with ProcessPoolExecutor(max_workers=len(devices), mp_context=ctx) as executor:
        futures = [
            executor.submit(worker_run, dev, group, df, cfg)
            for dev, group in zip(devices, capture_groups)
            if group
        ]
        for future in as_completed(futures):
            all_results.extend(future.result())

    all_results.sort(key=lambda r: r["capture_id"])
    before = np.concatenate([np.asarray(r["before"], dtype=float) for r in all_results])
    after = np.concatenate([np.asarray(r["after"], dtype=float) for r in all_results])
    before_s = summarize(before)
    after_s = summarize(after)

    print("\nROTO dynamic GPU SVGP leave-one-capture-out summary")
    print("-" * 72)
    print(f"{'Condition':28s} {'Median (mm)':>12s} {'P95 (mm)':>12s} {'RMSE (mm)':>12s}")
    print("-" * 72)
    print(f"{'Before compensation':28s} {before_s['median']:12.3f} {before_s['p95']:12.3f} {before_s['rmse']:12.3f}")
    print(f"{'After GPU SVGP LOCO':28s} {after_s['median']:12.3f} {after_s['p95']:12.3f} {after_s['rmse']:12.3f}")
    print("-" * 72)
    print(
        "Delta after-before: "
        f"median={after_s['median'] - before_s['median']:+.3f} mm, "
        f"P95={after_s['p95'] - before_s['p95']:+.3f} mm, "
        f"RMSE={after_s['rmse'] - before_s['rmse']:+.3f} mm"
    )

    print("\nPer-capture median deltas (after-before, mm):")
    for r in all_results:
        delta = r["after_summary"]["median"] - r["before_summary"]["median"]
        print(
            f"  {r['capture_id']} GPU{r['device_id']}: {delta:+.3f} "
            f"(before {r['before_summary']['median']:.3f}, after {r['after_summary']['median']:.3f}, "
            f"elapsed {r['elapsed_s']:.1f}s)"
        )

    plot_cdf(before, after, args.output)
    return 0


if __name__ == "__main__":
    # Avoid accidental CPU oversubscription when running two CUDA workers.
    os.environ.setdefault("OMP_NUM_THREADS", "2")
    os.environ.setdefault("MKL_NUM_THREADS", "2")
    raise SystemExit(main())
