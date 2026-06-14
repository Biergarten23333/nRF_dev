#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import math
import re
import shutil
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from scipy.optimize import least_squares

WORKSPACE_ROOT = Path(__file__).resolve().parents[3]
SOLVER_ROOT = Path(__file__).resolve().parents[2]
if str(SOLVER_ROOT) not in sys.path:
    sys.path.insert(0, str(SOLVER_ROOT))

from biospur_tag_positioning_offline_solver.c_solver import TagPositionSolver
from biospur_tag_positioning_offline_solver.models import (
    Anchor as SolverAnchor,
    Frame as SolverFrame,
    Layout as SolverLayout,
    Observation as SolverObservation,
    SolverConfig,
)

DATA = WORKSPACE_ROOT / "autopos_pipeline" / "outdoor_20260513"
SWEEP_CSV = DATA / "sweep1000" / "pairs_all.csv"
EVAL = DATA / "analysis_20260513_182053" / "run_full_evaluation_same_pipeline_20260513.py"
ANCHORS = "ABCDEFGH"
TAG_SOLVER_METHOD = "T4"
OUT_SUFFIX = ""

WAND_GT = {
    ("BSCCF4", "BS9336"): 670.0,
    ("BSCCF4", "BS955A"): 659.7,
    ("BS9336", "BS955A"): 708.7,
}
ROTO_RADIUS = {"BS2DCE": 440.0, "BSDC91": 560.0}

VERSIONS = [
    ("v1-old", "V1", "early MDS baseline"),
    ("v2", "V2", "IVW fusion no-delay"),
    ("v3-lite", "V3-lite", "MAD/MVUE no-delay"),
    ("v3-full", "V3-full", "Tukey + delay"),
    ("v4-io", "V4-io", "Huber bounded-delay inter-anchor"),
    ("v4-io-commonmode", "V4-io common-mode", "V4-io with d_i = c + e_i common-mode anchor delays"),
    ("v4-io-td", "V4-io-td", "V4-io + static common tag-delay scan"),
    ("v4-io-roto", "V4-io-roto", "V4-io + RotoArm soft constraints"),
    ("v4-io-wand", "V4-io-wand", "V4-io + W01-W04 wand soft constraints"),
    ("v5", "V5", "V4-io diagnostics"),
]


STATIC_META = {
    "ID01": ("edge", "low", "ABEF"),
    "ID02": ("edge", "mid", "ABEF"),
    "ID03": ("edge", "high", "ABEF"),
    "ID04": ("edge", "low", "BCGF"),
    "ID05": ("edge", "mid", "BCGF"),
    "ID06": ("edge", "high", "BCGF"),
    "ID07": ("edge", "low", "CDHG"),
    "ID08": ("edge", "mid", "CDHG"),
    "ID09": ("edge", "high", "CDHG"),
    "ID10": ("edge", "low", "ADHE"),
    "ID11": ("edge", "mid", "ADHE"),
    "ID12": ("edge", "high", "ADHE"),
    "ID13": ("center", "mid", "ABEF"),
    "ID14": ("center", "mid", "BCGF"),
    "ID15": ("center", "mid", "CDHG"),
    "ID16": ("center", "mid", "ADHE"),
    "ID17": ("center", "low", "ABEF"),
    "ID18": ("center", "low", "BCGF"),
    "ID19": ("center", "low", "CDHG"),
    "ID20": ("center", "low", "ADHE"),
    "ID21": ("center", "high", "ABEF"),
    "ID22": ("center", "high", "BCGF"),
    "ID23": ("center", "high", "CDHG"),
    "ID24": ("center", "high", "ADHE"),
}

ROTO_META = {
    "ID25": ("planar", "none"),
    "ID26": ("small", "BCGF"),
    "ID27": ("small", "CDHG"),
    "ID28": ("small", "ADHE"),
    "ID29": ("mid", "ABEF"),
    "ID30": ("mid", "BCGF"),
    "ID31": ("mid", "CDHG"),
    "ID32": ("mid", "ADHE"),
    "ID33": ("high", "ABEF"),
    "ID34": ("high", "BCGF"),
    "ID35": ("high", "CDHG"),
    "ID36": ("high", "ADHE"),
    "ID37": ("high-extra", "suspect"),
    "ID38": ("vertical", "ABEF"),
    "ID39": ("vertical", "BCGF"),
    "ID40": ("vertical", "CDHG"),
    "ID41": ("vertical", "ADHE"),
}


@dataclass
class Layout:
    version: str
    label: str
    x: np.ndarray
    dly: np.ndarray
    extra: dict
    tag_delay_mm: float = 0.0


def load_eval_module():
    spec = importlib.util.spec_from_file_location("same_eval", EVAL)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {EVAL}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def anchor_idx(v: str) -> int:
    s = str(v).strip().upper()
    if s in ANCHORS:
        return ANCHORS.index(s)
    return int(s)


def safe_float(v, default=float("nan")):
    try:
        return float(v)
    except Exception:
        return default


def write_csv(path: Path, rows: list[dict], fields: list[str] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if fields is None:
        fields = []
        for row in rows:
            for k in row:
                if k not in fields:
                    fields.append(k)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for row in rows:
            w.writerow({k: row.get(k, "") for k in fields})


def dump_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2), encoding="utf-8")


def summarize(vals) -> dict:
    arr = np.asarray([v for v in vals if np.isfinite(v)], dtype=float)
    if arr.size == 0:
        return {"n": 0}
    return {
        "n": int(arr.size),
        "mean": float(np.mean(arr)),
        "rms": float(np.sqrt(np.mean(arr * arr))),
        "p50": float(np.percentile(arr, 50)),
        "p75": float(np.percentile(arr, 75)),
        "p95": float(np.percentile(arr, 95)),
        "max": float(np.max(arr)),
        "min": float(np.min(arr)),
    }


def md_table(headers, rows) -> str:
    out = ["| " + " | ".join(headers) + " |", "| " + " | ".join(["---"] * len(headers)) + " |"]
    out += ["| " + " | ".join(str(x) for x in row) + " |" for row in rows]
    return "\n".join(out)


def resolve_data_file(path: Path) -> Path:
    if path.exists():
        return path
    if path.is_symlink():
        target = path.readlink()
        target_str = str(target)
        desktop_prefix = "/home/zekaixiao/Desktop/28052026_Erlangen_Official"
        if target.is_absolute() and target_str.startswith(desktop_prefix):
            rel = target_str[len(desktop_prefix) :].lstrip("/")
            repo_target = WORKSPACE_ROOT / "autopos_pipeline" / "28052026_Erlangen_Official" / rel
            if repo_target.exists():
                return repo_target
    return path


def latest_dirs(base: Path, prefix: str) -> dict[str, Path]:
    pat = re.compile(rf"^({prefix}\d+)(?:_|$)")
    out: dict[str, Path] = {}
    if not base.exists():
        return out
    for p in sorted(base.iterdir()):
        if not p.is_dir():
            continue
        m = pat.match(p.name)
        if m and resolve_data_file(p / "tr_all.csv").exists():
            out[m.group(1)] = p
    return out


def load_sweep_grouped() -> dict[tuple[int, int], list[float]]:
    directed: dict[tuple[int, int], list[float]] = defaultdict(list)
    with SWEEP_CSV.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            a, b = anchor_idx(row["a"]), anchor_idx(row["b"])
            master = anchor_idx(row.get("master", row["a"]))
            d = safe_float(row["dist_mm"])
            q = safe_float(row.get("quality_percent") or 100)
            ok = int(safe_float(row.get("ok") or 1, 1))
            if a == b or d <= 0 or q <= 0 or not ok:
                continue
            if master == a:
                directed[(a, b)].append(d)
            elif master == b:
                directed[(b, a)].append(d)
            else:
                directed[(a, b)].append(d)
    return directed


def slice_raw(raw: dict[tuple[int, int], list[float]], mode: str) -> dict[tuple[int, int], list[float]]:
    out = {}
    for k, vals in raw.items():
        if mode == "all":
            out[k] = list(vals)
        elif mode == "first500":
            out[k] = list(vals[:500])
        elif mode == "last500":
            out[k] = list(vals[500:1000])
        else:
            raise ValueError(mode)
    return out


def compute_anchor_sigma(mod, raw) -> dict[int, float]:
    sig: dict[int, list[float]] = defaultdict(list)
    for i in range(8):
        for j in range(i + 1, 8):
            for vals in (raw.get((i, j), []), raw.get((j, i), [])):
                if len(vals) >= 3:
                    s = mod.mad_sigma(vals, 1.0)
                    sig[i].append(s)
                    sig[j].append(s)
    return {i: max(5.0, float(np.median(sig[i])) if sig[i] else 50.0) for i in range(8)}


def fuse_all(mod, raw, anchor_ids):
    return {m: mod.fuse_from_directed(raw, m, anchor_ids) for m in ["v1", "v2", "v3"]}


def solve_v1_old(mod, pair_dists, anchor_ids):
    lp, _g2l, _l2g = mod.local_pairs(pair_dists, anchor_ids)
    x = mod.mds_init(lp, len(anchor_ids))
    dly = np.zeros(len(anchor_ids), dtype=float)
    return x, dly, {"implementation": "archive_v1_classical_mds_only", "delay_aware": False}


def solve_version(mod, version: str, fused, anchor_ids, extra_data=None) -> Layout:
    if version == "v1-old":
        x, d, extra = solve_v1_old(mod, fused["v1"], anchor_ids)
        return Layout(version, "V1", x, d, extra)
    if version == "v2":
        x, res = mod.solve_autopos_v2(fused["v2"], anchor_ids)
        return Layout(version, "V2", x, np.zeros(len(anchor_ids)), {"success": bool(getattr(res, "success", False))})
    if version == "v3-lite":
        x, res = mod.solve_autopos_v1(fused["v3"], anchor_ids)
        return Layout(version, "V3-lite", x, np.zeros(len(anchor_ids)), {"success": bool(getattr(res, "success", False))})
    if version == "v3-full":
        x, d, res, extra = mod.solve_v3_full(fused["v3"], anchor_ids)
        extra["success"] = bool(getattr(res, "success", False))
        return Layout(version, "V3-full", x, d, extra)
    if version == "v4-io-commonmode":
        init, _ = mod.solve_autopos_v1(fused["v3"], anchor_ids)
        x, d, res = mod.solve_v4_common_mode(fused["v3"], anchor_ids, init)
        extra = {
            "success": bool(getattr(res, "success", False)),
            "based_on": "v4-io",
            "delay_parameterization": "d_i = c + e_i for all anchors; no d_A=0 gauge",
            "common_mode_mm": float(getattr(res, "common_mode_mm", float("nan"))),
            "differential_delay_mm": [
                float(v) for v in np.asarray(getattr(res, "differential_delay_mm", []), dtype=float).tolist()
            ],
            "mean_e_mm": float(getattr(res, "mean_e_mm", float("nan"))),
            "max_abs_e_mm": float(getattr(res, "max_abs_e_mm", float("nan"))),
            "pair_rmse_mm": float(getattr(res, "pair_rmse_mm", float("nan"))),
            "tag_delay_note": "layout tag_delay_mm remains 0. Fixed tag-side delay cases, including 91.153 mm, are ORACLE STAND-IN replay overrides and are not measured calibration.",
        }
        physical = getattr(res, "physical_diagnostics", None)
        if physical:
            extra.update(physical)
        return Layout(version, "V4-io common-mode", x, d, extra, 0.0)
    if version in {"v4-io", "v5"}:
        init, _ = mod.solve_autopos_v1(fused["v3"], anchor_ids)
        x, d, res = mod.solve_v4(fused["v3"], anchor_ids, init)
        extra = {"success": bool(getattr(res, "success", False))}
        if version == "v5":
            rows, cond = mod.compute_fim_v5(res, len(anchor_ids))
            extra.update({"based_on": "v4-io", "condition_number": cond, "fim_rows": rows})
        return Layout(version, "V5" if version == "v5" else "V4-io", x, d, extra)
    if version == "v4-io-td":
        base = solve_version(mod, "v4-io", fused, anchor_ids)
        tag_delay, scan_rows = estimate_common_static_tag_delay(mod, base, anchor_ids)
        extra = dict(base.extra)
        extra.update({
            "based_on": "v4-io",
            "tag_delay_model": "single common static type-level tag delay",
            "tag_delay_source": "Static_Test median per-anchor observations",
            "tag_delay_scan_rows": scan_rows,
        })
        return Layout(version, "V4-io-td", base.x.copy(), base.dly.copy(), extra, tag_delay)
    if version == "v4-io-wand":
        base = solve_version(mod, "v4-io", fused, anchor_ids)
        return solve_v4_wand(mod, fused["v3"], anchor_ids, base, extra_data or {})
    if version == "v4-io-roto":
        base = solve_version(mod, "v4-io", fused, anchor_ids)
        return solve_v4_roto(mod, fused["v3"], anchor_ids, base, extra_data or {})
    raise ValueError(version)


def layout_to_global(mod, layout: Layout, anchor_ids):
    return mod.to_global_layout(layout.x, layout.dly, anchor_ids)


def layout_to_solver_model(mod, layout: Layout, anchor_ids) -> SolverLayout:
    global_xyz, global_delay = layout_to_global(mod, layout, anchor_ids)
    anchors: dict[int, SolverAnchor] = {}
    for aid in anchor_ids:
        delay = float(global_delay[aid])
        if not math.isfinite(delay):
            delay = 0.0
        sigma = float(mod.ANCHOR_SIGMA.get(aid, 50.0))
        anchors[aid] = SolverAnchor(
            id=int(aid),
            label=ANCHORS[aid],
            x_mm=float(global_xyz[aid][0]),
            y_mm=float(global_xyz[aid][1]),
            z_mm=float(global_xyz[aid][2]),
            d_anchor_mm=delay,
            sigma_mm=max(5.0, sigma),
        )
    return SolverLayout(
        path=f"production_export:{layout.version}",
        anchors=anchors,
        tag_delay_mm=float(layout.tag_delay_mm),
        metadata={
            "source_layout_version": layout.version,
            "solver_method": TAG_SOLVER_METHOD,
            "note": "Production export solver model built from globalized AutoPos layout.",
        },
    )


def load_frames_by_peer(path: Path) -> dict[str, list[dict]]:
    grouped: dict[tuple[str, int], dict] = {}
    path = resolve_data_file(path)
    with path.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            try:
                if int(float(row.get("valid", "0") or 0)) != 1:
                    continue
                status = row.get("status") or "O"
                if status not in {"", "O"}:
                    continue
                aid = int(float(row["anchor_id"]))
                rng = float(row.get("range_mm") or row.get("raw_mm"))
                sweep = int(float(row["sweep"]))
                peer = (row.get("peer_name") or "unknown").strip() or "unknown"
                t = float(row.get("host_elapsed_s") or 0.0)
            except Exception:
                continue
            if 0 <= aid < 8 and rng > 0:
                key = (peer, sweep)
                rec = grouped.setdefault(key, {"peer": peer, "sweep": sweep, "t": t, "obs": []})
                rec["obs"].append((aid, rng))
    by_peer: dict[str, list[dict]] = defaultdict(list)
    for (_peer, _sweep), rec in sorted(grouped.items(), key=lambda kv: (kv[0][0], kv[0][1])):
        if len(rec["obs"]) >= 4:
            by_peer[rec["peer"]].append(rec)
    return by_peer


def load_static_median_observations(min_samples_per_anchor: int = 20) -> list[dict]:
    """Median per-anchor static observations for common tag-delay scan.

    The scan intentionally uses each static capture as one fixed unknown
    position. It does not use dynamic roto or wand data, and it does not modify
    the anchor layout. This keeps v4-io-td as a downstream tag-delay
    compensation branch rather than a new AutoPos layout solver.
    """
    cases = []
    dirs = latest_dirs(DATA / "Static_Test", "ID")
    for sid in [f"ID{i:02d}" for i in range(1, 25)]:
        if sid not in dirs:
            continue
        by_peer = load_frames_by_peer(dirs[sid] / "tr_all.csv")
        per_anchor: dict[int, list[float]] = defaultdict(list)
        for frames in by_peer.values():
            for fr in frames:
                for a, r in fr["obs"]:
                    per_anchor[a].append(r)
        obs = [(a, float(np.median(vals))) for a, vals in per_anchor.items() if len(vals) >= min_samples_per_anchor]
        if len(obs) >= 4:
            cases.append({"ID": sid, "obs": obs, "path": str(dirs[sid])})
    return cases


def residuals_for_static_delay(obs, global_xyz, global_delay, anchor_sigma, tag_delay_mm: float) -> tuple[np.ndarray, np.ndarray]:
    p = solve_position_fast(obs, global_xyz, global_delay, anchor_sigma, None, tag_delay_mm)
    raw = []
    normed = []
    for a, measured in obs:
        diff = p - global_xyz[a]
        dist = float(np.linalg.norm(diff))
        pred = dist + (0.0 if np.isnan(global_delay[a]) else global_delay[a]) + tag_delay_mm
        err = pred - measured
        sigma = max(5.0, float(anchor_sigma.get(a, 50.0)))
        raw.append(err)
        normed.append(err / sigma)
    return np.asarray(raw, dtype=float), np.asarray(normed, dtype=float)


def estimate_common_static_tag_delay(mod, base: Layout, anchor_ids, grid_min=-120.0, grid_max=120.0, grid_step=1.0) -> tuple[float, list[dict]]:
    cases = load_static_median_observations()
    global_xyz, global_delay = layout_to_global(mod, base, anchor_ids)
    scan_rows = []
    if not cases:
        return 0.0, [{"status": "no_static_cases", "tag_delay_mm": 0.0}]
    grid = np.arange(grid_min, grid_max + 0.5 * grid_step, grid_step, dtype=float)
    for c in grid:
        raw_all = []
        norm_all = []
        per_case_rms = []
        for case in cases:
            raw, normed = residuals_for_static_delay(case["obs"], global_xyz, global_delay, mod.ANCHOR_SIGMA, float(c))
            if raw.size:
                raw_all.extend(raw.tolist())
                norm_all.extend(normed.tolist())
                per_case_rms.append(float(np.sqrt(np.mean(raw * raw))))
        raw_arr = np.asarray(raw_all, dtype=float)
        norm_arr = np.asarray(norm_all, dtype=float)
        case_arr = np.asarray(per_case_rms, dtype=float)
        if raw_arr.size == 0:
            continue
        abs_arr = np.abs(raw_arr)
        scan_rows.append({
            "tag_delay_mm": float(c),
            "n_static_cases": len(cases),
            "n_residuals": int(raw_arr.size),
            "raw_rms": float(np.sqrt(np.mean(raw_arr * raw_arr))),
            "raw_p50_abs": float(np.percentile(abs_arr, 50)),
            "raw_p95_abs": float(np.percentile(abs_arr, 95)),
            "norm_rms": float(np.sqrt(np.mean(norm_arr * norm_arr))) if norm_arr.size else float("nan"),
            "case_rms_median": float(np.median(case_arr)) if case_arr.size else float("nan"),
            "case_rms_p95": float(np.percentile(case_arr, 95)) if case_arr.size else float("nan"),
            "status": "ok",
        })
    if not scan_rows:
        return 0.0, [{"status": "empty_scan", "tag_delay_mm": 0.0}]
    best = min(scan_rows, key=lambda r: (float(r["case_rms_median"]), float(r["raw_rms"])))
    return float(best["tag_delay_mm"]), scan_rows


def solve_positions(mod, records: list[dict], layout: Layout, anchor_ids):
    global_xyz, global_delay = layout_to_global(mod, layout, anchor_ids)
    active = set(anchor_ids)
    solver = None
    if TAG_SOLVER_METHOD != "FAST_T1":
        solver_layout = layout_to_solver_model(mod, layout, anchor_ids)
        solver = TagPositionSolver(solver_layout, SolverConfig(method=TAG_SOLVER_METHOD))
    positions = []
    times = []
    counts = []
    last = None
    for rec in records:
        obs = [(a, r) for a, r in rec["obs"] if a in active]
        counts.append(len(obs))
        if len(obs) < 4:
            continue
        if solver is None:
            pos = solve_position_fast(obs, global_xyz, global_delay, mod.ANCHOR_SIGMA, last, layout.tag_delay_mm)
        else:
            frame = SolverFrame(
                tag=str(rec.get("peer") or "tag"),
                sweep=int(rec.get("sweep") or 0),
                host_elapsed_s=float(rec.get("t") or 0.0),
                host_epoch_s=0.0,
                observations=tuple(
                    SolverObservation(anchor_id=int(a), range_mm=float(r), quality_percent=100.0, status="O")
                    for a, r in obs
                ),
            )
            result = solver.solve_frame(frame)
            if result is None or result.status != "ok":
                continue
            pos = np.asarray([result.x_mm, result.y_mm, result.z_mm], dtype=float)
        positions.append(pos)
        times.append(rec["t"])
        last = pos
    return np.asarray(positions, dtype=float), np.asarray(times, dtype=float), np.asarray(counts, dtype=float)


def solve_position_fast(obs, global_xyz, global_delay, anchor_sigma, x0=None, tag_delay_mm: float = 0.0):
    """Fast per-frame WLS/HUBER trilateration.

    This replaces scipy least_squares for downstream evaluation only. It still
    evaluates every captured frame; the speedup comes from a small analytic
    Gauss-Newton loop instead of constructing a scipy optimizer per frame.
    """
    if x0 is None or not np.all(np.isfinite(x0)):
        x0 = np.nanmean([global_xyz[a] for a, _r in obs], axis=0)
    p = np.asarray(x0, dtype=float).copy()
    for _ in range(8):
        j_rows = []
        r_rows = []
        w_rows = []
        for a, measured in obs:
            anchor = global_xyz[a]
            diff = p - anchor
            dist = float(np.linalg.norm(diff))
            if dist < 1e-6:
                continue
            pred = dist + (0.0 if np.isnan(global_delay[a]) else global_delay[a]) + tag_delay_mm
            sigma = max(5.0, float(anchor_sigma.get(a, 50.0)))
            rn = (pred - measured) / sigma
            # Huber f=2 in normalized residual units.
            hw = 1.0 if abs(rn) <= 2.0 else 2.0 / max(abs(rn), 1e-9)
            j_rows.append(diff / dist / sigma)
            r_rows.append(rn)
            w_rows.append(math.sqrt(hw))
        if len(j_rows) < 3:
            break
        j = np.asarray(j_rows) * np.asarray(w_rows)[:, None]
        r = np.asarray(r_rows) * np.asarray(w_rows)
        try:
            step, *_ = np.linalg.lstsq(j, -r, rcond=None)
        except np.linalg.LinAlgError:
            break
        if not np.all(np.isfinite(step)):
            break
        max_step = 500.0
        norm = float(np.linalg.norm(step))
        if norm > max_step:
            step *= max_step / norm
        p += step
        if float(np.linalg.norm(step)) < 0.02:
            break
    return p


def position_summary(arr: np.ndarray, counts: np.ndarray) -> dict:
    if arr.shape[0] < 2:
        return {"N_frames": int(arr.shape[0]), "status": "insufficient"}
    mean = np.mean(arr, axis=0)
    std = np.std(arr, axis=0, ddof=1)
    radial = np.linalg.norm(arr - mean, axis=1)
    return {
        "status": "ok",
        "N_frames": int(arr.shape[0]),
        "mean_x": float(mean[0]),
        "mean_y": float(mean[1]),
        "mean_z": float(mean[2]),
        "X_std": float(std[0]),
        "Y_std": float(std[1]),
        "Z_std": float(std[2]),
        "D3_std": float(np.linalg.norm(std)),
        "radial_p50": float(np.percentile(radial, 50)),
        "radial_p75": float(np.percentile(radial, 75)),
        "radial_p95": float(np.percentile(radial, 95)),
        "radial_max": float(np.max(radial)),
        "median_anchors": float(np.median(counts)) if counts.size else float("nan"),
        "pct_ge7": float(np.mean(counts >= 7) * 100.0) if counts.size else float("nan"),
        "pct_ge8": float(np.mean(counts >= 8) * 100.0) if counts.size else float("nan"),
    }


def fit_circle_3d(points: np.ndarray) -> dict:
    pts = np.asarray(points, dtype=float)
    if pts.shape[0] < 20:
        return {"N_frames": int(pts.shape[0]), "status": "insufficient"}
    center0 = np.mean(pts, axis=0)
    _u, _s, vh = np.linalg.svd(pts - center0, full_matrices=False)
    normal = vh[-1]
    e1, e2 = vh[0], vh[1]
    uv = np.column_stack([(pts - center0) @ e1, (pts - center0) @ e2])
    x, y = uv[:, 0], uv[:, 1]
    a = np.column_stack([2 * x, 2 * y, np.ones_like(x)])
    b = x * x + y * y
    sol, *_ = np.linalg.lstsq(a, b, rcond=None)
    cx, cy, c = sol
    radius = math.sqrt(max(0.0, c + cx * cx + cy * cy))
    radial = np.sqrt((x - cx) ** 2 + (y - cy) ** 2) - radius
    zplane = (pts - center0) @ normal
    total = np.sqrt(radial * radial + zplane * zplane)
    center3 = center0 + cx * e1 + cy * e2
    theta = np.unwrap(np.arctan2(y - cy, x - cx))
    if theta.size and theta[-1] < theta[0]:
        theta = -theta
    return {
        "status": "ok",
        "N_frames": int(pts.shape[0]),
        "radius": float(radius),
        "radial_std": float(np.std(radial, ddof=1)),
        "z_plane_std": float(np.std(zplane, ddof=1)),
        "circle_thickness_std_diagnostic": float(np.std(total, ddof=1)),
        "circle_thickness_rms_diagnostic": float(np.sqrt(np.mean(total * total))),
        "circle_thickness_p95_diagnostic": float(np.percentile(total, 95)),
        "plane_tilt_deg": float(np.degrees(np.arccos(np.clip(abs(normal[2]), -1.0, 1.0)))),
        "center_x": float(center3[0]),
        "center_y": float(center3[1]),
        "center_z": float(center3[2]),
        "normal_x": float(normal[0]),
        "normal_y": float(normal[1]),
        "normal_z": float(normal[2]),
        "_theta": theta,
    }


def per_turn_center_stats(points: np.ndarray, theta: np.ndarray) -> dict:
    pts = np.asarray(points, dtype=float)
    th = np.asarray(theta, dtype=float)
    if pts.shape[0] < 80 or th.size != pts.shape[0]:
        return {"turn_count": 0}
    th = th - th[0]
    estimated_turns = float((th[-1] - th[0]) / (2.0 * math.pi))
    bins = np.floor(th / (2.0 * math.pi)).astype(int)
    centers = []
    radii = []
    for b in range(int(np.min(bins)), int(np.max(bins)) + 1):
        idx = np.where(bins == b)[0]
        if idx.size < 30:
            continue
        fit = fit_circle_3d(pts[idx])
        if fit.get("status") == "ok":
            centers.append([fit["center_x"], fit["center_y"], fit["center_z"]])
            radii.append(float(fit["radius"]))
    if len(centers) < 2:
        return {"turn_count": len(centers), "estimated_turns": estimated_turns}
    c = np.asarray(centers, dtype=float)
    mean = np.mean(c, axis=0)
    dist = np.linalg.norm(c - mean, axis=1)
    std = np.std(c, axis=0, ddof=1)
    return {
        "turn_count": int(len(centers)),
        "estimated_turns": estimated_turns,
        "turn_center_rms_3d_mm": float(np.sqrt(np.mean(dist * dist))),
        "turn_center_p95_3d_mm": float(np.percentile(dist, 95)),
        "turn_center_x_std_mm": float(std[0]),
        "turn_center_y_std_mm": float(std[1]),
        "turn_center_z_std_mm": float(std[2]),
        "turn_radius_std_mm": float(np.std(radii, ddof=1)) if len(radii) > 1 else float("nan"),
    }


def inter_residual_rows(mod, layout: Layout, pair_dists, anchor_ids, eval_set: str) -> list[dict]:
    lp, _g2l, l2g = mod.local_pairs(pair_dists, anchor_ids)
    rows = []
    for (li, lj), measured in sorted(lp.items()):
        pred = float(np.linalg.norm(layout.x[li] - layout.x[lj]) + layout.dly[li] + layout.dly[lj])
        gi, gj = l2g[li], l2g[lj]
        rows.append({
            "version": layout.version,
            "eval_set": eval_set,
            "pair": f"{ANCHORS[gi]}-{ANCHORS[gj]}",
            "measured_mm": measured,
            "predicted_mm": pred,
            "residual_mm": pred - measured,
            "abs_residual_mm": abs(pred - measured),
        })
    return rows


def per_anchor_from_pair_rows(rows: list[dict]) -> list[dict]:
    grouped = defaultdict(list)
    for r in rows:
        a, b = r["pair"].split("-")
        grouped[a].append(float(r["residual_mm"]))
        grouped[b].append(float(r["residual_mm"]))
    out = []
    for a in ANCHORS:
        vals = np.asarray(grouped.get(a, []), dtype=float)
        if vals.size == 0:
            continue
        out.append({
            "version": rows[0]["version"] if rows else "",
            "eval_set": rows[0]["eval_set"] if rows else "",
            "anchor": a,
            "n_pairs": int(vals.size),
            "rms": float(np.sqrt(np.mean(vals * vals))),
            "p50_abs": float(np.percentile(np.abs(vals), 50)),
            "p95_abs": float(np.percentile(np.abs(vals), 95)),
            "max_abs": float(np.max(np.abs(vals))),
        })
    return out


def pair_quality_rows(mod, raw, eval_set: str) -> list[dict]:
    rows = []
    for i in range(8):
        for j in range(i + 1, 8):
            ab = np.asarray(raw.get((i, j), []), dtype=float)
            ba = np.asarray(raw.get((j, i), []), dtype=float)
            allv = np.concatenate([ab, ba])
            if allv.size == 0:
                continue
            med_ab = float(np.median(ab)) if ab.size else float("nan")
            med_ba = float(np.median(ba)) if ba.size else float("nan")
            rows.append({
                "eval_set": eval_set,
                "pair": f"{ANCHORS[i]}-{ANCHORS[j]}",
                "n_ab": int(ab.size),
                "n_ba": int(ba.size),
                "med_ab": med_ab,
                "med_ba": med_ba,
                "mad_ab": mod.mad_sigma(ab, 0.1) if ab.size else float("nan"),
                "mad_ba": mod.mad_sigma(ba, 0.1) if ba.size else float("nan"),
                "asymmetry": abs(med_ab - med_ba) if np.isfinite(med_ab) and np.isfinite(med_ba) else float("nan"),
                "median_all": float(np.median(allv)),
                "mad_all": mod.mad_sigma(allv, 0.1),
            })
    return rows


def save_layout(path: Path, layout: Layout, anchor_ids, stats=None):
    obj = {
        "version": layout.version,
        "label": layout.label,
        "anchor_ids": anchor_ids,
        "anchors": [
            {"id": int(gi), "label": ANCHORS[gi], "x_mm": float(layout.x[li, 0]), "y_mm": float(layout.x[li, 1]), "z_mm": float(layout.x[li, 2]), "d_anchor_mm": float(layout.dly[li])}
            for li, gi in enumerate(anchor_ids)
        ],
        "tag_delay_mm": float(layout.tag_delay_mm),
        "stats": stats or {},
        "extra": layout.extra,
    }
    dump_json(path, obj)


def wand_constraint_data(mod, base: Layout, anchor_ids, max_frames=120):
    out = []
    for wid, cap_dir in latest_dirs(DATA / "Wand_Test", "W").items():
        if wid not in {"W01", "W02", "W03", "W04"}:
            continue
        by_peer = load_frames_by_peer(cap_dir / "tr_all.csv")
        rec = {"id": wid, "peers": {}, "init": {}}
        for peer, frames in by_peer.items():
            if not frames:
                continue
            idx = np.linspace(0, len(frames) - 1, min(max_frames, len(frames))).round().astype(int)
            chosen = [frames[int(i)] for i in idx]
            per_anchor = defaultdict(list)
            for fr in chosen:
                for a, r in fr["obs"]:
                    per_anchor[a].append(r)
            obs = [(a, float(np.median(v))) for a, v in per_anchor.items() if len(v) >= 5]
            if len(obs) >= 4:
                rec["peers"][peer] = obs
                pos, _t, _c = solve_positions(mod, chosen, base, anchor_ids)
                if pos.shape[0]:
                    rec["init"][peer] = np.median(pos, axis=0)
        if len(rec["peers"]) >= 2:
            out.append(rec)
    return out


def solve_v4_wand(mod, pair_dists, anchor_ids, base: Layout, extra_data) -> Layout:
    data = extra_data.get("wand_constraints")
    if data is None:
        data = wand_constraint_data(mod, base, anchor_ids)
    lp, _g2l, _l2g = mod.local_pairs(pair_dists, anchor_ids)
    n = len(anchor_ids)
    pmap = mod.pos_param_map(n)
    tags = []
    for cap in data:
        for peer in sorted(cap["peers"]):
            tags.append((cap["id"], peer, cap["peers"][peer], np.asarray(cap["init"].get(peer, np.mean(base.x, axis=0)), dtype=float)))
    t_index = {(cid, peer): k for k, (cid, peer, _obs, _init) in enumerate(tags)}

    def unpack(v):
        x = mod.unpack_pos(v[:len(pmap)], n)
        d = np.zeros(n)
        offset = len(pmap)
        d[1:] = v[offset:offset + n - 1]
        offset += n - 1
        pts = v[offset:].reshape((-1, 3)) if tags else np.zeros((0, 3))
        return x, d, pts

    x0 = np.r_[mod.pack_pos(base.x), base.dly[1:], np.concatenate([init for *_rest, init in tags]) if tags else np.array([])]
    lo = np.r_[np.full(len(pmap), -np.inf), np.full(n - 1, -60.0), np.full(3 * len(tags), -np.inf)]
    hi = np.r_[np.full(len(pmap), np.inf), np.full(n - 1, 60.0), np.full(3 * len(tags), np.inf)]

    def fun(v):
        x, d, pts = unpack(v)
        out = []
        for (i, j), dist in lp.items():
            out.append((np.linalg.norm(x[i] - x[j]) + d[i] + d[j] - dist) / 15.0)
        out.extend((d[1:] / 20.0).tolist())
        for ti, (_cid, _peer, obs, _init) in enumerate(tags):
            p = pts[ti]
            for a, r in obs:
                out.append((np.linalg.norm(p - x[a]) + d[a] - r) / 55.0)
        for cap in data:
            for (pa, pb), gt in WAND_GT.items():
                if (cap["id"], pa) in t_index and (cap["id"], pb) in t_index:
                    p1 = pts[t_index[(cap["id"], pa)]]
                    p2 = pts[t_index[(cap["id"], pb)]]
                    out.append((np.linalg.norm(p1 - p2) - gt) / 25.0)
        return np.asarray(out)

    res = least_squares(fun, x0, loss="huber", f_scale=2.0, bounds=(lo, hi), max_nfev=2000)
    x, d, _pts = unpack(res.x)
    return Layout("v4-io-wand", "V4-io-wand", mod.gauge_align_local(x), d, {"success": bool(res.success), "n_wand_tags": len(tags), "n_wand_caps": len(data)})


def roto_constraint_data(mod, base: Layout, anchor_ids, max_frames_per_peer=24):
    out = []
    for rid, cap_dir in latest_dirs(DATA / "Roto_Test", "ID").items():
        by_peer = load_frames_by_peer(cap_dir / "tr_all.csv")
        for peer, frames in by_peer.items():
            if peer not in ROTO_RADIUS or not frames:
                continue
            idx = np.linspace(0, len(frames) - 1, min(max_frames_per_peer, len(frames))).round().astype(int)
            chosen = [frames[int(i)] for i in idx]
            pos, _t, _c = solve_positions(mod, chosen, base, anchor_ids)
            if pos.shape[0] < 4:
                continue
            out.append({"id": rid, "peer": peer, "radius": ROTO_RADIUS[peer], "frames": chosen, "init_points": pos, "init_center": np.mean(pos, axis=0)})
    return out


def solve_v4_roto(mod, pair_dists, anchor_ids, base: Layout, extra_data) -> Layout:
    data = extra_data.get("roto_constraints")
    if data is None:
        data = roto_constraint_data(mod, base, anchor_ids)
    lp, _g2l, _l2g = mod.local_pairs(pair_dists, anchor_ids)
    n = len(anchor_ids)
    pmap = mod.pos_param_map(n)

    def unpack(v):
        x = mod.unpack_pos(v[:len(pmap)], n)
        dly = np.zeros(n)
        offset = len(pmap)
        dly[1:] = v[offset:offset + n - 1]
        return x, dly

    x0 = np.r_[mod.pack_pos(base.x), base.dly[1:]]
    lo = np.r_[np.full(len(pmap), -np.inf), np.full(n - 1, -60.0)]
    hi = np.r_[np.full(len(pmap), np.inf), np.full(n - 1, 60.0)]

    def fun(v):
        x, dly = unpack(v)
        out = []
        for (i, j), dist in lp.items():
            out.append((np.linalg.norm(x[i] - x[j]) + dly[i] + dly[j] - dist) / 15.0)
        out.extend((dly[1:] / 20.0).tolist())
        # RotoArm pseudo-constraints: positions are initialized from V4-io and
        # treated as soft observations. This keeps the branch computationally
        # bounded while still injecting wide-spread roto coverage into the
        # anchor layout objective.
        for block in data:
            for p, fr in zip(block["init_points"], block["frames"]):
                for a, r in fr["obs"]:
                    out.append((np.linalg.norm(p - x[a]) + dly[a] - r) / 80.0)
        return np.asarray(out)

    res = least_squares(fun, x0, loss="huber", f_scale=2.0, bounds=(lo, hi), max_nfev=800)
    x, d = unpack(res.x)
    total_pts = sum(len(block["frames"]) for block in data)
    return Layout("v4-io-roto", "V4-io-roto", mod.gauge_align_local(x), d, {"success": bool(res.success), "n_roto_blocks": len(data), "n_roto_points": total_pts, "constraint_model": "fixed V4-io roto pseudo-positions"})


def align_layout_to_ref(src: Layout, ref: Layout) -> tuple[np.ndarray, np.ndarray, float]:
    a = np.asarray(src.x, dtype=float)
    b = np.asarray(ref.x, dtype=float)
    ca, cb = np.mean(a, axis=0), np.mean(b, axis=0)
    aa, bb = a - ca, b - cb
    u, _s, vt = np.linalg.svd(aa.T @ bb)
    r = u @ vt
    if np.linalg.det(r) < 0:
        u[:, -1] *= -1
        r = u @ vt
    aligned = aa @ r + cb
    diff = np.linalg.norm(aligned - b, axis=1)
    return aligned, src.dly.copy(), float(np.sqrt(np.mean(diff * diff)))


def consensus_layout(a: Layout, b: Layout, version: str, label: str) -> tuple[Layout, list[dict]]:
    bx, bd, rms_align = align_layout_to_ref(b, a)
    x = 0.5 * (a.x + bx)
    d = 0.5 * (a.dly + bd)
    rows = []
    for i in range(len(x)):
        delta = bx[i] - a.x[i]
        rows.append({
            "version": version,
            "anchor": ANCHORS[i],
            "delta_x": float(delta[0]),
            "delta_y": float(delta[1]),
            "delta_z": float(delta[2]),
            "delta_xy": float(np.linalg.norm(delta[:2])),
            "delta_3d": float(np.linalg.norm(delta)),
            "delta_delay": float(bd[i] - a.dly[i]),
        })
    tag_delay = 0.5 * (float(a.tag_delay_mm) + float(b.tag_delay_mm))
    return Layout(version, label, x, d, {"split_align_rms": rms_align, "source": "mean(first500,last500_aligned)"}, tag_delay), rows


def evaluate_static(mod, layout: Layout, anchor_ids) -> list[dict]:
    rows = []
    dirs = latest_dirs(DATA / "Static_Test", "ID")
    for sid in [f"ID{i:02d}" for i in range(1, 25)]:
        loc, height, facing = STATIC_META.get(sid, ("unknown", "unknown", "unknown"))
        if sid not in dirs:
            rows.append({"version": layout.version, "ID": sid, "status": "missing", "location": loc, "height": height, "facing": facing})
            continue
        by_peer = load_frames_by_peer(dirs[sid] / "tr_all.csv")
        frames = []
        for peer_frames in by_peer.values():
            frames.extend(peer_frames)
        pos, _t, counts = solve_positions(mod, sorted(frames, key=lambda r: (r["t"], r["sweep"])), layout, anchor_ids)
        row = {"version": layout.version, "ID": sid, "path": str(dirs[sid]), "location": loc, "height": height, "facing": facing}
        row.update(position_summary(pos, counts))
        rows.append(row)
    return rows


def evaluate_roto(mod, layout: Layout, anchor_ids) -> list[dict]:
    rows = []
    for rid, cap_dir in latest_dirs(DATA / "Roto_Test", "ID").items():
        tilt, facing = ROTO_META.get(rid, ("unknown", "unknown"))
        by_peer = load_frames_by_peer(cap_dir / "tr_all.csv")
        for peer, frames in by_peer.items():
            pos, _t, counts = solve_positions(mod, frames, layout, anchor_ids)
            row = {"version": layout.version, "ID": rid, "peer": peer, "path": str(cap_dir), "tilt": tilt, "facing": facing}
            fit = fit_circle_3d(pos)
            theta = fit.pop("_theta", None)
            row.update(fit)
            if fit.get("status") == "ok" and theta is not None:
                row.update(per_turn_center_stats(pos, theta))
            if counts.size:
                row.update({"median_anchors": float(np.median(counts)), "pct_ge7": float(np.mean(counts >= 7) * 100), "pct_ge8": float(np.mean(counts >= 8) * 100)})
            rows.append(row)
    return rows


def evaluate_wand_static(mod, layout: Layout, anchor_ids) -> list[dict]:
    rows = []
    for wid, cap_dir in latest_dirs(DATA / "Wand_Test", "W").items():
        if wid not in {"W01", "W02", "W03", "W04"}:
            continue
        by_peer = load_frames_by_peer(cap_dir / "tr_all.csv")
        med_pos = {}
        for peer, frames in by_peer.items():
            pos, _t, counts = solve_positions(mod, frames, layout, anchor_ids)
            if pos.shape[0] >= 20:
                med_pos[peer] = np.median(pos, axis=0)
        for (pa, pb), gt in WAND_GT.items():
            row = {"version": layout.version, "ID": wid, "pair": f"{pa}-{pb}", "gt_mm": gt, "path": str(cap_dir)}
            if pa in med_pos and pb in med_pos:
                measured = float(np.linalg.norm(med_pos[pa] - med_pos[pb]))
                row.update({"status": "ok", "measured_median_mm": measured, "bias_mm": measured - gt})
            else:
                row.update({"status": "missing_peer"})
            rows.append(row)
    return rows


def group_static(rows: list[dict]) -> list[dict]:
    out = []
    for key in ["location", "height", "facing"]:
        groups = defaultdict(list)
        for r in rows:
            if r.get("status") == "ok":
                groups[r.get(key, "unknown")].append(float(r["D3_std"]))
        for name, vals in sorted(groups.items()):
            s = summarize(vals)
            out.append({"group_by": key, "group": name, "n": s["n"], "D3_median": s["p50"], "D3_p75": s["p75"], "D3_p95": s["p95"], "D3_max": s["max"]})
    return out


def static_orientation_effect(rows: list[dict]) -> list[dict]:
    groups = defaultdict(list)
    for r in rows:
        if r.get("status") == "ok":
            groups[(r["version"], r.get("location", "unknown"), r.get("height", "unknown"), r.get("facing", "unknown"))].append(float(r["D3_std"]))
    out = []
    for (version, location, height, facing), vals in sorted(groups.items()):
        s = summarize(vals)
        out.append({
            "version": version,
            "location": location,
            "height": height,
            "facing": facing,
            "n": s["n"],
            "D3_median": s["p50"],
            "D3_p75": s["p75"],
            "D3_p95": s["p95"],
            "D3_max": s["max"],
        })
    return out


def radius_consistency(rows: list[dict]) -> list[dict]:
    by_id_ver = defaultdict(dict)
    for r in rows:
        if r.get("status") == "ok" and "radius" in r:
            by_id_ver[(r["version"], r["ID"])][r["peer"]] = float(r["radius"])
    out = []
    for (version, rid), vals in sorted(by_id_ver.items()):
        row = {"version": version, "ID": rid}
        for p in ["BS2DCE", "BSDC91"]:
            row[f"radius_{p}"] = vals.get(p, "")
        if "BS2DCE" in vals and "BSDC91" in vals:
            row["delta_radius"] = vals["BSDC91"] - vals["BS2DCE"]
            row["delta_radius_bias_vs_120"] = row["delta_radius"] - 120.0
        out.append(row)
    return out


def roto_physical_consistency(rows: list[dict]) -> list[dict]:
    by_id_ver = defaultdict(dict)
    for r in rows:
        if r.get("status") == "ok" and r.get("peer") in ROTO_RADIUS:
            by_id_ver[(r["version"], r["ID"])][r["peer"]] = r
    out = []
    for (version, rid), vals in sorted(by_id_ver.items()):
        if "BS2DCE" not in vals or "BSDC91" not in vals:
            continue
        inner = vals["BS2DCE"]
        outer = vals["BSDC91"]
        ri = float(inner["radius"])
        ro = float(outer["radius"])
        delta = ro - ri
        ci = np.array([float(inner["center_x"]), float(inner["center_y"]), float(inner["center_z"])])
        co = np.array([float(outer["center_x"]), float(outer["center_y"]), float(outer["center_z"])])
        row = {
            "version": version,
            "ID": rid,
            "tilt": inner.get("tilt", ""),
            "facing": inner.get("facing", ""),
            "inner_radius_mm": ri,
            "outer_radius_mm": ro,
            "deltaR_mm": delta,
            "deltaR_error_mm": delta - 120.0,
            "abs_deltaR_error_mm": abs(delta - 120.0),
            "inner_radius_bias_mm": ri - ROTO_RADIUS["BS2DCE"],
            "outer_radius_bias_mm": ro - ROTO_RADIUS["BSDC91"],
            "inner_outer_center_sep_mm": float(np.linalg.norm(co - ci)),
            "inner_turn_center_rms_3d_mm": inner.get("turn_center_rms_3d_mm", ""),
            "outer_turn_center_rms_3d_mm": outer.get("turn_center_rms_3d_mm", ""),
            "inner_turn_center_p95_3d_mm": inner.get("turn_center_p95_3d_mm", ""),
            "outer_turn_center_p95_3d_mm": outer.get("turn_center_p95_3d_mm", ""),
            "inner_turn_count": inner.get("turn_count", ""),
            "outer_turn_count": outer.get("turn_count", ""),
            "inner_circle_thickness_rms_diagnostic": inner.get("circle_thickness_rms_diagnostic", ""),
            "outer_circle_thickness_rms_diagnostic": outer.get("circle_thickness_rms_diagnostic", ""),
        }
        out.append(row)
    return out


def roto_physical_summary(rows: list[dict]) -> list[dict]:
    out = []
    for version in sorted({r["version"] for r in rows}):
        rs = [r for r in rows if r["version"] == version]
        err = np.asarray([float(r["deltaR_error_mm"]) for r in rs], dtype=float)
        abs_err = np.abs(err)
        sep = np.asarray([float(r["inner_outer_center_sep_mm"]) for r in rs], dtype=float)
        turn = []
        thick = []
        for r in rs:
            for k in ("inner_turn_center_rms_3d_mm", "outer_turn_center_rms_3d_mm"):
                v = safe_float(r.get(k))
                if np.isfinite(v):
                    turn.append(v)
            for k in ("inner_circle_thickness_rms_diagnostic", "outer_circle_thickness_rms_diagnostic"):
                v = safe_float(r.get(k))
                if np.isfinite(v):
                    thick.append(v)
        turn_s = summarize(turn)
        thick_s = summarize(thick)
        out.append({
            "version": version,
            "capture_pairs": len(rs),
            "deltaR_error_mean_mm": float(np.mean(err)) if err.size else "",
            "deltaR_error_rms_mm": float(np.sqrt(np.mean(err * err))) if err.size else "",
            "abs_deltaR_error_median_mm": float(np.percentile(abs_err, 50)) if abs_err.size else "",
            "abs_deltaR_error_p95_mm": float(np.percentile(abs_err, 95)) if abs_err.size else "",
            "inner_outer_center_sep_median_mm": float(np.percentile(sep, 50)) if sep.size else "",
            "inner_outer_center_sep_p95_mm": float(np.percentile(sep, 95)) if sep.size else "",
            "turn_center_rms_median_mm": turn_s.get("p50", ""),
            "turn_center_rms_p95_mm": turn_s.get("p95", ""),
            "circle_thickness_rms_median_mm_diagnostic": thick_s.get("p50", ""),
            "circle_thickness_rms_p95_mm_diagnostic": thick_s.get("p95", ""),
        })
    return out


def delay_sanity(layouts: list[Layout]) -> list[dict]:
    rows = []
    for l in layouts:
        d = np.asarray(l.dly, dtype=float)
        rows.append({
            "version": l.version,
            "delay_min": float(np.min(d)),
            "delay_max": float(np.max(d)),
            "delay_l2": float(np.linalg.norm(d)),
            "n_near_bounds_55mm": int(np.sum(np.abs(d) >= 55.0)),
            "tag_delay_mm": float(l.tag_delay_mm),
        })
    return rows


def v5_fim_rows(layouts: list[Layout]) -> list[dict]:
    rows = []
    for l in layouts:
        fim_rows = l.extra.get("fim_rows") if isinstance(l.extra, dict) else None
        if not fim_rows:
            continue
        cond = l.extra.get("condition_number", "")
        for r in fim_rows:
            gi = int(r.get("local", 0))
            row = {"version": l.version, "condition_number": cond, "anchor": ANCHORS[gi]}
            row.update({k: v for k, v in r.items() if k != "local"})
            rows.append(row)
    return rows


def holdout_from_quality(quality_rows: list[dict]) -> list[dict]:
    out = []
    for r in quality_rows:
        if r.get("eval_set") == "holdout_last500":
            out.append({
                "version": r["version"],
                "train": "first500",
                "eval": "last500",
                "rms": r["rms"],
                "p50_abs": r["p50_abs"],
                "p75_abs": r["p75_abs"],
                "p95_abs": r["p95_abs"],
                "max_abs": r["max_abs"],
            })
    return out


def autopos_quality_rows(pair_rows: list[dict]) -> list[dict]:
    grouped = defaultdict(list)
    for r in pair_rows:
        grouped[(r["version"], r["eval_set"])].append(float(r["residual_mm"]))
    out = []
    for (version, eval_set), vals in sorted(grouped.items()):
        arr = np.asarray(vals, dtype=float)
        abs_arr = np.abs(arr)
        out.append({
            "version": version,
            "eval_set": eval_set,
            "rms": float(np.sqrt(np.mean(arr * arr))),
            "p50_abs": float(np.percentile(abs_arr, 50)),
            "p75_abs": float(np.percentile(abs_arr, 75)),
            "p95_abs": float(np.percentile(abs_arr, 95)),
            "max_abs": float(np.max(abs_arr)),
        })
    return out


def save_figures(out_dir: Path, summary_rows, static_rows, roto_phys_rows, split_rows=None):
    fig_dir = out_dir / "figures"
    fig_dir.mkdir(parents=True, exist_ok=True)
    labels = [r["version"] for r in summary_rows]
    inter = [safe_float(r.get("autopos_rms")) for r in summary_rows]
    stat = [safe_float(r.get("static_median")) for r in summary_rows]
    roto = [safe_float(r.get("roto_deltaR_rms")) for r in summary_rows]
    plt.figure(figsize=(9, 5))
    plt.plot(labels, inter, marker="o", label="AutoPos inter-anchor RMS")
    plt.plot(labels, stat, marker="s", label="Static median 3D std")
    plt.plot(labels, roto, marker="^", label="Roto ΔR error RMS")
    plt.ylabel("mm")
    plt.xticks(rotation=25, ha="right")
    plt.grid(True, alpha=0.3)
    plt.legend()
    plt.tight_layout()
    plt.savefig(fig_dir / "progression_autopos_static_roto.png", dpi=300)
    plt.close()

    plt.figure(figsize=(10, 5))
    data = [
        [
            safe_float(r.get("D3_std"))
            for r in static_rows
            if r.get("version") == v and r.get("status") == "ok" and np.isfinite(safe_float(r.get("D3_std")))
        ]
        for v in labels
    ]
    plt.boxplot(data, labels=labels, showmeans=True)
    plt.ylabel("Static 3D std (mm)")
    plt.xticks(rotation=25, ha="right")
    plt.grid(True, axis="y", alpha=0.3)
    plt.tight_layout()
    plt.savefig(fig_dir / "static_distribution_all_versions.png", dpi=300)
    plt.close()

    plt.figure(figsize=(10, 5))
    data = [
        [
            safe_float(r.get("abs_deltaR_error_mm"))
            for r in roto_phys_rows
            if r.get("version") == v and np.isfinite(safe_float(r.get("abs_deltaR_error_mm")))
        ]
        for v in labels
    ]
    plt.boxplot(data, labels=labels, showmeans=True)
    plt.ylabel("Roto |ΔR error| (mm)")
    plt.xticks(rotation=25, ha="right")
    plt.grid(True, axis="y", alpha=0.3)
    plt.tight_layout()
    plt.savefig(fig_dir / "roto_deltaR_distribution.png", dpi=300)
    plt.close()

    plt.figure(figsize=(10, 5))
    data = []
    for v in labels:
        vals = []
        for r in roto_phys_rows:
            if r.get("version") != v:
                continue
            for k in ("inner_turn_center_rms_3d_mm", "outer_turn_center_rms_3d_mm"):
                vv = safe_float(r.get(k))
                if np.isfinite(vv):
                    vals.append(vv)
        data.append(vals)
    plt.boxplot(data, labels=labels, showmeans=True)
    plt.ylabel("Roto per-turn center RMS (mm)")
    plt.xticks(rotation=25, ha="right")
    plt.grid(True, axis="y", alpha=0.3)
    plt.tight_layout()
    plt.savefig(fig_dir / "roto_turn_center_rms_distribution.png", dpi=300)
    plt.close()


def make_report(out_dir: Path, mode: str, summary_rows, quality_rows, static_rows, roto_phys_rows, split_rows=None):
    lines = [f"# {out_dir.name} Report\n"]
    lines.append(f"Mode: `{mode}`\n")
    lines.append("## Version Summary\n")
    rows = []
    for r in summary_rows:
        rows.append([
            r["version"],
            f"{safe_float(r.get('tag_delay_mm'), 0.0):.1f}",
            f"{safe_float(r.get('autopos_rms')):.2f}",
            f"{safe_float(r.get('autopos_p95')):.2f}",
            f"{safe_float(r.get('static_median')):.2f}",
            f"{safe_float(r.get('static_p95')):.2f}",
            f"{safe_float(r.get('roto_deltaR_rms')):.2f}",
            f"{safe_float(r.get('roto_abs_deltaR_p95')):.2f}",
        ])
    lines.append(md_table(["Version", "Tag delay", "AutoPos RMS", "AutoPos p95", "Static med", "Static p95", "Roto ΔR RMS", "Roto |ΔR| p95"], rows))
    lines.append("\n## Notes\n")
    lines.append("- AutoPos RMS/p95 are inter-anchor layout residual metrics, not Tag RMS.")
    lines.append("- Static validation uses all available ID01-ID24 captures; missing captures are recorded as missing.")
    lines.append("- Roto validation uses every collected Roto_Test/ID* folder and both peers when present.")
    lines.append("- Roto is reported as kinematic consistency: ΔR error and per-turn center repeatability. Circle-thickness diagnostics are not dynamic positioning accuracy.")
    lines.append("- `v4-io-roto` and `v4-io-wand` are experimental soft-constraint branches built on top of `v4-io`.")
    lines.append("- `v4-io-td` keeps the V4-io anchor layout fixed and scans one common static type-level tag delay; it is a downstream compensation test, not a factory calibration.")
    if split_rows:
        vals = [float(r["delta_3d"]) for r in split_rows if r.get("delta_3d") not in {"", None}]
        if vals:
            s = summarize(vals)
            lines.append(f"- Split layout stability across anchors: median={s['p50']:.2f} mm, p95={s['p95']:.2f} mm.")
    (out_dir / "reports").mkdir(parents=True, exist_ok=True)
    (out_dir / "reports" / "full_compare_report.md").write_text("\n".join(lines), encoding="utf-8")


def prepare_out(out_dir: Path):
    plan = out_dir / "ANALYSIS_PLAN.md"
    plan_text = plan.read_text(encoding="utf-8") if plan.exists() else None
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)
    if plan_text is not None:
        plan.write_text(plan_text, encoding="utf-8")
    for sub in ["tables", "reports", "figures"]:
        (out_dir / sub).mkdir()


def run_single(mode: str, out_name: str):
    mod = load_eval_module()
    anchor_ids = list(range(8))
    out_dir = DATA / f"{out_name}{OUT_SUFFIX}"
    prepare_out(out_dir)
    print(f"[run] {out_dir.name} mode={mode} tag_solver={TAG_SOLVER_METHOD}", flush=True)
    raw_all = load_sweep_grouped()
    raw_solve = slice_raw(raw_all, "all" if mode == "1000" else "first500")
    raw_eval_all = slice_raw(raw_all, "all")
    raw_eval_first = slice_raw(raw_all, "first500")
    raw_eval_last = slice_raw(raw_all, "last500")
    mod.ANCHOR_SIGMA = compute_anchor_sigma(mod, raw_solve)
    dump_json(out_dir / "tables" / "anchor_sigma.json", {ANCHORS[i]: mod.ANCHOR_SIGMA[i] for i in range(8)})

    write_csv(out_dir / "tables" / "pair_quality_solve.csv", pair_quality_rows(mod, raw_solve, "solve"))
    fused_solve = fuse_all(mod, raw_solve, anchor_ids)
    fused_all = fuse_all(mod, raw_eval_all, anchor_ids)
    fused_first = fuse_all(mod, raw_eval_first, anchor_ids)
    fused_last = fuse_all(mod, raw_eval_last, anchor_ids)

    extra_cache = {}
    layouts: list[Layout] = []
    pair_rows = []
    static_rows = []
    roto_rows = []
    wand_rows = []
    for version, label, _meaning in VERSIONS:
        print(f"[solve] {out_name} {version}", flush=True)
        layout = solve_version(mod, version, fused_solve, anchor_ids, extra_cache)
        layouts.append(layout)
        save_layout(out_dir / version / "layout.json", layout, anchor_ids)
        if layout.extra.get("tag_delay_scan_rows"):
            write_csv(out_dir / version / "tag_delay_scan.csv", layout.extra["tag_delay_scan_rows"])
        eval_sets = [("solve", fused_solve["v3"] if version not in {"v1-old", "v2"} else fused_solve["v1" if version == "v1-old" else "v2"]),
                     ("all1000", fused_all["v3"] if version not in {"v1-old", "v2"} else fused_all["v1" if version == "v1-old" else "v2"])]
        if mode == "500":
            eval_sets.append(("holdout_last500", fused_last["v3"] if version not in {"v1-old", "v2"} else fused_last["v1" if version == "v1-old" else "v2"]))
        for eval_name, pd in eval_sets:
            rows = inter_residual_rows(mod, layout, pd, anchor_ids, eval_name)
            pair_rows.extend(rows)
            write_csv(out_dir / version / f"layout_residuals_{eval_name}.csv", rows)
        st = evaluate_static(mod, layout, anchor_ids)
        ro = evaluate_roto(mod, layout, anchor_ids)
        wa = evaluate_wand_static(mod, layout, anchor_ids)
        static_rows.extend(st)
        roto_rows.extend(ro)
        wand_rows.extend(wa)
        write_csv(out_dir / version / "static_all_captures.csv", st)
        write_csv(out_dir / version / "roto_all_captures.csv", ro)
        write_csv(out_dir / version / "wand_static_summary.csv", wa)

    pair_anchor_rows = []
    for key in sorted(set((r["version"], r["eval_set"]) for r in pair_rows)):
        pair_anchor_rows.extend(per_anchor_from_pair_rows([r for r in pair_rows if (r["version"], r["eval_set"]) == key]))
    quality_rows = autopos_quality_rows(pair_rows)
    write_csv(out_dir / "tables" / "layout_residuals_per_pair.csv", pair_rows)
    write_csv(out_dir / "tables" / "layout_residuals_per_anchor.csv", pair_anchor_rows)
    write_csv(out_dir / "tables" / "autopos_quality_summary.csv", quality_rows)
    holdout_rows = holdout_from_quality(quality_rows)
    if holdout_rows:
        write_csv(out_dir / "tables" / "holdout_generalization.csv", holdout_rows)
    write_csv(out_dir / "tables" / "delay_sanity.csv", delay_sanity(layouts))
    write_csv(out_dir / "tables" / "v5_fim_diagnostics.csv", v5_fim_rows(layouts))
    write_csv(out_dir / "tables" / "static_all_captures.csv", static_rows)
    write_csv(out_dir / "tables" / "static_group_summary.csv", group_static(static_rows))
    write_csv(out_dir / "tables" / "static_orientation_effect.csv", static_orientation_effect(static_rows))
    write_csv(out_dir / "tables" / "roto_all_captures.csv", roto_rows)
    write_csv(out_dir / "tables" / "roto_radius_consistency.csv", radius_consistency(roto_rows))
    roto_phys_rows = roto_physical_consistency(roto_rows)
    roto_phys_summary = roto_physical_summary(roto_phys_rows)
    write_csv(out_dir / "tables" / "roto_physical_consistency_all.csv", roto_phys_rows)
    write_csv(out_dir / "tables" / "roto_physical_consistency_summary.csv", roto_phys_summary)
    write_csv(out_dir / "tables" / "wand_static_summary.csv", wand_rows)

    summary_rows = []
    for version, label, meaning in VERSIONS:
        q = next((r for r in quality_rows if r["version"] == version and r["eval_set"] == "solve"), {})
        st_vals = [float(r["D3_std"]) for r in static_rows if r.get("version") == version and r.get("status") == "ok"]
        st_s = summarize(st_vals)
        ro_s = next((r for r in roto_phys_summary if r.get("version") == version), {})
        summary_rows.append({
            "version": version,
            "label": label,
            "meaning": meaning,
            "tag_delay_mm": next((l.tag_delay_mm for l in layouts if l.version == version), ""),
            "autopos_rms": q.get("rms", ""),
            "autopos_p95": q.get("p95_abs", ""),
            "static_n": st_s["n"],
            "static_median": st_s.get("p50", ""),
            "static_p95": st_s.get("p95", ""),
            "static_max": st_s.get("max", ""),
            "roto_n": ro_s.get("capture_pairs", ""),
            "roto_deltaR_mean": ro_s.get("deltaR_error_mean_mm", ""),
            "roto_deltaR_rms": ro_s.get("deltaR_error_rms_mm", ""),
            "roto_abs_deltaR_median": ro_s.get("abs_deltaR_error_median_mm", ""),
            "roto_abs_deltaR_p95": ro_s.get("abs_deltaR_error_p95_mm", ""),
            "roto_turn_center_median": ro_s.get("turn_center_rms_median_mm", ""),
            "roto_turn_center_p95": ro_s.get("turn_center_rms_p95_mm", ""),
        })
    write_csv(out_dir / "tables" / "version_summary.csv", summary_rows)
    save_figures(out_dir, summary_rows, static_rows, roto_phys_rows)
    make_report(out_dir, mode, summary_rows, quality_rows, static_rows, roto_phys_rows)


def run_split():
    mod = load_eval_module()
    anchor_ids = list(range(8))
    out_dir = DATA / f"FULL-COMPARE-500+500{OUT_SUFFIX}"
    prepare_out(out_dir)
    print(f"[run] {out_dir.name} tag_solver={TAG_SOLVER_METHOD}", flush=True)
    raw_all = load_sweep_grouped()
    raw_first = slice_raw(raw_all, "first500")
    raw_last = slice_raw(raw_all, "last500")
    raw_eval_all = slice_raw(raw_all, "all")
    mod.ANCHOR_SIGMA = compute_anchor_sigma(mod, raw_first)
    fused_first = fuse_all(mod, raw_first, anchor_ids)
    fused_last = fuse_all(mod, raw_last, anchor_ids)
    fused_all = fuse_all(mod, raw_eval_all, anchor_ids)
    write_csv(out_dir / "tables" / "pair_quality_first500.csv", pair_quality_rows(mod, raw_first, "first500"))
    write_csv(out_dir / "tables" / "pair_quality_last500.csv", pair_quality_rows(mod, raw_last, "last500"))

    pair_rows = []
    static_rows = []
    roto_rows = []
    wand_rows = []
    split_rows = []
    final_layouts = []
    holdout_rows = []
    extra_cache = {}
    for version, label, _meaning in VERSIONS:
        print(f"[solve split] {version}", flush=True)
        a = solve_version(mod, version, fused_first, anchor_ids, extra_cache)
        b = solve_version(mod, version, fused_last, anchor_ids, extra_cache)
        final, srows = consensus_layout(a, b, version, label)
        if version == "v5":
            # V5 is diagnostic-only. Preserve first-half FIM diagnostics in the
            # consensus result so the split report still has a concrete V5 table.
            final.extra.update({
                "fim_rows": a.extra.get("fim_rows", []),
                "condition_number": a.extra.get("condition_number", ""),
                "fim_source": "first500_v4_io",
            })
        split_rows.extend(srows)
        final_layouts.append(final)
        vdir = out_dir / version
        save_layout(vdir / "layout_first500.json", a, anchor_ids)
        bx, bd, _r = align_layout_to_ref(b, a)
        save_layout(vdir / "layout_last500_aligned.json", Layout(version, label, bx, bd, b.extra, b.tag_delay_mm), anchor_ids)
        save_layout(vdir / "layout_consensus.json", final, anchor_ids)
        if a.extra.get("tag_delay_scan_rows"):
            write_csv(vdir / "tag_delay_scan_first500.csv", a.extra["tag_delay_scan_rows"])
        if b.extra.get("tag_delay_scan_rows"):
            write_csv(vdir / "tag_delay_scan_last500.csv", b.extra["tag_delay_scan_rows"])
        eval_defs = [
            ("first500", fused_first),
            ("last500", fused_last),
            ("all1000", fused_all),
        ]
        for eval_name, fused in eval_defs:
            key = "v1" if version == "v1-old" else "v2" if version == "v2" else "v3"
            rows = inter_residual_rows(mod, final, fused[key], anchor_ids, eval_name)
            pair_rows.extend(rows)
            write_csv(vdir / f"layout_residuals_{eval_name}.csv", rows)
        # Cross holdout: first layout on last, last layout on first.
        key = "v1" if version == "v1-old" else "v2" if version == "v2" else "v3"
        for lname, layout, fused, train_name, hold_name in [
            ("first_to_last", a, fused_last[key], "first500", "last500"),
            ("last_to_first", Layout(version, label, bx, bd, b.extra, b.tag_delay_mm), fused_first[key], "last500", "first500"),
        ]:
            rows = inter_residual_rows(mod, layout, fused, anchor_ids, lname)
            s = summarize([float(r["residual_mm"]) for r in rows])
            holdout_rows.append({"version": version, "direction": lname, "train": train_name, "eval": hold_name, "rms": s["rms"], "p50_abs": summarize([abs(float(r["residual_mm"])) for r in rows])["p50"], "p95_abs": summarize([abs(float(r["residual_mm"])) for r in rows])["p95"], "max_abs": summarize([abs(float(r["residual_mm"])) for r in rows])["max"]})
        st = evaluate_static(mod, final, anchor_ids)
        ro = evaluate_roto(mod, final, anchor_ids)
        wa = evaluate_wand_static(mod, final, anchor_ids)
        static_rows.extend(st)
        roto_rows.extend(ro)
        wand_rows.extend(wa)
        write_csv(vdir / "static_all_captures.csv", st)
        write_csv(vdir / "roto_all_captures.csv", ro)
        write_csv(vdir / "wand_static_summary.csv", wa)

    pair_anchor_rows = []
    for key in sorted(set((r["version"], r["eval_set"]) for r in pair_rows)):
        pair_anchor_rows.extend(per_anchor_from_pair_rows([r for r in pair_rows if (r["version"], r["eval_set"]) == key]))
    quality_rows = autopos_quality_rows(pair_rows)
    write_csv(out_dir / "tables" / "split_layout_stability.csv", split_rows)
    write_csv(out_dir / "tables" / "split_layout_disagreement.csv", split_rows)
    write_csv(out_dir / "tables" / "holdout_generalization.csv", holdout_rows)
    write_csv(out_dir / "tables" / "layout_residuals_per_pair.csv", pair_rows)
    write_csv(out_dir / "tables" / "layout_residuals_per_anchor.csv", pair_anchor_rows)
    write_csv(out_dir / "tables" / "autopos_quality_summary.csv", quality_rows)
    write_csv(out_dir / "tables" / "delay_sanity.csv", delay_sanity(final_layouts))
    write_csv(out_dir / "tables" / "v5_fim_diagnostics.csv", v5_fim_rows(final_layouts))
    write_csv(out_dir / "tables" / "static_all_captures.csv", static_rows)
    write_csv(out_dir / "tables" / "static_group_summary.csv", group_static(static_rows))
    write_csv(out_dir / "tables" / "static_orientation_effect.csv", static_orientation_effect(static_rows))
    write_csv(out_dir / "tables" / "roto_all_captures.csv", roto_rows)
    write_csv(out_dir / "tables" / "roto_radius_consistency.csv", radius_consistency(roto_rows))
    roto_phys_rows = roto_physical_consistency(roto_rows)
    roto_phys_summary = roto_physical_summary(roto_phys_rows)
    write_csv(out_dir / "tables" / "roto_physical_consistency_all.csv", roto_phys_rows)
    write_csv(out_dir / "tables" / "roto_physical_consistency_summary.csv", roto_phys_summary)
    write_csv(out_dir / "tables" / "wand_static_summary.csv", wand_rows)

    summary_rows = []
    for version, label, meaning in VERSIONS:
        q = next((r for r in quality_rows if r["version"] == version and r["eval_set"] == "all1000"), {})
        st_vals = [float(r["D3_std"]) for r in static_rows if r.get("version") == version and r.get("status") == "ok"]
        st_s = summarize(st_vals)
        ro_s = next((r for r in roto_phys_summary if r.get("version") == version), {})
        summary_rows.append({
            "version": version,
            "label": label,
            "meaning": meaning,
            "tag_delay_mm": next((l.tag_delay_mm for l in final_layouts if l.version == version), ""),
            "autopos_rms": q.get("rms", ""),
            "autopos_p95": q.get("p95_abs", ""),
            "static_n": st_s["n"],
            "static_median": st_s.get("p50", ""),
            "static_p95": st_s.get("p95", ""),
            "static_max": st_s.get("max", ""),
            "roto_n": ro_s.get("capture_pairs", ""),
            "roto_deltaR_mean": ro_s.get("deltaR_error_mean_mm", ""),
            "roto_deltaR_rms": ro_s.get("deltaR_error_rms_mm", ""),
            "roto_abs_deltaR_median": ro_s.get("abs_deltaR_error_median_mm", ""),
            "roto_abs_deltaR_p95": ro_s.get("abs_deltaR_error_p95_mm", ""),
            "roto_turn_center_median": ro_s.get("turn_center_rms_median_mm", ""),
            "roto_turn_center_p95": ro_s.get("turn_center_rms_p95_mm", ""),
        })
    write_csv(out_dir / "tables" / "version_summary.csv", summary_rows)
    save_figures(out_dir, summary_rows, static_rows, roto_phys_rows, split_rows)
    make_report(out_dir, "500+500", summary_rows, quality_rows, static_rows, roto_phys_rows, split_rows)


def main() -> int:
    global DATA, SWEEP_CSV, EVAL, TAG_SOLVER_METHOD, OUT_SUFFIX, VERSIONS
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["1000", "500", "500+500", "all"], default="all")
    ap.add_argument("--data-root", default=str(DATA), help="Dataset root containing sweep1000, Static_Test, Roto_Test, Wand_Test.")
    ap.add_argument("--eval-module", default=str(EVAL), help="Path to run_full_evaluation_same_pipeline_*.py helper module.")
    ap.add_argument("--tag-solver-method", default=TAG_SOLVER_METHOD, help="Production export tag solver method: T4, T3, T2, T1, or FAST_T1 for the legacy analytic path.")
    ap.add_argument("--versions", default="", help="Comma-separated layout versions to run; blank means all versions.")
    ap.add_argument("--out-suffix", default="", help="Suffix appended to output directory names, e.g. -production-T4-real.")
    args = ap.parse_args()
    DATA = Path(args.data_root).resolve()
    SWEEP_CSV = DATA / "sweep1000" / "pairs_all.csv"
    EVAL = Path(args.eval_module).resolve()
    TAG_SOLVER_METHOD = str(args.tag_solver_method).strip() or TAG_SOLVER_METHOD
    OUT_SUFFIX = str(args.out_suffix or "")
    if args.versions.strip():
        requested = {v.strip() for v in args.versions.split(",") if v.strip()}
        missing = requested - {v[0] for v in VERSIONS}
        if missing:
            raise ValueError(f"unknown versions {sorted(missing)}; available {[v[0] for v in VERSIONS]}")
        VERSIONS = [v for v in VERSIONS if v[0] in requested]
    if args.mode in {"1000", "all"}:
        run_single("1000", "FULL-COMPARE-1000")
    if args.mode in {"500", "all"}:
        run_single("500", "FULL-COMPARE-500")
    if args.mode in {"500+500", "all"}:
        run_split()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
