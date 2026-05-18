#!/usr/bin/env python3
from __future__ import annotations

import csv
import importlib.util
import json
import math
import sys
from pathlib import Path

import numpy as np


REPO = Path(__file__).resolve().parents[4]
DATA = REPO / "autopos_pipeline" / "outdoor_20260513"
OUT = DATA / "reports" / "strict8_static"
FULL = DATA / "FULL-COMPARE-1000"
BASELINE_CSV = FULL / "v4-io" / "static_all_captures.csv"
LAYOUT_JSON = FULL / "v4-io" / "layout.json"
SIGMA_JSON = FULL / "tables" / "anchor_sigma.json"
RUN_CLEAN = DATA / "run_clean_full_compare.py"

ANCHORS = "ABCDEFGH"


def load_run_clean():
    spec = importlib.util.spec_from_file_location("run_clean_full_compare", RUN_CLEAN)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {RUN_CLEAN}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def read_csv(path: Path) -> list[dict]:
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields: list[str] = []
    for row in rows:
        for key in row:
            if key not in fields:
                fields.append(key)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for row in rows:
            w.writerow({k: row.get(k, "") for k in fields})


def fnum(v, default=float("nan")) -> float:
    try:
        return float(v)
    except Exception:
        return default


def load_v4_layout(run_clean):
    obj = json.loads(LAYOUT_JSON.read_text(encoding="utf-8"))
    anchors = sorted(obj["anchors"], key=lambda r: int(r["id"]))
    x = np.array([[float(a["x_mm"]), float(a["y_mm"]), float(a["z_mm"])] for a in anchors], dtype=float)
    d = np.array([float(a["d_anchor_mm"]) for a in anchors], dtype=float)
    return run_clean.Layout(
        version="v4-io-strict8",
        label="V4-io strict 8/8 static validation",
        x=x,
        dly=d,
        extra={"source_layout": str(LAYOUT_JSON), "filter": "only frames with exactly all 8 anchors valid"},
        tag_delay_mm=float(obj.get("tag_delay_mm", 0.0) or 0.0),
    )


def strict8_records(frames: list[dict]) -> list[dict]:
    out = []
    full = set(range(8))
    for fr in frames:
        by_anchor: dict[int, list[float]] = {}
        for a, r in fr["obs"]:
            if 0 <= int(a) < 8 and r > 0:
                by_anchor.setdefault(int(a), []).append(float(r))
        if set(by_anchor) != full:
            continue
        obs = [(a, float(np.median(vals))) for a, vals in sorted(by_anchor.items())]
        out.append({"peer": fr["peer"], "sweep": fr["sweep"], "t": fr["t"], "obs": obs})
    return out


def summarize_metric(vals: list[float]) -> dict:
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
    }


def md_table(headers: list[str], rows: list[list[object]]) -> str:
    out = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join(["---"] * len(headers)) + " |",
    ]
    out.extend("| " + " | ".join(str(x) for x in row) + " |" for row in rows)
    return "\n".join(out)


def fmt(v, nd=1) -> str:
    try:
        x = float(v)
    except Exception:
        return ""
    if not math.isfinite(x):
        return ""
    return f"{x:.{nd}f}"


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    run_clean = load_run_clean()
    sigma_obj = json.loads(SIGMA_JSON.read_text(encoding="utf-8"))
    run_clean.load_eval_module().ANCHOR_SIGMA = {i: float(sigma_obj[ANCHORS[i]]) for i in range(8)}
    # solve_position_fast reads ANCHOR_SIGMA from the module object we pass in,
    # so use the actual eval module instance.
    eval_mod = run_clean.load_eval_module()
    eval_mod.ANCHOR_SIGMA = {i: float(sigma_obj[ANCHORS[i]]) for i in range(8)}
    layout = load_v4_layout(run_clean)
    anchor_ids = list(range(8))

    baseline_rows = {r["ID"]: r for r in read_csv(BASELINE_CSV) if r.get("status") == "ok"}
    rows: list[dict] = []
    for sid in [f"ID{i:02d}" for i in range(1, 25)]:
        loc, height, facing = run_clean.STATIC_META.get(sid, ("unknown", "unknown", "unknown"))
        cap_dirs = run_clean.latest_dirs(DATA / "Static_Test", "ID")
        if sid not in cap_dirs:
            rows.append({
                "ID": sid,
                "status": "missing",
                "location": loc,
                "height": height,
                "facing": facing,
            })
            continue
        by_peer = run_clean.load_frames_by_peer(cap_dirs[sid] / "tr_all.csv")
        frames = []
        for peer_frames in by_peer.values():
            frames.extend(peer_frames)
        frames = sorted(frames, key=lambda r: (r["t"], r["sweep"]))
        strict = strict8_records(frames)
        pos, _t, counts = run_clean.solve_positions(eval_mod, strict, layout, anchor_ids)
        row = {
            "ID": sid,
            "status": "ok" if pos.shape[0] >= 2 else "insufficient",
            "location": loc,
            "height": height,
            "facing": facing,
            "all_available_frames": len(frames),
            "strict8_frames": int(pos.shape[0]),
            "strict8_ratio_percent": 100.0 * int(pos.shape[0]) / max(1, len(frames)),
        }
        row.update(run_clean.position_summary(pos, counts))
        base = baseline_rows.get(sid)
        if base:
            row.update({
                "allavail_N_frames": base.get("N_frames", ""),
                "allavail_X_std": base.get("X_std", ""),
                "allavail_Y_std": base.get("Y_std", ""),
                "allavail_Z_std": base.get("Z_std", ""),
                "allavail_D3_std": base.get("D3_std", ""),
                "delta_D3_strict8_minus_allavail": fnum(row.get("D3_std")) - fnum(base.get("D3_std")),
                "delta_Z_strict8_minus_allavail": fnum(row.get("Z_std")) - fnum(base.get("Z_std")),
            })
        rows.append(row)

    ok = [r for r in rows if r.get("status") == "ok"]
    base_ok = [baseline_rows[r["ID"]] for r in ok if r["ID"] in baseline_rows]

    summary = {
        "strict8_capture_count": len(ok),
        "strict8_total_frames": int(sum(int(fnum(r.get("strict8_frames"), 0)) for r in ok)),
        "all_available_total_frames_matching_captures": int(sum(int(fnum(b.get("N_frames"), 0)) for b in base_ok)),
    }
    for prefix, source in [("strict8", ok), ("allavail", base_ok)]:
        for metric, col in [("X", "X_std"), ("Y", "Y_std"), ("Z", "Z_std"), ("D3", "D3_std")]:
            s = summarize_metric([fnum(r.get(col)) for r in source])
            for k in ["rms", "p50", "p75", "p95", "max"]:
                summary[f"{prefix}_{metric}_{k}"] = s.get(k, float("nan"))
    summary["strict8_frame_retention_percent"] = (
        100.0 * summary["strict8_total_frames"] / max(1, summary["all_available_total_frames_matching_captures"])
    )

    write_csv(OUT / "strict8_static_by_capture.csv", rows)
    write_csv(OUT / "strict8_summary.csv", [summary])

    rows_md = [
        ["All-available", len(base_ok), int(summary["all_available_total_frames_matching_captures"]),
         fmt(summary["allavail_X_p50"]), fmt(summary["allavail_Y_p50"]), fmt(summary["allavail_Z_p50"]),
         fmt(summary["allavail_D3_p50"]), fmt(summary["allavail_D3_rms"]), fmt(summary["allavail_D3_p95"])],
        ["Strict 8/8 only", len(ok), int(summary["strict8_total_frames"]),
         fmt(summary["strict8_X_p50"]), fmt(summary["strict8_Y_p50"]), fmt(summary["strict8_Z_p50"]),
         fmt(summary["strict8_D3_p50"]), fmt(summary["strict8_D3_rms"]), fmt(summary["strict8_D3_p95"])],
    ]
    worst = sorted(ok, key=lambda r: fnum(r.get("D3_std")), reverse=True)[:8]
    worst_rows = [
        [
            r["ID"], r["location"], r["height"], r["facing"],
            int(fnum(r["strict8_frames"], 0)), fmt(r["strict8_ratio_percent"]),
            fmt(r["X_std"]), fmt(r["Y_std"]), fmt(r["Z_std"]), fmt(r["D3_std"]),
            fmt(r.get("delta_D3_strict8_minus_allavail")),
        ]
        for r in worst
    ]
    retention_rows = [
        [
            r["ID"], r["location"], r["height"], r["facing"],
            int(fnum(r["all_available_frames"], 0)), int(fnum(r["strict8_frames"], 0)),
            fmt(r["strict8_ratio_percent"]), fmt(r.get("allavail_D3_std")), fmt(r.get("D3_std")),
        ]
        for r in sorted(ok, key=lambda r: fnum(r.get("strict8_ratio_percent")))[:10]
    ]

    readme = f"""# Strict 8/8 Static Validation

这个目录只回答一个问题：如果 static Tag validation **只保留 8 个 anchor 全部有效的帧**，少一个 anchor 都丢掉，那么 V4-io 的 repeatability 会变成什么？

输入：

- Layout: `{LAYOUT_JSON}`
- Baseline: `{BASELINE_CSV}`
- Filter: per-frame valid anchors must be exactly A-H all present.
- Solver: same downstream sigma-weighted Huber position solve as clean rebuild.

## Main result

{md_table(["Condition", "captures", "frames", "X med", "Y med", "Z med", "3D med", "3D RMS", "3D p95"], rows_md)}

Frame retention under strict 8/8: `{summary["strict8_frame_retention_percent"]:.1f}%`.

## Interpretation

Strict 8/8 会让 X/Y 和 tail 小幅变好：X median 从 `{summary["allavail_X_p50"]:.1f}mm` 变成 `{summary["strict8_X_p50"]:.1f}mm`，Y median 从 `{summary["allavail_Y_p50"]:.1f}mm` 变成 `{summary["strict8_Y_p50"]:.1f}mm`，3D RMS 从 `{summary["allavail_D3_rms"]:.1f}mm` 变成 `{summary["strict8_D3_rms"]:.1f}mm`，3D p95 从 `{summary["allavail_D3_p95"]:.1f}mm` 变成 `{summary["strict8_D3_p95"]:.1f}mm`。

但最重要的发现是：Z median 基本不变，从 `{summary["allavail_Z_p50"]:.1f}mm` 到 `{summary["strict8_Z_p50"]:.1f}mm`。也就是说，缺 anchor 帧主要恶化 horizontal/tail；Z 弱点即使在 full-response frames 里也存在。

这说明之前 all-available 的 `49mm` 结果不是被“缺 anchor 帧”简单污染出来的。更准确地说：

1. **缺 anchor 帧确实会污染 X/Y 和 tail**。strict 8/8 的 X/Y median 和 3D p95 明显更好，说明少 anchor epoch 会给 all-available 结果带来一部分尾部退化。
2. **Z weakness 更像 geometry-driven，而不是 availability-driven**。Z median 几乎不随 strict 8/8 filtering 改变，说明只靠“每帧凑齐 8 anchor”不能解决 Z。
3. **它不是 100mm+ 的主因**。strict 8/8 只把 3D median 从 `{summary["allavail_D3_p50"]:.1f}mm` 改到 `{summary["strict8_D3_p50"]:.1f}mm`，没有从 `100mm+` 拉回 `40mm` 这种数量级变化。
4. **低冗余危险主要发生在强制 keep-4/5/6 或 selector 长时间退化时**。All-available solve 即使有些帧少于 8 个 anchor，只要多数帧仍有 6/7/8 个 anchor，robust solver 可以吸收一部分波动。
5. **strict 8/8 是 coverage/availability 指标，不一定是 production accuracy 上界**。它只保留 `{summary["strict8_frame_retention_percent"]:.1f}%` 的帧；这些帧更干净，但不是完整 session。

因此，当前结论应该改成：

> Strict 8/8 filtering mainly improves X/Y and tail behavior, while Z remains almost unchanged. This indicates that the persistent Z weakness is geometry-driven rather than availability-driven. The 100mm+ regime is more consistent with long low-redundancy periods, such as 4/5-anchor selector behavior, rather than occasional missing-anchor frames alone.

## Worst strict-8 captures

{md_table(["ID", "loc", "height", "facing", "strict8 frames", "strict8 %", "X", "Y", "Z", "3D", "delta 3D vs allavail"], worst_rows)}

## Lowest strict-8 retention captures

{md_table(["ID", "loc", "height", "facing", "all frames", "strict8 frames", "strict8 %", "allavail 3D", "strict8 3D"], retention_rows)}

## Files

- `strict8_static_by_capture.csv`: per-capture strict 8/8 result and all-available comparison.
- `strict8_summary.csv`: aggregate median / p95 / RMS.
"""
    (OUT / "README.md").write_text(readme, encoding="utf-8")
    print(readme)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
