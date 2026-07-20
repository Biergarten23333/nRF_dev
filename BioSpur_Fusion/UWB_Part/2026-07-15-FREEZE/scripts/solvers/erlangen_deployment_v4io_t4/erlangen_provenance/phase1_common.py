from __future__ import annotations

import importlib.util
import itertools
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd

from scripts.audit_helpers import (
    ANCHOR_LABELS,
    discover_inputs,
    list_capture_dirs,
    load_anchor_truth,
    load_sweep_pairs,
    load_tag_capture_frame,
    load_tag_truth,
    markdown_table,
    relpath,
    valid_mask,
)


RNG_SEED = 20260609
DYNAMIC_EXCLUDE_CAPTURE_IDS = {"R01-Static-middle-test"}


@dataclass
class Phase1Data:
    data_dir: Path
    out_dir: Path
    tables_dir: Path
    figures_dir: Path
    fragments_dir: Path
    sweep_df: pd.DataFrame
    sweep_source: str
    static_df: pd.DataFrame
    static_inventory: list[dict]
    roto_df: pd.DataFrame
    roto_inventory: list[dict]
    anchor_by_file: pd.DataFrame
    anchor_truth: pd.DataFrame
    tag_truth: pd.DataFrame
    static_meta: pd.DataFrame


def ensure_phase1_dirs(out_dir: Path) -> tuple[Path, Path, Path]:
    tables_dir = out_dir / "tables"
    figures_dir = out_dir / "figures"
    fragments_dir = out_dir / "fragments"
    for path in (out_dir, tables_dir, figures_dir, fragments_dir):
        path.mkdir(parents=True, exist_ok=True)
    return tables_dir, figures_dir, fragments_dir


def load_phase1_data(data_dir: Path, out_dir: Path) -> Phase1Data:
    data_dir = data_dir.resolve()
    out_dir = out_dir.resolve()
    tables_dir, figures_dir, fragments_dir = ensure_phase1_dirs(out_dir)
    inputs = discover_inputs(data_dir)
    sweep_df, sweep_source = load_sweep_pairs(inputs)
    static_df, static_inventory = load_tag_capture_frame(list_capture_dirs(inputs.capture_root, "static"), "static")
    roto_df, roto_inventory = load_tag_capture_frame(list_capture_dirs(inputs.capture_root, "roto"), "roto")
    anchor_by_file, anchor_truth = load_anchor_truth(inputs)
    tag_truth = load_tag_truth(inputs)
    static_meta = load_static_meta(data_dir)
    return Phase1Data(
        data_dir=data_dir,
        out_dir=out_dir,
        tables_dir=tables_dir,
        figures_dir=figures_dir,
        fragments_dir=fragments_dir,
        sweep_df=sweep_df,
        sweep_source=sweep_source,
        static_df=static_df,
        static_inventory=static_inventory,
        roto_df=roto_df,
        roto_inventory=roto_inventory,
        anchor_by_file=anchor_by_file,
        anchor_truth=anchor_truth,
        tag_truth=tag_truth,
        static_meta=static_meta,
    )


def load_static_meta(data_dir: Path) -> pd.DataFrame:
    candidates = [
        data_dir / "solver" / "outputs" / "v1_to_v4_io_field_check" / "v4-io" / "static_all_captures.csv",
        data_dir / "solver" / "outputs" / "v1_to_v4_io_field_check" / "tables" / "static_all_captures.csv",
    ]
    for path in candidates:
        if path.exists():
            df = pd.read_csv(path)
            return df[df["version"].astype(str).eq("v4-io")].copy() if "version" in df else df
    return pd.DataFrame()


def anchor_coord_map(anchor_truth: pd.DataFrame) -> dict[str, np.ndarray]:
    return {
        row["anchor"]: np.array([row["x_mm"], row["y_vertical_mm"], row["z_mm"]], dtype=float)
        for _, row in anchor_truth.iterrows()
    }


def tag_coord_map(tag_truth: pd.DataFrame) -> dict[str, np.ndarray]:
    return {
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


def pairwise_vicon_distances(anchor_truth: pd.DataFrame) -> dict[str, float]:
    coords = anchor_coord_map(anchor_truth)
    out: dict[str, float] = {}
    for i, a in enumerate(ANCHOR_LABELS):
        for b in ANCHOR_LABELS[i + 1 :]:
            out[f"{a}-{b}"] = float(np.linalg.norm(coords[a] - coords[b]))
    return out


def robust_center(values: Iterable[float]) -> float:
    arr = np.asarray(list(values), dtype=float)
    arr = arr[np.isfinite(arr)]
    if arr.size == 0:
        return float("nan")
    return float(np.median(arr))


def assert_sweep_direction_columns(sweep_df: pd.DataFrame) -> dict:
    required = {"a", "b", "master", "initiator", "responder"}
    missing = sorted(required - set(sweep_df.columns))
    if missing:
        raise AssertionError(f"sweep columns missing: {missing}")
    a_b_sorted = sweep_df[["a", "b"]].astype(str).apply(lambda r: tuple(sorted(r)), axis=1)
    dir_sorted = sweep_df[["initiator", "responder"]].astype(str).apply(lambda r: tuple(sorted(r)), axis=1)
    pair_consistent = bool((a_b_sorted == dir_sorted).all())
    master_is_initiator = bool((sweep_df["master"].astype(str) == sweep_df["initiator"].astype(str)).all())
    self_links = int((sweep_df["initiator"].astype(str) == sweep_df["responder"].astype(str)).sum())
    if not pair_consistent:
        bad = sweep_df.loc[a_b_sorted != dir_sorted].head(5).to_dict("records")
        raise AssertionError(f"(a,b) inconsistent with (initiator,responder), examples={bad}")
    if self_links:
        raise AssertionError(f"sweep has {self_links} self-links")
    if not master_is_initiator:
        bad = sweep_df.loc[sweep_df["master"].astype(str) != sweep_df["initiator"].astype(str)].head(5).to_dict("records")
        raise AssertionError(f"master != initiator, examples={bad}")
    return {
        "pair_columns_consistent": pair_consistent,
        "master_equals_initiator": master_is_initiator,
        "self_links": self_links,
        "direction_definition": "initiator->responder",
    }


def static_session_medians(static_df: pd.DataFrame) -> pd.DataFrame:
    work = static_df[valid_mask(static_df)].copy()
    work["range_mm"] = pd.to_numeric(work["range_mm"], errors="coerce")
    med = work.groupby(["capture_id", "anchor_id"])["range_mm"].median().unstack()
    return med


def compute_anchor_assignment_cost(static_df: pd.DataFrame, anchor_truth: pd.DataFrame, tag_truth: pd.DataFrame) -> np.ndarray:
    med = static_session_medians(static_df)
    anchors = anchor_coord_map(anchor_truth)
    tags = tag_coord_map(tag_truth)
    cost = np.zeros((8, 8), dtype=float)
    for anchor_id in range(8):
        for label_idx, label in enumerate(ANCHOR_LABELS):
            sse = 0.0
            for capture_id, row in med.iterrows():
                if capture_id not in tags or anchor_id not in row or pd.isna(row[anchor_id]):
                    continue
                truth_d = float(np.linalg.norm(tags[capture_id] - anchors[label]))
                residual = float(row[anchor_id]) - truth_d
                sse += residual * residual
            cost[anchor_id, label_idx] = sse
    return cost


def rank_anchor_assignments(cost: np.ndarray, top_n: int = 10) -> list[dict]:
    rows = []
    for perm in itertools.permutations(range(8)):
        total = float(sum(cost[i, perm[i]] for i in range(8)))
        mapping = {i: ANCHOR_LABELS[perm[i]] for i in range(8)}
        rows.append({"rank_cost": total, "mapping": mapping})
    rows.sort(key=lambda r: r["rank_cost"])
    for rank, row in enumerate(rows[:top_n], start=1):
        row["rank"] = rank
        row["mapping_str"] = ", ".join(f"{k}->{v}" for k, v in row["mapping"].items())
        row["rms_mm"] = float(np.sqrt(row["rank_cost"] / (24 * 8)))
    return rows[:top_n]


def write_data_config(data_dir: Path, mapping: dict[int, str], best: dict, second: dict) -> Path:
    reverse = {v: k for k, v in mapping.items()}
    text = (
        "# Auto-generated by verify_anchor_mapping.py. Do not edit by hand.\n"
        "ANCHOR_ID_TO_LABEL = " + repr(mapping) + "\n"
        "ANCHOR_LABEL_TO_ID = " + repr(reverse) + "\n"
        "MAPPING_VERIFICATION = "
        + repr(
            {
                "best_cost": best["rank_cost"],
                "second_best_cost": second["rank_cost"],
                "best_rms_mm": best["rms_mm"],
                "second_best_rms_mm": second["rms_mm"],
                "second_over_best_cost_ratio": second["rank_cost"] / best["rank_cost"],
            }
        )
        + "\n"
    )
    path = data_dir / "data_config.py"
    path.write_text(text)
    return path


def load_data_config(data_dir: Path):
    path = data_dir / "data_config.py"
    if not path.exists():
        raise FileNotFoundError("data_config.py missing; run verify_anchor_mapping.py first")
    spec = importlib.util.spec_from_file_location("data_config", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["data_config"] = module
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def quality_distribution(data_name: str, df: pd.DataFrame, columns: list[str]) -> tuple[list[dict], list[dict]]:
    distribution_rows: list[dict] = []
    summary_rows: list[dict] = []
    for column in columns:
        if column not in df.columns:
            summary_rows.append(
                {
                    "dataset": data_name,
                    "field": column,
                    "rows": int(len(df)),
                    "non_null": 0,
                    "top_value": "",
                    "top_percent": "",
                    "informative": "missing",
                }
            )
            continue
        vals = df[column].dropna()
        counts = vals.value_counts(dropna=False).sort_index()
        non_null = int(vals.shape[0])
        if non_null:
            top_value = counts.idxmax()
            top_count = int(counts.max())
            top_percent = top_count / non_null * 100.0
            informative = "no" if top_percent > 95.0 else "yes"
        else:
            top_value = ""
            top_percent = 0.0
            informative = "missing"
        summary_rows.append(
            {
                "dataset": data_name,
                "field": column,
                "rows": int(len(df)),
                "non_null": non_null,
                "top_value": top_value,
                "top_percent": top_percent,
                "informative": informative,
            }
        )
        for value, count in counts.items():
            distribution_rows.append(
                {
                    "dataset": data_name,
                    "field": column,
                    "value": value,
                    "count": int(count),
                    "percent": float(count / non_null * 100.0) if non_null else 0.0,
                }
            )
    return distribution_rows, summary_rows


def write_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")


def save_markdown_fragment(path: Path, title: str, body: str) -> None:
    path.write_text(f"## {title}\n\n{body.strip()}\n")


def table_or_empty(rows: list[dict], headers: list[str]) -> str:
    return markdown_table(rows, headers) if rows else "_No rows._\n"


def write_dataframe(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False)


def mapping_as_anchor_label(anchor_id: int, mapping: dict[int, str]) -> str:
    return mapping[int(anchor_id)]

