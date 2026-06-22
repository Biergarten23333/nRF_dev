#!/usr/bin/env python3
from __future__ import annotations

import argparse
import importlib.util
import json
import math
import os
import random
import subprocess
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
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
import optuna
import pandas as pd
import torch
from scipy.optimize import least_squares
from sklearn.preprocessing import StandardScaler
from torch import nn
from torch.utils.data import DataLoader, TensorDataset


ANCHOR_COUNT = 8
DEFAULT_OUTER_TEST = "R14"


def find_base() -> Path:
    return Path(__file__).resolve().parents[4]


BASE = find_base()
ANALYSIS = BASE / "Analysis/official_extra_analysis"
FULL = ANALYSIS / "FULL"
TENSOR_PATH = FULL / "tables/roto_dnn_feature_tensor.npz"
FRAME_INDEX_PATH = FULL / "tables/roto_dnn_frame_index.csv"
LAYOUT_PATH = BASE / "solver/outputs/v1_to_v4_io_field_check/v5-commonmode/layout.json"
ROTO_DEEP_SCRIPT = ANALYSIS / "FULL_V5_roto_deepdive/scripts/run_roto_deepdive.py"
STUDY_DB = FULL / "tables/roto_spatiotemporal_dnn_optuna.db"
RESULTS_CSV = FULL / "tables/roto_spatiotemporal_dnn_final_eval.csv"
TRIALS_CSV = FULL / "tables/roto_spatiotemporal_dnn_trials.csv"
MODEL_PATH = FULL / "models/roto_spatiotemporal_dnn_best.pt"
CDF_PATH = FULL / "figs/roto_spatiotemporal_dnn_final_cdf.png"
HISTORY_PATH = FULL / "figs/roto_spatiotemporal_dnn_optuna_history.png"
IMPORTANCE_PATH = FULL / "figs/roto_spatiotemporal_dnn_param_importance.png"


def log(msg: str) -> None:
    print(msg, flush=True)


def load_module(path: Path, name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load module from {path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def gpu_status() -> str:
    try:
        out = subprocess.check_output(
            [
                "nvidia-smi",
                "--query-gpu=index,name,memory.used,memory.total,utilization.gpu",
                "--format=csv,noheader,nounits",
            ],
            text=True,
        )
        return "; ".join(line.strip() for line in out.splitlines() if line.strip())
    except Exception as exc:
        return f"nvidia-smi unavailable: {exc!r}"


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


def selected_feature_indices(feature_names: list[str], mode: str) -> list[int]:
    if mode == "all6":
        names = ["range_mm", "quality_percent", "geo_dist_mm", "uwb_x", "uwb_y", "uwb_z"]
    elif mode == "no_geo":
        names = ["range_mm", "quality_percent", "uwb_x", "uwb_y", "uwb_z"]
    else:
        raise ValueError(f"unknown feature mode: {mode}")
    return [feature_names.index(name) for name in names]


def load_aligned_anchors() -> np.ndarray:
    data = json.loads(LAYOUT_PATH.read_text(encoding="utf-8"))
    if len(data.get("anchors", [])) != ANCHOR_COUNT:
        raise ValueError(f"expected {ANCHOR_COUNT} anchors in {LAYOUT_PATH}")
    roto = load_module(ROTO_DEEP_SCRIPT, "roto_deep_for_spatiotemporal_dnn")
    ctx = roto.build_context()
    anchors = np.asarray(ctx["anchors_vicon"], dtype=np.float64)
    if anchors.shape != (ANCHOR_COUNT, 3):
        raise ValueError(f"unexpected aligned anchor shape: {anchors.shape}")
    return anchors


@dataclass
class SequenceData:
    X: np.ndarray
    Y: np.ndarray
    end_frame_idx: np.ndarray
    capture_id: np.ndarray
    tag: np.ndarray


class SequenceCache:
    def __init__(self, X_frames: np.ndarray, Y_frames: np.ndarray, frames: pd.DataFrame) -> None:
        self.X_frames = X_frames
        self.Y_frames = Y_frames
        self.frames = frames
        self.cache: dict[int, SequenceData] = {}

    def get(self, window_size: int) -> SequenceData:
        if window_size not in self.cache:
            self.cache[window_size] = self._build(window_size)
        return self.cache[window_size]

    def _build(self, window_size: int) -> SequenceData:
        xs: list[np.ndarray] = []
        ys: list[np.ndarray] = []
        end_indices: list[int] = []
        caps: list[str] = []
        tags: list[str] = []
        # Keep sequences inside both capture and tag. This avoids crossing the
        # BS2DCE/BSDC91 boundary within a capture.
        grouped = self.frames.groupby(["capture_id", "tag"], sort=False)
        for (cap, tag), group in grouped:
            ordered = group.sort_values("sweep", kind="mergesort")
            idx = ordered.index.to_numpy(dtype=np.int64)
            if len(idx) < window_size:
                continue
            for end_pos in range(window_size - 1, len(idx)):
                win_idx = idx[end_pos - window_size + 1 : end_pos + 1]
                end_idx = int(idx[end_pos])
                # Store as channels,time,anchors.
                xs.append(np.transpose(self.X_frames[win_idx], (2, 0, 1)))
                ys.append(self.Y_frames[end_idx])
                end_indices.append(end_idx)
                caps.append(str(cap))
                tags.append(str(tag))
        X_seq = np.asarray(xs, dtype=np.float32)
        Y_seq = np.asarray(ys, dtype=np.float32)
        log(f"built sequences: window={window_size}, X={X_seq.shape}, Y={Y_seq.shape}")
        return SequenceData(
            X=X_seq,
            Y=Y_seq,
            end_frame_idx=np.asarray(end_indices, dtype=np.int64),
            capture_id=np.asarray(caps),
            tag=np.asarray(tags),
        )


class TargetNormalizer:
    def __init__(self) -> None:
        self.mean = 0.0
        self.std = 1.0

    def fit(self, y: np.ndarray) -> None:
        vals = y[np.isfinite(y)]
        if vals.size == 0:
            raise ValueError("no finite target values for normalization")
        self.mean = float(vals.mean())
        self.std = float(vals.std())
        if not np.isfinite(self.std) or self.std < 1e-6:
            self.std = 1.0

    def transform(self, y: np.ndarray) -> np.ndarray:
        return ((y - self.mean) / self.std).astype(np.float32)

    def inverse(self, y: np.ndarray) -> np.ndarray:
        return y.astype(np.float32) * self.std + self.mean


def fit_transform_sequences(
    seq: SequenceData,
    train_mask: np.ndarray,
    other_mask: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, StandardScaler]:
    # X shape: N,C,T,A. Scale each channel over train sequences/time/anchors.
    c = seq.X.shape[1]
    scaler = StandardScaler()
    train_flat = np.transpose(seq.X[train_mask], (0, 2, 3, 1)).reshape(-1, c)
    finite = np.isfinite(train_flat).all(axis=1)
    scaler.fit(train_flat[finite])

    def transform(part: np.ndarray) -> np.ndarray:
        shape = part.shape
        flat = np.transpose(part, (0, 2, 3, 1)).reshape(-1, c)
        out = scaler.transform(flat).reshape(shape[0], shape[2], shape[3], c)
        out = np.transpose(out, (0, 3, 1, 2)).astype(np.float32)
        return np.nan_to_num(out, nan=0.0, posinf=0.0, neginf=0.0)

    return transform(seq.X[train_mask]), transform(seq.X[other_mask]), scaler


class SpatioTemporalNet(nn.Module):
    def __init__(
        self,
        in_channels: int,
        window_size: int,
        conv_channels: int,
        num_conv_layers: int,
        lstm_hidden_size: int,
        dropout: float,
    ) -> None:
        super().__init__()
        layers: list[nn.Module] = []
        ch = in_channels
        for _ in range(num_conv_layers):
            layers.extend(
                [
                    nn.Conv2d(ch, conv_channels, kernel_size=(3, 3), padding=(1, 1), bias=False),
                    nn.BatchNorm2d(conv_channels),
                    nn.ReLU(inplace=True),
                ]
            )
            ch = conv_channels
        self.conv = nn.Sequential(*layers)
        self.temporal = nn.LSTM(
            input_size=conv_channels * ANCHOR_COUNT,
            hidden_size=lstm_hidden_size,
            num_layers=1,
            batch_first=True,
        )
        self.head = nn.Sequential(
            nn.Dropout(dropout),
            nn.Linear(lstm_hidden_size, 256),
            nn.ReLU(inplace=True),
            nn.Dropout(dropout),
            nn.Linear(256, ANCHOR_COUNT),
        )
        self.window_size = window_size

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        # x: B,C,T,A
        h = self.conv(x)
        h = h.permute(0, 2, 1, 3).contiguous()
        h = h.view(h.shape[0], h.shape[1], -1)
        out, _ = self.temporal(h)
        return self.head(out[:, -1, :])


def masked_mse(pred: torch.Tensor, target: torch.Tensor, mask: torch.Tensor) -> torch.Tensor:
    diff = (pred - target).pow(2) * mask
    return diff.sum() / mask.sum().clamp_min(1.0)


def make_grad_scaler(enabled: bool) -> torch.amp.GradScaler:
    return torch.amp.GradScaler("cuda", enabled=enabled)


def autocast_cuda(enabled: bool):
    return torch.amp.autocast("cuda", enabled=enabled)


def make_loader(X: np.ndarray, Y: np.ndarray, mask: np.ndarray, batch_size: int, shuffle: bool) -> DataLoader:
    return DataLoader(
        TensorDataset(torch.from_numpy(X), torch.from_numpy(Y), torch.from_numpy(mask)),
        batch_size=batch_size,
        shuffle=shuffle,
        num_workers=4,
        pin_memory=torch.cuda.is_available(),
        persistent_workers=True,
    )


def train_eval_residual_model(
    seq: SequenceData,
    train_mask: np.ndarray,
    val_mask: np.ndarray,
    params: dict[str, Any],
    epochs: int,
    patience: int,
    device: torch.device,
    seed: int,
    trial: optuna.Trial | None = None,
    startup_gpu_log: bool = False,
) -> tuple[float, dict[str, Any]]:
    set_seed(seed)
    X_train, X_val, _x_scaler = fit_transform_sequences(seq, train_mask, val_mask)
    y_norm = TargetNormalizer()
    y_norm.fit(seq.Y[train_mask])
    Y_train_raw = seq.Y[train_mask]
    Y_val_raw = seq.Y[val_mask]
    Y_train = y_norm.transform(np.nan_to_num(Y_train_raw, nan=y_norm.mean))
    Y_val = y_norm.transform(np.nan_to_num(Y_val_raw, nan=y_norm.mean))
    M_train = np.isfinite(Y_train_raw).astype(np.float32)
    M_val = np.isfinite(Y_val_raw).astype(np.float32)

    model: nn.Module = SpatioTemporalNet(
        in_channels=seq.X.shape[1],
        window_size=params["window_size"],
        conv_channels=params["conv_channels"],
        num_conv_layers=params["num_conv_layers"],
        lstm_hidden_size=params["lstm_hidden_size"],
        dropout=params["dropout"],
    ).to(device)
    if torch.cuda.is_available() and torch.cuda.device_count() > 1:
        model = nn.DataParallel(model)

    optimizer = torch.optim.AdamW(model.parameters(), lr=params["lr"], weight_decay=1e-4)
    scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=max(epochs, 1))
    amp_enabled = device.type == "cuda"
    scaler = make_grad_scaler(amp_enabled)

    train_loader = make_loader(X_train, Y_train, M_train, params["batch_size"], shuffle=True)
    val_loader = make_loader(X_val, Y_val, M_val, params["batch_size"] * 2, shuffle=False)

    if startup_gpu_log:
        log(f"    gpu before first batch: {gpu_status()}")

    best_val = float("inf")
    best_state: dict[str, torch.Tensor] | None = None
    stale = 0
    first_batch_logged = False
    for epoch in range(epochs):
        model.train()
        for xb, yb, mb in train_loader:
            xb = xb.to(device, non_blocking=True)
            yb = yb.to(device, non_blocking=True)
            mb = mb.to(device, non_blocking=True)
            optimizer.zero_grad(set_to_none=True)
            with autocast_cuda(amp_enabled):
                pred = model(xb)
                loss = masked_mse(pred, yb, mb)
            scaler.scale(loss).backward()
            scaler.step(optimizer)
            scaler.update()
            if startup_gpu_log and not first_batch_logged:
                if torch.cuda.is_available():
                    torch.cuda.synchronize()
                log(f"    gpu after first batch: {gpu_status()}")
                first_batch_logged = True
        scheduler.step()

        model.eval()
        val_loss_sum = 0.0
        val_weight = 0.0
        with torch.no_grad():
            for xb, yb, mb in val_loader:
                xb = xb.to(device, non_blocking=True)
                yb = yb.to(device, non_blocking=True)
                mb = mb.to(device, non_blocking=True)
                with autocast_cuda(amp_enabled):
                    pred = model(xb)
                    loss = masked_mse(pred, yb, mb)
                w = float(mb.sum().detach().cpu().item())
                val_loss_sum += float(loss.detach().cpu().item()) * max(w, 1.0)
                val_weight += max(w, 1.0)
        val_loss = val_loss_sum / max(val_weight, 1.0)
        if trial is not None:
            trial.report(val_loss, epoch)
            if trial.should_prune():
                raise optuna.TrialPruned()
        if val_loss + 1e-6 < best_val:
            best_val = val_loss
            stale = 0
            state = model.module.state_dict() if isinstance(model, nn.DataParallel) else model.state_dict()
            best_state = {k: v.detach().cpu().clone() for k, v in state.items()}
        else:
            stale += 1
            if stale >= patience:
                break

    meta = {
        "best_state": best_state,
        "target_mean": y_norm.mean,
        "target_std": y_norm.std,
        "epochs_run": epoch + 1,
    }
    return best_val, meta


def fit_final_and_predict(
    seq: SequenceData,
    train_mask: np.ndarray,
    test_mask: np.ndarray,
    params: dict[str, Any],
    epochs: int,
    device: torch.device,
    seed: int,
) -> tuple[np.ndarray, dict[str, Any]]:
    set_seed(seed)
    X_train, X_test, x_scaler = fit_transform_sequences(seq, train_mask, test_mask)
    y_norm = TargetNormalizer()
    y_norm.fit(seq.Y[train_mask])
    Y_train_raw = seq.Y[train_mask]
    Y_train = y_norm.transform(np.nan_to_num(Y_train_raw, nan=y_norm.mean))
    M_train = np.isfinite(Y_train_raw).astype(np.float32)

    model: nn.Module = SpatioTemporalNet(
        in_channels=seq.X.shape[1],
        window_size=params["window_size"],
        conv_channels=params["conv_channels"],
        num_conv_layers=params["num_conv_layers"],
        lstm_hidden_size=params["lstm_hidden_size"],
        dropout=params["dropout"],
    ).to(device)
    if torch.cuda.is_available() and torch.cuda.device_count() > 1:
        model = nn.DataParallel(model)

    optimizer = torch.optim.AdamW(model.parameters(), lr=params["lr"], weight_decay=1e-4)
    scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=max(epochs, 1))
    amp_enabled = device.type == "cuda"
    scaler = make_grad_scaler(amp_enabled)
    loader = make_loader(X_train, Y_train, M_train, params["batch_size"], shuffle=True)
    log(f"[final] gpu before training: {gpu_status()}")
    for _epoch in range(epochs):
        model.train()
        for xb, yb, mb in loader:
            xb = xb.to(device, non_blocking=True)
            yb = yb.to(device, non_blocking=True)
            mb = mb.to(device, non_blocking=True)
            optimizer.zero_grad(set_to_none=True)
            with autocast_cuda(amp_enabled):
                pred = model(xb)
                loss = masked_mse(pred, yb, mb)
            scaler.scale(loss).backward()
            scaler.step(optimizer)
            scaler.update()
        scheduler.step()

    model.eval()
    test_loader = DataLoader(
        TensorDataset(torch.from_numpy(X_test)),
        batch_size=params["batch_size"] * 2,
        shuffle=False,
        num_workers=4,
        pin_memory=torch.cuda.is_available(),
        persistent_workers=True,
    )
    preds: list[np.ndarray] = []
    with torch.no_grad():
        for (xb,) in test_loader:
            xb = xb.to(device, non_blocking=True)
            with autocast_cuda(amp_enabled):
                pred = model(xb)
            preds.append(pred.detach().cpu().numpy())
    pred_norm = np.concatenate(preds, axis=0)
    pred = y_norm.inverse(pred_norm)
    state = model.module.state_dict() if isinstance(model, nn.DataParallel) else model.state_dict()
    meta = {
        "model_state": state,
        "target_mean": y_norm.mean,
        "target_std": y_norm.std,
        "x_scaler_mean": x_scaler.mean_,
        "x_scaler_scale": x_scaler.scale_,
    }
    return pred, meta


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
        return res.x.astype(np.float64)
    except Exception:
        return np.array([np.nan, np.nan, np.nan], dtype=np.float64)


def solve_chunk(
    anchors: np.ndarray,
    ranges: np.ndarray,
    initial: np.ndarray,
    truth: np.ndarray,
) -> np.ndarray:
    err = np.empty(len(ranges), dtype=np.float64)
    for i in range(len(ranges)):
        p = solve_position(anchors, ranges[i], initial[i])
        err[i] = np.linalg.norm(p - truth[i]) if np.isfinite(p).all() else np.nan
    return err


def solve_many(
    anchors: np.ndarray,
    ranges: np.ndarray,
    initial: np.ndarray,
    truth: np.ndarray,
    workers: int,
    chunk_size: int,
) -> np.ndarray:
    if workers <= 1 or len(ranges) <= chunk_size:
        return solve_chunk(anchors, ranges, initial, truth)
    err = np.empty(len(ranges), dtype=np.float64)
    with ProcessPoolExecutor(max_workers=workers) as ex:
        futures = []
        for start in range(0, len(ranges), chunk_size):
            end = min(start + chunk_size, len(ranges))
            futures.append((start, end, ex.submit(solve_chunk, anchors, ranges[start:end], initial[start:end], truth[start:end])))
        for start, end, fut in futures:
            err[start:end] = fut.result()
    return err


def make_cdf(err: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    arr = np.sort(np.asarray(err, dtype=np.float64)[np.isfinite(err)])
    if arr.size == 0:
        return arr, arr
    return arr, np.arange(1, arr.size + 1, dtype=np.float64) / arr.size


def save_optuna_figures(study: optuna.Study) -> None:
    try:
        import optuna.visualization.matplotlib as ovm

        HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
        ax = ovm.plot_optimization_history(study)
        ax.figure.tight_layout()
        ax.figure.savefig(HISTORY_PATH, dpi=160)
        plt.close(ax.figure)
        ax = ovm.plot_param_importances(study)
        ax.figure.tight_layout()
        ax.figure.savefig(IMPORTANCE_PATH, dpi=160)
        plt.close(ax.figure)
        log(f"saved optuna figures: {HISTORY_PATH}, {IMPORTANCE_PATH}")
    except Exception as exc:
        log(f"WARNING: could not save optuna visualization figures: {exc!r}")


def sample_params(trial: optuna.Trial) -> dict[str, Any]:
    return {
        "window_size": trial.suggest_categorical("window_size", [10, 20, 30]),
        "conv_channels": trial.suggest_categorical("conv_channels", [64, 128, 256]),
        "num_conv_layers": trial.suggest_categorical("num_conv_layers", [2, 3, 4]),
        "lstm_hidden_size": trial.suggest_categorical("lstm_hidden_size", [128, 256]),
        "dropout": trial.suggest_float("dropout", 0.1, 0.5),
        "lr": trial.suggest_float("lr", 1e-4, 1e-3, log=True),
        "batch_size": trial.suggest_categorical("batch_size", [256, 512]),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Optuna spatiotemporal DNN search for ROTO range residual compensation.")
    parser.add_argument("--outer-test", default=DEFAULT_OUTER_TEST)
    parser.add_argument("--feature-mode", choices=["all6", "no_geo"], default="all6")
    parser.add_argument("--n-trials", type=int, default=100)
    parser.add_argument("--epochs", type=int, default=30)
    parser.add_argument("--final-epochs", type=int, default=50)
    parser.add_argument("--patience", type=int, default=5)
    parser.add_argument("--seed", type=int, default=20260622)
    parser.add_argument("--storage", default=f"sqlite:///{STUDY_DB}")
    parser.add_argument("--study-name", default="roto_spatiotemporal_dnn_loco")
    parser.add_argument("--skip-final", action="store_true")
    parser.add_argument("--solve-workers", type=int, default=8)
    parser.add_argument("--solve-chunk-size", type=int, default=128)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    started = time.perf_counter()
    torch.backends.cudnn.benchmark = True
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    log(f"python: {sys.executable}")
    log(f"torch={torch.__version__}; optuna={optuna.__version__}")
    log(f"cuda={torch.cuda.is_available()}; devices={torch.cuda.device_count()}")
    if torch.cuda.is_available():
        log(f"cuda names: {[torch.cuda.get_device_name(i) for i in range(torch.cuda.device_count())]}")
    log(f"DataParallel enabled={torch.cuda.is_available() and torch.cuda.device_count() > 1}")
    log("NOTE: GTX 1080 Ti has no Tensor Cores; AMP is enabled but Tensor-Core acceleration is not available on Pascal.")
    log(f"initial gpu: {gpu_status()}")

    z = np.load(TENSOR_PATH, allow_pickle=True)
    X_full = z["X"].astype(np.float32)
    Y = z["Y"].astype(np.float32)
    feature_names = [str(x) for x in z["feature_names"].tolist()]
    feature_idx = selected_feature_indices(feature_names, args.feature_mode)
    selected_names = [feature_names[i] for i in feature_idx]
    X = X_full[:, :, feature_idx].astype(np.float32)
    frames = pd.read_csv(FRAME_INDEX_PATH).reset_index(drop=True)
    captures = sorted(frames["capture_id"].astype(str).unique().tolist())
    if args.outer_test not in captures:
        raise ValueError(f"outer test capture {args.outer_test} not in {captures}")
    if args.feature_mode == "all6":
        log("WARNING: all6 includes geo_dist_mm, while Y=range_mm-geo_dist_mm. This is an oracle/leaky diagnostic.")
    log(f"selected features ({len(selected_names)}): {selected_names}")
    log(f"X frames={X.shape}; Y frames={Y.shape}; captures={captures}; outer_test={args.outer_test}")

    seq_cache = SequenceCache(X, Y, frames)
    train_capture_pool = [c for c in captures if c != args.outer_test]
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

    def objective(trial: optuna.Trial) -> float:
        params = sample_params(trial)
        seq = seq_cache.get(int(params["window_size"]))
        rng = random.Random(args.seed + trial.number * 7919)
        val_capture = rng.choice(train_capture_pool)
        train_mask = (seq.capture_id != args.outer_test) & (seq.capture_id != val_capture)
        val_mask = seq.capture_id == val_capture
        log(
            f"[trial {trial.number} START] val_capture={val_capture}; "
            f"train_seq={int(train_mask.sum()):,}; val_seq={int(val_mask.sum()):,}; params={params}; gpu={gpu_status()}"
        )
        val_loss, meta = train_eval_residual_model(
            seq,
            train_mask=train_mask,
            val_mask=val_mask,
            params=params,
            epochs=args.epochs,
            patience=args.patience,
            device=device,
            seed=args.seed + trial.number,
            trial=trial,
            startup_gpu_log=trial.number < 3,
        )
        trial.set_user_attr("val_capture", val_capture)
        trial.set_user_attr("epochs_run", meta["epochs_run"])
        log(
            f"[trial {trial.number} DONE] val_loss={val_loss:.6f}; "
            f"epochs_run={meta['epochs_run']}; gpu={gpu_status()}"
        )
        return val_loss

    study = optuna.create_study(
        study_name=args.study_name,
        storage=args.storage,
        direction="minimize",
        load_if_exists=True,
        pruner=optuna.pruners.MedianPruner(n_startup_trials=8, n_warmup_steps=5),
    )
    study.optimize(objective, n_trials=args.n_trials, gc_after_trial=True)

    trials_df = study.trials_dataframe(attrs=("number", "value", "params", "state", "user_attrs"))
    TRIALS_CSV.parent.mkdir(parents=True, exist_ok=True)
    trials_df.to_csv(TRIALS_CSV, index=False)
    log(f"saved trials: {TRIALS_CSV}")
    save_optuna_figures(study)
    log(f"best value={study.best_value:.6f}")
    log(f"best params={study.best_params}")

    if args.skip_final:
        log("skip-final requested; stopping after Optuna search.")
        return 0

    best_params = dict(study.best_params)
    best_params["window_size"] = int(best_params["window_size"])
    best_params["conv_channels"] = int(best_params["conv_channels"])
    best_params["num_conv_layers"] = int(best_params["num_conv_layers"])
    best_params["lstm_hidden_size"] = int(best_params["lstm_hidden_size"])
    best_params["batch_size"] = int(best_params["batch_size"])
    seq = seq_cache.get(int(best_params["window_size"]))
    final_train_mask = seq.capture_id != args.outer_test
    final_test_mask = seq.capture_id == args.outer_test
    log(
        f"[final] train_seq={int(final_train_mask.sum()):,}; test_seq={int(final_test_mask.sum()):,}; "
        f"outer_test={args.outer_test}; params={best_params}"
    )
    pred_residual, final_meta = fit_final_and_predict(
        seq,
        train_mask=final_train_mask,
        test_mask=final_test_mask,
        params=best_params,
        epochs=args.final_epochs,
        device=device,
        seed=args.seed + 999,
    )

    test_end_idx = seq.end_frame_idx[final_test_mask]
    raw_feature_idx = feature_names.index("range_mm")
    raw_ranges = X_full[test_end_idx, :, raw_feature_idx].astype(np.float64)
    corrected_ranges = raw_ranges - pred_residual.astype(np.float64)
    corrected_ranges[~np.isfinite(raw_ranges)] = np.nan
    corrected_ranges[corrected_ranges <= 0.0] = np.nan
    initial = frames.loc[test_end_idx, ["x", "y", "z"]].to_numpy(dtype=np.float64)
    truth = frames.loc[test_end_idx, ["truth_x", "truth_y", "truth_z"]].to_numpy(dtype=np.float64)
    existing_err = np.linalg.norm(initial - truth, axis=1)

    anchors = load_aligned_anchors()
    log("[final] solving baseline raw ranges...")
    baseline_err = solve_many(anchors, raw_ranges, initial, truth, args.solve_workers, args.solve_chunk_size)
    log("[final] solving corrected ranges...")
    corrected_err = solve_many(anchors, corrected_ranges, initial, truth, args.solve_workers, args.solve_chunk_size)
    summary = pd.DataFrame(
        [
            {"condition": "Existing table x/y/z", **stats(existing_err)},
            {"condition": "Raw ranges re-solved", **stats(baseline_err)},
            {"condition": "Spatiotemporal DNN corrected re-solved", **stats(corrected_err)},
        ]
    )
    log("final aggregate summary:")
    with pd.option_context("display.max_columns", None, "display.width", 180, "display.float_format", "{:.3f}".format):
        log(summary.to_string(index=False))
    RESULTS_CSV.parent.mkdir(parents=True, exist_ok=True)
    summary.to_csv(RESULTS_CSV, index=False)
    log(f"saved final summary: {RESULTS_CSV}")

    MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
    torch.save(
        {
            "params": best_params,
            "feature_mode": args.feature_mode,
            "feature_names": selected_names,
            "outer_test": args.outer_test,
            "model_state": final_meta["model_state"],
            "target_mean": final_meta["target_mean"],
            "target_std": final_meta["target_std"],
            "x_scaler_mean": final_meta["x_scaler_mean"],
            "x_scaler_scale": final_meta["x_scaler_scale"],
            "study_best_value": study.best_value,
        },
        MODEL_PATH,
    )
    log(f"saved best model: {MODEL_PATH}")

    CDF_PATH.parent.mkdir(parents=True, exist_ok=True)
    plt.figure(figsize=(10, 6))
    for label, err in [
        ("Existing table x/y/z", existing_err),
        ("Raw ranges re-solved", baseline_err),
        ("DNN corrected re-solved", corrected_err),
    ]:
        xs, ys = make_cdf(err)
        plt.plot(xs, ys, linewidth=2, label=label)
    plt.xlabel("3D Error (mm)")
    plt.ylabel("Cumulative Probability")
    plt.title(f"ROTO Spatiotemporal DNN Range Compensation, outer={args.outer_test}")
    plt.grid(True, alpha=0.35)
    plt.legend()
    plt.tight_layout()
    plt.savefig(CDF_PATH, dpi=160)
    plt.close()
    log(f"saved CDF: {CDF_PATH}")
    log(f"total elapsed_s={time.perf_counter() - started:.1f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
