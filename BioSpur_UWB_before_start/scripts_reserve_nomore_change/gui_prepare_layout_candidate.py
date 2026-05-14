#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


PREFERRED_RESULT_KEYS = [
    "apos_init_huber",
    "apos_init_linear",
    "apos_init_soft_l1",
    "mds_init_huber",
    "mds_init_linear",
    "mds_init_soft_l1",
]


def _load_json(path: Path) -> dict[str, Any]:
    obj = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(obj, dict):
        raise ValueError(f"{path} is not a JSON object")
    return obj


def _pick_result(results: dict[str, Any], explicit: str | None) -> tuple[str, dict[str, Any]]:
    if explicit:
        result = results.get(explicit)
        if not isinstance(result, dict):
            raise KeyError(f"result key not found: {explicit}")
        return explicit, result

    for key in PREFERRED_RESULT_KEYS:
        result = results.get(key)
        if isinstance(result, dict):
            return key, result

    for key, result in results.items():
        if isinstance(result, dict):
            return key, result

    raise ValueError("no usable result found")


def _normalize_anchor(item: dict[str, Any]) -> dict[str, Any]:
    aid = int(item.get("id", item.get("anchor_id")))
    label = str(item.get("label", item.get("name", chr(ord("A") + aid))))
    return {
        "id": aid,
        "label": label,
        "x_mm": int(round(float(item["x_mm"]))),
        "y_mm": int(round(float(item["y_mm"]))),
        "z_mm": int(round(float(item["z_mm"]))),
    }


def build_candidate(payload: dict[str, Any], source_path: Path, result_key: str | None) -> dict[str, Any]:
    if isinstance(payload.get("anchors"), list):
        anchors = [_normalize_anchor(item) for item in payload["anchors"]]
        return {
            "schema": "gui_layout_candidate_v1",
            "source_file": str(source_path),
            "selected_result": payload.get("selected_result", "anchors"),
            "reason": payload.get("reason", "direct anchors payload"),
            "stats": payload.get("stats", {}),
            "optimizer": payload.get("optimizer", {}),
            "anchors": anchors,
        }

    results = payload.get("results")
    if not isinstance(results, dict):
        raise ValueError("input has neither top-level anchors nor results")

    chosen_key, chosen = _pick_result(results, result_key)
    anchors_raw = chosen.get("anchors")
    if not isinstance(anchors_raw, list):
        raise ValueError(f"result {chosen_key} has no anchors list")

    anchors = [_normalize_anchor(item) for item in anchors_raw]
    return {
        "schema": "gui_layout_candidate_v1",
        "source_file": str(source_path),
        "selected_result": chosen_key,
        "reason": payload.get("reason", "preferred result selection"),
        "stats": chosen.get("stats", {}),
        "optimizer": chosen.get("optimizer", {}),
        "anchors": anchors,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Normalize solver/layout JSON into one GUI candidate layout file.")
    ap.add_argument("--input", required=True, help="Solver JSON or layout JSON")
    ap.add_argument("--output", required=True, help="Candidate JSON output path")
    ap.add_argument("--result-key", help="Explicit result key for multi-result solve JSON")
    args = ap.parse_args()

    input_path = Path(args.input)
    output_path = Path(args.output)
    payload = _load_json(input_path)
    candidate = build_candidate(payload, input_path, args.result_key)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(candidate, indent=2) + "\n", encoding="utf-8")
    print(f"[ok] candidate={output_path} selected_result={candidate['selected_result']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
