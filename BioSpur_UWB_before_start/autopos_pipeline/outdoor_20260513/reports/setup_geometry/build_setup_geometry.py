#!/usr/bin/env python3
from __future__ import annotations

import csv
import json
from collections import Counter, defaultdict
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np


REPO = Path(__file__).resolve().parents[4]
DATA = REPO / "autopos_pipeline" / "outdoor_20260513"
OUT = DATA / "reports" / "setup_geometry"
LAYOUT_JSON = DATA / "FULL-COMPARE-1000" / "v4-io" / "layout.json"
STATIC = DATA / "Static_Test"
ANCHORS = "ABCDEFGH"


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


def load_layout() -> list[dict]:
    obj = json.loads(LAYOUT_JSON.read_text(encoding="utf-8"))
    rows = []
    for a in sorted(obj["anchors"], key=lambda r: int(r["id"])):
        # The AutoPos layout is gauge-invariant up to a Z mirror. For the
        # report, use the physical convention requested during field work:
        # ABCD are the lower layer, EFGH are the upper layer.
        z_solver = float(a["z_mm"])
        z_physical = -z_solver
        aid = int(a["id"])
        rows.append({
            "anchor": a["label"],
            "id": aid,
            "x_mm": float(a["x_mm"]),
            "y_mm": float(a["y_mm"]),
            "z_mm": z_physical,
            "z_solver_mm": z_solver,
            "d_anchor_mm": float(a["d_anchor_mm"]),
            "layer": "lower" if aid < 4 else "upper",
        })
    return rows


def latest_dirs(base: Path, prefix: str) -> dict[str, Path]:
    out = {}
    for p in sorted(base.iterdir() if base.exists() else []):
        if p.is_dir() and p.name.startswith(prefix) and (p / "tr_all.csv").exists():
            sid = p.name.split("_", 1)[0]
            out[sid] = p
    return out


def static_anchor_counts() -> tuple[list[dict], list[dict]]:
    per_cap = []
    total = Counter()
    total_eligible = Counter()
    for sid, p in sorted(latest_dirs(STATIC, "ID").items()):
        grouped: dict[tuple[str, int], set[int]] = defaultdict(set)
        with (p / "tr_all.csv").open(newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                try:
                    valid = int(float(row.get("valid") or 0))
                    status = row.get("status") or "O"
                    aid = int(float(row["anchor_id"]))
                    rng = float(row.get("range_mm") or row.get("raw_mm") or 0)
                    sweep = int(float(row["sweep"]))
                    peer = (row.get("peer_name") or "unknown").strip() or "unknown"
                except Exception:
                    continue
                if valid == 1 and status in {"", "O"} and 0 <= aid < 8 and rng > 0:
                    grouped[(peer, sweep)].add(aid)
        counts = Counter(len(v) for v in grouped.values())
        for k, v in counts.items():
            total[k] += v
            if k >= 4:
                total_eligible[k] += v
        denom = sum(counts.values())
        elig = sum(v for k, v in counts.items() if k >= 4)
        row = {"ID": sid, "total_epochs": denom, "solve_eligible_epochs_ge4": elig}
        for k in range(0, 9):
            row[f"count_{k}"] = counts.get(k, 0)
            row[f"pct_{k}"] = 100.0 * counts.get(k, 0) / max(1, denom)
        row["pct_ge4"] = 100.0 * elig / max(1, denom)
        row["pct_ge7"] = 100.0 * sum(v for k, v in counts.items() if k >= 7) / max(1, denom)
        row["pct_ge8"] = 100.0 * counts.get(8, 0) / max(1, denom)
        per_cap.append(row)

    total_epochs = sum(total.values())
    eligible_epochs = sum(total_eligible.values())
    overall = []
    for k in range(0, 9):
        c = total.get(k, 0)
        overall.append({
            "anchor_count": k,
            "epochs": c,
            "percent_all_grouped_epochs": 100.0 * c / max(1, total_epochs),
            "percent_solve_eligible_ge4": 100.0 * c / max(1, eligible_epochs) if k >= 4 else 0.0,
        })
    return overall, per_cap


def plot_layout(rows: list[dict]) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    fig, axes = plt.subplots(1, 3, figsize=(13, 4), dpi=160)
    pts = {r["anchor"]: np.array([r["x_mm"], r["y_mm"], r["z_mm"]], dtype=float) for r in rows}
    colors = {"lower": "#1f77b4", "upper": "#d62728"}
    for ax, dims, title in [
        (axes[0], (0, 1), "XY layout"),
        (axes[1], (0, 2), "XZ layout"),
        (axes[2], (1, 2), "YZ layout"),
    ]:
        for r in rows:
            p = pts[r["anchor"]]
            ax.scatter(p[dims[0]], p[dims[1]], s=80, c=colors[r["layer"]], edgecolor="black", linewidth=0.7)
            ax.text(p[dims[0]] + 45, p[dims[1]] + 45, r["anchor"], fontsize=10, weight="bold")
        ax.set_title(title)
        ax.set_xlabel(["X", "Y", "Z"][dims[0]] + " (mm)")
        ax.set_ylabel(["X", "Y", "Z"][dims[1]] + " (mm)")
        ax.grid(True, alpha=0.3)
        ax.set_aspect("equal", adjustable="datalim")
    handles = [
        plt.Line2D([0], [0], marker="o", color="w", markerfacecolor=colors["lower"], markeredgecolor="black", label="lower A-D", markersize=8),
        plt.Line2D([0], [0], marker="o", color="w", markerfacecolor=colors["upper"], markeredgecolor="black", label="upper E-H", markersize=8),
    ]
    axes[0].legend(handles=handles, loc="best")
    fig.tight_layout()
    fig.savefig(OUT / "anchor_layout_v4io.png")
    plt.close(fig)


def plot_geometry_report(rows: list[dict]) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    pts = {r["anchor"]: np.array([r["x_mm"], r["y_mm"], r["z_mm"]], dtype=float) for r in rows}
    colors = {"lower": "#1f77b4", "upper": "#d62728"}

    fig = plt.figure(figsize=(12, 6.5), dpi=180)
    ax3d = fig.add_subplot(1, 2, 1, projection="3d")
    axxy = fig.add_subplot(1, 2, 2)

    lower_order = ["A", "B", "C", "D", "A"]
    upper_order = ["E", "F", "G", "H", "E"]
    vertical_pairs = [("A", "E"), ("B", "F"), ("C", "G"), ("D", "H")]

    for order, color, label in [(lower_order, colors["lower"], "lower A-D"), (upper_order, colors["upper"], "upper E-H")]:
        arr = np.array([pts[a] for a in order])
        ax3d.plot(arr[:, 0], arr[:, 1], arr[:, 2], color=color, linewidth=2.0, label=label)
        axxy.plot(arr[:, 0], arr[:, 1], color=color, linewidth=2.0, label=label)

    for a, b in vertical_pairs:
        arr = np.array([pts[a], pts[b]])
        ax3d.plot(arr[:, 0], arr[:, 1], arr[:, 2], color="#666666", linestyle="--", linewidth=1.2, alpha=0.8)
        axxy.plot(arr[:, 0], arr[:, 1], color="#999999", linestyle="--", linewidth=0.9, alpha=0.5)

    for r in rows:
        p = pts[r["anchor"]]
        color = colors[r["layer"]]
        ax3d.scatter(p[0], p[1], p[2], s=70, color=color, edgecolor="black", linewidth=0.7)
        ax3d.text(p[0] + 55, p[1] + 55, p[2] + 55, r["anchor"], fontsize=11, weight="bold")
        axxy.scatter(p[0], p[1], s=75, color=color, edgecolor="black", linewidth=0.7)
        axxy.text(p[0] + 55, p[1] + 55, r["anchor"], fontsize=11, weight="bold")

    xyz = np.array([pts[a] for a in ANCHORS])
    x_span = np.ptp(xyz[:, 0])
    y_span = np.ptp(xyz[:, 1])
    z_span = np.ptp(xyz[:, 2])
    ax3d.set_title(f"Recovered 3D anchor layout\nXY {x_span/1000:.2f}m x {y_span/1000:.2f}m, Z span {z_span/1000:.2f}m")
    ax3d.set_xlabel("X (mm)")
    ax3d.set_ylabel("Y (mm)")
    ax3d.set_zlabel("Z (mm)")
    ax3d.view_init(elev=22, azim=-58)
    ax3d.legend(loc="upper left")

    axxy.set_title("XY footprint")
    axxy.set_xlabel("X (mm)")
    axxy.set_ylabel("Y (mm)")
    axxy.grid(True, alpha=0.3)
    axxy.set_aspect("equal", adjustable="datalim")
    axxy.legend(loc="best")

    fig.tight_layout()
    fig.savefig(OUT / "anchor_geometry_report.png")
    plt.close(fig)


def plot_counts(overall: list[dict]) -> None:
    xs = [r["anchor_count"] for r in overall]
    ys = [r["percent_solve_eligible_ge4"] for r in overall]
    fig, ax = plt.subplots(figsize=(7, 4), dpi=160)
    ax.bar(xs, ys, color="#4c78a8")
    ax.set_xlabel("valid anchors per static epoch")
    ax.set_ylabel("% of solve-eligible epochs (>=4 anchors)")
    ax.set_title("Static per-epoch anchor count distribution")
    ax.set_xticks(range(0, 9))
    ax.grid(axis="y", alpha=0.3)
    fig.tight_layout()
    fig.savefig(OUT / "static_anchor_count_distribution.png")
    plt.close(fig)


def md_table(headers: list[str], rows: list[list[object]]) -> str:
    out = ["| " + " | ".join(headers) + " |", "| " + " | ".join(["---"] * len(headers)) + " |"]
    out.extend("| " + " | ".join(str(x) for x in row) + " |" for row in rows)
    return "\n".join(out)


def f(v: float) -> str:
    x = float(v)
    if abs(x) < 0.05:
        x = 0.0
    return f"{x:.1f}"


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    layout = load_layout()
    write_csv(OUT / "anchor_layout_v4io.csv", layout)

    xyz = np.array([[r["x_mm"], r["y_mm"], r["z_mm"]] for r in layout], dtype=float)
    upper = np.array([[r["x_mm"], r["y_mm"], r["z_mm"]] for r in layout if r["layer"] == "upper"], dtype=float)
    lower = np.array([[r["x_mm"], r["y_mm"], r["z_mm"]] for r in layout if r["layer"] == "lower"], dtype=float)
    summary = [{
        "x_span_mm": float(np.max(xyz[:, 0]) - np.min(xyz[:, 0])),
        "y_span_mm": float(np.max(xyz[:, 1]) - np.min(xyz[:, 1])),
        "z_span_mm": float(np.max(xyz[:, 2]) - np.min(xyz[:, 2])),
        "upper_z_mean_mm": float(np.mean(upper[:, 2])),
        "lower_z_mean_mm": float(np.mean(lower[:, 2])),
        "layer_separation_mm": float(np.mean(upper[:, 2]) - np.mean(lower[:, 2])),
    }]
    write_csv(OUT / "anchor_geometry_summary.csv", summary)

    overall, per_cap = static_anchor_counts()
    write_csv(OUT / "static_anchor_count_distribution.csv", overall)
    write_csv(OUT / "static_anchor_count_by_capture.csv", per_cap)
    plot_layout(layout)
    plot_geometry_report(layout)
    plot_counts(overall)

    layout_rows = [[r["anchor"], r["layer"], f(r["x_mm"]), f(r["y_mm"]), f(r["z_mm"]), f(r["d_anchor_mm"])] for r in layout]
    count_rows = [
        [r["anchor_count"], r["epochs"], f(r["percent_solve_eligible_ge4"])]
        for r in overall if r["anchor_count"] >= 4
    ]
    top_low = sorted(per_cap, key=lambda r: r["pct_ge8"])[:8]
    low_rows = [[r["ID"], r["total_epochs"], r["solve_eligible_epochs_ge4"], f(r["pct_ge8"]), f(r["pct_ge7"])] for r in top_low]
    s = summary[0]
    readme = f"""# Setup Geometry and Anchor Availability

本目录给最终报告提供两个 setup-level 证据：

1. V4-io anchor layout 的 A-H 3D 坐标。
2. Static captures 中每个 epoch 实际可用 anchor 数量的分布。

## Anchor Layout

![Anchor geometry report](anchor_geometry_report.png)

Three-view helper:

![Anchor layout](anchor_layout_v4io.png)

{md_table(["Anchor", "Layer", "X mm", "Y mm", "Z mm", "delay mm"], layout_rows)}

Geometry summary:

| x span | y span | z span | upper z mean | lower z mean | layer separation |
| ---: | ---: | ---: | ---: | ---: | ---: |
| {f(s["x_span_mm"])} | {f(s["y_span_mm"])} | {f(s["z_span_mm"])} | {f(s["upper_z_mean_mm"])} | {f(s["lower_z_mean_mm"])} | {f(s["layer_separation_mm"])} |

Interpretation: 这里使用物理展示坐标，已经把 solver 的 Z mirror 翻到现场约定方向：A-D 是 lower layer，E-H 是 upper layer。整体 XY footprint 约 `{s["x_span_mm"]/1000:.2f}m x {s["y_span_mm"]/1000:.2f}m`，上下层平均 Z separation 约 `{s["layer_separation_mm"]/1000:.2f}m`。这个 Z 翻转只影响报告展示，不改变 solver residual / repeatability 结果。

## Static Anchor Count Distribution

![Static anchor count distribution](static_anchor_count_distribution.png)

{md_table(["valid anchors", "epochs", "% of solve-eligible epochs"], count_rows)}

这个分布说明：`all-available` 并不等于 strict all-8；但本次 broadcast static dataset 仍然是高冗余的，几乎全部 solve-eligible epochs 都是 7/8 anchor。也就是说，当前数据里的 all-available 主要是 7/8-anchor solve，而不是旧 unicast/selector 条件下那种长期 4/5-anchor solve。

## Lowest 8/8 Retention Captures

{md_table(["ID", "total epochs", "solve eligible >=4", "% ge8", "% ge7"], low_rows)}

## Files

- `anchor_layout_v4io.csv`
- `anchor_geometry_summary.csv`
- `static_anchor_count_distribution.csv`
- `static_anchor_count_by_capture.csv`
- `anchor_layout_v4io.png`
- `anchor_geometry_report.png`
- `static_anchor_count_distribution.png`
"""
    (OUT / "README.md").write_text(readme, encoding="utf-8")
    print(readme)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
