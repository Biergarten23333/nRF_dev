#!/usr/bin/env python3
"""Add the authoritative Chinese action instructions to C2 avatar images."""

from __future__ import annotations

import argparse
import hashlib
import html
import json
from pathlib import Path
from typing import Any

import numpy as np

from biospur_fusion.c2_coupled_progressive.contracts import (
    EPISODES,
    ROOT,
    load_effective_config,
)
from biospur_fusion.c2_coupled_progressive.estimator import SEGMENTS
from biospur_fusion.c2_coupled_progressive.renderer import (
    display_models,
    joints_for_frame,
    render_triptych,
)


ACTION_TABLE = ROOT / (
    "datasets/phase2_calibration/"
    "phase2_targeted_calibration_20260817t130918z_capture_2_with_joint_label_c8645eb2/"
    "subject/ACTUAL_ACTION_EXECUTION_TABLE.md"
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _action_descriptions() -> dict[str, str]:
    descriptions: dict[str, str] = {}
    for line in ACTION_TABLE.read_text(encoding="utf-8").splitlines():
        if not line.startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) != 6 or not cells[0].isdigit():
            continue
        action_id = cells[1].strip("`")
        descriptions[action_id] = cells[5]
    missing = [action for action in EPISODES if action not in descriptions]
    if missing:
        raise RuntimeError(f"Chinese action descriptions missing: {missing}")
    return descriptions


def _load_trajectory(path: Path) -> dict[str, Any]:
    trajectory: dict[str, dict[str, dict[str, np.ndarray]]] = {}
    with np.load(path, allow_pickle=False) as archive:
        for episode_index in range(len(EPISODES)):
            episode_key = f"{episode_index:02d}"
            trajectory[episode_key] = {}
            for segment in SEGMENTS:
                base = f"trajectory/{episode_key}/{segment}"
                trajectory[episode_key][segment] = {
                    "time_root_s": np.array(archive[f"{base}/time_root_s"]),
                    "quat_world_segment_wxyz": np.array(
                        archive[f"{base}/quat_world_segment_wxyz"]
                    ),
                    "mask": np.array(archive[f"{base}/mask"], dtype=bool),
                }
    return {"trajectory": trajectory}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", type=Path, required=True)
    args = parser.parse_args()
    run_dir = args.run_dir.resolve()
    if ROOT not in run_dir.parents:
        raise SystemExit("run directory must remain in canonical Fusion_Part")
    report_path = run_dir / "POSE_RESET_QMT_DIAGNOSTIC.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    trajectory_path = ROOT / report["trajectory"]["path"]
    if _sha256(trajectory_path) != report["trajectory"]["sha256"]:
        raise RuntimeError("source trajectory hash mismatch")
    descriptions = _action_descriptions()
    trajectory = _load_trajectory(trajectory_path)
    config = load_effective_config()
    model = display_models(config)[1]
    output = run_dir / "ANNOTATED_ZH"
    output.mkdir(exist_ok=False)

    rows: list[dict[str, Any]] = []
    for source_row in report["renders"]:
        episode_index = int(source_row["episode_index"])
        episode_key = f"{episode_index:02d}"
        action_id = EPISODES[episode_index]
        description = descriptions[action_id]
        frame = int(source_row["frame"])
        joints = joints_for_frame(
            trajectory, episode_key, frame, model, config
        )
        image_path = output / f"{episode_key}_{action_id}_zh_explained.png"
        caption = (
            f"原始动作说明：{description}\n"
            "图解：左图为正面，中央为侧面，右图为俯视；蓝色为身体左侧，橙色为身体右侧。"
        )
        if action_id in {"12_heel_raise_left", "13_heel_raise_right"}:
            caption += (
                " 注意：十节点拓扑没有足部 IMU，本图只能显示可观测的小腿姿态，"
                "不能把脚跟/踝关节运动本身当作已重建。"
            )
        render_triptych(
            joints,
            image_path,
            f"{action_id}｜frame={frame}",
            caption_zh=caption,
        )
        rows.append({
            "action_id": action_id,
            "episode_index": episode_index,
            "frame": frame,
            "original_instruction_zh": description,
            "source_image": source_row["image"],
            "source_image_sha256": source_row["image_sha256"],
            "annotated_image": str(image_path.relative_to(ROOT)),
            "annotated_image_sha256": _sha256(image_path),
        })

    manifest = {
        "schema": "biospur-c2-avatar-annotated-zh-v1",
        "source_run_status": report["status"],
        "source_report": str(report_path.relative_to(ROOT)),
        "source_report_sha256": _sha256(report_path),
        "source_trajectory": report["trajectory"],
        "action_description_source": str(ACTION_TABLE.relative_to(ROOT)),
        "action_description_source_sha256": _sha256(ACTION_TABLE),
        "trajectory_recomputed": False,
        "calibration_recomputed": False,
        "frames_reselected": False,
        "image_count": len(rows),
        "images": rows,
    }
    manifest_path = output / "ANNOTATED_ZH_MANIFEST.json"
    manifest_path.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    sections = []
    for row in rows:
        image_name = Path(row["annotated_image"]).name
        sections.append(
            "<section>"
            f"<h2>{html.escape(row['action_id'])}</h2>"
            f"<p>{html.escape(row['original_instruction_zh'])}</p>"
            f"<img src=\"{html.escape(image_name)}\" loading=\"lazy\">"
            "</section>"
        )
    index = output / "index.html"
    index.write_text(
        "<!doctype html><meta charset=\"utf-8\">"
        "<title>BioSpur C2 中文动作图解</title>"
        "<style>body{font-family:'Noto Sans CJK SC',sans-serif;max-width:1200px;margin:auto}"
        "section{margin:2rem 0;border-bottom:1px solid #ccc}img{width:100%;height:auto}</style>"
        "<h1>BioSpur C2 火柴人：原始中文动作说明</h1>"
        "<p>姿态、帧号和轨迹均与源诊断运行一致；本页只增加中文图解。</p>"
        + "".join(sections),
        encoding="utf-8",
    )
    print(json.dumps({
        "output": str(output),
        "image_count": len(rows),
        "manifest": str(manifest_path),
        "index": str(index),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
