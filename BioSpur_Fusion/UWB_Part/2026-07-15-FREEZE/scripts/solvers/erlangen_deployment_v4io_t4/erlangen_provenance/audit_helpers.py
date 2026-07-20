from __future__ import annotations

import csv
import json
import math
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd


ANCHOR_LABELS = list("ABCDEFGH")


@dataclass
class AuditInputs:
    data_dir: Path
    capture_root: Path
    opti_root: Path
    staged_sweep_csv: Path | None
    derived_tables_dir: Path | None


def relpath(path: Path | None, root: Path) -> str:
    if path is None:
        return "-"
    try:
        return str(path.resolve().relative_to(root.resolve()))
    except ValueError:
        return str(path)


def markdown_table(rows: Iterable[dict], headers: list[str]) -> str:
    rows = list(rows)
    if not rows:
        return "_No rows._\n"
    out = ["| " + " | ".join(headers) + " |"]
    out.append("| " + " | ".join("---" for _ in headers) + " |")
    for row in rows:
        vals = []
        for h in headers:
            value = row.get(h, "")
            if isinstance(value, float):
                if math.isnan(value):
                    value = ""
                elif abs(value) >= 1000:
                    value = f"{value:.1f}"
                else:
                    value = f"{value:.3f}"
            vals.append(str(value).replace("\n", " "))
        out.append("| " + " | ".join(vals) + " |")
    return "\n".join(out) + "\n"


def dataframe_schema(df: pd.DataFrame) -> list[dict]:
    rows = []
    n = len(df)
    for col in df.columns:
        non_null = int(df[col].notna().sum())
        nulls = n - non_null
        sample = ""
        series = df[col].dropna()
        if not series.empty:
            sample = str(series.iloc[0])
        rows.append(
            {
                "column": col,
                "dtype": str(df[col].dtype),
                "non_null": non_null,
                "nulls": nulls,
                "sample": sample,
            }
        )
    return rows


def first_existing(paths: Iterable[Path]) -> Path | None:
    for path in paths:
        if path.exists():
            return path
    return None


def load_workspace(data_dir: Path) -> dict:
    workspace_path = data_dir / "workspace.json"
    if workspace_path.exists():
        return json.loads(workspace_path.read_text())
    return {}


def discover_inputs(data_dir: Path) -> AuditInputs:
    workspace = load_workspace(data_dir)
    session = workspace.get("capture_session")
    if session:
        capture_root = data_dir / "captures" / session
    else:
        capture_candidates = sorted((data_dir / "captures").glob("*"))
        capture_root = capture_candidates[0] if capture_candidates else data_dir / "captures"

    opti_root = data_dir / "opti_captures"
    staged_sweep = first_existing(
        [
            data_dir / "solver" / "work" / "field_dataset_staged" / "sweep1000" / "pairs_all.csv",
            data_dir / "solver" / "outputs" / "v1_to_v4_io_field_check" / "tables" / "pair_quality_solve.csv",
        ]
    )
    derived = first_existing(
        [
            data_dir / "Analysis" / "official_extra_analysis" / "FULL" / "tables",
            data_dir / "Analysis" / "official_extra_analysis" / "FULL_US" / "tables",
        ]
    )
    return AuditInputs(
        data_dir=data_dir,
        capture_root=capture_root,
        opti_root=opti_root,
        staged_sweep_csv=staged_sweep,
        derived_tables_dir=derived,
    )


def find_tr_all(capture_dir: Path) -> Path | None:
    direct = sorted(capture_dir.glob("tag_capture_*/tr_all.csv"))
    if direct:
        return direct[0]
    recursive = sorted(capture_dir.glob("**/tr_all.csv"))
    return recursive[0] if recursive else None


def list_capture_dirs(capture_root: Path, prefix: str) -> list[Path]:
    return sorted(p for p in capture_root.glob(f"{prefix}_*") if p.is_dir())


def infer_capture_id(path: Path, kind: str) -> str:
    name = path.name
    if kind == "static":
        m = re.match(r"static_(ID\d+)_", name)
    elif kind == "roto":
        m = re.match(r"roto_(R[^_]+)_", name)
    else:
        m = None
    return m.group(1) if m else name


def load_tag_capture_frame(capture_dirs: list[Path], kind: str) -> tuple[pd.DataFrame, list[dict]]:
    frames: list[pd.DataFrame] = []
    inventory: list[dict] = []
    for capture_dir in capture_dirs:
        capture_id = infer_capture_id(capture_dir, kind)
        tr_all = find_tr_all(capture_dir)
        if tr_all is None:
            inventory.append(
                {
                    "id": capture_id,
                    "path": capture_dir,
                    "tr_all": None,
                    "status": "missing_tr_all.csv",
                    "rows": 0,
                }
            )
            continue
        df = pd.read_csv(tr_all, low_memory=False)
        df.insert(0, "capture_kind", kind)
        df.insert(1, "capture_id", capture_id)
        df.insert(2, "capture_path", str(capture_dir))
        frames.append(df)
        inventory.append(
            {
                "id": capture_id,
                "path": capture_dir,
                "tr_all": tr_all,
                "status": "ok",
                "rows": int(len(df)),
            }
        )
    if frames:
        return pd.concat(frames, ignore_index=True), inventory
    return pd.DataFrame(), inventory


def epoch_range(df: pd.DataFrame) -> tuple[str, str]:
    if "host_epoch_s" not in df.columns or df.empty:
        return "-", "-"
    vals = pd.to_numeric(df["host_epoch_s"], errors="coerce").dropna()
    if vals.empty:
        return "-", "-"
    start = datetime.fromtimestamp(float(vals.min())).isoformat(timespec="seconds")
    end = datetime.fromtimestamp(float(vals.max())).isoformat(timespec="seconds")
    return start, end


def compact_status_counts(df: pd.DataFrame) -> str:
    if "status" not in df.columns or df.empty:
        return "-"
    counts = df["status"].fillna("<NA>").astype(str).value_counts().sort_index()
    return ", ".join(f"{k}:{int(v)}" for k, v in counts.items())


def valid_mask(df: pd.DataFrame) -> pd.Series:
    if df.empty:
        return pd.Series([], dtype=bool)
    if "valid" in df.columns:
        return pd.to_numeric(df["valid"], errors="coerce").fillna(0).astype(int) == 1
    if "status" in df.columns:
        return df["status"].astype(str).eq("O")
    return pd.Series(True, index=df.index)


def tag_capture_summary(df: pd.DataFrame, kind: str) -> dict:
    if df.empty:
        return {
            "kind": kind,
            "captures": 0,
            "rows": 0,
            "valid_rows": 0,
            "valid_percent": 0.0,
            "anchors_seen": "",
            "peers_seen": "",
            "timestamp_start": "-",
            "timestamp_end": "-",
            "status_counts": "-",
        }
    vm = valid_mask(df)
    anchors = sorted(pd.to_numeric(df["anchor_id"], errors="coerce").dropna().astype(int).unique())
    peers = sorted(df["peer_name"].dropna().astype(str).unique()) if "peer_name" in df.columns else []
    start, end = epoch_range(df)
    return {
        "kind": kind,
        "captures": int(df["capture_id"].nunique()),
        "rows": int(len(df)),
        "valid_rows": int(vm.sum()),
        "valid_percent": float(vm.mean() * 100.0),
        "anchors_seen": ",".join(str(a) for a in anchors),
        "peers_seen": ",".join(peers),
        "timestamp_start": start,
        "timestamp_end": end,
        "status_counts": compact_status_counts(df),
    }


def per_capture_anchor_counts(df: pd.DataFrame, max_rows: int | None = None) -> list[dict]:
    if df.empty:
        return []
    vm = valid_mask(df)
    work = df.copy()
    work["_valid"] = vm.astype(int)
    group_cols = ["capture_id", "peer_name", "anchor_id"]
    grouped = (
        work.groupby(group_cols, dropna=False)
        .agg(rows=("anchor_id", "size"), valid_rows=("_valid", "sum"))
        .reset_index()
    )
    grouped["valid_percent"] = grouped["valid_rows"] / grouped["rows"] * 100.0
    grouped = grouped.sort_values(group_cols)
    if max_rows is not None:
        grouped = grouped.head(max_rows)
    rows = []
    for _, r in grouped.iterrows():
        rows.append(
            {
                "capture_id": r["capture_id"],
                "peer_name": r["peer_name"],
                "anchor_id": int(r["anchor_id"]) if pd.notna(r["anchor_id"]) else "",
                "rows": int(r["rows"]),
                "valid_rows": int(r["valid_rows"]),
                "valid_percent": float(r["valid_percent"]),
            }
        )
    return rows


def static_position_matrix(df: pd.DataFrame) -> list[dict]:
    if df.empty:
        return []
    vm = valid_mask(df)
    work = df.copy()
    work["_valid"] = vm.astype(int)
    pivot = work.pivot_table(
        index="capture_id",
        columns="anchor_id",
        values="_valid",
        aggfunc="sum",
        fill_value=0,
    )
    rows = []
    for capture_id in sorted(pivot.index):
        row = {"position": capture_id}
        for anchor in range(8):
            row[str(anchor)] = int(pivot.loc[capture_id, anchor]) if anchor in pivot.columns else 0
        rows.append(row)
    return rows


def parse_sweep_logs(sweep_root: Path) -> pd.DataFrame:
    rows = []
    pattern = re.compile(r"\[AUTOPOS\]\s+SW-([A-H]),(.+)")
    for log_path in sorted(sweep_root.glob("round_*/master.log")):
        with log_path.open(errors="replace") as f:
            for line_no, line in enumerate(f, start=1):
                m = pattern.search(line)
                if not m:
                    continue
                master = m.group(1)
                fields = [x.strip() for x in m.group(2).strip().split(",")]
                for idx in range(0, len(fields) - 2, 3):
                    responder = fields[idx]
                    try:
                        dist = float(fields[idx + 1])
                        quality = float(fields[idx + 2])
                    except ValueError:
                        continue
                    pair = "-".join(sorted([master, responder]))
                    rows.append(
                        {
                            "a": pair[0],
                            "b": pair[2],
                            "master": master,
                            "dist_mm": dist,
                            "quality_percent": quality,
                            "raw_mm": dist,
                            "ok": 1,
                            "fail": 0,
                            "source_log": str(log_path),
                            "line_no": line_no,
                        }
                    )
    return pd.DataFrame(rows)


def load_sweep_pairs(inputs: AuditInputs) -> tuple[pd.DataFrame, str]:
    if inputs.staged_sweep_csv and inputs.staged_sweep_csv.exists():
        df = pd.read_csv(inputs.staged_sweep_csv)
        source = relpath(inputs.staged_sweep_csv, inputs.data_dir)
    else:
        sweep_dirs = sorted(inputs.capture_root.glob("sweep_*/sweep1000"))
        df = parse_sweep_logs(sweep_dirs[0]) if sweep_dirs else pd.DataFrame()
        source = relpath(sweep_dirs[0] if sweep_dirs else None, inputs.data_dir)
    if not df.empty:
        df["initiator"] = df["master"].astype(str)
        df["responder"] = np.where(df["initiator"] == df["a"], df["b"], df["a"])
        df["valid"] = pd.to_numeric(df.get("ok", 1), errors="coerce").fillna(1).astype(int) == 1
    return df, source


def sweep_summary(df: pd.DataFrame) -> dict:
    if df.empty:
        return {
            "rows": 0,
            "valid_rows": 0,
            "directed_links": 0,
            "pairs": 0,
            "range_median_mm": np.nan,
            "quality_median_percent": np.nan,
        }
    valid = df["valid"].astype(bool)
    pairs = df[["a", "b"]].astype(str).agg("-".join, axis=1).nunique()
    directed = df[["initiator", "responder"]].astype(str).agg("->".join, axis=1).nunique()
    quality = pd.to_numeric(df["quality_percent"], errors="coerce") if "quality_percent" in df else pd.Series(dtype=float)
    return {
        "rows": int(len(df)),
        "valid_rows": int(valid.sum()),
        "directed_links": int(directed),
        "pairs": int(pairs),
        "range_median_mm": float(pd.to_numeric(df.loc[valid, "dist_mm"], errors="coerce").median()),
        "quality_median_percent": float(quality.loc[valid].median()) if not quality.empty else np.nan,
    }


def directed_coverage_matrix(df: pd.DataFrame) -> list[dict]:
    if df.empty:
        return []
    valid = df[df["valid"].astype(bool)].copy()
    counts = valid.groupby(["initiator", "responder"]).size()
    rows = []
    for src in ANCHOR_LABELS:
        row = {"from\\to": src}
        for dst in ANCHOR_LABELS:
            row[dst] = "-" if src == dst else int(counts.get((src, dst), 0))
        rows.append(row)
    return rows


def sweep_pair_counts(df: pd.DataFrame) -> list[dict]:
    if df.empty:
        return []
    rows = []
    for (a, b), g in df.groupby(["a", "b"]):
        valid = g[g["valid"].astype(bool)]
        ab = valid[(valid["initiator"] == a) & (valid["responder"] == b)]
        ba = valid[(valid["initiator"] == b) & (valid["responder"] == a)]
        rows.append(
            {
                "pair": f"{a}-{b}",
                f"{a}->{b}": int(len(ab)),
                f"{b}->{a}": int(len(ba)),
                "median_all_mm": float(pd.to_numeric(valid["dist_mm"], errors="coerce").median()),
                "quality_median": float(pd.to_numeric(valid["quality_percent"], errors="coerce").median())
                if "quality_percent" in valid
                else np.nan,
            }
        )
    return rows


def motive_export_info(path: Path) -> dict:
    if not path.exists():
        return {"path": str(path), "exists": False}
    with path.open("r", errors="replace", newline="") as f:
        rows = []
        reader = csv.reader(f, delimiter="\t")
        for i, row in enumerate(reader):
            if i >= 10:
                break
            rows.append(row)
    text_fields = [field.strip() for row in rows for field in row if field.strip()]
    units = "mm" if any(field == "mm" for field in text_fields) else "unknown"
    marker_names = sorted(
        {
            field.strip()
            for field in text_fields
            if re.match(r"^(Responder:)?[A-I][A-Za-z0-9]*(antenna|center)?$", field.strip())
            or re.match(r"^I[1-5]$", field.strip())
        }
    )
    first_data_line = None
    n_data = 0
    with path.open("r", errors="replace") as f:
        for idx, line in enumerate(f, start=1):
            parts = line.strip().split("\t")
            if len(parts) >= 20:
                try:
                    int(float(parts[0]))
                    float(parts[1])
                    first_data_line = first_data_line or idx
                    n_data += 1
                except ValueError:
                    pass
    return {
        "path": str(path),
        "exists": True,
        "first_line": text_fields[0] if text_fields else "",
        "units": units,
        "header_rows_sampled": len(rows),
        "markers_in_header_sample": len(marker_names),
        "frame_rows": n_data,
        "first_data_line": first_data_line or "-",
    }


def load_anchor_truth(inputs: AuditInputs) -> tuple[pd.DataFrame, pd.DataFrame]:
    if not inputs.derived_tables_dir:
        return pd.DataFrame(), pd.DataFrame()
    by_file_path = inputs.derived_tables_dir / "opti_anchor_medians_by_file.csv"
    if not by_file_path.exists():
        return pd.DataFrame(), pd.DataFrame()
    by_file = pd.read_csv(by_file_path)
    truth = (
        by_file.groupby("anchor", as_index=False)
        .agg(
            x_mm=("x_mm", "median"),
            y_vertical_mm=("y_vertical_mm", "median"),
            z_mm=("z_mm", "median"),
            files=("file_id", "nunique"),
            n_valid_median=("n_valid", "median"),
            std_3d_median_mm=("std_3d_mm", "median"),
        )
        .sort_values("anchor")
    )
    return by_file, truth


def load_tag_truth(inputs: AuditInputs) -> pd.DataFrame:
    if not inputs.derived_tables_dir:
        return pd.DataFrame()
    path = inputs.derived_tables_dir / "tag_ground_truth_correction_summary.csv"
    if not path.exists():
        return pd.DataFrame()
    return pd.read_csv(path)


def pairwise_truth_distances(anchor_truth: pd.DataFrame) -> dict[str, float]:
    if anchor_truth.empty:
        return {}
    coords = {
        row["anchor"]: np.array([row["x_mm"], row["y_vertical_mm"], row["z_mm"]], dtype=float)
        for _, row in anchor_truth.iterrows()
    }
    out = {}
    for i, a in enumerate(ANCHOR_LABELS):
        for b in ANCHOR_LABELS[i + 1 :]:
            if a in coords and b in coords:
                out[f"{a}-{b}"] = float(np.linalg.norm(coords[a] - coords[b]))
    return out


def sweep_unit_check(df: pd.DataFrame, anchor_truth: pd.DataFrame) -> dict:
    truth = pairwise_truth_distances(anchor_truth)
    if df.empty or not truth:
        return {}
    valid = df[df["valid"].astype(bool)].copy()
    valid["pair"] = valid[["a", "b"]].astype(str).agg("-".join, axis=1)
    med = valid.groupby("pair")["dist_mm"].median()
    ratios = []
    rows = []
    for pair, measured in med.items():
        if pair not in truth:
            continue
        ratio = float(measured) / truth[pair]
        ratios.append(ratio)
        rows.append(
            {
                "pair": pair,
                "sweep_median_mm": float(measured),
                "vicon_mm": truth[pair],
                "ratio": ratio,
            }
        )
    if not ratios:
        return {}
    return {
        "rows": rows,
        "median_ratio": float(np.median(ratios)),
        "min_ratio": float(np.min(ratios)),
        "max_ratio": float(np.max(ratios)),
        "n_pairs": len(ratios),
    }


def tag_static_unit_check(static_df: pd.DataFrame, anchor_truth: pd.DataFrame, tag_truth: pd.DataFrame) -> dict:
    if static_df.empty or anchor_truth.empty or tag_truth.empty:
        return {}
    anchor_coords = {
        row["anchor"]: np.array([row["x_mm"], row["y_vertical_mm"], row["z_mm"]], dtype=float)
        for _, row in anchor_truth.iterrows()
    }
    tag_coords = {
        row["ID"]: np.array(
            [
                row["corrected_iantenna_x_mm"],
                row["corrected_iantenna_y_vertical_mm"],
                row["corrected_iantenna_z_mm"],
            ],
            dtype=float,
        )
        for _, row in tag_truth.iterrows()
    }
    work = static_df[valid_mask(static_df)].copy()
    work["anchor_label"] = work["anchor_id"].map(lambda x: ANCHOR_LABELS[int(x)] if pd.notna(x) and int(x) < 8 else "")
    med = work.groupby(["capture_id", "anchor_label"])["range_mm"].median()
    ratios = []
    for (capture_id, anchor), measured in med.items():
        if capture_id not in tag_coords or anchor not in anchor_coords:
            continue
        d = float(np.linalg.norm(tag_coords[capture_id] - anchor_coords[anchor]))
        if d > 0:
            ratios.append(float(measured) / d)
    if not ratios:
        return {}
    return {
        "n_links": len(ratios),
        "median_ratio": float(np.median(ratios)),
        "min_ratio": float(np.min(ratios)),
        "max_ratio": float(np.max(ratios)),
    }


def numeric_range_summary(df: pd.DataFrame, column: str) -> dict:
    if df.empty or column not in df:
        return {"column": column, "min": np.nan, "median": np.nan, "p95": np.nan, "max": np.nan}
    vals = pd.to_numeric(df[column], errors="coerce").dropna()
    if vals.empty:
        return {"column": column, "min": np.nan, "median": np.nan, "p95": np.nan, "max": np.nan}
    return {
        "column": column,
        "min": float(vals.min()),
        "median": float(vals.median()),
        "p95": float(vals.quantile(0.95)),
        "max": float(vals.max()),
    }


def session_notes_summary(inputs: AuditInputs) -> tuple[pd.DataFrame, list[dict]]:
    path = inputs.capture_root / "session_notes.csv"
    if not path.exists():
        return pd.DataFrame(), []
    notes = pd.read_csv(path)
    rows = []
    for typ, group in notes.groupby("type", dropna=False):
        rows.append(
            {
                "type": typ,
                "rows": int(len(group)),
                "first_timestamp": str(group["timestamp"].min()),
                "last_timestamp": str(group["timestamp"].max()),
                "nonzero_rc_rows": int(group["notes"].astype(str).str.contains(r"rc=[1-9]", regex=True).sum())
                if "notes" in group
                else 0,
            }
        )
    return notes, sorted(rows, key=lambda r: str(r["type"]))


def render_report(
    *,
    inputs: AuditInputs,
    workspace: dict,
    session_notes_rows: list[dict],
    sweep_df: pd.DataFrame,
    sweep_source: str,
    static_df: pd.DataFrame,
    static_inventory: list[dict],
    roto_df: pd.DataFrame,
    roto_inventory: list[dict],
    anchor_by_file: pd.DataFrame,
    anchor_truth: pd.DataFrame,
    tag_truth: pd.DataFrame,
    opti_infos: list[dict],
) -> str:
    lines: list[str] = []
    lines.append("# Phase 0 Data Audit")
    lines.append("")
    lines.append(f"- Generated: `{datetime.now().isoformat(timespec='seconds')}`")
    lines.append(f"- Data dir: `{inputs.data_dir}`")
    lines.append(f"- Capture root: `{relpath(inputs.capture_root, inputs.data_dir)}`")
    lines.append(f"- Vicon capture root: `{relpath(inputs.opti_root, inputs.data_dir)}`")
    lines.append(f"- Workspace name: `{workspace.get('workspace_name', '-')}`")
    lines.append("")

    lines.append("## Inventory")
    lines.append("")
    lines.append(markdown_table(session_notes_rows, ["type", "rows", "first_timestamp", "last_timestamp", "nonzero_rc_rows"]))
    lines.append("")
    opti_full_csv_count = len(list((inputs.opti_root / "full").glob("*.csv"))) if (inputs.opti_root / "full").exists() else 0
    opti_static_trc_count = len(list((inputs.opti_root / "static").glob("*.trc"))) if (inputs.opti_root / "static").exists() else 0
    inv_rows = [
        {
            "dataset": "sweep",
            "captures_found": len(list(inputs.capture_root.glob("sweep_*"))),
            "usable_files": 1 if not sweep_df.empty else 0,
            "rows": int(len(sweep_df)),
            "source": sweep_source,
        },
        {
            "dataset": "static tag",
            "captures_found": len(static_inventory),
            "usable_files": sum(1 for x in static_inventory if x["status"] == "ok"),
            "rows": int(len(static_df)),
            "source": "captures/*/tag_capture_*/tr_all.csv",
        },
        {
            "dataset": "roto tag",
            "captures_found": len(roto_inventory),
            "usable_files": sum(1 for x in roto_inventory if x["status"] == "ok"),
            "rows": int(len(roto_df)),
            "source": "captures/*/tag_capture_*/tr_all.csv",
        },
        {
            "dataset": "Vicon raw full",
            "captures_found": opti_full_csv_count,
            "usable_files": opti_full_csv_count,
            "rows": "",
            "source": "opti_captures/full/*.csv",
        },
        {
            "dataset": "Vicon raw static",
            "captures_found": opti_static_trc_count,
            "usable_files": opti_static_trc_count,
            "rows": "",
            "source": "opti_captures/static/*.trc",
        },
    ]
    lines.append(markdown_table(inv_rows, ["dataset", "captures_found", "usable_files", "rows", "source"]))
    lines.append("")

    missing_static = [x for x in static_inventory if x["status"] != "ok"]
    missing_roto = [x for x in roto_inventory if x["status"] != "ok"]
    if missing_static or missing_roto:
        lines.append("### Missing Capture Files")
        lines.append("")
        rows = []
        for entry in missing_static + missing_roto:
            rows.append(
                {
                    "id": entry["id"],
                    "status": entry["status"],
                    "path": relpath(entry["path"], inputs.data_dir),
                }
            )
        lines.append(markdown_table(rows, ["id", "status", "path"]))
        lines.append("")

    lines.append("## Schemas")
    lines.append("")
    if not sweep_df.empty:
        lines.append(f"### Sweep Pairs (`{sweep_source}`)")
        lines.append("")
        lines.append(markdown_table(dataframe_schema(sweep_df), ["column", "dtype", "non_null", "nulls", "sample"]))
        lines.append("")
    if not static_df.empty:
        first_static = next((x["tr_all"] for x in static_inventory if x["status"] == "ok"), None)
        lines.append(f"### Static Tag Ranges (`{relpath(first_static, inputs.data_dir)}` example)")
        lines.append("")
        lines.append(markdown_table(dataframe_schema(static_df.drop(columns=["capture_path"], errors="ignore")), ["column", "dtype", "non_null", "nulls", "sample"]))
        lines.append("")
    if not roto_df.empty:
        first_roto = next((x["tr_all"] for x in roto_inventory if x["status"] == "ok"), None)
        lines.append(f"### Roto Tag Ranges (`{relpath(first_roto, inputs.data_dir)}` example)")
        lines.append("")
        lines.append(markdown_table(dataframe_schema(roto_df.drop(columns=["capture_path"], errors="ignore")), ["column", "dtype", "non_null", "nulls", "sample"]))
        lines.append("")

    lines.append("### Vicon Raw Exports")
    lines.append("")
    lines.append(markdown_table(opti_infos, ["path", "units", "first_line", "markers_in_header_sample", "frame_rows", "first_data_line"]))
    lines.append("")

    if not anchor_by_file.empty:
        lines.append("### Derived Vicon Anchor Table")
        lines.append("")
        lines.append(f"- Source: `{relpath(inputs.derived_tables_dir / 'opti_anchor_medians_by_file.csv', inputs.data_dir)}`")
        lines.append(f"- Rows: `{len(anchor_by_file)}`")
        lines.append(markdown_table(dataframe_schema(anchor_by_file), ["column", "dtype", "non_null", "nulls", "sample"]))
        lines.append("")
    if not tag_truth.empty:
        lines.append("### Derived Static Tag Truth Table")
        lines.append("")
        lines.append(f"- Source: `{relpath(inputs.derived_tables_dir / 'tag_ground_truth_correction_summary.csv', inputs.data_dir)}`")
        lines.append(f"- Rows: `{len(tag_truth)}`")
        lines.append(markdown_table(dataframe_schema(tag_truth), ["column", "dtype", "non_null", "nulls", "sample"]))
        lines.append("")

    lines.append("## Sweep Audit")
    lines.append("")
    sweep_sum = sweep_summary(sweep_df)
    lines.append(markdown_table([sweep_sum], ["rows", "valid_rows", "directed_links", "pairs", "range_median_mm", "quality_median_percent"]))
    lines.append("")
    lines.append("Directed valid sample coverage, row = initiator/master, column = responder:")
    lines.append("")
    lines.append(markdown_table(directed_coverage_matrix(sweep_df), ["from\\to"] + ANCHOR_LABELS))
    lines.append("")
    pair_rows = sweep_pair_counts(sweep_df)
    if pair_rows:
        normalized_pair_rows = []
        for row in pair_rows:
            count_keys = [k for k in row if "->" in k]
            normalized_pair_rows.append(
                {
                    "pair": row["pair"],
                    "dir1_count": row[count_keys[0]] if count_keys else 0,
                    "dir2_count": row[count_keys[1]] if len(count_keys) > 1 else 0,
                    "median_all_mm": row["median_all_mm"],
                    "quality_median": row["quality_median"],
                }
            )
        lines.append("Per-pair directed counts:")
        lines.append("")
        lines.append(markdown_table(normalized_pair_rows, ["pair", "dir1_count", "dir2_count", "median_all_mm", "quality_median"]))
        lines.append("")

    lines.append("## Static Tag Audit")
    lines.append("")
    lines.append(markdown_table([tag_capture_summary(static_df, "static")], ["kind", "captures", "rows", "valid_rows", "valid_percent", "anchors_seen", "peers_seen", "timestamp_start", "timestamp_end", "status_counts"]))
    lines.append("")
    lines.append("Valid sample counts by static position and anchor:")
    lines.append("")
    lines.append(markdown_table(static_position_matrix(static_df), ["position"] + [str(i) for i in range(8)]))
    lines.append("")
    lines.append("Per-position/per-anchor rows are available for Phase 1; first 32 groups:")
    lines.append("")
    lines.append(markdown_table(per_capture_anchor_counts(static_df, max_rows=32), ["capture_id", "peer_name", "anchor_id", "rows", "valid_rows", "valid_percent"]))
    lines.append("")

    lines.append("## Roto Audit")
    lines.append("")
    lines.append(markdown_table([tag_capture_summary(roto_df, "roto")], ["kind", "captures", "rows", "valid_rows", "valid_percent", "anchors_seen", "peers_seen", "timestamp_start", "timestamp_end", "status_counts"]))
    lines.append("")
    lines.append("Per-capture/per-peer/per-anchor rows, first 48 groups:")
    lines.append("")
    lines.append(markdown_table(per_capture_anchor_counts(roto_df, max_rows=48), ["capture_id", "peer_name", "anchor_id", "rows", "valid_rows", "valid_percent"]))
    lines.append("")

    lines.append("## Vicon Truth Availability")
    lines.append("")
    if not anchor_truth.empty:
        lines.append("Anchor truth medians from derived Vicon table:")
        lines.append("")
        rows = []
        for _, row in anchor_truth.iterrows():
            rows.append(
                {
                    "anchor": row["anchor"],
                    "x_mm": row["x_mm"],
                    "y_vertical_mm": row["y_vertical_mm"],
                    "z_mm": row["z_mm"],
                    "files": int(row["files"]),
                    "std_3d_median_mm": row["std_3d_median_mm"],
                }
            )
        lines.append(markdown_table(rows, ["anchor", "x_mm", "y_vertical_mm", "z_mm", "files", "std_3d_median_mm"]))
        lines.append("")
    else:
        lines.append("_No derived anchor truth table found._")
        lines.append("")
    if not tag_truth.empty:
        corrected = int(tag_truth["tag_truth_corrected"].astype(str).str.lower().eq("true").sum())
        lines.append(f"Static tag truth positions found: `{tag_truth['ID'].nunique()}`; corrected/reconstructed positions: `{corrected}`.")
        lines.append("")
    else:
        lines.append("_No derived static tag truth table found._")
        lines.append("")

    lines.append("## Unit Sanity Checks")
    lines.append("")
    range_rows = [
        numeric_range_summary(sweep_df[sweep_df.get("valid", False).astype(bool)] if not sweep_df.empty else sweep_df, "dist_mm"),
        numeric_range_summary(static_df[valid_mask(static_df)] if not static_df.empty else static_df, "range_mm"),
        numeric_range_summary(roto_df[valid_mask(roto_df)] if not roto_df.empty else roto_df, "range_mm"),
    ]
    range_rows[0]["dataset"] = "sweep dist_mm"
    range_rows[1]["dataset"] = "static range_mm"
    range_rows[2]["dataset"] = "roto range_mm"
    lines.append(markdown_table(range_rows, ["dataset", "min", "median", "p95", "max"]))
    lines.append("")
    sw_unit = sweep_unit_check(sweep_df, anchor_truth)
    if sw_unit:
        lines.append(
            f"Sweep/Vicon inter-anchor distance ratio over `{sw_unit['n_pairs']}` pairs: "
            f"median `{sw_unit['median_ratio']:.4f}`, range `{sw_unit['min_ratio']:.4f}`-`{sw_unit['max_ratio']:.4f}`. "
            "This confirms the sweep numeric field is in millimetres; the ratio is intentionally not used as a correction in Phase 0."
        )
        lines.append("")
    static_unit = tag_static_unit_check(static_df, anchor_truth, tag_truth)
    if static_unit:
        lines.append(
            f"Static tag range/Vicon link-distance ratio over `{static_unit['n_links']}` links: "
            f"median `{static_unit['median_ratio']:.4f}`, range `{static_unit['min_ratio']:.4f}`-`{static_unit['max_ratio']:.4f}`. "
            "This is only a coarse unit check; link-level bias modeling belongs to Phase 1."
        )
        lines.append("")

    lines.append("## Audit Notes / Blockers Before Phase 1")
    lines.append("")
    notes = [
        {
            "item": "Directed sweep",
            "status": "OK" if sweep_sum["directed_links"] == 56 else "CHECK",
            "note": "56 directed links expected for 8 anchors.",
        },
        {
            "item": "Static tag positions",
            "status": "OK" if tag_capture_summary(static_df, "static")["captures"] == 24 else "CHECK",
            "note": "24 static positions expected.",
        },
        {
            "item": "Roto captures",
            "status": "OK" if tag_capture_summary(roto_df, "roto")["captures"] >= 17 else "CHECK",
            "note": "Directory includes dynamic captures plus any named static-middle test.",
        },
        {
            "item": "CIR/RX power",
            "status": "ABSENT",
            "note": "No CIR or RX-power columns found. quality_percent/quality_flag_percent are present in range logs.",
        },
        {
            "item": "Per-sample sweep timestamps",
            "status": "LIMITED",
            "note": "Sweep rows have direction and order via round logs/staged CSV, but no per-sample epoch column.",
        },
    ]
    lines.append(markdown_table(notes, ["item", "status", "note"]))
    lines.append("")
    lines.append("Phase 0 stops here. Phase 1 should not run until this audit is reviewed.")
    lines.append("")
    return "\n".join(lines)
